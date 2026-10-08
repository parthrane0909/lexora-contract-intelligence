"""Explanation endpoints (Phase 7.21). Nested under findings, org-scoped.

  POST /contracts/{cid}/findings/{fid}/explanation            generate (appends)
  GET  /contracts/{cid}/findings/{fid}/explanation            latest completed
  GET  /contracts/{cid}/findings/{fid}/explanations           full history
  GET  /contracts/{cid}/findings/{fid}/evidence               retrieval preview

Generate: admin/legal_reviewer/contract_manager (same trigger convention as
classify/risk/compare). Read + evidence preview: any member, viewers included.
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import Contract, RiskExplanation, RiskFinding
from app.modules.llm_explanation import schemas
from app.modules.llm_explanation.evidence import collect_evidence
from app.modules.llm_explanation.providers import LLMUnavailableError, get_llm_provider
from app.modules.llm_explanation.service import (
    explanation_history,
    generate_explanation,
    latest_explanation,
)
from app.modules.llm_explanation.validator import GroundingError

router = APIRouter(tags=["explanations"])
_GENERATORS = ("admin", "legal_reviewer", "contract_manager")


def _finding(db: Session, contract_id: str, finding_id: str, org_id) -> RiskFinding:
    try:
        cid, fid = uuid.UUID(contract_id), uuid.UUID(finding_id)
    except ValueError:
        raise HTTPException(404, "Finding not found")
    contract = db.query(Contract).filter(
        Contract.id == cid, Contract.organization_id == org_id).first()
    if contract is None:
        raise HTTPException(404, "Contract not found")
    finding = db.query(RiskFinding).filter(
        RiskFinding.id == fid, RiskFinding.contract_id == cid).first()
    if finding is None:
        raise HTTPException(404, "Finding not found")
    return finding


def _out(row: RiskExplanation) -> dict:
    body = json.loads(row.explanation or "{}")
    refs = body.get("source_references") or []
    snap = json.loads(row.evidence_snapshot or "[]")

    def _text(key: str) -> str:
        value = body.get(key, "")
        return value if isinstance(value, str) else json.dumps(value)

    return {
        "id": str(row.id), "finding_id": str(row.finding_id), "status": row.status,
        "summary": _text("summary"), "issue": _text("issue"),
        "why_it_matters": _text("why_it_matters"),
        "evidence_summary": _text("evidence_summary"),
        "policy_comparison": _text("policy_comparison"),
        "template_comparison": _text("template_comparison"),
        "historical_context": _text("historical_context"),
        "recommendation": _text("recommendation"),
        "limitations": _text("limitations"),
        "source_references": [{"source_index": r.get("source_index"), "quote": r.get("quote", "")}
                              for r in refs if isinstance(r, dict)],
        "model": row.model or "", "model_version": row.model_version or "",
        "prompt_version": row.prompt_version or "", "evidence_count": len(snap),
        "latency_ms": row.latency_ms, "error": row.error,
        "created_at": row.created_at.isoformat(),
    }


@router.post("/contracts/{contract_id}/findings/{finding_id}/explanation",
             response_model=schemas.ExplanationOut)
def create(contract_id: str, finding_id: str,
           member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _GENERATORS:
        raise HTTPException(403, "Viewers cannot generate explanations")
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    try:
        row = generate_explanation(db, finding.id, triggered_by=member.user_id)
    except (LLMUnavailableError, GroundingError, ValueError) as e:
        raise HTTPException(503 if isinstance(e, LLMUnavailableError) else 422, str(e))
    return _out(row)


@router.get("/contracts/{contract_id}/findings/{finding_id}/explanation",
            response_model=schemas.ExplanationOut)
def latest(contract_id: str, finding_id: str,
           member=Depends(get_current_membership), db: Session = Depends(get_db)):
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    row = latest_explanation(db, finding.id)
    if row is None:
        raise HTTPException(404, "No explanation generated yet for this finding")
    return _out(row)


@router.get("/contracts/{contract_id}/findings/{finding_id}/explanations",
            response_model=list[schemas.ExplanationOut])
def history(contract_id: str, finding_id: str,
            member=Depends(get_current_membership), db: Session = Depends(get_db)):
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    return [_out(r) for r in explanation_history(db, finding.id)]


@router.get("/contracts/{contract_id}/findings/{finding_id}/evidence",
            response_model=list[schemas.EvidenceItemOut])
def evidence_preview(contract_id: str, finding_id: str,
                     member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """Retrieval-only preview of what an explanation would cite (no LLM call)."""
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    try:
        items = collect_evidence(db, finding)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return [{"evidence_id": e["evidence_id"], "source_type": e["source_type"],
             "source_id": e["source_id"], "title": e["title"],
             "category": e.get("category"), "page": e.get("page"),
             "section": e.get("section"), "similarity_score": e.get("similarity_score"),
             "rank": e["rank"], "excerpt": e["text"][:600],
             "contract_id": e.get("contract_id"), "clause_id": e.get("clause_id"),
             "template_id": e.get("template_id")} for e in items]


@router.get("/explanations/health")
def provider_health(member=Depends(get_current_membership)):
    try:
        ok = get_llm_provider().health_check()
    except LLMUnavailableError:
        ok = False
    return {"llm_reachable": ok}
