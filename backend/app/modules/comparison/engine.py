"""Comparison engine (Phase 5.5–5.7). Deterministic, explainable, no LLM.

For each template↔contract pair:
  difference_type: unchanged (normalized text equal) | modified |
                   missing (template-only) | added (contract-only)
  value_diff: structured field comparison via risk_engine.extract_facts
              (cap_amount, period_days, notice_days, jurisdiction, coverage…)
  policy impact: latest policy_deviation risk finding on the contract clause
  materiality:
    material            — linked policy deviation, linked critical/high risk
                          finding, or a changed value in a core category
                          (liability, indemnification, governing_law)
    potentially_material— any other modification, medium linked finding, or an
                          added/missing clause in a core category
                          (liability, payment, termination, renewal,
                          governing_law, jurisdiction, insurance)
    informational       — unchanged, or added/missing boilerplate
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import (ClauseClassification, Comparison, ComparisonFinding, Contract,
                        RiskFinding, Template)
from app.modules.audit.service import record
from app.modules.comparison.matching import get_matcher, normalize
from app.modules.risk_engine.extractors import extract_facts

ENGINE_VERSION = "1.0"

CORE_VALUE_CATEGORIES = ("liability", "indemnification", "governing_law")
CORE_PRESENCE_CATEGORIES = ("liability", "payment", "termination", "renewal",
                             "governing_law", "jurisdiction", "insurance")

# structured fields compared per category for value_diff
VALUE_FIELDS = {
    "liability": ("cap_amount",),
    "payment": ("period_days",),
    "termination": ("notice_days",),
    "renewal": ("auto_renew",),
    "governing_law": ("jurisdiction",),
    "jurisdiction": ("jurisdiction",),
    "insurance": ("min_coverage",),
}


def _latest_policy_deviations(db: Session, contract_id: uuid.UUID) -> dict[str, RiskFinding]:
    """Latest policy_deviation finding per contract clause (any risk run)."""
    rows = (db.query(RiskFinding)
            .filter(RiskFinding.contract_id == contract_id,
                    RiskFinding.finding_type == "policy_deviation")
            .order_by(RiskFinding.created_at.desc()).all())
    latest: dict[str, RiskFinding] = {}
    for r in rows:
        latest.setdefault(str(r.clause_id), r)
    return latest


def _latest_risk_by_clause(db: Session, contract_id: uuid.UUID) -> dict[str, RiskFinding]:
    """Highest-priority latest-run finding per clause (for materiality)."""
    rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    runs = (db.query(RiskFinding.analysis_run_id)
            .filter(RiskFinding.contract_id == contract_id)
            .order_by(RiskFinding.created_at.desc()).all())
    if not runs:
        return {}
    latest_run = runs[0][0]
    out: dict[str, RiskFinding] = {}
    for r in db.query(RiskFinding).filter(
            RiskFinding.contract_id == contract_id,
            RiskFinding.analysis_run_id == latest_run).all():
        key = str(r.clause_id)
        if key not in out or rank.get(r.priority, 0) > rank.get(out[key].priority, 0):
            out[key] = r
    return out


def _category(template_clause, contract_clause, db: Session,
              contract_id: uuid.UUID) -> str | None:
    """Best available category: contract classification first, else template's."""
    if contract_clause is not None:
        row = (db.query(ClauseClassification)
               .filter(ClauseClassification.clause_id == contract_clause.id)
               .order_by(ClauseClassification.created_at.desc()).first())
        if row:
            return row.predicted_category
    if template_clause is not None and getattr(template_clause, "predicted_category", None):
        return template_clause.predicted_category
    return None


def _value_diff(category: str | None, template_text: str, contract_text: str) -> list[dict]:
    diffs = []
    if not category or category not in VALUE_FIELDS:
        return diffs
    t_facts, c_facts = extract_facts(category, template_text), extract_facts(category, contract_text)
    for field in VALUE_FIELDS[category]:
        t_val, c_val = t_facts.get(field), c_facts.get(field)
        if t_val != c_val and (t_val is not None or c_val is not None):
            diffs.append({"field": field, "template": t_val, "contract": c_val})
    return diffs


def compare_contract(db: Session, contract: Contract, template: Template, *,
                     triggered_by: uuid.UUID | None, matcher: str = "deterministic") -> Comparison:
    """Run one comparison; persist comparison + findings (append-only)."""
    from app.models import Clause, TemplateClause

    t_clauses = db.query(TemplateClause).filter(
        TemplateClause.template_id == template.id).order_by(TemplateClause.order_index).all()
    c_clauses = db.query(Clause).filter(
        Clause.contract_id == contract.id).order_by(Clause.order_index).all()
    if template.status != "ready" or not t_clauses:
        raise ValueError("Template is not ready for comparison (no clauses parsed)")

    deviations = _latest_policy_deviations(db, contract.id)
    worst_risk = _latest_risk_by_clause(db, contract.id)

    comp = Comparison(organization_id=contract.organization_id, contract_id=contract.id,
                      template_id=template.id, engine_version=ENGINE_VERSION, status="done")
    db.add(comp)
    db.flush()

    counts: dict[str, int] = {}
    for t_cl, c_cl, method in get_matcher(matcher).match(t_clauses, c_clauses):
        finding = _pair(db, contract.id, template.id, t_cl, c_cl, method,
                        deviations, worst_risk)
        db.add(ComparisonFinding(
            organization_id=contract.organization_id, comparison_id=comp.id,
            contract_id=contract.id, template_id=template.id,
            template_clause_id=t_cl.id if t_cl else None,
            contract_clause_id=c_cl.id if c_cl else None,
            category=finding["category"], difference_type=finding["difference_type"],
            materiality=finding["materiality"], title=finding["title"],
            template_text=t_cl.text[:2000] if t_cl else None,
            contract_text=c_cl.text[:2000] if c_cl else None,
            value_diff=json.dumps(finding["value_diff"]),
            risk_finding_id=finding["risk_finding_id"],
            reasons=json.dumps(finding["reasons"])))
        counts[finding["difference_type"]] = counts.get(finding["difference_type"], 0) + 1
        counts[finding["materiality"]] = counts.get(finding["materiality"], 0) + 1
    comp.stats = json.dumps({"pairs": len(t_clauses) + len(c_clauses),
                             "template_clauses": len(t_clauses),
                             "contract_clauses": len(c_clauses), **counts})
    db.commit()
    db.refresh(comp)
    record(db, action="contract.compared", organization_id=contract.organization_id,
           user_id=triggered_by, object_type="comparison", object_id=str(comp.id),
           detail=f"{template.name} v{template.version}: {json.dumps(counts)[:200]}")
    return comp


def _pair(db: Session, contract_id: uuid.UUID, template_id: uuid.UUID,
          t_cl, c_cl, method: str | None,
          deviations: dict[str, RiskFinding],
          worst_risk: dict[str, RiskFinding]) -> dict:
    category = _category(t_cl, c_cl, db, contract_id)
    section = (t_cl.section_number if t_cl else None) or (c_cl.section_number if c_cl else None)
    label = f"§{section} " if section else ""
    if t_cl is None:
        difference, title = "added", f"{label}Clause added beyond the template"
        value_diff: list[dict] = []
    elif c_cl is None:
        difference, title = "missing", f"{label}Template clause missing from contract"
        value_diff = []
    elif normalize(t_cl.text) == normalize(c_cl.text):
        difference, title = "unchanged", f"{label}Matches the approved template"
        value_diff = []
    else:
        difference = "modified"
        value_diff = _value_diff(category, t_cl.text, c_cl.text)
        title = (f"{label}Material term changed" if value_diff
                 else f"{label}Wording differs from template")

    risk = worst_risk.get(str(c_cl.id)) if c_cl else None
    dev = deviations.get(str(c_cl.id)) if c_cl else None
    materiality, reasons = _materiality(
        difference, category or "", bool(value_diff), risk, dev is not None, method)
    if dev:
        reasons.append(f"Policy deviation {dev.rule_id} (policy v{dev.policy_version})")
    return {"category": category, "difference_type": difference, "materiality": materiality,
            "title": title, "value_diff": value_diff,
            "risk_finding_id": dev.id if dev else (risk.id if risk and difference != "unchanged" else None),
            "reasons": reasons}


def _materiality(difference: str, category: str, value_changed: bool,
                 risk: RiskFinding | None, has_deviation: bool,
                 method: str | None) -> tuple[str, list[str]]:
    reasons = []
    if difference == "unchanged":
        return "informational", ["Text matches the approved template"]
    if has_deviation:
        reasons.append("Linked policy deviation")
    if risk and risk.priority in ("critical", "high"):
        reasons.append(f"Linked {risk.priority}-priority risk finding {risk.rule_id}")
    if has_deviation or (risk and risk.priority in ("critical", "high")):
        return "material", reasons
    if value_changed and category in CORE_VALUE_CATEGORIES:
        return "material", [f"Changed {category} value vs template"]
    if difference == "modified":
        reasons.append(f"Matched by {method}; wording differs")
        if risk and risk.priority == "medium":
            reasons.append(f"Linked medium-priority finding {risk.rule_id}")
        return "potentially_material", reasons
    # added / missing
    if category in CORE_PRESENCE_CATEGORIES:
        return "potentially_material", [f"{difference} clause in core category {category}"]
    return "informational", [f"{difference} clause outside core categories"]
