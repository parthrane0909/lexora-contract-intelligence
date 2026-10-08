"""Registry, promotion gates, error-analysis/calibration edge cases."""
import pytest

from app.modules.ml_evaluation import promotion as promo
from app.modules.ml_evaluation import registry as reg


def _report(macro, weighted, per):
    return {"summary": {"macro": {"f1": macro}, "weighted": {"f1": weighted}},
            "per_class": {k: {"f1": v} for k, v in per.items()}}


LABELS = ("liability", "termination", "payment")


def test_registry_uniqueness_and_production_singleton(client):
    from conftest import TestingSession

    db = TestingSession()
    try:
        a = reg.register_model(db, version="v1", metrics={}, artifact_location="/tmp/a")
        assert a.status == "candidate"
        with pytest.raises(ValueError, match="already registered"):
            reg.register_model(db, version="v1")
        with pytest.raises(ValueError, match="Invalid status"):
            reg.register_model(db, version="vx", status="bogus")
        assert reg.production_model(db) is None
    finally:
        db.close()


def test_gates_pass_and_fail():
    prod = _report(0.70, 0.85, {"liability": 0.66, "termination": 0.58,
                                "payment": 0.85, "other": 0.9})
    good = _report(0.72, 0.86, {"liability": 0.68, "termination": 0.60,
                                "payment": 0.86, "other": 0.9})
    out = promo.check_gates(good, prod, protected=LABELS, max_regression=0.03)
    assert out["approved"] is True and all(g["passed"] for g in out["gates"])
    # macro up but a protected class regresses beyond tolerance
    sneaky = _report(0.75, 0.87, {"liability": 0.60, "termination": 0.60,
                                  "payment": 0.90, "other": 0.95})
    out2 = promo.check_gates(sneaky, prod, protected=LABELS, max_regression=0.03)
    assert out2["approved"] is False
    assert any(g["name"] == "protected_liability" and not g["passed"]
               for g in out2["gates"])
    # missing per-class data fails loudly, never silently passes
    out3 = promo.check_gates({"summary": {"macro": {"f1": 0.9},
                                          "weighted": {"f1": 0.9}}, "per_class": {}},
                             prod, protected=LABELS)
    assert out3["approved"] is False
