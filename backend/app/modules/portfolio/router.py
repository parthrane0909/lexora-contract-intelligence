"""Portfolio endpoints (Phase 11.21). All GET, org-scoped, readable by all
members (read-only views). No admin configuration exists in Phase 11.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.modules.portfolio import schemas, service
from app.modules.portfolio.counterparties import counterparty_portfolio
from app.modules.portfolio.health import portfolio_health
from app.modules.portfolio.trends import trends

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/health", response_model=schemas.PortfolioHealthOut)
def health_view(member=Depends(get_current_membership),
                db: Session = Depends(get_db)):
    """Whole-portfolio health score (backend-authoritative, versioned).

    Distinct from GET /portfolio/attention: that returns a per-contract
    OPERATIONAL ATTENTION score (higher = more attention needed); this
    returns a portfolio HEALTH score (higher = healthier)."""
    return portfolio_health(db, member.organization_id)


@router.get("/summary", response_model=schemas.SummaryOut)
def summary(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.portfolio_summary(db, member.organization_id)


@router.get("/attention", response_model=list[schemas.AttentionItem])
def attention(member=Depends(get_current_membership), db: Session = Depends(get_db),
              min_score: int | None = Query(default=None),
              counterparty: str | None = Query(default=None),
              has_unreviewed: bool = Query(default=False),
              has_overdue: bool = Query(default=False),
              priority: str | None = Query(default=None),
              category: str | None = Query(default=None),
              review_state: str | None = Query(default=None)):
    if priority is not None and priority not in ("critical", "high"):
        raise HTTPException(400, "priority filter must be 'critical' or 'high'")
    if review_state is not None and review_state not in (
            "unreviewed", "accepted", "rejected", "modified", "needs_more_review"):
        raise HTTPException(400, f"Invalid review_state {review_state!r}")
    return service.attention_queue(
        db, member.organization_id, min_score=min_score, counterparty=counterparty,
        has_unreviewed=has_unreviewed, has_overdue=has_overdue, priority=priority,
        category=category, review_state=review_state)


@router.get("/risks", response_model=schemas.RiskPortfolioOut)
def risks(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.risk_portfolio(db, member.organization_id)


@router.get("/obligations", response_model=schemas.ObligationPortfolioOut)
def obligations(member=Depends(get_current_membership), db: Session = Depends(get_db),
                contract_id: str | None = Query(default=None),
                type: str | None = Query(default=None),
                status: str | None = Query(default=None),
                party: str | None = Query(default=None),
                date_from: str | None = Query(default=None),
                date_to: str | None = Query(default=None)):
    for name, value in (("contract_id", contract_id),):
        if value is not None:
            try:
                uuid.UUID(value)
            except ValueError:
                raise HTTPException(400, f"Invalid {name}.")
    return service.obligation_portfolio(
        db, member.organization_id, contract_id=contract_id, type=type,
        status=status, party=party, date_from=date_from, date_to=date_to)


@router.get("/renewals", response_model=schemas.RenewalPortfolioOut)
def renewals(member=Depends(get_current_membership), db: Session = Depends(get_db),
             window_days: int = Query(default=90)):
    if window_days not in (30, 60, 90, 180):
        raise HTTPException(400, "window_days must be one of 30, 60, 90, 180")
    return service.renewal_portfolio(db, member.organization_id, window_days)


@router.get("/policy-deviations", response_model=schemas.PolicyDeviationOut)
def deviations(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.policy_deviation_portfolio(db, member.organization_id)


@router.get("/comparisons", response_model=schemas.ComparisonPortfolioOut)
def comparisons(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.comparison_portfolio(db, member.organization_id)


@router.get("/counterparties", response_model=schemas.CounterpartyPortfolioOut)
def counterparties(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return counterparty_portfolio(db, member.organization_id)


@router.get("/trends", response_model=schemas.TrendsOut)
def trend_view(member=Depends(get_current_membership), db: Session = Depends(get_db),
               granularity: str = Query(default="day")):
    try:
        return trends(db, member.organization_id, granularity)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/review-feedback", response_model=schemas.FeedbackAnalyticsOut)
def feedback(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.feedback_analytics(db, member.organization_id)


@router.get("/notifications", response_model=schemas.NotificationAnalyticsOut)
def notifications(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    return service.notification_analytics(db, member.organization_id)


@router.get("/contracts/{contract_id}", response_model=schemas.ContractDrilldownOut)
def contract_drilldown(contract_id: str, member=Depends(get_current_membership),
                       db: Session = Depends(get_db)):
    try:
        cid = uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")
    from app.models import Contract

    contract = db.query(Contract).filter(
        Contract.id == cid, Contract.organization_id == member.organization_id).first()
    if not contract:
        raise HTTPException(404, "Contract not found")
    return {
        "attention": service.contract_attention(db, member.organization_id, cid),
        "obligations": service.obligation_portfolio(
            db, member.organization_id, contract_id=str(cid)),
        "renewals": service.renewal_portfolio(db, member.organization_id, 180),
        "policy_deviations": service.policy_deviation_portfolio(
            db, member.organization_id),
    }
