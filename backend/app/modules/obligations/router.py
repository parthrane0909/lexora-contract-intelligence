"""Obligation/lifecycle endpoints (Phase 9.19). Org-scoped throughout.

Read: all members. Extract/review/override: admin/legal_reviewer/
contract_manager (established trigger convention). Cross-org → 404.
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import (Contract, LifecycleEvent, LifecycleFeedback, Obligation,
                        ObligationReview, User)
from app.modules.obligations import dates as D
from app.modules.obligations import schemas, service
from app.modules.obligations.rules import policy_expectation
from app.modules.obligations.taxonomy import NOTIFICATION_WINDOWS

router = APIRouter(tags=["obligations"])
_WRITERS = ("admin", "legal_reviewer", "contract_manager")


def _contract(db: Session, contract_id: str, org_id) -> Contract:
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")
    c = db.query(Contract).filter(
        Contract.id == cid, Contract.organization_id == org_id).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    return c


def _obligation(db: Session, obligation_id: str, org_id) -> Obligation:
    try:
        oid = uuid.UUID(obligation_id)
    except ValueError:
        raise HTTPException(404, "Obligation not found")
    ob = db.query(Obligation).filter(
        Obligation.id == oid, Obligation.organization_id == org_id).first()
    if not ob:
        raise HTTPException(404, "Obligation not found")
    return ob


def _ob_out(db: Session, o: Obligation) -> dict:
    d = service._ob_out(o)
    d["policy_expectation"] = policy_expectation(db, o.organization_id, o.type)
    return d


def _ev_out(e: LifecycleEvent) -> dict:
    return service._ev_out(e)


def _review_out(db: Session, r: ObligationReview) -> dict:
    u = db.get(User, r.reviewer_id)
    return {
        "id": str(r.id), "obligation_id": str(r.obligation_id),
        "reviewer_id": str(r.reviewer_id),
        "reviewer_email": u.email if u else None,
        "decision": r.decision, "comment": r.comment,
        "correction_type": r.correction_type, "corrected_type": r.corrected_type,
        "corrected_date": r.corrected_date,
        "corrected_notice_days": r.corrected_notice_days,
        "corrected_party": r.corrected_party,
        "corrected_trigger": r.corrected_trigger,
        "corrected_status": r.corrected_status,
        "snapshot": json.loads(r.snapshot or "{}"),
        "created_at": r.created_at.isoformat(),
    }


@router.post("/contracts/{contract_id}/obligations/extract",
             response_model=schemas.ExtractOut)
def extract(contract_id: str, member=Depends(get_current_membership),
            db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot run extraction")
    c = _contract(db, contract_id, member.organization_id)
    try:
        return service.extract_contract(db, c.id, triggered_by=member.user_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/contracts/{contract_id}/obligations",
            response_model=list[schemas.ObligationOut])
def list_obligations(contract_id: str, member=Depends(get_current_membership),
                     db: Session = Depends(get_db),
                     type: str | None = Query(default=None),
                     status: str | None = Query(default=None)):
    c = _contract(db, contract_id, member.organization_id)
    q = db.query(Obligation).filter(Obligation.contract_id == c.id)
    if type:
        q = q.filter(Obligation.type == type)
    if status:
        q = q.filter(Obligation.status == status)
    return [_ob_out(db, o) for o in q.order_by(Obligation.created_at).all()]


@router.get("/contracts/{contract_id}/obligations/{obligation_id}",
            response_model=schemas.ObligationOut)
def get_obligation(contract_id: str, obligation_id: str,
                   member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    c = _contract(db, contract_id, member.organization_id)
    ob = _obligation(db, obligation_id, member.organization_id)
    if ob.contract_id != c.id:
        raise HTTPException(404, "Obligation not found")
    return _ob_out(db, ob)


@router.get("/contracts/{contract_id}/lifecycle", response_model=schemas.LifecycleOut)
def lifecycle(contract_id: str, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    c = _contract(db, contract_id, member.organization_id)
    summ = service.lifecycle_summary(db, c)
    for o in summ["obligations"]:
        o["policy_expectation"] = policy_expectation(db, c.organization_id, o["type"])
    return summ


@router.post("/contracts/{contract_id}/lifecycle/override")
def override(contract_id: str, body: schemas.OverrideIn,
             member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot set lifecycle status")
    c = _contract(db, contract_id, member.organization_id)
    service.set_override(db, c, body.status, member.user_id)
    return {"ok": True, "override": body.status}


@router.get("/contracts/{contract_id}/events", response_model=list[schemas.EventOut])
def events(contract_id: str, member=Depends(get_current_membership),
           db: Session = Depends(get_db),
           state: str | None = Query(default=None),
           event_type: str | None = Query(default=None)):
    c = _contract(db, contract_id, member.organization_id)
    service.refresh_event_states(db, c.id)
    q = db.query(LifecycleEvent).filter(LifecycleEvent.contract_id == c.id)
    if event_type:
        q = q.filter(LifecycleEvent.event_type == event_type)
    rows = q.order_by(LifecycleEvent.event_date).all()
    if state:
        want = state.upper()
        rows = [e for e in rows if D.classify_event(e.event_date) == want]
    return [_ev_out(e) for e in rows]


@router.get("/events/upcoming", response_model=list[schemas.EventOut])
def upcoming(within_days: int = Query(default=30),
             member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if within_days not in NOTIFICATION_WINDOWS and within_days != 30:
        raise HTTPException(400, f"within_days must be one of {list(NOTIFICATION_WINDOWS)}.")
    ref = D.today()
    rows = (db.query(LifecycleEvent)
            .filter(LifecycleEvent.organization_id == member.organization_id)
            .order_by(LifecycleEvent.event_date).all())
    out = []
    changed = False
    for e in rows:
        day = D.parse_date(e.event_date)
        if day is None or day < ref or (day - ref).days > within_days:
            continue
        live = D.classify_event(e.event_date)
        if live != e.status and e.status not in ("COMPLETED", "CANCELLED"):
            e.status = live
            changed = True
        out.append(_ev_out(e))
    if changed:
        db.commit()
    return out


@router.get("/events/overdue", response_model=list[schemas.EventOut])
def overdue(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    ref = D.today()
    rows = (db.query(LifecycleEvent)
            .filter(LifecycleEvent.organization_id == member.organization_id)
            .order_by(LifecycleEvent.event_date).all())
    return [_ev_out(e) for e in rows
            if (d := D.parse_date(e.event_date)) is not None and d < ref]


@router.get("/obligations", response_model=list[schemas.ObligationOut])
def all_obligations(member=Depends(get_current_membership), db: Session = Depends(get_db),
                    status: str | None = Query(default=None),
                    type: str | None = Query(default=None)):
    q = db.query(Obligation).filter(Obligation.organization_id == member.organization_id)
    if status:
        q = q.filter(Obligation.status == status)
    if type:
        q = q.filter(Obligation.type == type)
    return [_ob_out(db, o) for o in q.order_by(Obligation.created_at.desc()).limit(200).all()]


@router.get("/obligations/{obligation_id}", response_model=schemas.ObligationOut)
def get_obligation_by_id(obligation_id: str, member=Depends(get_current_membership),
                         db: Session = Depends(get_db)):
    return _ob_out(db, _obligation(db, obligation_id, member.organization_id))


@router.get("/lifecycle/dashboard", response_model=schemas.DashboardOut)
def dashboard(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.dashboard_counts(db, member.organization_id)


@router.post("/obligations/{obligation_id}/reviews", response_model=schemas.ReviewOut)
def review(obligation_id: str, body: schemas.ReviewIn,
           member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot review obligations")
    ob = _obligation(db, obligation_id, member.organization_id)
    r, _ = service.review_obligation(
        db, ob, reviewer_id=member.user_id, reviewer_role=member.role,
        decision=body.decision, comment=body.comment,
        correction_type=body.correction_type,
        corrected={"corrected_type": body.corrected_type,
                   "corrected_date": body.corrected_date,
                   "corrected_notice_days": body.corrected_notice_days,
                   "corrected_party": body.corrected_party,
                   "corrected_trigger": body.corrected_trigger,
                   "corrected_status": body.corrected_status})
    return _review_out(db, r)


@router.get("/obligations/{obligation_id}/reviews", response_model=list[schemas.ReviewOut])
def reviews(obligation_id: str, member=Depends(get_current_membership),
            db: Session = Depends(get_db)):
    ob = _obligation(db, obligation_id, member.organization_id)
    rows = (db.query(ObligationReview)
            .filter(ObligationReview.obligation_id == ob.id)
            .order_by(ObligationReview.created_at.desc()).all())
    return [_review_out(db, r) for r in rows]


@router.get("/obligations/{obligation_id}/feedback")
def feedback(obligation_id: str, member=Depends(get_current_membership),
             db: Session = Depends(get_db)):
    from app.models import LifecycleFeedback

    ob = _obligation(db, obligation_id, member.organization_id)
    rows = (db.query(LifecycleFeedback)
            .filter(LifecycleFeedback.obligation_id == ob.id)
            .order_by(LifecycleFeedback.created_at.desc()).all())
    return [{
        "id": str(f.id), "obligation_id": str(f.obligation_id),
        "review_id": str(f.review_id), "reviewer_role": f.reviewer_role,
        "original_type": f.original_type, "original_deadline": f.original_deadline,
        "original_notice_days": f.original_notice_days,
        "original_party": f.original_party, "original_trigger": f.original_trigger,
        "original_confidence": f.original_confidence,
        "extraction_method": f.extraction_method, "decision": f.decision,
        "correction_type": f.correction_type, "corrected_type": f.corrected_type,
        "corrected_date": f.corrected_date,
        "corrected_notice_days": f.corrected_notice_days,
        "reviewer_comment": f.reviewer_comment,
        "created_at": f.created_at.isoformat()} for f in rows]
