"""Structured, deterministic risk explanation (Part 1, Phase 5).

Answers four questions for every finding — WHAT / WHY / EVIDENCE / ATTENTION —
using only data the rule engine already produced (rule id + version, title,
reason, extracted facts, policy comparison, priority reasons) plus the
taxonomy. No LLM is involved: the deterministic engine stays the source of
truth, and nothing here can add, soften or remove a risk.

The reviewer-facing wording is rule-agnostic glue around those fields; any
unknown finding_type falls back to a neutral statement instead of guessing.
"""
from __future__ import annotations

import json

from app.models import RiskFinding

EXPLANATION_VERSION = "1.0"

FINDING_TYPE_LABELS = {
    "rule": "Potential risk in the clause wording",
    "missing_term": "Expected term not identified",
    "policy_deviation": "Difference from the organization's policy position",
    "uncertain_classification": "Clause category could not be trusted",
}

# What the reviewer should verify, derived from the finding type (taxonomy),
# never invented per finding.
ATTENTION = {
    "rule": ("Check the clause wording against what the organization is "
             "prepared to accept for this category, and confirm the detected "
             "condition is intended."),
    "missing_term": ("Confirm the expected term is intentionally absent; if "
                     "the exposure is not accepted, ask for it to be added."),
    "policy_deviation": ("Compare this position with the organization's "
                         "active policy for this category and decide whether "
                         "the difference is approved."),
    "uncertain_classification": ("Verify the clause category manually before "
                                 "relying on the findings for this clause."),
}


def _loads(text: str | None) -> dict:
    try:
        return json.loads(text) if text else {}
    except (ValueError, TypeError):
        return {}


def requires_attention(finding: RiskFinding) -> tuple[bool, str]:
    """Attention flag: does a reviewer need to look at this first? Derived
    only from the persisted priority — the same thresholds the contract-level
    `needs_attention` count uses (critical/high/medium)."""
    if finding.priority in ("critical", "high", "medium"):
        return True, f"priority_{finding.priority}"
    return False, f"priority_{finding.priority or 'unspecified'}"


def explain(finding: RiskFinding) -> dict:
    """Structured explanation for one persisted finding."""
    ftype = finding.finding_type if finding.finding_type in FINDING_TYPE_LABELS else "rule"
    label = FINDING_TYPE_LABELS[ftype]
    facts = _loads(finding.extracted_facts) or _loads(finding.contract_value)
    location = []
    if finding.section_number:
        location.append(f"§{finding.section_number}")
    if finding.page_start:
        location.append(f"page {finding.page_start}"
                        + (f"–{finding.page_end}" if finding.page_end else ""))
    where = ", ".join(location) or "location not recorded"
    what = (f"{label} ({finding.risk_category.replace('_', ' ')}), detected by rule "
            f"{finding.rule_id} v{finding.rule_version} in clause at {where}: {finding.title}")
    attention = ATTENTION.get(
        ftype, "Review this finding against the original clause text.")
    if finding.priority in ("critical", "high"):
        attention = f"High priority — {attention[0].lower()}{attention[1:]}"
    elif finding.priority == "low":
        attention = f"Low priority — {attention[0].lower()}{attention[1:]}"
    return {
        "explanation_version": EXPLANATION_VERSION,
        "source": "deterministic risk engine (no LLM)",
        "what": what,
        "detected_condition": {
            "rule_id": finding.rule_id,
            "rule_version": finding.rule_version,
            "finding_type": finding.finding_type,
            "finding_type_label": label,
            "risk_category": finding.risk_category,
            "priority": finding.priority,
        },
        "why": finding.reason,
        "evidence": {
            "clause_text": finding.evidence or "",
            "facts": facts,
            "contract_value": _loads(finding.contract_value),
            "expected_value": _loads(finding.expected_value),
            "section_number": finding.section_number,
            "page_start": finding.page_start,
            "page_end": finding.page_end,
        },
        "attention": attention,
        "priority_reasons": _loads(finding.priority_reasons)
        if isinstance(finding.priority_reasons, str) else (finding.priority_reasons or []),
        "policy": {
            "policy_id": str(finding.policy_id) if finding.policy_id else None,
            "version": finding.policy_version,
        },
        "classification": {
            "model_name": finding.classification_model,
            "model_version": finding.classification_version,
            "confidence": finding.classification_confidence,
        },
        "status": finding.status,
    }
