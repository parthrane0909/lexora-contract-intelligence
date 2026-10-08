"""Notification scan + delivery (Phase 10.27). Deterministic and directly
invocable (tests, cron, K8s CronJob); no always-on scheduler here.

run_notification_scan(db, org_id): refresh states → eligible events →
windows → recipients × preferences → idempotent create → deliver → summary.
Delivery failures are recorded with reason; retry_deliveries() re-attempts
FAILED rows with attempt_count < MAX (bounded, original error preserved).
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import (Contract, LifecycleEvent, Notification, NotificationDelivery,
                        Obligation, ObligationEventInstance, User)
from app.modules.audit.service import record
from app.modules.notifications import rules as R
from app.modules.notifications.preferences import is_enabled
from app.modules.notifications.providers import DeliveryError, NotificationProvider, get_provider
from app.modules.notifications.taxonomy import MAX_DELIVERY_ATTEMPTS
from app.modules.obligations import dates as D

TYPE_LABELS = {
    "UPCOMING_DEADLINE": "Upcoming deadline",
    "DUE_TODAY": "Due today",
    "OVERDUE": "Overdue",
    "RENEWAL_APPROACHING": "Renewal approaching",
    "NOTICE_DEADLINE_APPROACHING": "Notice deadline approaching",
    "CONTRACT_EXPIRING": "Contract expiring",
    "OBLIGATION_REQUIRES_REVIEW": "Obligation requires review",
}


def _title(ntype: str, subject: str, event_date: str | None) -> str:
    base = f"{TYPE_LABELS.get(ntype, ntype)}: {subject}"
    return f"{base} ({event_date})" if event_date else base


def _get_or_create(db: Session, *, org_id, contract_id, obligation_id,
                   event_id, recipient_id, ntype, title, body,
                   event_date, window, key: str):
    existing = db.query(Notification).filter(
        Notification.idempotency_key == key).first()
    if existing is not None:
        return existing, False
    row = Notification(
        organization_id=org_id, contract_id=contract_id, obligation_id=obligation_id,
        lifecycle_event_id=event_id, recipient_user_id=recipient_id,
        notification_type=ntype, title=title, body=body, event_date=event_date,
        window_days=window, status="PENDING", idempotency_key=key)
    db.add(row)
    db.flush()
    return row, True


def _deliver(db: Session, notif: Notification, provider: NotificationProvider,
             recipient_email: str | None) -> str:
    """One attempt, recorded. Returns final delivery status."""
    attempt = NotificationDelivery(
        organization_id=notif.organization_id, notification_id=notif.id,
        recipient_user_id=notif.recipient_user_id, provider=provider.name,
        channel=provider.channel, status="PENDING",
        attempt_count=_attempts(db, notif.id) + 1)
    db.add(attempt)
    db.flush()
    payload = {"id": str(notif.id), "title": notif.title, "body": notif.body or "",
               "event_date": notif.event_date, "recipient_email": recipient_email}
    try:
        import datetime

        mid = provider.send(payload)
        attempt.status = "SENT"
        attempt.provider_message_id = mid
        attempt.sent_at = datetime.datetime.now(datetime.timezone.utc)
        notif.status = "SENT"
        final = "SENT"
    except DeliveryError as e:
        attempt.status = "FAILED"
        attempt.error = str(e)[:2000]
        notif.status = "FAILED"
        final = "FAILED"
    db.commit()
    record(db, action=f"notification.{final.lower()}",
           organization_id=notif.organization_id, user_id=None,
           object_type="notification", object_id=str(notif.id),
           detail=f"type={notif.notification_type} provider={provider.name}")
    return final


def _attempts(db: Session, notification_id: uuid.UUID) -> int:
    rows = db.query(NotificationDelivery).filter(
        NotificationDelivery.notification_id == notification_id).all()
    return max([r.attempt_count for r in rows] + [0])


def retry_deliveries(db: Session, organization_id: uuid.UUID,
                     provider: NotificationProvider | None = None) -> dict:
    """Re-attempt FAILED deliveries below the attempt cap. Original failures
    stay in the ledger; only the outcome changes."""
    provider = provider or get_provider()
    eligible = {n.id for n in db.query(Notification).filter(
        Notification.organization_id == organization_id,
        Notification.status == "FAILED").all()}
    retried = sent = 0
    for n in db.query(Notification).filter(Notification.id.in_(eligible)).all() \
            if eligible else []:
        if _attempts(db, n.id) >= MAX_DELIVERY_ATTEMPTS:
            continue
        email = _recipient_email(db, n.recipient_user_id)
        if _deliver(db, n, provider, email) == "SENT":
            sent += 1
        retried += 1
    return {"retried": retried, "sent": sent}


def _recipient_email(db: Session, user_id) -> str | None:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    return u.email if u else None


def run_notification_scan(db: Session, organization_id: uuid.UUID,
                          provider: NotificationProvider | None = None,
                          ref=None) -> dict:
    """Full scan for one org. Returns explicit summary counts (never hides
    failures). Safe to run repeatedly: idempotency keys prevent duplicates."""
    from app.modules.obligations import service as _ls

    provider = provider or get_provider()
    ref = ref or D.today()
    summary = {"events_checked": 0, "instances_checked": 0, "notifications_created": 0,
               "notifications_skipped": 0, "deliveries_sent": 0, "deliveries_failed": 0}
    members = R.eligible_recipients(db, organization_id)
    if not members:
        return summary
    contracts = db.query(Contract).filter(
        Contract.organization_id == organization_id,
        Contract.is_archived.is_(False)).all()
    filenames = {c.id: c.filename for c in contracts}
    for c in contracts:
        _ls.refresh_event_states(db, c.id)

    events = (db.query(LifecycleEvent)
              .filter(LifecycleEvent.organization_id == organization_id,
                      LifecycleEvent.event_date.is_not(None)).all())
    ob_map = {o.id: o for o in db.query(Obligation).filter(
        Obligation.organization_id == organization_id).all()}
    for ev in events:
        summary["events_checked"] += 1
        ob = ob_map.get(ev.obligation_id) if ev.obligation_id else None
        if ob is not None:
            ok, mode = R.obligation_eligible(ob)
            if not ok:
                summary["notifications_skipped"] += 1
                continue
            if mode == "review_only":
                continue  # handled by the per-obligation review pass below
        for cand in R.candidates_for_event(db, ev, ref):
            for user, _role in members:
                if not is_enabled(db, organization_id, user.id, cand["notification_type"]):
                    summary["notifications_skipped"] += 1
                    continue
                key = R.idempotency_key(
                    organization_id, f"event:{ev.id}", user.id,
                    cand["notification_type"], cand["window_days"])
                title = _title(cand["notification_type"],
                               filenames.get(ev.contract_id, "contract"), ev.event_date)
                body = (f"{TYPE_LABELS.get(cand['notification_type'])}: "
                        f"{ev.event_type} on {ev.event_date}. "
                        f"Contract: {filenames.get(ev.contract_id, '?')}. "
                        f"This is an operational reminder for human review.")
                notif, created = _get_or_create(
                    db, org_id=organization_id, contract_id=ev.contract_id,
                    obligation_id=ev.obligation_id, event_id=ev.id, recipient_id=user.id,
                    ntype=cand["notification_type"], title=title, body=body,
                    event_date=ev.event_date, window=cand["window_days"], key=key)
                if not created:
                    summary["notifications_skipped"] += 1
                    continue
                summary["notifications_created"] += 1
                outcome = _deliver(db, notif, provider, user.email)
                summary["deliveries_sent" if outcome == "SENT" else "deliveries_failed"] += 1

    # review-only pass for unconfirmed obligations (once per recipient)
    for ob in ob_map.values():
        ok, mode = R.obligation_eligible(ob)
        if not (ok and mode == "review_only"):
            continue
        for user, _role in members:
            if not is_enabled(db, organization_id, user.id, "OBLIGATION_REQUIRES_REVIEW"):
                summary["notifications_skipped"] += 1
                continue
            key = R.review_candidate_key(organization_id, ob.id, user.id)
            title = _title("OBLIGATION_REQUIRES_REVIEW", ob.title or ob.type, ob.deadline)
            body = (f"Extracted {ob.type} obligation requires human review. "
                    f"Contract: {filenames.get(ob.contract_id, '?')}.")
            notif, created = _get_or_create(
                db, org_id=organization_id, contract_id=ob.contract_id,
                obligation_id=ob.id, event_id=None, recipient_id=user.id,
                ntype="OBLIGATION_REQUIRES_REVIEW", title=title, body=body,
                event_date=ob.deadline, window=None, key=key)
            if not created:
                summary["notifications_skipped"] += 1
                continue
            summary["notifications_created"] += 1
            outcome = _deliver(db, notif, provider, user.email)
            summary["deliveries_sent" if outcome == "SENT" else "deliveries_failed"] += 1

    # recurrence instances: due/overdue occurrences notify like events
    for inst in db.query(ObligationEventInstance).filter(
            ObligationEventInstance.organization_id == organization_id).all():
        if inst.status in ("COMPLETED", "SKIPPED", "CANCELLED"):
            continue
        ob = ob_map.get(inst.obligation_id)
        if ob is not None and not R.obligation_eligible(ob)[0]:
            continue
        summary["instances_checked"] += 1
        day = D.parse_date(inst.due_date)
        if day is None:
            continue
        delta = (day - ref).days
        if delta < 0:
            ntype, window = "OVERDUE", None
        elif delta <= 7:
            ntype, window = "UPCOMING_DEADLINE", 7
        elif delta <= 30:
            ntype, window = "UPCOMING_DEADLINE", 30
        else:
            continue
        for user, _role in members:
            if not is_enabled(db, organization_id, user.id, ntype):
                summary["notifications_skipped"] += 1
                continue
            key = R.idempotency_key(organization_id, f"instance:{inst.id}", user.id,
                                    ntype, window)
            title = _title(ntype, f"recurring {ob.type if ob else 'obligation'}",
                           inst.due_date)
            cname = filenames.get(ob.contract_id, '?') if ob else '?'
            body = (f"Recurring obligation instance due {inst.due_date}. "
                    f"Contract: {cname}. "
                    f"Complete or skip it from the lifecycle view.")
            notif, created = _get_or_create(
                db, org_id=organization_id,
                contract_id=ob.contract_id if ob else None,
                obligation_id=inst.obligation_id, event_id=None, recipient_id=user.id,
                ntype=ntype, title=title, body=body, event_date=inst.due_date,
                window=window, key=key)
            if not created:
                summary["notifications_skipped"] += 1
                continue
            summary["notifications_created"] += 1
            outcome = _deliver(db, notif, provider, user.email)
            summary["deliveries_sent" if outcome == "SENT" else "deliveries_failed"] += 1

    record(db, action="notification.scan", organization_id=organization_id,
           user_id=None, object_type="organization", object_id=str(organization_id),
           detail=f"events={summary['events_checked']} created={summary['notifications_created']} "
                  f"sent={summary['deliveries_sent']} failed={summary['deliveries_failed']}")
    return summary
