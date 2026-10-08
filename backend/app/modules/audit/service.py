"""Audit log writer (spec Module 28). Every major action records who/what/when."""
import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def record(
    db: Session,
    *,
    action: str,
    organization_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    detail: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        organization_id=organization_id,
        user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        detail=detail,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
