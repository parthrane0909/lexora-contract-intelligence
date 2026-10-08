"""Reproducible dataset pipeline (Phase 3.1). Preparation only — no training here.

Sources:
  LEDGAR via HF parquet mirror MAdAiLab/lex_glue_ledgar (official LexGLUE
    train/validation/test splits; provisions with integer labels 0-99).
  CUAD v1 JSON (theatticusproject/cuad, CC-BY-4.0): expert answer spans →
    clause = containing paragraph of the contract text.

Leakage safeguards:
  1. LEDGAR: keep official splits; drop val/test rows whose normalized text
     also appears in train (exact-match dedupe, within + across datasets).
  2. CUAD: split BY CONTRACT (seed-fixed shuffle, 80/10/10) — one contract's
     clauses never appear in two splits — then the same cross-split dedupe.

Balancing: per-class caps (train smaller than val/test), sampling evenly
across source labels inside "other" so the majority class stays diverse.

Output: train.jsonl / validation.jsonl / test.jsonl + manifest.json
(version, source shas, mapping digest, caps, seed, per-class counts,
CUAD contract split lists). Re-running with the same flags is deterministic.

Usage:
  python -m app.modules.ml_nlp.dataset --out ml/data/clause_v1 \\
      --cuad-json /path/to/CUAD_v1.json --train-cap 500 --seed 42
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
from collections import Counter, defaultdict

from app.modules.ml_nlp.mapping import (
    CUAD_EXCLUDED,
    CUAD_TO_TAXONOMY,
    LEDGAR_OTHER_IDS,
    LEDGAR_TO_TAXONOMY,
    extract_cuad_category,
)
from app.modules.ml_nlp.taxonomy import MODEL_LABELS

DATASET_VERSION = "clause_v1"
LEDGAR_DATASET_ID = "MAdAiLab/lex_glue_ledgar"

MAX_CLAUSE_CHARS = 1500  # truncate pathological paragraphs; recorded in manifest


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def sha_short(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode() if isinstance(p, str) else p)
    return h.hexdigest()[:12]


def file_sha_short(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


# ------------------------------------------------------------- LEDGAR ----


def load_ledgar() -> dict[str, list[dict]]:
    """Load official splits. Returns {split: [{text, ledgar_id, contract_id}]}."""
    from datasets import load_dataset

    ds = load_dataset(LEDGAR_DATASET_ID)
    out: dict[str, list[dict]] = {}
    for split in ("train", "validation", "test"):
        rows = []
        for i, ex in enumerate(ds[split]):
            rows.append({"text": ex["text"], "ledgar_id": int(ex["label"]),
                         "contract_id": f"ledgar-{split}-{i}"})
        out[split] = rows
    return out


def map_ledgar(rows: list[dict]) -> list[dict]:
    """Integer label → taxonomy ("other" fallback is explicit, see mapping.py)."""
    out = []
    for r in rows:
        lid = r["ledgar_id"]
        cat = LEDGAR_TO_TAXONOMY.get(lid, "other")
        assert lid in LEDGAR_TO_TAXONOMY or lid in LEDGAR_OTHER_IDS
        out.append({"text": r["text"], "label": cat, "source": "ledgar",
                    "source_label": str(lid), "contract_id": r["contract_id"]})
    return out


# --------------------------------------------------------------- CUAD ----


def span_to_clause(context: str, answer_start: int, span_len: int) -> str:
    """Clause = paragraph containing the span; windowed if pathological."""
    paras = [p for p in re.split(r"\n\s*\n", context) if p.strip()]
    if not paras:
        return context.strip()[:MAX_CLAUSE_CHARS]
    # Locate paragraph by cumulative offsets.
    pos = 0
    chosen = paras[-1]
    for p in paras:
        idx = context.find(p, pos)
        start = idx if idx != -1 else pos
        end = start + len(p)
        if start <= answer_start < end:
            chosen = p
            break
        pos = end
    chosen = chosen.strip()
    if len(chosen) <= MAX_CLAUSE_CHARS:
        return chosen
    center = max(0, answer_start - (context.find(chosen) if context.find(chosen) != -1 else 0))
    lo = max(0, center - MAX_CLAUSE_CHARS // 2)
    return chosen[lo:lo + MAX_CLAUSE_CHARS].strip()


def load_cuad(json_path: str) -> tuple[list[dict], list[str]]:
    """Parse CUAD JSON → rows. Returns (rows, unknown_questions_or_empty)."""
    with open(json_path) as f:
        data = json.load(f)
    rows: list[dict] = []
    for contract in data["data"]:
        title = contract.get("title", "unknown")
        for para in contract.get("paragraphs", []):
            context = para.get("context", "")
            for qa in para.get("qas", []):
                cat = extract_cuad_category(qa["question"])  # raises on unknown
                if cat in CUAD_EXCLUDED:
                    continue
                seen: set[tuple[int, str]] = set()
                for a in qa.get("answers", []):
                    text, start = (a.get("text") or "").strip(), int(a.get("answer_start", 0))
                    if not text or (start, text) in seen:
                        continue
                    seen.add((start, text))
                    clause = span_to_clause(context, start, len(text))
                    if not clause:
                        continue
                    rows.append({"text": clause, "label": CUAD_TO_TAXONOMY[cat],
                                 "source": "cuad", "source_label": cat, "contract_id": title})
    return rows, []


def split_cuad_by_contract(rows: list[dict], seed: int,
                            ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)
                            ) -> dict[str, list[dict]]:
    """Whole contracts go to exactly one split (no cross-split contract leakage)."""
    contracts = sorted({r["contract_id"] for r in rows})
    rng = random.Random(seed)
    rng.shuffle(contracts)
    n = len(contracts)
    n_train = int(n * ratios[0])
    n_val = int(n * ratios[1])
    groups = {"train": set(contracts[:n_train]),
              "validation": set(contracts[n_train:n_train + n_val]),
              "test": set(contracts[n_train + n_val:])}
    return {s: [r for r in rows if r["contract_id"] in groups[s]] for s in groups}


# ------------------------------------------------------- dedupe + caps ----


def dedupe_splits(splits: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Drop val/test rows whose normalized text appears in train (any source)."""
    train_texts = {norm_text(r["text"]) for r in splits["train"]}
    out = {"train": splits["train"]}
    for s in ("validation", "test"):
        kept, dropped = [], 0
        for r in splits[s]:
            if norm_text(r["text"]) in train_texts:
                dropped += 1
            else:
                kept.append(r)
                train_texts.add(norm_text(r["text"]))  # test also deduped vs val
        out[s] = kept
        print(f"dedupe: dropped {dropped} {s} rows seen in train/earlier splits")
    return out


def cap_per_class(rows: list[dict], cap: int, seed: int) -> list[dict]:
    """Cap examples per class; inside a class sample evenly across sources
    and source labels (keeps 'other' diverse instead of first-N)."""
    rng = random.Random(seed)
    by_class: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["label"]].append(r)
    out = []
    for label, items in by_class.items():
        if len(items) <= cap:
            out.extend(items)
            continue
        by_source: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for r in items:
            by_source[(r["source"], r["source_label"])].append(r)
        per, picked = max(1, cap // len(by_source)), []
        for group in by_source.values():
            rng.shuffle(group)
            picked.extend(group[:per])
        rng.shuffle(picked)
        out.extend(picked[:cap])
    rng.shuffle(out)
    return out


# ------------------------------------------------------------- assemble ----


def build_dataset(cuad_json: str, train_cap: int, eval_cap: int | None, seed: int,
                  out_dir: str) -> dict:
    ledgar = load_ledgar()
    cuad_rows, _ = load_cuad(cuad_json)
    cuad_splits = split_cuad_by_contract(cuad_rows, seed)
    splits = {s: map_ledgar(ledgar[s]) + cuad_splits[s] for s in ("train", "validation", "test")}
    for s in splits:
        random.Random(seed).shuffle(splits[s])
    splits = dedupe_splits(splits)
    splits["train"] = cap_per_class(splits["train"], train_cap, seed)
    if eval_cap:
        for s in ("validation", "test"):
            splits[s] = cap_per_class(splits[s], eval_cap, seed)

    os.makedirs(out_dir, exist_ok=True)
    counts = {}
    for s, rows in splits.items():
        with open(os.path.join(out_dir, f"{s}.jsonl"), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        counts[s] = dict(Counter(r["label"] for r in rows))
    manifest = {
        "dataset_version": DATASET_VERSION,
        "sources": {"ledgar": LEDGAR_DATASET_ID,
                     "cuad": "theatticusproject/cuad CUAD_v1.json "
                             f"sha16={file_sha_short(cuad_json)}"},
        "mapping": "app.modules.ml_nlp.mapping (LEDGAR_TO_TAXONOMY + CUAD_TO_TAXONOMY)",
        "labels": MODEL_LABELS,
        "seed": seed, "train_cap": train_cap, "eval_cap": eval_cap,
        "max_clause_chars": MAX_CLAUSE_CHARS,
        "counts": counts,
        "cuad_contracts": {s: sorted({r["contract_id"] for r in splits[s] if r["source"] == "cuad"})
                           for s in splits},
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Build clause-classification dataset")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cuad-json", required=True)
    ap.add_argument("--train-cap", type=int, default=500)
    ap.add_argument("--eval-cap", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    manifest = build_dataset(args.cuad_json, args.train_cap, args.eval_cap, args.seed, args.out)
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    main()
