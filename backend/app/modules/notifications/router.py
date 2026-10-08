"""Notification/recurrence endpoints (Phase 10.26). Org-scoped throughout.

Read (notifications, deliveries, preferences, instances): all members.
Mutations (scan, preferences, read-own, recurrence, complete/skip):
scan+preferences+recurrence+complete = admin/legal_reviewer/contract_manager;
read-state changes are per-recipient (own rows, or privileged roles).
Cross-org → 404. Viewers never mutate lifecycle state.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import (Notification, NotificationDelivery, Obligation,
                        ObligationEventInstance, RecurrenceDefinition, User)
from app.modules.notifications import recurrence as RC
from app.modules.notifications import schemas, service
from app.modules.notifications.preferences import get_map, set_preference
from app.modules.notifications.providers import get_provider

router = APIRouter(tags=["notifications"])
_WRITERS = ("admin", "legal_reviewer", "contract_manager")
_PRIVILEGED = ("admin", "legal_reviewer")


def _owned_notification(db: Session, notification_id: str, org_id) -> Notification:
    try:
        nid = uuid.UUID(notification_id)
    except ValueError:
        raise HTTPException(404, "Notification not found")
    n = db.query(Notification).filter(
        Notification.id == nid, Notification.organization_id == org_id).first()
    if not n:
        raise HTTPException(404, "Notification not found")
    return n


def _owned_obligation(db: Session, obligation_id: str, org_id) -> Obligation:
    try:
        oid = uuid.UUID(obligation_id)
    except ValueError:
        raise HTTPException(404, "Obligation not found")
    ob = db.query(Obligation).filter(
        Obligation.id == oid, Obligation.organization_id == org_id).first()
    if not ob:
        raise HTTPException(404, "Obligation not found")
    return ob


def _notif_out(n: Notification) -> dict:
    return {
        "id": str(n.id), "contract_id": str(n.contract_id) if n.contract_id else None,
        "obligation_id": str(n.obligation_id) if n.obligation_id else None,
        "lifecycle_event_id": str(n.lifecycle_event_id) if n.lifecycle_event_id else None,
        "notification_type": n.notification_type, "title": n.title, "body": n.body,
        "event_date": n.event_date, "window_days": n.window_days, "status": n.status,
        "is_read": n.is_read, "created_at": n.created_at.isoformat(),
    }


@router.post("/notifications/scan", response_model=schemas.ScanOut)
def scan(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot trigger scans")
    return service.run_notification_scan(db, member.organization_id)


@router.get("/notifications", response_model=list[schemas.NotificationOut])
def list_notifications(member=Depends(get_current_membership), db: Session = Depends(get_db),
                       unread_only: bool = False,
                       notification_type: str | None = Query(default=None)):
    q = db.query(Notification).filter(
        Notification.organization_id == member.organization_id,
        Notification.recipient_user_id == member.user_id)
    if unread_only:
        q = q.filter(Notification.is_read.is_(False))
    if notification_type:
        q = q.filter(Notification.notification_type == notification_type)
    return [_notif_out(n) for n in q.order_by(Notification.created_at.desc()).limit(200).all()]


@router.get("/notifications/unread", response_model=list[schemas.NotificationOut])
def unread(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    rows = db.query(Notification).filter(
        Notification.organization_id == member.organization_id,
        Notification.recipient_user_id == member.user_id,
        Notification.is_read.is_(False)).order_by(Notification.created_at.desc()).all()
    return [_notif_out(n) for n in rows]


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    n = _owned_notification(db, notification_id, member.organization_id)
    if n.recipient_user_id != member.user_id and member.role not in _PRIVILEGED:
        raise HTTPException(403, "Cannot change another user's read state")
    n.is_read = True
    from app.modules.obligations.dates import today as _today

    import datetime

    n.read_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    from app.modules.audit.service import record

    record(db, action="notification.read", organization_id=member.organization_id,
           user_id=member.user_id, object_type="notification", object_id=str(n.id))
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    import datetime

    rows = db.query(Notification).filter(
        Notification.organization_id == member.organization_id,
        Notification.recipient_user_id == member.user_id,
        Notification.is_read.is_(False)).all()
    for n in rows:
        n.is_read = True
        n.read_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    return {"ok": True, "marked": len(rows)}


@router.get("/notifications/deliveries", response_model=list[schemas.DeliveryOut])
def deliveries(member=Depends(get_current_membership), db: Session = Depends(get_db),
               status: str | None = Query(default=None)):
    if member.role not in _PRIVILEGED:
        raise HTTPException(403, "Delivery ledger is restricted to admin/reviewer roles")
    q = db.query(NotificationDelivery).filter(
        NotificationDelivery.organization_id == member.organization_id)
    if status:
        q = q.filter(NotificationDelivery.status == status)
    out = []
    for d in q.order_by(NotificationDelivery.created_at.desc()).limit(200).all():
        out.append({
            "id": str(d.id), "notification_id": str(d.notification_id),
            "provider": d.provider, "channel": d.channel, "status": d.status,
            "attempt_count": d.attempt_count, "provider_message_id": d.provider_message_id,
            "error": d.error,
            "sent_at": d.sent_at.isoformat() if d.sent_at else None})
    return out


@router.post("/notifications/retry", response_model=schemas.ScanOut)
def retry(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot retry deliveries")
    r = service.retry_deliveries(db, member.organization_id)
    return {"events_checked": 0, "instances_checked": 0, "notifications_created": 0,
            "notifications_skipped": 0, "deliveries_sent": r["sent"],
            "deliveries_failed": r["retried"] - r["sent"]}


@router.get("/notification-preferences", response_model=list[schemas.PreferenceOut])
def get_prefs(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    from app.modules.notifications.taxonomy import NOTIFICATION_TYPES as _T

    saved = get_map(db, member.organization_id, member.user_id)
    return [{"notification_type": t, "enabled": saved.get(t, True), "channel": "inapp"}
            for t in _T]


@router.put("/notification-preferences", response_model=schemas.PreferenceOut)
def put_prefs(body: schemas.PreferenceIn, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    target = member.user_id
    if body.user_id:
        try:
            target = uuid.UUID(body.user_id)
        except ValueError:
            raise HTTPException(400, "Invalid user_id.")
    row = set_preference(db, organization_id=member.organization_id,
                         target_user_id=target, actor_id=member.user_id,
                         actor_role=member.role, notification_type=body.notification_type,
                         enabled=body.enabled, channel=body.channel)
    return {"notification_type": row.notification_type, "enabled": row.enabled,
            "channel": row.channel}


@router.get("/notifications/health")
def provider_health(member=Depends(get_current_membership)):
    try:
        ok = get_provider().health()
    except Exception:
        ok = False
    return {"provider_reachable": ok}


@router.get("/obligations/{obligation_id}/recurrence",
            response_model=schemas.RecurrenceOut | None)
def get_recurrence(obligation_id: str, member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    ob = _owned_obligation(db, obligation_id, member.organization_id)
    rec = db.query(RecurrenceDefinition).filter(
        RecurrenceDefinition.obligation_id == ob.id,
        RecurrenceDefinition.active.is_(True)).first()
    if not rec:
        return None
    return {"id": str(rec.id), "frequency": rec.frequency, "interval": rec.interval,
            "anchor_type": rec.anchor_type, "anchor_date": rec.anchor_date,
            "next_due_date": rec.next_due_date, "active": rec.active,
            "instances_created": db.query(ObligationEventInstance).filter(
                ObligationEventInstance.recurrence_id == rec.id).count()}


@router.post("/obligations/{obligation_id}/recurrence",
             response_model=schemas.RecurrenceOut)
def set_recurrence(obligation_id: str, body: schemas.RecurrenceIn,
                   member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot configure recurrence")
    ob = _owned_obligation(db, obligation_id, member.organization_id)
    return RC.create_or_replace(
        db, organization_id=member.organization_id, obligation=ob,
        user_id=member.user_id, frequency=body.frequency, interval=body.interval,
        anchor_date=body.anchor_date, anchor_type=body.anchor_type,
        timezone=body.timezone, horizon_months=body.horizon_months,
        event_type=f"{ob.type}_RECURRING")


@router.get("/obligations/{obligation_id}/instances",
            response_model=list[schemas.InstanceOut])
def instances(obligation_id: str, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    from app.modules.obligations import dates as D

    ob = _owned_obligation(db, obligation_id, member.organization_id)
    rows = db.query(ObligationEventInstance).filter(
        ObligationEventInstance.obligation_id == ob.id).order_by(
        ObligationEventInstance.due_date).all()
    out = []
    for i in rows:
        live = D.classify_event(i.due_date)
        if live != i.status and i.status not in ("COMPLETED", "SKIPPED", "CANCELLED"):
            i.status = live
        out.append({
            "id": str(i.id), "obligation_id": str(i.obligation_id),
            "event_type": i.event_type, "due_date": i.due_date, "status": i.status,
            "completed_at": i.completed_at.isoformat() if i.completed_at else None})
    db.commit()
    return out


def _owned_instance(db: Session, instance_id: str, org_id) -> ObligationEventInstance:
    try:
        iid = uuid.UUID(instance_id)
    except ValueError:
        raise HTTPException(404, "Instance not found")
    inst = db.query(ObligationEventInstance).filter(
        ObligationEventInstance.id == iid,
        ObligationEventInstance.organization_id == org_id).first()
    if not inst:
        raise HTTPException(404, "Instance not found")
    return inst


@router.post("/obligations/{obligation_id}/instances/{instance_id}/complete")
def complete(obligation_id: str, instance_id: str,
             member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot complete instances")
    ob = _owned_obligation(db, obligation_id, member.organization_id)
    inst = _owned_instance(db, instance_id, member.organization_id)
    if inst.obligation_id != ob.id:
        raise HTTPException(404, "Instance not found")
    RC.set_instance_status(db, instance=inst, user_id=member.user_id, to_status="COMPLETED")
    return {"ok": True, "status": "COMPLETED"}


@router.post("/obligations/{obligation_id}/instances/{instance_id}/skip")
def skip(obligation_id: str, instance_id: str,
         member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot skip instances")
    ob = _owned_obligation(db, obligation_id, member.organization_id)
    inst = _owned_instance(db, instance_id, member.organization_id)
    if inst.obligation_id != ob.id:
        raise HTTPException(404, "Instance not found")
    RC.set_instance_status(db, instance=inst, user_id=member.user_id, to_status="SKIPPED")
    return {"ok": True, "status": "SKIPPED"}


@router.get("/lifecycle/recurrence/upcoming", response_model=list[schemas.InstanceOut])
def recurrence_upcoming(within_days: int = Query(default=30),
                        member=Depends(get_current_membership), db: Session = Depends(get_db)):
    from app.modules.obligations import dates as D

    ref = D.today()
    rows = (db.query(ObligationEventInstance)
            .filter(ObligationEventInstance.organization_id == member.organization_id)
            .order_by(ObligationEventInstance.due_date).all())
    out = []
    for i in rows:
        if i.status in ("COMPLETED", "SKIPPED", "CANCELLED"):
            continue
        day = D.parse_date(i.due_date)
        if day is None or day < ref or (day - ref).days > within_days:
            continue
        out.append({
            "id": str(i.id), "obligation_id": str(i.obligation_id),
            "event_type": i.event_type, "due_date": i.due_date,
            "status": D.classify_event(i.due_date),
            "completed_at": i.completed_at.isoformat() if i.completed_at else None})
    return out
