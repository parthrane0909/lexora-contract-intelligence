"""Metrics unit tests (pure, hand-computed)."""
from app.modules.ml_evaluation import metrics as M

LABELS = ["a", "b", "c"]
# a: 2/2 right; b: 1 right 1 missed(as c); c: 0 right, 2 missed(as a, b)
Y_TRUE = [0, 0, 1, 1, 2, 2]
Y_PRED = [0, 0, 1, 2, 0, 1]


def test_summary_metrics():
    s = M.summary_metrics(Y_TRUE, Y_PRED)
    assert s["n"] == 6
    assert s["accuracy"] == 3 / 6
    assert s["micro"]["f1"] == 3 / 6
    # macro f1 = mean(0.8, 0.5, 0.0)
    assert abs(s["macro"]["f1"] - (0.8 + 0.5 + 0.0) / 3) < 1e-9


def test_per_class_and_confusion():
    pc = M.per_class_metrics(Y_TRUE, Y_PRED, LABELS)
    assert pc["a"] == {"precision": 2 / 3, "recall": 1.0, "f1": 0.8, "support": 2}
    assert pc["c"]["recall"] == 0.0 and pc["c"]["support"] == 2
    cm = M.confusion_matrix(Y_TRUE, Y_PRED, LABELS)
    assert cm["labels"] == LABELS
    assert cm["matrix"] == [[2, 0, 0], [0, 1, 1], [1, 1, 0]]
    rep = M.full_report(Y_TRUE, Y_PRED, LABELS)
    assert set(rep) == {"summary", "per_class", "confusion"}


def test_empty_inputs_rejected_explicitly():
    import pytest as _pt

    with _pt.raises(ValueError, match="zero examples"):
        M.summary_metrics([], [])
    assert M.per_class_metrics([], [], LABELS)["a"]["support"] == 0


# ---------------------------------------------------------------------------
# Phase 14: multi-seed aggregation + paired comparison (hand-computed).


def _rep14(macro_f1, per=None):
    return {"summary": {"accuracy": 0.8, "micro": {"f1": 0.8},
                        "macro": {"f1": macro_f1},
                        "weighted": {"f1": 0.8}},
            "per_class": {k: {"f1": v} for k, v in (per or {}).items()}}


def test_aggregate_runs_mean_std_min_max():
    agg = M.aggregate_runs([
        {"seed": 42, "summary": {"accuracy": 0.8, "macro": {"f1": 0.70},
                                 "micro": {"f1": 0.8}, "weighted": {"f1": 0.78}},
         "per_class": {"payment": {"f1": 0.9}}},
        {"seed": 43, "summary": {"accuracy": 0.82, "macro": {"f1": 0.74},
                                 "micro": {"f1": 0.82}, "weighted": {"f1": 0.80}},
         "per_class": {"payment": {"f1": 0.7}}},
    ])
    assert agg["n_runs"] == 2 and agg["seeds"] == [42, 43]
    assert agg["macro"]["mean"] == 0.72
    assert agg["macro"]["min"] == 0.70 and agg["macro"]["max"] == 0.74
    assert agg["macro"]["std"] == 0.02
    assert agg["per_class"]["payment"]["n"] == 2
    import pytest as _pt

    with _pt.raises(ValueError, match="No runs"):
        M.aggregate_runs([])


def test_paired_compare_ci_and_guards():
    base = {s: _rep14(0.70 if s != 44 else 0.69, {}) for s in (42, 43, 44, 45, 46)}
    cand = {s: _rep14(0.72 if s != 44 else 0.715, {}) for s in (42, 43, 44, 45, 46)}
    # diffs: .02, .02, .025, .02, .02 -> mean .021
    out = M.paired_compare(base, cand, scope="macro")
    assert out["n"] == 5 and out["seeds"] == [42, 43, 44, 45, 46]
    assert out["mean_difference"] == 0.021
    assert out["min_improvement"] == 0.02 and out["max_improvement"] == 0.025
    lo, hi = out["ci95"]
    assert lo < 0.021 < hi and out["ci_excludes_zero"] is True
    assert "paired t-interval" in out["method"]
    import pytest as _pt

    with _pt.raises(ValueError, match=">=2 shared seeds"):
        M.paired_compare({42: _rep14(0.7, {})}, {43: _rep14(0.7, {})})
    with _pt.raises(ValueError, match="Missing macro metric"):
        M.paired_compare({42: {"summary": {}}, 43: {"summary": {}}},
                         {42: _rep14(0.7, {}), 43: _rep14(0.7, {})})
