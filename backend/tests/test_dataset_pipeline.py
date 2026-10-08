"""Dataset pipeline units: pure functions on synthetic rows (no downloads)."""
from app.modules.ml_nlp.dataset import (
    cap_per_class,
    dedupe_splits,
    map_ledgar,
    norm_text,
    span_to_clause,
    split_cuad_by_contract,
)


def _rows():
    return [
        {"text": "Pay within 30 days.", "ledgar_id": 68, "contract_id": "l-1"},
        {"text": "Fees are due monthly.", "ledgar_id": 42, "contract_id": "l-2"},
        {"text": "Entire agreement clause.", "ledgar_id": 38, "contract_id": "l-3"},
    ]


def test_map_ledgar_explicit_other():
    out = map_ledgar(_rows())
    assert [r["label"] for r in out] == ["payment", "payment", "other"]
    assert all(r["source"] == "ledgar" for r in out)


def test_span_to_clause_picks_containing_paragraph():
    ctx = "First paragraph about payment terms here.\n\nSecond paragraph about liability caps there."
    span_at = ctx.find("liability")
    clause = span_to_clause(ctx, span_at, len("liability"))
    assert "liability" in clause and "payment" not in clause
    # Pathological paragraph gets windowed, never huge.
    big = "word " * 2000
    assert len(span_to_clause(big, 10, 4)) <= 1500


def test_split_by_contract_disjoint_and_deterministic():
    rows = [{"text": f"clause {i}", "label": "other", "source": "cuad",
             "source_label": "x", "contract_id": f"c{i % 10}"} for i in range(100)]
    a = split_cuad_by_contract(rows, seed=7)
    b = split_cuad_by_contract(rows, seed=7)
    assert {r["text"] for r in a["train"]} == {r["text"] for r in b["train"]}
    contracts = [{r["contract_id"] for r in a[s]} for s in ("train", "validation", "test")]
    assert contracts[0].isdisjoint(contracts[1]) and contracts[0].isdisjoint(contracts[2])


def test_dedupe_drops_cross_split_duplicates():
    splits = {
        "train": [{"text": "Same text here", "label": "payment"}],
        "validation": [{"text": "  same   TEXT here ", "label": "payment"},
                       {"text": "unique val", "label": "other"}],
        "test": [{"text": "Same text here", "label": "payment"},
                 {"text": "unique test", "label": "other"}],
    }
    out = dedupe_splits(splits)
    assert [r["text"] for r in out["validation"]] == ["unique val"]
    assert [r["text"] for r in out["test"]] == ["unique test"]


def test_cap_per_class_respected_and_seeded():
    rows = [{"text": f"t{i}", "label": "payment" if i < 50 else "other",
             "source": "ledgar", "source_label": str(i % 5), "contract_id": f"c{i}"}
            for i in range(100)]
    out = cap_per_class(rows, cap=10, seed=1)
    from collections import Counter
    assert Counter(r["label"] for r in out) == {"payment": 10, "other": 10}
    again = cap_per_class(rows, cap=10, seed=1)
    assert [r["text"] for r in out] == [r["text"] for r in again]


def test_norm_text():
    assert norm_text("  Pay\n WITHIN\t30 days. ") == "pay within 30 days."
