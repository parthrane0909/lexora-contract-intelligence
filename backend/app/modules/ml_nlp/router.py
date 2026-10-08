"""Classification endpoints (Phase 3). All org-scoped; same auth/RBAC as contracts.

  POST /contracts/{id}/classify — run the Transformer over every clause
  GET  /contracts/{id}/classifications — latest prediction per clause
  GET  /contracts/{id}/clauses/{clause_id}/classification — latest for one clause
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import Clause, ClauseClassification, Contract
from app.modules.ml_nlp import schemas
from app.modules.ml_nlp.classifier import (
    ModelUnavailableError,
    classify_contract,
)

router = APIRouter(tags=["classification"])


def _get_owned(db: Session, contract_id: str, org_id) -> Contract:
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")
    c = db.query(Contract).filter(Contract.id == cid, Contract.organization_id == org_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    return c


def _out(row: ClauseClassification) -> dict:
    return {
        "clause_id": str(row.clause_id), "predicted_category": row.predicted_category,
        "confidence": row.confidence, "model_name": row.model_name,
        "model_version": row.model_version,
        "analysis_run_id": str(row.analysis_run_id) if row.analysis_run_id else None,
        "created_at": row.created_at.isoformat(),
    }


def _latest_map(db: Session, contract: Contract) -> dict[str, ClauseClassification]:
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id == contract.id)
            .order_by(ClauseClassification.created_at.desc()).all())
    latest: dict[str, ClauseClassification] = {}
    for r in rows:  # append-only history; first hit per clause is the latest
        latest.setdefault(str(r.clause_id), r)
    return latest


@router.post("/contracts/{contract_id}/classify", response_model=schemas.ClassifyRunOut)
def classify(contract_id: str, member=Depends(get_current_membership),
             db: Session = Depends(get_db)):
    """Trigger Transformer classification over all clauses (re-runnable)."""
    if member.role == "viewer":
        raise HTTPException(403, "Viewers cannot run classification")
    c = _get_owned(db, contract_id, member.organization_id)
    try:
        return classify_contract(db, c.id, triggered_by=member.user_id)
    except ModelUnavailableError as e:
        raise HTTPException(503, str(e))


@router.get("/contracts/{contract_id}/classifications",
            response_model=list[schemas.ClassificationOut])
def list_classifications(contract_id: str, member=Depends(get_current_membership),
                         db: Session = Depends(get_db)):
    c = _get_owned(db, contract_id, member.organization_id)
    return [_out(r) for r in _latest_map(db, c).values()]


@router.get("/contracts/{contract_id}/clauses/{clause_id}/classification",
            response_model=schemas.ClassificationOut)
def get_classification(contract_id: str, clause_id: str,
                       member=Depends(get_current_membership), db: Session = Depends(get_db)):
    c = _get_owned(db, contract_id, member.organization_id)
    try:
        cid = uuid.UUID(clause_id)
    except ValueError:
        raise HTTPException(404, "Clause not found")
    if not db.query(Clause).filter(Clause.id == cid, Clause.contract_id == c.id).first():
        raise HTTPException(404, "Clause not found")
    latest = _latest_map(db, c).get(str(cid))
    if not latest:
        raise HTTPException(404, "No classification for this clause yet (run classification first)")
    return _out(latest)
