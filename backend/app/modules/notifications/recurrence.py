"""Recurrence definitions + deterministic instance generation (Phase 10.17-20).

Frequencies: DAILY, WEEKLY, MONTHLY, QUARTERLY, YEARLY, BUSINESS_DAYS.
Month arithmetic reuses Phase 9 `add_period` (month-end clamping) — no
second date implementation. BUSINESS_DAYS = daily stepping that lands on
Mon–Fri (documented, deterministic). Generation is horizon-bounded and
idempotent (unique per recurrence+due_date); generation failures preserve
existing instances.
"""
from __future__ import annotations

import datetime
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Obligation, ObligationEventInstance, RecurrenceDefinition
from app.modules.audit.service import record
from app.modules.obligations import dates as D

FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY", "BUSINESS_DAYS")
DEFAULT_HORIZON_MONTHS = 12
MAX_HORIZON_MONTHS = 24


def _step_kwargs(frequency: str, interval: int) -> dict:
    if frequency == "DAILY":
        return {"days": interval}
    if frequency == "WEEKLY":
        return {"days": 7 * interval}
    if frequency == "MONTHLY":
        return {"months": interval}
    if frequency == "QUARTERLY":
        return {"months": 3 * interval}
    if frequency == "YEARLY":
        return {"years": interval}
    if frequency == "BUSINESS_DAYS":
        return {"days": interval, "business": True}
    raise HTTPException(400, f"Unknown frequency {frequency!r}.")


def _advance(day: datetime.date, step: dict) -> datetime.date:
    if step.get("business"):
        nxt = day
        added = 0
        while added < step["days"]:
            nxt += datetime.timedelta(days=1)
            if nxt.weekday() < 5:
                added += 1
        return nxt
    kw = {k: v for k, v in step.items() if k != "business"}
    return D.add_period(day, **kw)


def upcoming_series(anchor: str, frequency: str, interval: int = 1,
                    horizon_months: int = DEFAULT_HORIZON_MONTHS,
                    ref: datetime.date | None = None) -> list[str]:
    """Due dates in (ref, ref+horizon]. Each occurrence derives from the
    ANCHOR (add k periods), so month-ends never drift (Jan 31 → Feb 28 →
    Mar 31, not Mar 28). Pure + unit-testable."""
    if not 1 <= horizon_months <= MAX_HORIZON_MONTHS:
        raise HTTPException(400, f"horizon_months must be 1..{MAX_HORIZON_MONTHS}.")
    start = D.parse_date(anchor)
    if start is None:
        raise HTTPException(400, f"Unparseable anchor date {anchor!r}.")
    ref = ref or D.today()
    horizon_end = D.add_period(ref, months=horizon_months)
    step = _step_kwargs(frequency, interval)
    out: list[str] = []
    if frequency == "BUSINESS_DAYS":
        day, guard = start, 0
        while guard < 500:
            guard += 1
            day = _advance(day, step)
            if day > horizon_end:
                break
            if day > ref:
                out.append(day.isoformat())
        return out
    k = 1
    while k <= 500:  # hard cap: never an infinite series
        if frequency == "DAILY":
            day = start + datetime.timedelta(days=interval * k)
        elif frequency == "WEEKLY":
            day = start + datetime.timedelta(weeks=interval * k)
        elif frequency == "MONTHLY":
            day = D.add_period(start, months=interval * k)
        elif frequency == "QUARTERLY":
            day = D.add_period(start, months=3 * interval * k)
        elif frequency == "YEARLY":
            day = D.add_period(start, years=interval * k)
        else:
            raise HTTPException(400, f"Unknown frequency {frequency!r}.")
        k += 1
        if day > horizon_end:
            break
        if day > ref:
            out.append(day.isoformat())
    return out


def _resolve_anchor(db: Session, ob: Obligation, anchor_date: str | None,
                    anchor_type: str | None) -> tuple[str, str]:
    if anchor_date:
        if D.parse_date(anchor_date) is None:
            raise HTTPException(400, f"Unparseable anchor_date {anchor_date!r}.")
        return anchor_date, anchor_type or "manual"
    for kind, value in (("deadline", ob.deadline), ("renewal_date", ob.renewal_date),
                        ("notice_deadline", ob.notice_deadline)):
        if value:
            return value, f"obligation_{kind}"
    raise HTTPException(400, "No anchor date: provide anchor_date or extract "
                             "an obligation with a known deadline.")


def create_or_replace(db: Session, *, organization_id: uuid.UUID, obligation: Obligation,
                      user_id: uuid.UUID | None, frequency: str, interval: int = 1,
                      anchor_date: str | None = None, anchor_type: str | None = None,
                      timezone: str = "UTC", horizon_months: int = DEFAULT_HORIZON_MONTHS,
                      event_type: str = "OTHER") -> dict:
    """Upsert the active definition (old ones deactivated, never deleted),
    then generate instances idempotently."""
    if frequency not in FREQUENCIES:
        raise HTTPException(400, f"Unknown frequency {frequency!r}.")
    if interval < 1:
        raise HTTPException(400, "interval must be >= 1.")
    anchor, atype = _resolve_anchor(db, obligation, anchor_date, anchor_type)
    for old in db.query(RecurrenceDefinition).filter(
            RecurrenceDefinition.obligation_id == obligation.id,
            RecurrenceDefinition.active.is_(True)).all():
        old.active = False
    rec = RecurrenceDefinition(
        organization_id=organization_id, obligation_id=obligation.id,
        frequency=frequency, interval=interval, anchor_type=atype,
        anchor_date=anchor, timezone=timezone, active=True)
    db.add(rec)
    db.flush()
    series = upcoming_series(anchor, frequency, interval, horizon_months)
    created = 0
    for due in series:
        exists = db.query(ObligationEventInstance).filter(
            ObligationEventInstance.recurrence_id == rec.id,
            ObligationEventInstance.due_date == due).first()
        if exists is None:
            db.add(ObligationEventInstance(
                organization_id=organization_id, obligation_id=obligation.id,
                recurrence_id=rec.id, event_type=event_type, due_date=due,
                status=D.classify_event(due)))
            created += 1
    rec.next_due_date = series[0] if series else None
    db.commit()
    db.refresh(rec)
    record(db, action="recurrence.created", organization_id=organization_id,
           user_id=user_id, object_type="recurrence_definition", object_id=str(rec.id),
           detail=f"obligation={obligation.id} {frequency}x{interval} +{horizon_months}mo")
    return {"id": str(rec.id), "frequency": frequency, "interval": interval,
            "anchor_type": atype, "anchor_date": anchor,
            "instances_created": created, "next_due_date": rec.next_due_date}


def set_instance_status(db: Session, *, instance, user_id: uuid.UUID | None,
                        to_status: str) -> None:
    if to_status not in ("COMPLETED", "SKIPPED", "CANCELLED"):
        raise HTTPException(400, f"Cannot transition instance to {to_status!r}.")
    if instance.status in ("COMPLETED", "CANCELLED"):
        raise HTTPException(409, f"Instance already {instance.status}.")
    instance.status = to_status
    if to_status == "COMPLETED":
        import datetime as _dt

        instance.completed_at = _dt.datetime.now(_dt.timezone.utc)
        instance.completed_by = user_id
    db.commit()
    record(db, action=f"obligation.instance.{to_status.lower()}",
           organization_id=instance.organization_id, user_id=user_id,
           object_type="obligation_event_instance", object_id=str(instance.id),
           detail=f"due={instance.due_date}")
