"""Dataset versioning, artifacts & quality gates (Phase 12.2/12.4).

A version is immutable once written: same version + different content is
refused (new version required). Artifacts per version:
  examples.jsonl, splits.json (frozen assignment), manifest.json, quality.json
Quality problems are RECORDED with reasons — never silently dropped.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from collections import Counter

from app.modules.ml_evaluation.dataset import norm_text
from app.modules.ml_evaluation.splits import (
    split_examples,
    verify_no_overlap,
    write_splits_artifact,
)

PREPROCESSING_VERSION = "v1"
LABEL_MAPPING_VERSION = "ml_nlp.taxonomy.v1"
SPLIT_VERSION = "contract-sha256-seed.v1"


def dataset_dir(base: str, version: str) -> str:
    return os.path.join(base, version)


def version_exists(base: str, version: str) -> bool:
    return os.path.exists(os.path.join(dataset_dir(base, version), "manifest.json"))


def _sha_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def quality_report(examples: list[dict], splits: dict,
                   valid_labels: tuple[str, ...]) -> dict:
    """Duplicates, empties, unknown labels, imbalance, contradictions —
    every exclusion carries its reason."""
    seen_text: dict[str, str] = {}
    dup_text, empty, short, unknown, contradictory = [], [], [], [], []
    gold_by_clause: dict[str, set] = {}
    for ex in examples:
        if not ex["text"].strip():
            empty.append(ex["example_id"])
            continue
        if len(ex["text"].split()) < 3:
            short.append(ex["example_id"])
        key = norm_text(ex["text"])
        if key in seen_text:
            dup_text.append(ex["example_id"])
        else:
            seen_text[key] = ex["example_id"]
        if ex["gold_label"] is not None and ex["gold_label"] not in valid_labels:
            unknown.append(ex["example_id"])
        if ex["gold_label"] is not None:
            gold_by_clause.setdefault(ex["clause_id"], set()).add(ex["gold_label"])
    for cid, golds in gold_by_clause.items():
        if len(golds) > 1:
            contradictory.append({"clause_id": cid, "golds": sorted(golds)})
    usable = [e for e in examples if e["usable_supervised"]]
    dist = dict(Counter(e["gold_label"] for e in usable))
    counts = Counter(len(v) for v in [usable])
    report = {
        "n_examples": len(examples),
        "n_usable_supervised": len(usable),
        "duplicate_text": dup_text,
        "empty_text": empty,
        "short_text_lt3_tokens": short,
        "unknown_labels": unknown,
        "contradictory_labels": contradictory,
        "class_distribution_usable": dist,
        "class_imbalance_ratio": (
            round(max(dist.values()) / min(dist.values()), 2) if len(dist) > 1 else None),
        "split_counts": {k: len(v) for k, v in splits.items()},
    }
    return report


def build_versioned_dataset(examples: list[dict], *, base_dir: str, version: str,
                            seed: int, valid_labels: tuple[str, ...],
                            ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
                            supervised_only: bool = True) -> dict:
    """Write an immutable dataset version. Raises if the version exists."""
    if version_exists(base_dir, version):
        raise ValueError(f"Dataset version {version!r} already exists — "
                         f"create a new version instead of mutating it.")
    pool = [e for e in examples if e["usable_supervised"]] if supervised_only else list(examples)
    splits = split_examples(pool, seed, ratios)
    leakage = verify_no_overlap(splits)  # raises on leakage
    outdir = dataset_dir(base_dir, version)
    os.makedirs(outdir, exist_ok=False)
    with open(os.path.join(outdir, "examples.jsonl"), "w") as f:
        for ex in pool:
            f.write(json.dumps(ex) + "\n")
    for split, rows in splits.items():
        with open(os.path.join(outdir, f"{split}.jsonl"), "w") as f:
            for ex in rows:
                f.write(json.dumps({"text": ex["text"], "label": ex["gold_label"],
                                    "example_id": ex["example_id"],
                                    "contract_id": ex["contract_id"]}) + "\n")
    frozen = write_splits_artifact(splits, os.path.join(outdir, "splits.json"))
    quality = quality_report(examples, splits, valid_labels)
    manifest = {
        "dataset_version": version,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seed": seed, "ratios": list(ratios), "supervised_only": supervised_only,
        "preprocessing_version": PREPROCESSING_VERSION,
        "label_mapping_version": LABEL_MAPPING_VERSION,
        "split_version": SPLIT_VERSION,
        "source_examples": len(examples),
        "usable_examples": len(pool),
        "splits_sha256": frozen["sha256"],
        "files_sha256": {name: _sha_file(os.path.join(outdir, name)) for name in
                         ("examples.jsonl", "train.jsonl", "validation.jsonl",
                          "test.jsonl", "splits.json")},
        "quality": quality,
    }
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    with open(os.path.join(outdir, "quality.json"), "w") as f:
        json.dump(quality, f, indent=2)
    return {"version": version, "path": outdir, "manifest": manifest,
            "leakage": leakage, "split_counts": {k: len(v) for k, v in splits.items()}}
