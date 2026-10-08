"""Historical trends from append-only tables (Phase 11.15). Buckets by day
or week using stored created_at timestamps — never reconstructed from
current rows. Model/rule versions ride along where recorded.
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import FeedbackLedger, ObligationReview, ReviewDecision, RiskFinding


def _bucket(ts, granularity: str) -> str:
    if granularity == "week":
        monday = ts.date() - datetime.timedelta(days=ts.weekday())
        return monday.isoformat()
    return ts.date().isoformat()


def _series(rows, granularity: str, key=None):
    out: dict[str, int] = {}
    versions: dict[str, set] = {}
    for r, extra in rows:
        b = _bucket(r.created_at, granularity)
        out[b] = out.get(b, 0) + 1
        if extra:
            versions.setdefault(b, set()).add(extra)
    return [{"bucket": b, "count": out[b],
             "versions": sorted(versions.get(b, []))} for b in sorted(out)]


def _validate(granularity: str):
    if granularity not in ("day", "week"):
        raise ValueError("granularity must be 'day' or 'week'")


def findings_over_time(db: Session, org_id: uuid.UUID, granularity: str = "day"):
    _validate(granularity)
    rows = (db.query(RiskFinding, RiskFinding.rule_version)
            .filter(RiskFinding.organization_id == org_id).all())
    return _series(rows, granularity)


def deviations_over_time(db: Session, org_id: uuid.UUID, granularity: str = "day"):
    _validate(granularity)
    rows = (db.query(RiskFinding, RiskFinding.rule_version)
            .filter(RiskFinding.organization_id == org_id,
                    RiskFinding.finding_type == "policy_deviation").all())
    return _series(rows, granularity)


def reviews_over_time(db: Session, org_id: uuid.UUID, granularity: str = "day"):
    _validate(granularity)
    rows = (db.query(ReviewDecision, ReviewDecision.decision)
            .filter(ReviewDecision.organization_id == org_id).all())
    return _series(rows, granularity)


def obligations_reviewed_over_time(db: Session, org_id: uuid.UUID,
                                   granularity: str = "day"):
    """Obligation-review completion signal (human confirmations over time)."""
    _validate(granularity)
    rows = (db.query(ObligationReview, ObligationReview.decision)
            .filter(ObligationReview.organization_id == org_id).all())
    return _series(rows, granularity)


def trends(db: Session, org_id: uuid.UUID, granularity: str = "day") -> dict:
    return {
        "granularity": granularity,
        "risk_findings": findings_over_time(db, org_id, granularity),
        "policy_deviations": deviations_over_time(db, org_id, granularity),
        "reviews": reviews_over_time(db, org_id, granularity),
        "obligation_reviews": obligations_reviewed_over_time(db, org_id, granularity),
    }
