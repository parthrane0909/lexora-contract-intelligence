"""Risk analysis runner (Phase 4.7/4.8). Consumes Phase 3 predictions —
never re-runs the Transformer.

Per classified clause:
  classification (category + confidence + model/version)
    → deterministic facts (extractors)
      → category rules + active org policy
        → findings (rule / missing_term / policy_deviation)
          → transparent priority + reasons
Plus one engine-level rule: confidence < 0.5 → uncertain_classification
(low; the category itself is not trustworthy). Clauses without any
classification are counted in run stats and skipped — never silently
"clean".

Runs are append-only AnalysisRuns (kind="risk_analysis"); re-analysis adds
a new run + findings, preserving history for version comparison.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import (AnalysisRun, Clause, ClauseClassification, Contract, Policy,
                        RiskFinding)
from app.modules.audit.service import record
from app.modules.policy_engine.service import active_policies
from app.modules.risk_engine import rules as rule_defs
from app.modules.risk_engine.extractors import extract_facts
from app.modules.risk_engine.priority import compute_priority
from app.modules.risk_engine.taxonomy import RISK_TAXONOMY_VERSION

ENGINE_NAME = "risk-engine"
ENGINE_VERSION = "1.0"  # == rule_defs.RULES_VERSION; bumped with any rule change
LOW_CONFIDENCE_THRESHOLD = 0.5
EVIDENCE_CHARS = 1000


def latest_classifications(db: Session, contract_id: uuid.UUID) -> dict[str, ClauseClassification]:
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id == contract_id)
            .order_by(ClauseClassification.created_at.desc()).all())
    latest: dict[str, ClauseClassification] = {}
    for r in rows:
        latest.setdefault(str(r.clause_id), r)
    return latest


def analyze_contract(db: Session, contract_id: uuid.UUID, *,
                     triggered_by: uuid.UUID | None) -> dict:
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    policies = {c: json.loads(p.params) for c, p in
                active_policies(db, contract.organization_id).items()}
    run = AnalysisRun(organization_id=contract.organization_id, contract_id=contract.id,
                      kind="risk_analysis", model_name=ENGINE_NAME,
                      model_version=ENGINE_VERSION, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        clauses = db.query(Clause).filter(Clause.contract_id == contract.id)\
            .order_by(Clause.order_index).all()
        latest = latest_classifications(db, contract.id)
        n_findings = n_unclassified = 0
        for cl in clauses:
            pred = latest.get(str(cl.id))
            if pred is None:
                n_unclassified += 1
                continue
            for finding in _assess(cl, pred, policies.get(pred.predicted_category)):
                policy_row = None
                if finding.get("policy_category"):
                    policy_row = _policy_row(db, contract.organization_id,
                                             finding.pop("policy_category"))
                priority, reasons = compute_priority(
                    finding["priority_base"],
                    policy_impact=(json.loads(policy_row.params).get("impact")
                                   if policy_row else None),
                    extra_reasons=finding["priority_reasons"])
                db.add(RiskFinding(
                    organization_id=contract.organization_id, contract_id=contract.id,
                    clause_id=cl.id, analysis_run_id=run.id,
                    risk_category=finding["risk_category"],
                    finding_type=finding["finding_type"], priority=priority,
                    title=finding["title"], reason=finding["reason"],
                    evidence=(cl.text or "")[:EVIDENCE_CHARS],
                    section_number=cl.section_number, page_start=cl.page_start,
                    page_end=cl.page_end, rule_id=finding["rule_id"],
                    rule_version=finding["rule_version"],
                    policy_id=policy_row.id if policy_row else None,
                    policy_version=policy_row.version if policy_row else None,
                    contract_value=json.dumps(finding["contract_value"]),
                    expected_value=json.dumps(finding["expected_value"]),
                    extracted_facts=json.dumps(finding.get("extracted_facts") or {}),
                    priority_reasons=json.dumps(reasons),
                    classification_model=pred.model_name,
                    classification_version=pred.model_version,
                    classification_confidence=pred.confidence,
                    status="open"))
                n_findings += 1
        run.status = "done"
        run.stats = json.dumps({"clauses": len(clauses), "unclassified": n_unclassified,
                                "findings": n_findings,
                                "taxonomy_version": RISK_TAXONOMY_VERSION})
        db.commit()
        record(db, action="contract.risk_analyzed", organization_id=contract.organization_id,
               user_id=triggered_by, object_type="contract", object_id=str(contract.id),
               detail=f"{n_findings} findings, engine v{ENGINE_VERSION}")
        from app.modules.risk_engine.state import refresh_analysis_status

        refresh_analysis_status(db, contract)
        return {"status": "done", "run_id": str(run.id), "clauses": len(clauses),
                "unclassified": n_unclassified, "findings": n_findings,
                "engine_version": ENGINE_VERSION}
    except Exception as e:
        db.rollback()
        run = db.get(AnalysisRun, run.id)
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"[:2000]
        db.commit()
        record(db, action="contract.risk_failed", organization_id=contract.organization_id,
               user_id=triggered_by, object_type="contract", object_id=str(contract.id),
               detail=run.error[:500])
        from app.modules.risk_engine.state import refresh_analysis_status

        refresh_analysis_status(db, contract)
        raise


def _policy_row(db: Session, organization_id: uuid.UUID, category: str) -> Policy | None:
    return db.query(Policy).filter(
        Policy.organization_id == organization_id,
        Policy.category == category, Policy.is_active.is_(True)).first()


def _assess(cl: Clause, pred: ClauseClassification, policy: dict | None) -> list[dict]:
    """All findings for one clause: category rules + confidence rule.

    Every returned finding carries `extracted_facts` — the exact deterministic
    facts the rule was evaluated on — so a finding can always be explained
    from what was actually detected (never re-derived later)."""
    category = pred.predicted_category
    facts = extract_facts(category, cl.text or "")
    out = []
    for rule in rule_defs.RULES.get(category, []):
        hit = rule(facts, policy, pred.confidence)
        if hit:
            hit["extracted_facts"] = facts
            if hit["finding_type"] == "policy_deviation":
                hit["policy_category"] = category
            out.append(hit)
    if pred.confidence is not None and pred.confidence < LOW_CONFIDENCE_THRESHOLD:
        out.append({
            "rule_id": "CONFIDENCE-001", "rule_version": ENGINE_VERSION,
            "risk_category": category, "finding_type": "uncertain_classification",
            "priority_base": "low", "extracted_facts": facts,
            "title": "Low classification confidence",
            "reason": (f"The classifier assigned '{category}' with confidence "
                       f"{pred.confidence:.2f} (below {LOW_CONFIDENCE_THRESHOLD}). "
                       "Verify the category manually before relying on these findings."),
            "priority_reasons": ["Classification confidence below threshold",
                                 "Category may be wrong; findings are provisional"],
            "contract_value": {"confidence": pred.confidence}, "expected_value": {}})
    return out
