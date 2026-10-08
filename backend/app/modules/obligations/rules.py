"""Cross-engine linkage (Phase 9.27-9.28). Read-only references — the Risk
Engine owns risk, the policy engine owns policy positions; lifecycle only
*points at* them. No rule logic is duplicated here.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import RiskFinding

# obligation type -> clause categories that may carry it (display/helper use)
TYPE_CATEGORIES = {
    "PAYMENT": ("payment",), "RENEWAL": ("renewal",),
    "TERMINATION": ("termination",), "INSURANCE": ("insurance",),
    "CONFIDENTIALITY": ("confidentiality",), "AUDIT": ("audit",),
    "NOTICE": ("termination", "renewal"), "REPORTING": ("other",),
    "EFFECTIVE": (), "EXPIRY": (),
}

# obligation type -> policy category exposing the org's expectation
TYPE_POLICY = {
    "PAYMENT": "payment", "TERMINATION": "termination",
    "RENEWAL": "renewal", "INSURANCE": "insurance",
}


def linked_policy_deviation(db: Session, contract_id: uuid.UUID,
                            clause_id: uuid.UUID) -> RiskFinding | None:
    """Latest policy_deviation finding on the same clause, if any."""
    return (db.query(RiskFinding)
            .filter(RiskFinding.contract_id == contract_id,
                    RiskFinding.clause_id == clause_id,
                    RiskFinding.finding_type == "policy_deviation")
            .order_by(RiskFinding.created_at.desc()).first())


def policy_expectation(db: Session, organization_id: uuid.UUID,
                       obligation_type: str) -> dict | None:
    """The org's configured expectation relevant to an obligation (display
    only — Phase 4 owns the actual comparison)."""
    from app.models import Policy

    cat = TYPE_POLICY.get(obligation_type)
    if not cat:
        return None
    row = (db.query(Policy)
           .filter(Policy.organization_id == organization_id,
                   Policy.category == cat, Policy.is_active.is_(True)).first())
    if not row:
        return None
    return {"category": cat, "version": row.version, "params": json.loads(row.params)}
