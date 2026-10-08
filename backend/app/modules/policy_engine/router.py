"""Policy endpoints (Phase 4.3 + 5.1/5.2). Org-scoped.

Reads: any member. Writes (upsert/restore/deactivate): admin + legal_reviewer.
Includes the contract-vs-policy overview, built strictly from active
policies × the latest risk run's findings — no duplicated risk logic.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.models import AnalysisRun, Contract, RiskFinding, User
from app.modules.policy_engine import schemas, service

router = APIRouter(prefix="/policies", tags=["policies"])
overview_router = APIRouter(tags=["policies"])


def _out(db: Session, p) -> dict:
    email = None
    if p.created_by:
        u = db.get(User, p.created_by)
        email = u.email if u else None
    return {"id": str(p.id), "category": p.category, "params": json.loads(p.params),
            "version": p.version, "is_active": p.is_active, "created_by": email,
            "created_at": p.created_at.isoformat()}


@router.get("", response_model=list[schemas.PolicyOut])
def list_policies(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return [_out(db, p) for p in service.active_policies(db, member.organization_id).values()]


@router.get("/history", response_model=list[schemas.PolicyOut])
def history(category: str | None = Query(default=None),
            member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return [_out(db, p) for p in service.policy_history(db, member.organization_id, category)]


@router.post("", response_model=schemas.PolicyOut,
             dependencies=[Depends(require_roles("admin", "legal_reviewer"))])
def upsert(body: schemas.PolicyIn, member=Depends(get_current_membership),
           db: Session = Depends(get_db)):
    row = service.set_policy(db, organization_id=member.organization_id,
                             user_id=member.user_id, category=body.category,
                             params=body.params)
    return _out(db, row)


@router.post("/{policy_id}/restore", response_model=schemas.PolicyOut,
             dependencies=[Depends(require_roles("admin", "legal_reviewer"))])
def restore(policy_id: str, member=Depends(get_current_membership),
            db: Session = Depends(get_db)):
    import uuid as _uuid

    try:
        pid = _uuid.UUID(policy_id)
    except ValueError:
        raise HTTPException(404, "Policy version not found")
    return _out(db, service.restore_version(
        db, organization_id=member.organization_id, user_id=member.user_id, policy_id=pid))


@router.delete("/{category}",
               dependencies=[Depends(require_roles("admin", "legal_reviewer"))])
def deactivate(category: str, member=Depends(get_current_membership),
               db: Session = Depends(get_db)):
    service.deactivate_category(db, organization_id=member.organization_id,
                                user_id=member.user_id, category=category)
    return {"ok": True}


# --------------------------------------------- contract policy overview ----


class _OverviewRow(BaseModel):
    category: str
    policy_version: int | None = None
    contract_summary: str | None = None
    policy_summary: str | None = None
    status: str  # deviation | within_policy | no_data | no_policy
    finding_ids: list[str] = []


class OverviewOut(BaseModel):
    contract_id: str
    rows: list[_OverviewRow]


def _summarize(category: str, params: dict, finding: RiskFinding | None) -> tuple[str | None, str]:
    """Human-readable contract vs policy lines from stored finding values."""
    if finding:
        cv = json.loads(finding.contract_value or "{}")
        ev = json.loads(finding.expected_value or "{}")
        if category == "liability":
            cap = cv.get("cap_amount")
            return ((f"Unlimited" if cv.get("unlimited") else
                     (f"${cap:,.0f}" if cap is not None else "No cap identified")),
                    (f"≤ ${ev.get('max_amount', 0):,.0f}" if ev.get("max_amount") is not None
                     else "Cap required"))
        if category == "payment":
            return (f"{cv.get('period_days')} days" if cv.get("period_days") is not None else None,
                    f"≤ {ev.get('max_days')} days")
        if category == "termination":
            return (f"{cv.get('notice_days')} days" if cv.get("notice_days") is not None else None,
                    f"≥ {ev.get('min_notice_days')} days")
        if category == "renewal":
            return ("Auto-renew" if cv.get("auto_renew") else None,
                    "No auto-renew" if ev.get("allow_auto_renew") is False else "Allowed")
        if category in ("governing_law", "jurisdiction"):
            prefs = ev.get("preferred_jurisdictions") or []
            return (cv.get("jurisdiction"), f"One of: {', '.join(prefs)}" if prefs else None)
        if category == "insurance":
            return ((f"${cv['min_coverage']:,.0f}" if cv.get("min_coverage") is not None else None),
                    (f"≥ ${ev['min_coverage']:,.0f}" if ev.get("min_coverage") is not None else None))
    # No deviation finding: describe the policy position alone.
    if category == "liability" and params.get("max_amount") is not None:
        return None, f"≤ ${float(params['max_amount']):,.0f}"
    if category == "payment" and params.get("max_days") is not None:
        return None, f"≤ {params['max_days']} days"
    if category == "termination" and params.get("min_notice_days") is not None:
        return None, f"≥ {params['min_notice_days']} days"
    if category == "renewal":
        return None, ("No auto-renew" if params.get("allow_auto_renew") is False else "Allowed")
    if category in ("governing_law", "jurisdiction") and params.get("preferred_jurisdictions"):
        return None, f"One of: {', '.join(params['preferred_jurisdictions'])}"
    if category == "insurance" and params.get("min_coverage") is not None:
        return None, f"≥ ${float(params['min_coverage']):,.0f}"
    return None, "Configured"


@overview_router.get("/contracts/{contract_id}/policy-overview", response_model=OverviewOut)
def policy_overview(contract_id: str, member=Depends(get_current_membership),
                    db: Session = Depends(get_db)):
    """Per-category policy standing from active policies × latest risk run."""
    import uuid as _uuid

    try:
        cid = _uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")
    contract = db.query(Contract).filter(
        Contract.id == cid, Contract.organization_id == member.organization_id).first()
    if not contract:
        raise HTTPException(404, "Contract not found")
    policies = service.active_policies(db, member.organization_id)
    run = (db.query(AnalysisRun)
           .filter(AnalysisRun.contract_id == contract.id,
                   AnalysisRun.kind == "risk_analysis", AnalysisRun.status == "done")
           .order_by(AnalysisRun.created_at.desc()).first())
    devs: dict[str, RiskFinding] = {}
    if run:
        for f in db.query(RiskFinding).filter(RiskFinding.analysis_run_id == run.id).all():
            if f.finding_type == "policy_deviation":
                devs.setdefault(f.risk_category, f)
    # "Reviewed" = the category was classified in this contract (even when the
    # engine found nothing to flag) — clean clauses read as within_policy.
    from app.models import ClauseClassification as _CC

    reviewed: set[str] = set()
    if run:
        for (cat,) in db.query(_CC.predicted_category).filter(
                _CC.contract_id == contract.id).distinct().all():
            reviewed.add(cat)
    rows = []
    for category, policy in policies.items():
        params = json.loads(policy.params)
        if category in devs:
            contract_s, policy_s = _summarize(category, params, devs[category])
            rows.append({"category": category, "policy_version": policy.version,
                         "contract_summary": contract_s, "policy_summary": policy_s,
                         "status": "deviation", "finding_ids": [str(devs[category].id)]})
        elif category in reviewed:
            _, policy_s = _summarize(category, params, None)
            rows.append({"category": category, "policy_version": policy.version,
                         "contract_summary": None, "policy_summary": policy_s,
                         "status": "within_policy", "finding_ids": []})
        else:
            _, policy_s = _summarize(category, params, None)
            rows.append({"category": category, "policy_version": policy.version,
                         "contract_summary": None, "policy_summary": policy_s,
                         "status": "no_data", "finding_ids": []})
    return {"contract_id": str(contract.id), "rows": rows}
