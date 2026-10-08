"""Portfolio service (Phase 11). Pure aggregation over authoritative tables —
no recomputation of risk, no duplicated domain logic.

Attention score (operational prioritization ONLY, never a legal-risk
probability). PORTFOLIO_SCORING_VERSION pins the formula:
  critical finding            +10
  high finding                + 6
  unreviewed finding          + 3
  overdue obligation          + 5
  renewal within 30 days      + 4
  notice deadline within 30d  + 4
  policy deviation            + 3
Scores use latest-run findings; rejected findings are shown but contribute
no risk weight (review state is exposed separately, never merged).
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy.orm import Session

from app.models import (
    ComparisonFinding,
    Contract,
    FeedbackLedger,
    LifecycleEvent,
    Notification,
    NotificationDelivery,
    Obligation,
    ObligationEventInstance,
    ReviewDecision,
    RiskFinding,
)
from app.modules.portfolio import aggregations as A

PORTFOLIO_SCORING_VERSION = "1.0"
WEIGHTS = {
    "critical": 10,
    "high": 6,
    "unreviewed": 3,
    "overdue_obligation": 5,
    "renewal_30d": 4,
    "notice_30d": 4,
    "policy_deviation": 3,
}

REVIEWED_EXCLUDE_PRIORITY = ("reject",)  # rejected findings add no risk weight


def _within_days(iso_date: str | None, days: int, today: str) -> bool:
    if not iso_date:
        return False
    try:
        end = (datetime.date.fromisoformat(today)
               + datetime.timedelta(days=days)).isoformat()
        return today <= iso_date <= end
    except ValueError:
        return False


def contract_attention(db: Session, org_id: uuid.UUID, contract_id: uuid.UUID,
                       today: str | None = None) -> dict:
    """Deterministic per-contract attention record with score breakdown."""
    today = today or A.today_iso()
    findings = (
        A.latest_run_findings_q(db, org_id)
        .filter(RiskFinding.contract_id == contract_id)
        .all()
    )
    reviews = A.latest_reviews(db, org_id)
    critical = high = unreviewed = deviations = 0
    breakdown: dict[str, int] = {}
    for f in findings:
        rev = reviews.get(str(f.id))
        state = A.review_state(rev.decision if rev else None)
        if rev is not None and rev.decision in REVIEWED_EXCLUDE_PRIORITY:
            continue  # shown elsewhere; contributes no risk weight
        if f.priority == "critical":
            critical += 1
            breakdown["critical"] = breakdown.get("critical", 0) + WEIGHTS["critical"]
        elif f.priority == "high":
            high += 1
            breakdown["high"] = breakdown.get("high", 0) + WEIGHTS["high"]
        if state == "unreviewed":
            unreviewed += 1
            breakdown["unreviewed"] = breakdown.get("unreviewed", 0) + WEIGHTS["unreviewed"]
        if f.finding_type == "policy_deviation":
            deviations += 1
            breakdown["policy_deviation"] = (
                breakdown.get("policy_deviation", 0) + WEIGHTS["policy_deviation"])
    overdue = sum(
        1 for o in db.query(Obligation)
        .filter(Obligation.contract_id == contract_id,
                Obligation.deadline.is_not(None),
                Obligation.deadline < today,
                Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE", "OVERDUE"))).all()
    )
    if overdue:
        breakdown["overdue_obligation"] = overdue * WEIGHTS["overdue_obligation"]
    renewals = [
        o for o in db.query(Obligation)
        .filter(Obligation.contract_id == contract_id,
                Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE"))).all()
        if _within_days(o.renewal_date, 30, today)
    ]
    if renewals:
        breakdown["renewal_30d"] = len(renewals) * WEIGHTS["renewal_30d"]
    notices = [
        o for o in db.query(Obligation)
        .filter(Obligation.contract_id == contract_id,
                Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE"))).all()
        if _within_days(o.notice_deadline, 30, today)
    ]
    if notices:
        breakdown["notice_30d"] = len(notices) * WEIGHTS["notice_30d"]
    upcoming = db.query(LifecycleEvent).filter(
        LifecycleEvent.contract_id == contract_id,
        LifecycleEvent.status.in_(("UPCOMING", "DUE"))).count()
    renewal_date = min(
        (o.renewal_date for o in renewals), default=None)
    contract = db.get(Contract, contract_id)
    return {
        "contract_id": str(contract_id),
        "contract_name": contract.filename if contract else "?",
        "contract_type": contract.contract_type if contract else None,
        "counterparty": contract.counterparty if contract else None,
        "status": contract.processing_status if contract else "?",
        "attention_score": sum(breakdown.values()),
        "score_breakdown": breakdown,
        "scoring_version": PORTFOLIO_SCORING_VERSION,
        "high_risk_count": high,
        "critical_risk_count": critical,
        "unreviewed_count": unreviewed,
        "overdue_obligation_count": overdue,
        "upcoming_event_count": upcoming,
        "renewal_date": renewal_date,
        "policy_deviation_count": deviations,
    }


def attention_queue(db: Session, org_id: uuid.UUID, **filters) -> list[dict]:
    rows = [contract_attention(db, org_id, cid) for cid in
            A.org_contract_ids(db, org_id)]
    rows = [r for r in rows if _match_filters(db, org_id, r, filters)]
    rows.sort(key=lambda r: (-r["attention_score"], r["contract_name"]))
    return rows


def _match_filters(db: Session, org_id: uuid.UUID, row: dict, filters: dict) -> bool:
    if filters.get("min_score") is not None and row["attention_score"] < filters["min_score"]:
        return False
    if filters.get("counterparty") and row["counterparty"] != filters["counterparty"]:
        return False
    if filters.get("has_unreviewed") and row["unreviewed_count"] == 0:
        return False
    if filters.get("has_overdue") and row["overdue_obligation_count"] == 0:
        return False
    if filters.get("priority"):
        want = filters["priority"]
        if want == "critical" and row["critical_risk_count"] == 0:
            return False
        if want == "high" and row["high_risk_count"] == 0 and row["critical_risk_count"] == 0:
            return False
    if filters.get("category"):
        ok = (A.latest_run_findings_q(db, org_id)
              .filter(RiskFinding.contract_id == uuid.UUID(row["contract_id"]),
                      RiskFinding.risk_category == filters["category"]).count() > 0)
        if not ok:
            return False
    if filters.get("review_state"):
        reviews = A.latest_reviews(db, org_id)
        found = False
        for f in (A.latest_run_findings_q(db, org_id)
                  .filter(RiskFinding.contract_id == uuid.UUID(row["contract_id"])).all()):
            rev = reviews.get(str(f.id))
            if A.review_state(rev.decision if rev else None) == filters["review_state"]:
                found = True
                break
        if not found:
            return False
    return True


def high_risk_contract_ids(db: Session, org_id: uuid.UUID) -> set[uuid.UUID]:
    """Distinct contracts holding at least one qualifying high/critical finding.

    "Qualifying" means exactly what the attention queue and the
    `contracts_with_high_critical_unresolved` KPI already mean: latest-run
    findings whose priority is high or critical, excluding findings a reviewer
    rejected (a rejected finding adds no risk weight). Counts CONTRACTS, never
    findings, so one contract with four high findings contributes one id.

    Read-only: this never re-derives or overrides a risk decision.
    """
    reviews = A.latest_reviews(db, org_id)
    ids: set[uuid.UUID] = set()
    for f in A.latest_run_findings_q(db, org_id).all():
        rev = reviews.get(str(f.id))
        if rev is not None and rev.decision in REVIEWED_EXCLUDE_PRIORITY:
            continue
        if f.priority in ("high", "critical"):
            ids.add(f.contract_id)
    return ids


def renewing_contract_ids(db: Session, org_id: uuid.UUID,
                          days: int) -> set[uuid.UUID]:
    """Distinct contracts with a STORED renewal date inside [today, today+days].

    Deduplicated by contract id — a contract with two renewal obligations in
    the window counts once, unlike the raw obligation count reported as
    `upcoming_renewals_30d`. Missing dates are never invented: obligations
    without a renewal_date are simply not renewing contracts.
    """
    return {o.contract_id for o in A.renewals_in_window(db, org_id, days)}


def kpi_contract_ids(db: Session, org_id: uuid.UUID, *, high_risk: bool = False,
                     renewing_within_days: int | None = None) -> set[uuid.UUID] | None:
    """Intersection of the optional KPI-derived contract-id constraints.

    `high_risk` and `renewing_within_days` are opt-in flags (absent/false means
    "no such constraint"), so this returns None when nothing was requested and
    an empty set when a requested constraint matches nothing — callers must
    then return zero rows rather than silently ignoring the filter. Used by
    BOTH the PostgreSQL listing and the Elasticsearch search re-verification,
    so a KPI filter means the same thing whichever endpoint serves the page.
    """
    if not high_risk and renewing_within_days is None:
        return None
    ids: set[uuid.UUID] | None = None
    if high_risk:
        ids = high_risk_contract_ids(db, org_id)
    if renewing_within_days is not None:
        renewing = renewing_contract_ids(db, org_id, renewing_within_days)
        ids = renewing if ids is None else (ids & renewing)
    return ids


def portfolio_summary(db: Session, org_id: uuid.UUID) -> dict:
    cids = A.org_contract_ids(db, org_id)
    findings = A.latest_run_findings_q(db, org_id).all()
    reviews = A.latest_reviews(db, org_id)
    by_review: dict[str, int] = {}
    unresolved_high_critical = 0
    for f in findings:
        rev = reviews.get(str(f.id))
        state = A.review_state(rev.decision if rev else None)
        by_review[state] = by_review.get(state, 0) + 1
        if (rev is None or rev.decision not in REVIEWED_EXCLUDE_PRIORITY) and \
                f.priority in ("high", "critical"):
            unresolved_high_critical += 1
    renewals = A.renewals_in_window(db, org_id, 30)
    queue = attention_queue(db, org_id)
    return {
        "total_contracts": len(cids),
        "active_contracts": sum(1 for q in queue if q["status"] == "processed"),
        "contracts_with_high_critical_unresolved": sum(
            1 for q in queue if q["critical_risk_count"] + q["high_risk_count"] > 0),
        "open_obligations": db.query(Obligation).filter(
            Obligation.organization_id == org_id,
            Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE"))).count(),
        "overdue_obligations": len(A.overdue_obligations(db, org_id)),
        # Obligation-level count (pre-existing field, unchanged semantics).
        "upcoming_renewals_30d": len(renewals),
        # Part 7: DISTINCT contracts renewing in the window — the number the
        # "Contracts renewing" KPI card shows. A contract with several renewal
        # obligations in the window is still one renewing contract.
        "contracts_renewing_30d": len({o.contract_id for o in renewals}),
        # Part 5 (Step 12): distinct ACTIVE contracts with is_important=true.
        # A user-controlled organizational marker — never a risk score.
        "important_contracts": db.query(Contract).filter(
            Contract.organization_id == org_id,
            Contract.is_archived.is_(False),
            Contract.is_important.is_(True)).count(),
        "contracts_requiring_review": sum(1 for q in queue if q["unreviewed_count"] > 0),
        "findings_by_review_state": by_review,
        "unresolved_high_critical": unresolved_high_critical,
        "scoring_version": PORTFOLIO_SCORING_VERSION,
    }


def risk_portfolio(db: Session, org_id: uuid.UUID) -> dict:
    findings = A.latest_run_findings_q(db, org_id).all()
    reviews = A.latest_reviews(db, org_id)
    review_states: dict[str, int] = {}
    for f in findings:
        rev = reviews.get(str(f.id))
        st = A.review_state(rev.decision if rev else None)
        review_states[st] = review_states.get(st, 0) + 1
    by_contract: dict[str, int] = {}
    for f in findings:
        by_contract[str(f.contract_id)] = by_contract.get(str(f.contract_id), 0) + 1
    return {
        "by_priority": A.findings_by_priority(db, org_id),
        "by_category": A.findings_by_category(db, org_id),
        "by_contract": by_contract,
        "by_review_state": review_states,
        "total": len(findings),
    }


def obligation_portfolio(db: Session, org_id: uuid.UUID, **filters) -> dict:
    q = db.query(Obligation).filter(Obligation.organization_id == org_id)
    if filters.get("contract_id"):
        q = q.filter(Obligation.contract_id == uuid.UUID(filters["contract_id"]))
    if filters.get("type"):
        q = q.filter(Obligation.type == filters["type"])
    if filters.get("status"):
        q = q.filter(Obligation.status == filters["status"])
    if filters.get("party"):
        q = q.filter(Obligation.party == filters["party"])
    if filters.get("date_from"):
        q = q.filter(Obligation.deadline >= filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(Obligation.deadline <= filters["date_to"])
    rows = q.all()
    today = A.today_iso()
    overdue = [o for o in rows if o.deadline and o.deadline < today
               and o.status in ("UNCONFIRMED", "CONFIRMED", "ACTIVE", "OVERDUE")]
    upcoming = [o for o in rows if o.deadline and o.deadline >= today
                and o.status in ("UNCONFIRMED", "CONFIRMED", "ACTIVE")]
    return {
        "total": len(rows),
        "by_status": _bucket(rows, lambda o: o.status),
        "by_type": _bucket(rows, lambda o: o.type),
        "overdue_ids": [str(o.id) for o in overdue],
        "upcoming_ids": [str(o.id) for o in upcoming],
        "obligations": [_ob_row(o) for o in rows[:200]],
    }


def _bucket(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        k = key(r) or "unspecified"
        out[k] = out.get(k, 0) + 1
    return out


def _ob_row(o: Obligation) -> dict:
    return {"id": str(o.id), "contract_id": str(o.contract_id),
            "clause_id": str(o.clause_id), "type": o.type, "title": o.title,
            "status": o.status, "deadline": o.deadline,
            "renewal_date": o.renewal_date, "party": o.party}


def renewal_portfolio(db: Session, org_id: uuid.UUID, days: int) -> dict:
    rows = A.renewals_in_window(db, org_id, days)
    today = A.today_iso()
    notices = [
        o for o in db.query(Obligation)
        .filter(Obligation.organization_id == org_id,
                Obligation.notice_deadline.is_not(None),
                Obligation.notice_deadline >= today,
                Obligation.status.in_(("UNCONFIRMED", "CONFIRMED", "ACTIVE"))).all()
        if o.notice_deadline <= (datetime.date.fromisoformat(today)
                                 + datetime.timedelta(days=days)).isoformat()
    ]
    contracts = {str(c.id): c for c in db.query(Contract)
                 .filter(Contract.organization_id == org_id).all()}
    review_counts = _review_counts(db, org_id)
    out = []
    for o in sorted(rows, key=lambda r: r.renewal_date or ""):
        c = contracts.get(str(o.contract_id))
        out.append({
            "contract_id": str(o.contract_id),
            "contract_name": c.filename if c else "?",
            "renewal_date": o.renewal_date,
            "notice_deadline": o.notice_deadline,
            "auto_renew": o.auto_renew,
            "obligation_id": str(o.id),
            "risk_count": review_counts.get(str(o.contract_id), 0),
        })
    return {"window_days": days, "renewals": out,
            "notice_deadlines": [
                {"contract_id": str(o.contract_id), "notice_deadline": o.notice_deadline,
                 "obligation_id": str(o.id)} for o in sorted(
                     notices, key=lambda r: r.notice_deadline or "")]}


def _review_counts(db: Session, org_id: uuid.UUID) -> dict[str, int]:
    rows = (A.latest_run_findings_q(db, org_id).with_entities(RiskFinding.contract_id).all())
    out: dict[str, int] = {}
    for (cid,) in rows:
        out[str(cid)] = out.get(str(cid), 0) + 1
    return out


def policy_deviation_portfolio(db: Session, org_id: uuid.UUID) -> dict:
    rows = (A.latest_run_findings_q(db, org_id)
            .filter(RiskFinding.finding_type == "policy_deviation").all())
    by_contract: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for f in rows:
        by_contract[str(f.contract_id)] = by_contract.get(str(f.contract_id), 0) + 1
        by_category[f.risk_category] = by_category.get(f.risk_category, 0) + 1
    statuses = _deviation_statuses(db, org_id, {str(f.contract_id) for f in rows})
    return {"total": len(rows), "by_contract": by_contract,
            "by_category": by_category, "by_risk_category": by_category,
            "contract_status": statuses}


def _deviation_statuses(db: Session, org_id: uuid.UUID, cids: set[str]) -> dict:
    # within_policy/no_data per contract derived from presence of deviations
    # vs any findings (mirrors policy-overview semantics, computed here).
    out = {}
    for cid in cids:
        n_dev = (A.latest_run_findings_q(db, org_id)
                 .filter(RiskFinding.contract_id == uuid.UUID(cid),
                         RiskFinding.finding_type == "policy_deviation").count())
        out[cid] = "deviation" if n_dev else "within_policy"
    return out


def comparison_portfolio(db: Session, org_id: uuid.UUID) -> dict:
    mat = A.comparison_materiality(db, org_id)
    comp_ids = A.latest_comparison_ids(db, org_id)
    per_contract: dict[str, dict] = {}
    if comp_ids:
        from app.models import Comparison

        rows = (db.query(ComparisonFinding, Comparison)
                .join(Comparison, Comparison.id == ComparisonFinding.comparison_id)
                .filter(ComparisonFinding.organization_id == org_id,
                        ComparisonFinding.comparison_id.in_(list(comp_ids.values()))).all())
        for f, c in rows:
            key = str(f.contract_id)
            cell = per_contract.setdefault(
                key, {"contract_id": key,
                      "contract_name": c and db.get(Contract, f.contract_id).filename,
                      "material": 0, "potentially_material": 0, "informational": 0})
            if f.materiality in cell:
                cell[f.materiality] += 1
    ranked = sorted(per_contract.values(),
                    key=lambda r: (-r["material"], -r["potentially_material"],
                                   r["contract_name"] or ""))
    return {"materiality_totals": mat, "by_contract": ranked}


def feedback_analytics(db: Session, org_id: uuid.UUID) -> dict:
    rows = db.query(FeedbackLedger).filter(
        FeedbackLedger.organization_id == org_id).all()
    by_correction: dict[str, int] = {}
    by_decision: dict[str, int] = {}
    false_positives = wrong_category = wrong_priority = extraction_errors = expl = 0
    for r in rows:
        by_decision[r.decision] = by_decision.get(r.decision, 0) + 1
        if r.correction_type:
            by_correction[r.correction_type] = by_correction.get(r.correction_type, 0) + 1
        if r.correction_type == "FALSE_POSITIVE" or r.decision == "reject":
            false_positives += 1
        if r.correction_type == "WRONG_CATEGORY" or r.corrected_category:
            wrong_category += 1
        if r.correction_type == "WRONG_PRIORITY" or r.corrected_priority:
            wrong_priority += 1
        if r.correction_type == "EXTRACTION_ERROR":
            extraction_errors += 1
        if r.correction_type == "EXPLANATION_PROBLEM":
            expl += 1
    return {"total": len(rows), "by_decision": by_decision,
            "by_correction_type": by_correction,
            "reviewer_rejected": false_positives,
            "wrong_category": wrong_category, "wrong_priority": wrong_priority,
            "extraction_errors": extraction_errors, "explanation_problems": expl}


def notification_analytics(db: Session, org_id: uuid.UUID) -> dict:
    from app.models import Notification, NotificationDelivery

    upcoming = db.query(LifecycleEvent).filter(
        LifecycleEvent.organization_id == org_id,
        LifecycleEvent.status.in_(("UPCOMING", "DUE"))).count()
    overdue = db.query(LifecycleEvent).filter(
        LifecycleEvent.organization_id == org_id,
        LifecycleEvent.status == "OVERDUE").count()
    completed = db.query(ObligationEventInstance).filter(
        ObligationEventInstance.organization_id == org_id,
        ObligationEventInstance.status == "COMPLETED").count()
    by_status: dict[str, int] = {}
    for st, in db.query(Notification.status).filter(
            Notification.organization_id == org_id).all():
        by_status[st] = by_status.get(st, 0) + 1
    failed = db.query(NotificationDelivery).filter(
        NotificationDelivery.organization_id == org_id,
        NotificationDelivery.status == "FAILED").count()
    return {"upcoming_events": upcoming, "overdue_events": overdue,
            "completed_instances": completed, "notifications_by_status": by_status,
            "failed_deliveries": failed}
