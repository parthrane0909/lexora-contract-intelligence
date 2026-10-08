"""Candidate building (Phase 10.8): event detection vs delivery, separated.

Lifecycle engine (Phase 9) decides an event is upcoming/due/overdue.
This module decides WHO should be notified: eligible recipients × enabled
preferences × configured windows. Returns candidate dicts; service.py
persists + delivers them.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import LifecycleEvent, Obligation, OrganizationMember
from app.modules.notifications.taxonomy import RECIPIENT_ROLES, windows_for


def eligible_recipients(db: Session, organization_id: uuid.UUID) -> list:
    """Active members in notification-eligible roles (viewers included as
    read-only recipients — they can read, never mutate, via these rows)."""
    from app.models import User

    return (db.query(User, OrganizationMember.role)
            .join(OrganizationMember, OrganizationMember.user_id == User.id)
            .filter(OrganizationMember.organization_id == organization_id,
                    OrganizationMember.role.in_(RECIPIENT_ROLES)).all())


def obligation_eligible(ob: Obligation) -> tuple[bool, str]:
    """Normal operational reminders skip rejected/waived obligations.
    Unconfirmed ones get OBLIGATION_REQUIRES_REVIEW instead (once)."""
    if ob.status in ("NOT_APPLICABLE", "WAIVED"):
        return False, "rejected"
    if ob.status == "UNCONFIRMED":
        return True, "review_only"
    return True, "normal"


def candidates_for_event(db: Session, event: LifecycleEvent,
                         ref=None) -> list[dict]:
    """Window matches for one event: largest entered window per rule type.
    Overdue is emitted once (window None)."""
    from app.modules.obligations import dates as D

    ref = ref or D.today()
    day = D.parse_date(event.event_date)
    cands: list[dict] = []
    if day is None:
        return cands
    delta = (day - ref).days
    if delta < 0:
        # overdue handled separately (single OVERDUE notification)
        return [{"window_days": None, "notification_type": "OVERDUE"}]
    # every entered window yields its own notification (idempotency keys
    # keep one row per window, so repeats and catch-ups stay correct)
    return [{"window_days": w, "notification_type": ntype}
            for w, ntype in sorted(windows_for(event.event_type), reverse=True)
            if delta <= w]


def idempotency_key(organization_id: uuid.UUID, scope_id: str, recipient_id: uuid.UUID,
                    notification_type: str, window) -> str:
    return f"{organization_id}:{scope_id}:{recipient_id}:{notification_type}:{window}"


def review_candidate_key(organization_id: uuid.UUID, obligation_id: uuid.UUID,
                         recipient_id: uuid.UUID) -> str:
    return idempotency_key(organization_id, f"obligation:{obligation_id}", recipient_id,
                           "OBLIGATION_REQUIRES_REVIEW", "once")
