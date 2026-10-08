"""Company policy store (Phase 4.3). Per-org, per-category, versioned.

Updating a category inserts a new row (version+1) and deactivates the old
one — history is never mutated, so findings always resolve to the exact
policy text that produced them. No company values are hard-coded anywhere.
"""
from __future__ import annotations

import json
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Policy
from app.modules.audit.service import record
from app.modules.policy_engine.schemas import POLICY_CATEGORIES, POLICY_SHAPES


def validate_policy(category: str, params: dict) -> dict:
    if category not in POLICY_SHAPES:
        raise HTTPException(400, f"Unknown policy category {category!r}. "
                                 f"Supported: {POLICY_CATEGORIES}")
    if not isinstance(params, dict):
        raise HTTPException(400, "params must be an object")
    unknown = set(params) - POLICY_SHAPES[category]
    if unknown:
        raise HTTPException(400, f"Unknown params for {category}: {sorted(unknown)}")
    impact = params.get("impact", "standard")
    if impact not in ("standard", "high"):
        raise HTTPException(400, "impact must be 'standard' or 'high'")
    for key in ("max_amount", "max_days", "min_notice_days", "min_coverage"):
        if key in params and (not isinstance(params[key], (int, float)) or params[key] < 0):
            raise HTTPException(400, f"{key} must be a non-negative number")
    if "allow_auto_renew" in params and not isinstance(params["allow_auto_renew"], bool):
        raise HTTPException(400, "allow_auto_renew must be a boolean")
    if "preferred_jurisdictions" in params:
        v = params["preferred_jurisdictions"]
        if not isinstance(v, list) or not v or not all(isinstance(j, str) for j in v):
            raise HTTPException(400, "preferred_jurisdictions must be a non-empty string list")
    cleaned = dict(params)
    cleaned.setdefault("impact", "standard")
    return cleaned


def set_policy(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID,
               category: str, params: dict) -> Policy:
    """Insert a new version; deactivate the previous active row."""
    cleaned = validate_policy(category, params)
    current = db.query(Policy).filter(
        Policy.organization_id == organization_id,
        Policy.category == category, Policy.is_active.is_(True)).first()
    # Version is max-over-history + 1 so deactivation never rewinds numbering.
    max_version = db.query(Policy.version).filter(
        Policy.organization_id == organization_id,
        Policy.category == category).order_by(Policy.version.desc()).first()
    version = (max_version[0] + 1) if max_version else 1
    if current:
        current.is_active = False
    row = Policy(organization_id=organization_id, category=category,
                 params=json.dumps(cleaned), version=version, is_active=True,
                 created_by=user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    record(db, action="policy.updated", organization_id=organization_id, user_id=user_id,
           object_type="policy", object_id=str(row.id),
           detail=f"{category} v{version}: {json.dumps(cleaned)[:200]}")
    return row


def active_policies(db: Session, organization_id: uuid.UUID) -> dict[str, Policy]:
    rows = db.query(Policy).filter(
        Policy.organization_id == organization_id, Policy.is_active.is_(True)).all()
    return {r.category: r for r in rows}


def policy_history(db: Session, organization_id: uuid.UUID,
                   category: str | None = None) -> list[Policy]:
    q = db.query(Policy).filter(Policy.organization_id == organization_id)
    if category:
        q = q.filter(Policy.category == category)
    return q.order_by(Policy.category, Policy.version.desc()).all()


def restore_version(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID,
                    policy_id: uuid.UUID) -> Policy:
    """Revert-as-new-version: copy an old version's params into version+1.
    History stays append-only; findings keep resolving to exact versions."""
    old = db.query(Policy).filter(
        Policy.id == policy_id, Policy.organization_id == organization_id).first()
    if not old:
        from fastapi import HTTPException

        raise HTTPException(404, "Policy version not found")
    return set_policy(db, organization_id=organization_id, user_id=user_id,
                      category=old.category, params=json.loads(old.params))


def deactivate_category(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID,
                        category: str) -> None:
    """Remove a category's active position (risk policy rules then skip it)."""
    from app.modules.policy_engine.schemas import POLICY_CATEGORIES

    if category not in POLICY_CATEGORIES:
        from fastapi import HTTPException

        raise HTTPException(400, f"Unknown policy category {category!r}")
    current = db.query(Policy).filter(
        Policy.organization_id == organization_id,
        Policy.category == category, Policy.is_active.is_(True)).first()
    if not current:
        from fastapi import HTTPException

        raise HTTPException(404, f"No active policy for {category}")
    current.is_active = False
    db.commit()
    record(db, action="policy.deactivated", organization_id=organization_id, user_id=user_id,
           object_type="policy", object_id=str(current.id),
           detail=f"{category} v{current.version} deactivated")
