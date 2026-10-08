"""Error-analysis unit tests (pure, hand-computed, no fabrication)."""
from app.modules.ml_evaluation import error_analysis as err

LABELS = ["a", "b", "c"]
Y_TRUE = [0, 0, 1, 1, 2, 2]
Y_PRED = [0, 0, 1, 2, 0, 1]


def test_pairs_and_focus():
    out = err.analyze(Y_TRUE, Y_PRED, LABELS, [0.9, 0.8, 0.7, 0.6, 0.5, 0.4],
                      [{"clause_id": f"c{i}", "contract_id": "k", "text": "t"}
                       for i in range(6)])
    assert out["per_class"]["b"]["false_negatives"] == 1
    assert out["per_class"]["a"]["false_positives"] == 1
    assert out["total_errors"] == 3
    pairs = {(p["actual"], p["predicted"]): p["count"]
             for p in out["misclassification_pairs"]}
    assert pairs == {("b", "c"): 1, ("c", "a"): 1, ("c", "b"): 1}  # only real pairs
    assert out["per_class"]["c"]["fn_examples"][0]["predicted"] == "a"
    assert "contract_id" in out["per_class"]["c"]["fn_examples"][0]
    assert out["focus_false_negatives"] == {}


def test_focus_classes_reported():
    labels = ["renewal", "termination", "payment"]
    out = err.analyze([0, 1, 1, 2], [1, 1, 2, 2], labels, focus=("renewal", "termination"))
    assert out["focus_false_negatives"] == {"renewal": 1, "termination": 1}


def test_truncation_report():
    rep = err.truncation_report([50, 200, 90, 300], 128, [True, False, True, False])
    assert rep["n_truncated"] == 2 and rep["pct_truncated"] == 0.5
    assert rep["error_rate_truncated"] == 1.0
    assert rep["error_rate_untruncated"] == 0.0
    assert rep["length_max"] == 300
    assert rep["length_p50"] == 200
