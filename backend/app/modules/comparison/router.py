"""Comparison endpoints (Phase 5.5/5.11). Org-scoped.

  POST /contracts/{id}/compare — run vs a template version (non-viewer)
  GET  /contracts/{id}/comparisons — run history, latest first
  GET  /comparisons/{id} — one run + its findings (filterable)
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import Comparison, ComparisonFinding, Contract, Template
from app.modules.comparison import schemas
from app.modules.comparison.engine import ENGINE_VERSION, compare_contract

router = APIRouter(tags=["comparison"])


def _get_contract(db: Session, contract_id: str, org_id) -> Contract:
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")
    c = db.query(Contract).filter(Contract.id == cid, Contract.organization_id == org_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    return c


def _get_template(db: Session, template_id: str, org_id) -> Template:
    try:
        tid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(404, "Template not found")
    t = db.query(Template).filter(Template.id == tid, Template.organization_id == org_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    return t


def _comp_out(db: Session, comp: Comparison) -> dict:
    t = db.get(Template, comp.template_id)
    return {
        "id": str(comp.id), "contract_id": str(comp.contract_id),
        "template_id": str(comp.template_id), "template_name": t.name if t else "?",
        "template_version": t.version if t else 0, "engine_version": comp.engine_version,
        "status": comp.status, "stats": json.loads(comp.stats or "{}"),
        "created_at": comp.created_at.isoformat(),
    }


def _finding_out(f: ComparisonFinding) -> dict:
    return {
        "id": str(f.id),
        "template_clause_id": str(f.template_clause_id) if f.template_clause_id else None,
        "contract_clause_id": str(f.contract_clause_id) if f.contract_clause_id else None,
        "category": f.category, "difference_type": f.difference_type,
        "materiality": f.materiality, "title": f.title,
        "template_text": f.template_text, "contract_text": f.contract_text,
        "value_diff": json.loads(f.value_diff or "[]"),
        "risk_finding_id": str(f.risk_finding_id) if f.risk_finding_id else None,
        "reasons": json.loads(f.reasons or "[]"),
    }


@router.post("/contracts/{contract_id}/compare", response_model=schemas.ComparisonOut)
def run_compare(contract_id: str, body: schemas.CompareIn,
                member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role == "viewer":
        raise HTTPException(403, "Viewers cannot run comparisons")
    contract = _get_contract(db, contract_id, member.organization_id)
    template = _get_template(db, body.template_id, member.organization_id)
    try:
        comp = compare_contract(db, contract, template,
                                triggered_by=member.user_id, matcher=body.matcher)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _comp_out(db, comp)


@router.get("/contracts/{contract_id}/comparisons",
            response_model=list[schemas.ComparisonOut])
def list_comparisons(contract_id: str, member=Depends(get_current_membership),
                     db: Session = Depends(get_db)):
    contract = _get_contract(db, contract_id, member.organization_id)
    rows = db.query(Comparison).filter(
        Comparison.contract_id == contract.id).order_by(Comparison.created_at.desc()).all()
    return [_comp_out(db, r) for r in rows]


@router.get("/comparisons/{comparison_id}", response_model=schemas.ComparisonOut)
def get_comparison(comparison_id: str, member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    try:
        cid = uuid.UUID(comparison_id)
    except ValueError:
        raise HTTPException(404, "Comparison not found")
    comp = db.query(Comparison).filter(
        Comparison.id == cid, Comparison.organization_id == member.organization_id).first()
    if not comp:
        raise HTTPException(404, "Comparison not found")
    out = _comp_out(db, comp)
    return out


@router.get("/comparisons/{comparison_id}/findings",
            response_model=list[schemas.ComparisonFindingOut])
def comparison_findings(comparison_id: str, member=Depends(get_current_membership),
                        db: Session = Depends(get_db),
                        difference_type: str | None = Query(default=None),
                        materiality: str | None = Query(default=None)):
    try:
        cid = uuid.UUID(comparison_id)
    except ValueError:
        raise HTTPException(404, "Comparison not found")
    comp = db.query(Comparison).filter(
        Comparison.id == cid, Comparison.organization_id == member.organization_id).first()
    if not comp:
        raise HTTPException(404, "Comparison not found")
    q = db.query(ComparisonFinding).filter(ComparisonFinding.comparison_id == comp.id)
    if difference_type:
        if difference_type not in ("added", "missing", "modified", "unchanged"):
            raise HTTPException(400, f"Invalid difference_type {difference_type!r}")
        q = q.filter(ComparisonFinding.difference_type == difference_type)
    if materiality:
        if materiality not in ("material", "potentially_material", "informational"):
            raise HTTPException(400, f"Invalid materiality {materiality!r}")
        q = q.filter(ComparisonFinding.materiality == materiality)
    return [_finding_out(f) for f in q.order_by(ComparisonFinding.created_at).all()]
