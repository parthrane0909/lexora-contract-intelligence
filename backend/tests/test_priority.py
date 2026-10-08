"""Priority + taxonomy units: transparent, inspectable, capped."""
import pytest

from app.modules.risk_engine.priority import compute_priority
from app.modules.risk_engine.taxonomy import RISK_CATEGORIES, validate_taxonomy


def test_priority_base_passthrough():
    p, reasons = compute_priority("high", extra_reasons=["Rule LIAB-001 (severity high)"])
    assert p == "high" and reasons == ["Rule LIAB-001 (severity high)"]


def test_high_impact_policy_bumps_one_level():
    p, reasons = compute_priority("medium", policy_impact="high",
                                  extra_reasons=["Policy deviation"])
    assert p == "high"
    assert any("Bumped" in r for r in reasons)
    # standard impact never bumps
    assert compute_priority("medium", policy_impact="standard")[0] == "medium"
    assert compute_priority("medium", policy_impact=None)[0] == "medium"


def test_priority_capped_at_critical():
    assert compute_priority("critical", policy_impact="high")[0] == "critical"


def test_invalid_base_rejected():
    with pytest.raises(ValueError):
        compute_priority("extreme")


def test_taxonomy_honest_about_support():
    assert validate_taxonomy() == []
    assert RISK_CATEGORIES["data_privacy"] == {"ml_support": False, "rules_support": False}
    assert RISK_CATEGORIES["intellectual_property"]["ml_support"] is True
    assert RISK_CATEGORIES["intellectual_property"]["rules_support"] is False
    assert RISK_CATEGORIES["liability"] == {"ml_support": True, "rules_support": True}
