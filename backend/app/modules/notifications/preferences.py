"""Notification preferences (Phase 10.7). Absent row = enabled (documented
default). Writes are self-or-privileged: a member may change their own row;
admin/legal_reviewer may change any row in their org. Viewers may read.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import NotificationPreference
from app.modules.audit.service import record
from app.modules.notifications.taxonomy import NOTIFICATION_TYPES, RECIPIENT_ROLES


def validate_type(notification_type: str) -> str:
    if notification_type not in NOTIFICATION_TYPES:
        raise HTTPException(400, f"Unknown notification type {notification_type!r}.")
    return notification_type


def get_map(db: Session, organization_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, bool]:
    rows = db.query(NotificationPreference).filter(
        NotificationPreference.organization_id == organization_id,
        NotificationPreference.user_id == user_id).all()
    return {r.notification_type: bool(r.enabled) for r in rows}


def is_enabled(db: Session, organization_id: uuid.UUID, user_id: uuid.UUID,
               notification_type: str) -> bool:
    row = db.query(NotificationPreference).filter(
        NotificationPreference.organization_id == organization_id,
        NotificationPreference.user_id == user_id,
        NotificationPreference.notification_type == notification_type).first()
    return True if row is None else bool(row.enabled)


def set_preference(db: Session, *, organization_id: uuid.UUID, target_user_id: uuid.UUID,
                   actor_id: uuid.UUID, actor_role: str, notification_type: str,
                   enabled: bool, channel: str = "inapp"):
    validate_type(notification_type)
    if channel not in ("inapp", "email", "webhook"):
        raise HTTPException(400, f"Unknown channel {channel!r}.")
    if target_user_id != actor_id and actor_role not in ("admin", "legal_reviewer"):
        raise HTTPException(403, "Only admin/legal_reviewer may change others' preferences")
    row = db.query(NotificationPreference).filter(
        NotificationPreference.organization_id == organization_id,
        NotificationPreference.user_id == target_user_id,
        NotificationPreference.notification_type == notification_type).first()
    if row is None:
        row = NotificationPreference(organization_id=organization_id, user_id=target_user_id,
                                     notification_type=notification_type)
        db.add(row)
    row.enabled = enabled
    row.channel = channel
    db.commit()
    db.refresh(row)
    record(db, action="notification.preference_changed", organization_id=organization_id,
           user_id=actor_id, object_type="notification_preference", object_id=str(row.id),
           detail=f"user={target_user_id} type={notification_type} enabled={enabled}")
    return row
