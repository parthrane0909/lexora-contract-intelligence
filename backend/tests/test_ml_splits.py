"""Split tests: contract-level integrity, determinism, frozen artifacts."""
import pytest

from app.modules.ml_evaluation.splits import (
    assign_split,
    split_contract_ids,
    split_examples,
    verify_no_overlap,
    write_splits_artifact,
)


def _ex(cid, i=0):
    return {"example_id": f"e-{cid}-{i}", "contract_id": cid, "text": "t",
            "gold_label": "payment"}


def test_contract_level_no_overlap():
    contracts = [f"contract-{i}" for i in range(60)]
    examples = [e for c in contracts for e in (_ex(c, 0), _ex(c, 1))]
    splits = split_examples(examples, seed=42)
    report = verify_no_overlap(splits)
    assert report["train_contracts"] + report["validation_contracts"] + \
        report["test_contracts"] == 60
    assert all(v == [] for v in report["overlap"].values())
    # every example follows its contract
    lookup = {}
    for split, rows in splits.items():
        for ex in rows:
            lookup.setdefault(ex["contract_id"], set()).add(split)
    assert all(len(v) == 1 for v in lookup.values())


def test_deterministic_seed_and_stability():
    a = split_contract_ids([f"c{i}" for i in range(50)], seed=7)
    b = split_contract_ids([f"c{i}" for i in range(50)], seed=7)
    assert a == b
    c = split_contract_ids([f"c{i}" for i in range(50)], seed=8)
    assert c != a  # seed matters


def test_verify_detects_leakage():
    splits = {"train": [_ex("c1")], "validation": [_ex("c1")], "test": []}
    with pytest.raises(ValueError, match="leakage"):
        verify_no_overlap(splits)


def test_frozen_artifact_roundtrip(tmp_path):
    examples = [_ex(f"c{i}") for i in range(10)]
    splits = split_examples(examples, seed=1)
    info = write_splits_artifact(splits, str(tmp_path / "splits.json"))
    assert info["examples"] == 10 and len(info["sha256"]) == 64
    import json

    with open(tmp_path / "splits.json") as f:
        frozen = json.load(f)
    assert set(frozen) == {f"e-c{i}-0" for i in range(10)}
    assert all(v["split"] in ("train", "validation", "test") for v in frozen.values())


def test_assign_split_distribution_sane():
    buckets = [assign_split(f"c{i}", seed=42) for i in range(1000)]
    train = sum(1 for b in buckets if b == "train")
    assert 700 < train < 900  # ~80% deterministic hash bucketing
