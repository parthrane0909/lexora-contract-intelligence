"""Rule units: every rule fires and stays silent on the right inputs.

Pure functions — no DB, no Transformer. policy=None means "org has not
configured this category" and must never produce a policy_deviation.
"""
from app.modules.risk_engine.rules import RULES, rule_ids


def _run(category: str, facts: dict, policy=None, conf: float = 0.9) -> list[dict]:
    return [h for rule in RULES[category] if (h := rule(facts, policy, conf))]


def test_rule_registry_complete():
    assert len(rule_ids()) == 18
    assert set(RULES) == {"liability", "payment", "termination", "renewal",
                          "governing_law", "jurisdiction", "insurance",
                          "confidentiality", "indemnification", "warranty"}


def test_liab_no_cap_and_unlimited():
    hits = _run("liability", {"cap_amount": None, "unlimited": False})
    assert [h["rule_id"] for h in hits] == ["LIAB-001"]
    assert hits[0]["finding_type"] == "missing_term" and hits[0]["priority_base"] == "high"
    hits = _run("liability", {"cap_amount": None, "unlimited": True})
    assert {h["rule_id"] for h in hits} == {"LIAB-002"}
    assert _run("liability", {"cap_amount": 5000.0, "unlimited": False}) == []


def test_liab_policy_deviation_and_missing_policy():
    hits = _run("liability", {"cap_amount": 5_000_000.0, "unlimited": False},
                {"max_amount": 1_000_000, "currency": "USD"})
    dev = [h for h in hits if h["finding_type"] == "policy_deviation"]
    assert len(dev) == 1 and dev[0]["rule_id"] == "LIAB-003"
    assert dev[0]["contract_value"]["cap_amount"] == 5_000_000.0
    assert dev[0]["expected_value"]["max_amount"] == 1_000_000.0
    # Cap within policy → no deviation; missing policy → never a deviation.
    assert not [h for h in _run("liability", {"cap_amount": 100.0}, {"max_amount": 1_000_000})
                if h["finding_type"] == "policy_deviation"]
    assert not [h for h in _run("liability", {"cap_amount": 9e9}, None)
                if h["finding_type"] == "policy_deviation"]
    # Unlimited + require_cap policy → deviation.
    hits = _run("liability", {"cap_amount": None, "unlimited": True},
                {"max_amount": 1_000_000, "require_cap": True})
    assert "LIAB-003" in {h["rule_id"] for h in hits}


def test_payment_rules():
    hits = _run("payment", {"period_days": 90}, {"max_days": 30})
    assert hits[0]["rule_id"] == "PAY-001" and "90" in hits[0]["reason"]
    assert _run("payment", {"period_days": 15}, {"max_days": 30}) == []
    hits = _run("payment", {"period_days": None})
    assert [h["rule_id"] for h in hits] == ["PAY-002"]


def test_termination_rules():
    hits = _run("termination", {"notice_days": 7, "for_convenience": False},
                {"min_notice_days": 30})
    assert hits[0]["rule_id"] == "TERM-001"
    hits = _run("termination", {"notice_days": None, "for_convenience": True})
    assert {h["rule_id"] for h in hits} == {"TERM-002", "TERM-003"}
    assert _run("termination", {"notice_days": 60, "for_convenience": False},
                {"min_notice_days": 30}) == []


def test_renewal_rules():
    hits = _run("renewal", {"auto_renew": True, "notice_days": None},
                {"allow_auto_renew": False})
    assert {h["rule_id"] for h in hits} == {"REN-001", "REN-002"}
    assert _run("renewal", {"auto_renew": True, "notice_days": 30},
                {"allow_auto_renew": True}) == []
    assert _run("renewal", {"auto_renew": False, "notice_days": None}) == []


def test_governing_law_and_jurisdiction():
    hits = _run("governing_law", {"jurisdiction": "Delaware"},
                {"preferred_jurisdictions": ["New York", "California"]})
    assert hits[0]["rule_id"] == "GOV-001" and hits[0]["finding_type"] == "policy_deviation"
    assert _run("governing_law", {"jurisdiction": "New York"},
                {"preferred_jurisdictions": ["New York"]}) == []
    assert _run("jurisdiction", {"jurisdiction": "Texas"},
                {"preferred_jurisdictions": ["New York"]})[0]["rule_id"] == "JUR-001"
    assert _run("governing_law", {"jurisdiction": None})[0]["rule_id"] == "GOV-002"


def test_insurance_conf_indemnity_warranty():
    hits = _run("insurance", {"coverage_amounts": [500000.0], "min_coverage": 500000.0},
                {"min_coverage": 1000000.0})
    assert hits[0]["rule_id"] == "INS-001"
    assert _run("insurance", {"coverage_amounts": [], "min_coverage": None})[0]["rule_id"] == "INS-002"
    assert _run("confidentiality", {"has_period": False})[0]["rule_id"] == "CONF-001"
    assert _run("confidentiality", {"has_period": True}) == []
    assert _run("indemnification", {"one_sided": True})[0]["rule_id"] == "IND-001"
    assert _run("indemnification", {"one_sided": False}) == []
    assert _run("warranty", {"has_duration": False})[0]["rule_id"] == "WAR-001"
    assert _run("warranty", {"has_duration": True}) == []
