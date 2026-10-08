"""Leakage-safe splitting (Phase 12.3). The split unit is contract_id —
clauses from one contract NEVER span splits. Deterministic via
sha256(seed:contract_id); the assignment is frozen into splits.json and
rebuilding the same version is refused (a new version is required).
"""
from __future__ import annotations

import hashlib
import json
import os


def assign_split(contract_id: str, seed: int,
                 ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)) -> str:
    h = int(hashlib.sha256(f"{seed}:{contract_id}".encode()).hexdigest(), 16) % 1000
    cut_train = int(ratios[0] * 1000)
    cut_val = cut_train + int(ratios[1] * 1000)
    if h < cut_train:
        return "train"
    if h < cut_val:
        return "validation"
    return "test"


def split_examples(examples: list[dict], seed: int,
                   ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)
                   ) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"train": [], "validation": [], "test": []}
    for ex in examples:
        out[assign_split(ex["contract_id"], seed, ratios)].append(ex)
    return out


def split_contract_ids(contract_ids: list[str], seed: int,
                       ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)
                       ) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    for cid in contract_ids:
        out[assign_split(cid, seed, ratios)].append(cid)
    return out


def verify_no_overlap(splits: dict[str, list[dict]]) -> dict:
    """Contract-id disjointness across splits. Raises on leakage."""
    sets = {k: {e["contract_id"] for e in v} for k, v in splits.items()}
    pairs = [("train", "validation"), ("train", "test"), ("validation", "test")]
    overlap = {f"{a}x{b}": sorted(sets[a] & sets[b]) for a, b in pairs}
    leaked = {k: v for k, v in overlap.items() if v}
    if leaked:
        raise ValueError(f"Split leakage detected: {leaked}")
    return {"train_contracts": len(sets["train"]),
            "validation_contracts": len(sets["validation"]),
            "test_contracts": len(sets["test"]),
            "overlap": overlap}


def write_splits_artifact(splits: dict[str, list[dict]], path: str) -> dict:
    """Freeze assignment: contract_id -> split (+ content hash)."""
    mapping = {}
    for split, rows in splits.items():
        for ex in rows:
            mapping[ex["example_id"]] = {"split": split,
                                         "contract_id": ex["contract_id"]}
    blob = json.dumps(mapping, sort_keys=True).encode()
    digest = hashlib.sha256(blob).hexdigest()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(blob.decode())
    return {"path": path, "sha256": digest, "examples": len(mapping)}
