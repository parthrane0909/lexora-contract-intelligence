"""Deterministic risk rules (Phase 4.2). Versioned, independently testable.

Each rule is a pure function: (facts, policy_params, confidence) → finding
or None. Rules NEVER see the Transformer and NEVER modify it. Adding a rule
means appending to RULES with a new ID — never editing an existing rule's
semantics in place (bump its version instead).

finding_type discipline (spec §6):
  rule                  — problematic characteristic in the clause itself
  missing_term          — expected term absent
  policy_deviation      — differs from the org's configured position
  (uncertain_classification is engine-level, see analyzer.py)
"""
from __future__ import annotations

RULES_VERSION = "1.0"


def _finding(rule_id: str, category: str, finding_type: str, priority: str,
             title: str, reason: str, reasons: list[str],
             contract_value: dict | None = None, expected_value: dict | None = None) -> dict:
    return {"rule_id": rule_id, "rule_version": RULES_VERSION, "risk_category": category,
            "finding_type": finding_type, "priority_base": priority, "title": title,
            "reason": reason, "priority_reasons": reasons,
            "contract_value": contract_value or {}, "expected_value": expected_value or {}}


# ------------------------------------------------------------ liability ----

def liab_001_no_cap(facts, policy, conf):
    if facts.get("cap_amount") is None and not facts.get("unlimited"):
        return _finding("LIAB-001", "liability", "missing_term", "high",
                        "Potentially broad liability exposure",
                        "No monetary liability cap was identified in this liability clause.",
                        ["Liability clause", "No liability cap detected"])
    return None


def liab_002_unlimited(facts, policy, conf):
    if facts.get("unlimited"):
        return _finding("LIAB-002", "liability", "rule", "critical",
                        "Unlimited liability language",
                        "The clause contains unlimited/uncapped liability language.",
                        ["Liability clause", "Unlimited liability stated explicitly"])
    return None


def liab_003_cap_exceeds_policy(facts, policy, conf):
    if not policy or policy.get("max_amount") is None:
        return None
    cap = facts.get("cap_amount")
    if cap is not None and cap > float(policy["max_amount"]):
        return _finding(
            "LIAB-003", "liability", "policy_deviation", "high",
            "Liability cap exceeds company policy",
            f"Contract cap {cap:,.0f} exceeds the policy maximum of "
            f"{float(policy['max_amount']):,.0f} {policy.get('currency', '')}.".strip(),
            ["Liability clause", "Cap above policy maximum", "Company policy requires a lower cap"],
            {"cap_amount": cap, "currency": facts.get("cap_currency")},
            {"max_amount": float(policy["max_amount"]), "currency": policy.get("currency")})
    if facts.get("unlimited") and policy.get("require_cap", True):
        return _finding(
            "LIAB-003", "liability", "policy_deviation", "high",
            "Unlimited liability violates company policy",
            "Company policy requires a liability cap; the clause is unlimited.",
            ["Liability clause", "Unlimited liability", "Company policy requires a cap"],
            {"unlimited": True},
            {"max_amount": float(policy["max_amount"]), "currency": policy.get("currency")})
    return None


# -------------------------------------------------------------- payment ----

def pay_001_exceeds_policy(facts, policy, conf):
    if not policy or policy.get("max_days") is None:
        return None
    days = facts.get("period_days")
    if days is not None and days > int(policy["max_days"]):
        return _finding(
            "PAY-001", "payment", "policy_deviation", "medium",
            "Payment terms exceed company policy",
            f"Contract allows {days} days; policy allows at most {int(policy['max_days'])}.",
            ["Payment clause", f"Payment period {days}d exceeds policy"],
            {"period_days": days}, {"max_days": int(policy["max_days"])})
    return None


def pay_002_no_period(facts, policy, conf):
    if facts.get("period_days") is None:
        return _finding("PAY-002", "payment", "missing_term", "low",
                        "No payment period identified",
                        "No payment period (e.g. 'within N days') was identified.",
                        ["Payment clause", "No payment period detected"])
    return None


# ----------------------------------------------------------- termination ----

def term_001_short_notice(facts, policy, conf):
    if not policy or policy.get("min_notice_days") is None:
        return None
    notice = facts.get("notice_days")
    if notice is not None and notice < int(policy["min_notice_days"]):
        return _finding(
            "TERM-001", "termination", "policy_deviation", "medium",
            "Termination notice shorter than policy",
            f"Contract requires {notice} days' notice; policy requires at least "
            f"{int(policy['min_notice_days'])}.",
            ["Termination clause", "Notice period below policy minimum"],
            {"notice_days": notice}, {"min_notice_days": int(policy["min_notice_days"])})
    return None


def term_002_no_notice(facts, policy, conf):
    if facts.get("notice_days") is None:
        return _finding("TERM-002", "termination", "missing_term", "low",
                        "No termination notice period identified",
                        "No termination notice period was identified in this clause.",
                        ["Termination clause", "No notice period detected"])
    return None


def term_003_convenience_without_notice(facts, policy, conf):
    if facts.get("for_convenience") and facts.get("notice_days") is None:
        return _finding("TERM-003", "termination", "rule", "medium",
                        "Termination for convenience without a notice period",
                        "The clause permits termination for convenience but states no notice period.",
                        ["Termination clause", "For-convenience right", "No notice period detected"])
    return None


# --------------------------------------------------------------- renewal ----

def ren_001_auto_renew_against_policy(facts, policy, conf):
    if facts.get("auto_renew") and policy and policy.get("allow_auto_renew") is False:
        return _finding(
            "REN-001", "renewal", "policy_deviation", "high",
            "Automatic renewal against company policy",
            "The clause renews automatically; company policy disallows automatic renewal.",
            ["Renewal clause", "Automatic renewal present", "Policy disallows automatic renewal"],
            {"auto_renew": True}, {"allow_auto_renew": False})
    return None


def ren_002_auto_renew_no_notice(facts, policy, conf):
    if facts.get("auto_renew") and facts.get("notice_days") is None:
        return _finding("REN-002", "renewal", "rule", "medium",
                        "Automatic renewal without a stated notice period",
                        "The clause renews automatically but states no non-renewal notice period.",
                        ["Renewal clause", "Automatic renewal present", "No notice period detected"])
    return None


# ----------------------------------------------------- governing law / jurisdiction ----

def _jurisdiction_deviation(rule_id: str, category: str, facts, policy):
    if not policy or not policy.get("preferred_jurisdictions"):
        return None
    jur = facts.get("jurisdiction")
    preferred = [str(j).lower() for j in policy["preferred_jurisdictions"]]
    if jur and jur.lower() not in preferred and not any(p in jur.lower() for p in preferred):
        return _finding(
            rule_id, category, "policy_deviation", "medium",
            "Jurisdiction differs from company policy",
            f"Contract jurisdiction '{jur}' is not among the preferred: "
            f"{', '.join(policy['preferred_jurisdictions'])}.",
            [f"{category} clause", "Jurisdiction outside preferred list"],
            {"jurisdiction": jur}, {"preferred_jurisdictions": policy["preferred_jurisdictions"]})
    return None


def gov_001_outside_policy(facts, policy, conf):
    return _jurisdiction_deviation("GOV-001", "governing_law", facts, policy)


def gov_002_no_jurisdiction(facts, policy, conf):
    if not facts.get("jurisdiction"):
        return _finding("GOV-002", "governing_law", "missing_term", "low",
                        "No governing jurisdiction identified",
                        "No governing jurisdiction was identified in this clause.",
                        ["Governing-law clause", "No jurisdiction detected"])
    return None


def jur_001_outside_policy(facts, policy, conf):
    return _jurisdiction_deviation("JUR-001", "jurisdiction", facts, policy)


# -------------------------------------------------------------- insurance ----

def ins_001_below_minimum(facts, policy, conf):
    if not policy or policy.get("min_coverage") is None:
        return None
    cov = facts.get("min_coverage")
    if cov is not None and cov < float(policy["min_coverage"]):
        return _finding(
            "INS-001", "insurance", "policy_deviation", "medium",
            "Insurance coverage below company minimum",
            f"Contract coverage {cov:,.0f} is below the required minimum of "
            f"{float(policy['min_coverage']):,.0f} {policy.get('currency', '')}.".strip(),
            ["Insurance clause", "Coverage below policy minimum"],
            {"min_coverage": cov}, {"min_coverage": float(policy["min_coverage"])})
    return None


def ins_002_no_amount(facts, policy, conf):
    if not facts.get("coverage_amounts"):
        return _finding("INS-002", "insurance", "missing_term", "low",
                        "No insurance coverage amount identified",
                        "No coverage amount was identified in this insurance clause.",
                        ["Insurance clause", "No coverage amount detected"])
    return None


# ---------------------------------------------------------- other rules ----

def conf_001_no_period(facts, policy, conf):
    if not facts.get("has_period"):
        return _finding("CONF-001", "confidentiality", "missing_term", "low",
                        "No confidentiality period identified",
                        "No survival period or duration was identified for confidentiality duties.",
                        ["Confidentiality clause", "No period detected"])
    return None


def ind_001_one_sided(facts, policy, conf):
    if facts.get("one_sided"):
        return _finding("IND-001", "indemnification", "rule", "medium",
                        "One-sided indemnification language",
                        "The indemnity appears to run in one direction only; no mutual language found.",
                        ["Indemnification clause", "No mutual indemnity language"])
    return None


def war_001_no_duration(facts, policy, conf):
    if not facts.get("has_duration"):
        return _finding("WAR-001", "warranty", "missing_term", "low",
                        "No warranty duration identified",
                        "No warranty duration was identified in this clause.",
                        ["Warranty clause", "No duration detected"])
    return None


# category → rules in evaluation order (first match per rule; all rules may fire)
RULES: dict[str, list] = {
    "liability": [liab_001_no_cap, liab_002_unlimited, liab_003_cap_exceeds_policy],
    "payment": [pay_001_exceeds_policy, pay_002_no_period],
    "termination": [term_001_short_notice, term_002_no_notice, term_003_convenience_without_notice],
    "renewal": [ren_001_auto_renew_against_policy, ren_002_auto_renew_no_notice],
    "governing_law": [gov_001_outside_policy, gov_002_no_jurisdiction],
    "jurisdiction": [jur_001_outside_policy],
    "insurance": [ins_001_below_minimum, ins_002_no_amount],
    "confidentiality": [conf_001_no_period],
    "indemnification": [ind_001_one_sided],
    "warranty": [war_001_no_duration],
}


def rule_ids() -> list[str]:
    """All registered rule IDs (used by tests to assert versioned coverage)."""
    return ["CONF-001", "GOV-001", "GOV-002", "IND-001", "INS-001", "INS-002",
            "JUR-001", "LIAB-001", "LIAB-002", "LIAB-003", "PAY-001", "PAY-002",
            "REN-001", "REN-002", "TERM-001", "TERM-002", "TERM-003", "WAR-001"]
