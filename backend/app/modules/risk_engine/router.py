"""Risk endpoints (Phase 4.9). Org-scoped; same auth/RBAC as contracts.

  POST /contracts/{id}/risk-analysis — run the engine (never the classifier)
  GET  /contracts/{id}/findings — latest run by default; ?run_id= for history
  GET  /contracts/{id}/findings/{finding_id} — one finding, any run
  GET  /contracts/{id}/clauses/{clause_id}/findings — findings for a clause
  GET  /contracts/{id}/risk-assessment — lifecycle + contract summary + every
      clause (incl. no-detected-risk / needs-review) + findings with location,
      original clause text, facts and a deterministic explanation

Filters: priority, category, status, finding_type. Statuses are read-only in
v1 — Phase 8 (human review) owns transitions.
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import AnalysisRun, Clause, Contract, RiskFinding
from app.modules.risk_engine import schemas
from app.modules.risk_engine.analyzer import analyze_contract
from app.modules.risk_engine.explain import explain, requires_attention as explain_attention
from app.modules.risk_engine.state import (analysis_lifecycle, clause_states,
                                           contract_clauses, contract_findings,
                                           latest_risk_run, risk_summary)
from app.modules.risk_engine.taxonomy import FINDING_TYPES, PRIORITIES

router = APIRouter(tags=["risk"])


def _get_owned(db: Session, contract_id: str, org_id) -> Contract:
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")
    c = db.query(Contract).filter(Contract.id == cid, Contract.organization_id == org_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    return c


def _clause_map(db: Session, contract_id: uuid.UUID) -> dict[str, Clause]:
    """clause_id → clause for one contract (one query, no N+1)."""
    return {str(c.id): c for c in
            db.query(Clause).filter(Clause.contract_id == contract_id).all()}


def _recorded_findings(run: AnalysisRun) -> int:
    """How many findings the run recorded when it finished (`stats` is a JSON
    text column). 0 when unreadable — never used to claim findings existed."""
    try:
        stats = json.loads(run.stats) if run.stats else {}
        return int(stats.get("findings") or 0)
    except (ValueError, TypeError, AttributeError):
        return 0


def _out(f: RiskFinding, clause: Clause | None = None) -> dict:
    """One finding, with everything needed to explain where it came from:
    location, rule, facts, original clause text, attention flag, and the
    deterministic explanation (Phase 4/5)."""
    attention, attention_reason = explain_attention(f)
    return {
        "id": str(f.id), "contract_id": str(f.contract_id), "clause_id": str(f.clause_id),
        "analysis_run_id": str(f.analysis_run_id) if f.analysis_run_id else None,
        "risk_category": f.risk_category, "finding_type": f.finding_type,
        "priority": f.priority, "title": f.title, "reason": f.reason,
        "evidence": f.evidence or "", "section_number": f.section_number,
        "clause_title": clause.title if clause is not None else None,
        "clause_text": clause.text if clause is not None else None,
        "page_start": f.page_start, "page_end": f.page_end,
        "requires_attention": attention, "attention_reason": attention_reason,
        "rule_id": f.rule_id, "rule_version": f.rule_version,
        "policy_id": str(f.policy_id) if f.policy_id else None,
        "policy_version": f.policy_version,
        "contract_value": json.loads(f.contract_value or "{}"),
        "expected_value": json.loads(f.expected_value or "{}"),
        "extracted_facts": json.loads(f.extracted_facts or "{}"),
        "priority_reasons": json.loads(f.priority_reasons or "[]"),
        "classification_model": f.classification_model,
        "classification_version": f.classification_version,
        "classification_confidence": f.classification_confidence,
        "status": f.status, "created_at": f.created_at.isoformat(),
        "explanation": explain(f),
    }


def _latest_risk_run(db: Session, contract: Contract) -> uuid.UUID | None:
    """Current risk run (any status) — same scope the summary/lifecycle use,
    so `/findings` can never disagree with the contract's risk summary."""
    run = latest_risk_run(db, contract.id)
    return run.id if run else None


@router.post("/contracts/{contract_id}/risk-analysis", response_model=schemas.RiskRunOut)
def run_risk(contract_id: str, member=Depends(get_current_membership),
             db: Session = Depends(get_db)):
    if member.role == "viewer":
        raise HTTPException(403, "Viewers cannot run risk analysis")
    c = _get_owned(db, contract_id, member.organization_id)
    return analyze_contract(db, c.id, triggered_by=member.user_id)


@router.get("/contracts/{contract_id}/findings", response_model=list[schemas.FindingOut])
def list_findings(contract_id: str, member=Depends(get_current_membership),
                  db: Session = Depends(get_db),
                  priority: str | None = Query(default=None),
                  category: str | None = Query(default=None),
                  status: str | None = Query(default=None),
                  finding_type: str | None = Query(default=None),
                  run_id: str | None = Query(default=None)):
    c = _get_owned(db, contract_id, member.organization_id)
    for name, value, allowed in (("priority", priority, PRIORITIES),
                                 ("finding_type", finding_type, FINDING_TYPES)):
        if value is not None and value not in allowed:
            raise HTTPException(400, f"Invalid {name} {value!r}. Allowed: {list(allowed)}")
    if run_id is not None:
        try:
            rid = uuid.UUID(run_id)
        except ValueError:
            raise HTTPException(404, "Analysis run not found")
    else:
        rid = _latest_risk_run(db, c)
    q = db.query(RiskFinding).filter(RiskFinding.contract_id == c.id)
    if rid is not None:
        # Current run only (failed runs own no rows) — never a mix of runs.
        q = q.filter(RiskFinding.analysis_run_id == rid)
    if priority:
        q = q.filter(RiskFinding.priority == priority)
    if category:
        q = q.filter(RiskFinding.risk_category == category)
    if status:
        q = q.filter(RiskFinding.status == status)
    if finding_type:
        q = q.filter(RiskFinding.finding_type == finding_type)
    clauses = _clause_map(db, c.id)
    rows = [f for f in q.order_by(RiskFinding.created_at).all()
            if str(f.clause_id) in clauses]  # orphaned rows are not current
    if run_id is not None and not rows and not any(
            (priority, category, status, finding_type)):
        # An explicit history lookup must not silently read as "this run found
        # nothing": re-processing cascade-deletes a run's findings along with
        # the clauses they described, while the run row (and its stats) stays.
        run = db.get(AnalysisRun, rid)
        if (run is not None and run.contract_id == c.id
                and (recorded := _recorded_findings(run))):
            raise HTTPException(
                410, f"Findings from this analysis run no longer exist: the "
                     f"contract was re-processed after it ran (the run "
                     f"recorded {recorded} findings). Run the risk analysis "
                     f"again to refresh the current set.")
    return [_out(f, clauses[str(f.clause_id)]) for f in rows]


@router.get("/contracts/{contract_id}/findings/{finding_id}",
            response_model=schemas.FindingOut)
def get_finding(contract_id: str, finding_id: str,
                member=Depends(get_current_membership), db: Session = Depends(get_db)):
    c = _get_owned(db, contract_id, member.organization_id)
    try:
        fid = uuid.UUID(finding_id)
    except ValueError:
        raise HTTPException(404, "Finding not found")
    f = db.query(RiskFinding).filter(RiskFinding.id == fid,
                                     RiskFinding.contract_id == c.id).first()
    if not f:
        raise HTTPException(404, "Finding not found")
    clause = db.get(Clause, f.clause_id)
    return _out(f, clause)


@router.get("/contracts/{contract_id}/clauses/{clause_id}/findings",
            response_model=list[schemas.FindingOut])
def clause_findings(contract_id: str, clause_id: str,
                    member=Depends(get_current_membership), db: Session = Depends(get_db)):
    c = _get_owned(db, contract_id, member.organization_id)
    try:
        cid = uuid.UUID(clause_id)
    except ValueError:
        raise HTTPException(404, "Clause not found")
    if not db.query(Clause).filter(Clause.id == cid, Clause.contract_id == c.id).first():
        raise HTTPException(404, "Clause not found")
    q = db.query(RiskFinding).filter(RiskFinding.contract_id == c.id,
                                     RiskFinding.clause_id == cid)
    rid = _latest_risk_run(db, c)
    if rid is not None:
        q = q.filter(RiskFinding.analysis_run_id == rid)
    rows = q.order_by(RiskFinding.created_at).all()
    clause = db.query(Clause).filter(Clause.id == cid).first()
    return [_out(f, clause) for f in rows]


def _run_info(db: Session, contract_id: uuid.UUID) -> dict:
    """Latest classification / risk run records (for honest failure states)."""
    out = {}
    for key, kind in (("classification", "clause_classification"),
                      ("risk_analysis", "risk_analysis")):
        run = (db.query(AnalysisRun)
               .filter(AnalysisRun.contract_id == contract_id,
                       AnalysisRun.kind == kind)
               .order_by(AnalysisRun.created_at.desc()).first())
        if run is None:
            out[key] = None
            continue
        stats = {}
        try:
            stats = json.loads(run.stats) if run.stats else {}
        except (ValueError, TypeError):
            stats = {}
        out[key] = {
            "run_id": str(run.id), "status": run.status, "error": run.error,
            "model_name": run.model_name, "model_version": run.model_version,
            "stats": stats, "created_at": run.created_at.isoformat(),
        }
    return out


@router.get("/contracts/{contract_id}/risk-assessment")
def risk_assessment(contract_id: str, member=Depends(get_current_membership),
                    db: Session = Depends(get_db)):
    """Complete, org-scoped risk assessment for one contract:

    analysis lifecycle (processing / classification / risk analysis),
    contract-level summary, every clause with its analysis state — including
    clauses with NO detected risk and clauses that were never analyzed — and
    every finding with location, rule, facts, original clause text and a
    structured deterministic explanation.

    Deterministic: derived only from stored clauses, classifications, runs,
    findings and rules. Another organization's contract is a 404.
    """
    c = _get_owned(db, contract_id, member.organization_id)
    analysis = analysis_lifecycle(db, c)
    summary = risk_summary(db, c, analysis)
    states = clause_states(db, c, analysis)
    clauses = contract_clauses(db, c.id)
    clause_map = {str(cl.id): cl for cl in clauses}
    findings = contract_findings(db, c.id)

    out_clauses = []
    for cl in clauses:
        st = states[str(cl.id)]
        out_clauses.append({
            "id": str(cl.id),
            "section_number": cl.section_number, "title": cl.title,
            "text": cl.text, "page_start": cl.page_start, "page_end": cl.page_end,
            "order_index": cl.order_index,
            "risk_state": st["risk_state"], "state_reason": st["state_reason"],
            "top_priority": st["top_priority"],
            "classification": st["classification"],
            "finding_ids": st["finding_ids"],
        })

    return {
        "contract_id": str(c.id),
        "filename": c.filename,
        "analysis": analysis,
        "summary": summary,
        "clauses": out_clauses,
        "findings": [_out(f, clause_map[str(f.clause_id)]) for f in findings
                     if str(f.clause_id) in clause_map],
        "runs": _run_info(db, c.id),
        "generated_from": "deterministic rule engine + stored classifications "
                          "(no LLM; a human reviewer decides)",
    }
