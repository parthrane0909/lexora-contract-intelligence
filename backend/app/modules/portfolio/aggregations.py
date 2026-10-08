"""SQL aggregation helpers (Phase 11.20/11.28). All counts come from GROUP
BY queries — never by hydrating every row into Python. Every helper scopes
by organization_id first.
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    AnalysisRun,
    Clause,
    ComparisonFinding,
    Contract,
    LifecycleEvent,
    Notification,
    Obligation,
    ObligationEventInstance,
    ReviewDecision,
    RiskFinding,
)


def today_iso() -> str:
    return datetime.date.today().isoformat()


def org_contract_ids(db: Session, org_id: uuid.UUID, include_archived: bool = False):
    q = db.query(Contract.id).filter(Contract.organization_id == org_id)
    if not include_archived:
        q = q.filter(Contract.is_archived.is_(False))
    return [r[0] for r in q.all()]


def latest_risk_run_ids(db: Session, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Latest risk-analysis AnalysisRun per contract (max created_at)."""
    sub = (
        db.query(
            AnalysisRun.contract_id,
            func.max(AnalysisRun.created_at).label("mx"),
        )
        .filter(AnalysisRun.organization_id == org_id,
                AnalysisRun.kind == "risk_analysis")
        .group_by(AnalysisRun.contract_id)
        .subquery()
    )
    rows = (
        db.query(AnalysisRun.contract_id, AnalysisRun.id)
        .join(sub, (AnalysisRun.contract_id == sub.c.contract_id)
              & (AnalysisRun.created_at == sub.c.mx))
        .all()
    )
    # ties on identical timestamps: keep first per contract (deterministic)
    out: dict[str, uuid.UUID] = {}
    for cid, rid in rows:
        out.setdefault(str(cid), rid)
    return out


def latest_run_findings_q(db: Session, org_id: uuid.UUID):
    """RiskFinding query restricted to each contract's latest risk run."""
    run_ids = latest_risk_run_ids(db, org_id)
    q = db.query(RiskFinding).filter(RiskFinding.organization_id == org_id)
    if not run_ids:
        return q.filter(RiskFinding.id.is_(None))  # empty, still scoped
    return q.filter(RiskFinding.analysis_run_id.in_(list(run_ids.values())))


def latest_reviews(db: Session, org_id: uuid.UUID) -> dict[str, ReviewDecision]:
    """Latest ReviewDecision per finding (max created_at, deterministic)."""
    sub = (
        db.query(
            ReviewDecision.finding_id,
            func.max(ReviewDecision.created_at).label("mx"),
        )
        .filter(ReviewDecision.organization_id == org_id)
        .group_by(ReviewDecision.finding_id)
        .subquery()
    )
    rows = (
        db.query(ReviewDecision)
        .join(sub, (ReviewDecision.finding_id == sub.c.finding_id)
              & (ReviewDecision.created_at == sub.c.mx))
        .all()
    )
    out: dict[str, ReviewDecision] = {}
    for r in rows:
        out.setdefault(str(r.finding_id), r)
    return out


def review_state(decision: str | None) -> str:
    if decision is None:
        return "unreviewed"
    return {"accept": "accepted", "reject": "rejected", "modify": "modified"}.get(
        decision, decision)


def count_by(db: Session, model, org_id: uuid.UUID, column, extra_filters=()):
    rows = (
        db.query(column, func.count())
        .filter(model.organization_id == org_id, *extra_filters)
        .group_by(column)
        .all()
    )
    return {k if k is not None else "unspecified": v for k, v in rows}


def findings_by_priority(db: Session, org_id: uuid.UUID):
    return count_by(db, RiskFinding, org_id, RiskFinding.priority,
                    (RiskFinding.id.in_(latest_run_findings_q(db, org_id)
                                        .with_entities(RiskFinding.id)),))


def findings_by_category(db: Session, org_id: uuid.UUID):
    return count_by(db, RiskFinding, org_id, RiskFinding.risk_category,
                    (RiskFinding.id.in_(latest_run_findings_q(db, org_id)
                                        .with_entities(RiskFinding.id)),))


def obligations_by_status(db: Session, org_id: uuid.UUID):
    return count_by(db, Obligation, org_id, Obligation.status)


def obligations_by_type(db: Session, org_id: uuid.UUID):
    return count_by(db, Obligation, org_id, Obligation.type)


def overdue_obligations(db: Session, org_id: uuid.UUID):
    """Obligations with past deadline not in a terminal/waived state."""
    return (
        db.query(Obligation)
        .filter(Obligation.organization_id == org_id,
                Obligation.deadline.is_not(None),
                Obligation.deadline < today_iso(),
                Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE", "OVERDUE")))
        .all()
    )


def renewals_in_window(db: Session, org_id: uuid.UUID, days: int):
    """Obligations with a renewal_date inside [today, today+days]."""
    end = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    return (
        db.query(Obligation)
        .filter(Obligation.organization_id == org_id,
                Obligation.renewal_date.is_not(None),
                Obligation.renewal_date >= today_iso(),
                Obligation.renewal_date <= end,
                Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE")))
        .all()
    )


def latest_comparison_ids(db: Session, org_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Latest Comparison per contract (max created_at)."""
    from app.models import Comparison

    sub = (
        db.query(Comparison.contract_id, func.max(Comparison.created_at).label("mx"))
        .filter(Comparison.organization_id == org_id)
        .group_by(Comparison.contract_id)
        .subquery()
    )
    rows = (
        db.query(Comparison.contract_id, Comparison.id)
        .join(sub, (Comparison.contract_id == sub.c.contract_id)
              & (Comparison.created_at == sub.c.mx))
        .all()
    )
    out: dict[str, uuid.UUID] = {}
    for cid, rid in rows:
        out.setdefault(str(cid), rid)
    return out


def comparison_materiality(db: Session, org_id: uuid.UUID):
    comp_ids = latest_comparison_ids(db, org_id)
    if not comp_ids:
        return {}
    rows = (
        db.query(ComparisonFinding.materiality, func.count())
        .filter(ComparisonFinding.organization_id == org_id,
                ComparisonFinding.comparison_id.in_(list(comp_ids.values())))
        .group_by(ComparisonFinding.materiality)
        .all()
    )
    return {k: v for k, v in rows}
