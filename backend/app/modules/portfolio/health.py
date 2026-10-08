"""Portfolio-level HEALTH score (Master Plan, Part 1) — one number for the
whole portfolio, computed by the backend from authoritative stored analysis.

Two different scores exist and must never be confused:

  * attention score  — per contract, operational prioritization weight
                       (`service.contract_attention`, PORTFOLIO_SCORING_VERSION)
  * health score     — portfolio-wide, 100 = healthiest
                       (this module, PORTFOLIO_HEALTH_SCORING_VERSION)

Neither is a legal-risk probability, an ML confidence or legal advice.

WHY RATES, NOT SUMS
-------------------
Summing attention scores would make a bigger portfolio look worse purely
because it is bigger. Every input below is therefore a *rate* in [0, 1]
(count / defined denominator), so the score of a portfolio does not drift
just because contracts were added.

FORMULA (PORTFOLIO_HEALTH_SCORING_VERSION = "1.0")
--------------------------------------------------
    rate_k    = clamp01(numerator_k / denominator_k)      # see RATE_BASIS
    burden    = Σ weight_k × rate_k                       # weights sum to 1.00
    raw       = 100 × (1 − burden)                        # 0..100
    coverage  = analyzed_contracts / active_contracts     # 0..1
    score     = round_half_up(raw × coverage)             # 0..100, higher is
                                                          # HEALTHIER

  raw       = health of the part of the portfolio we have actually analyzed
  coverage  = share of active contracts whose analysis is `complete`
  score     = raw × coverage, so contracts that were never (fully) analyzed
              can only ever lower the headline number — missing analysis is
              never silently read as "healthy". Coverage is also returned as
              its own field so the gap stays visible.

ROUNDING: intermediate values are rounded to 10 / 6 decimals, the final
value is rounded half-up (floor(x + 0.5)), so a human reproducing the
arithmetic from the API payload reaches the identical integer.

EDGE CASES (defined, never implicit)
------------------------------------
  no contracts            -> score/coverage null, label "No contracts"
  0 analyzed contracts    -> score 0, label "No analysis yet" (coverage 0)
  processing/queued/pending/partial/classified/stale/failed contracts
                            -> not analyzed: they lower coverage only; they are
                               itemised in `coverage.by_status`
  no findings             -> all finding rates 0 (a finding-free *analyzed*
                             portfolio is genuinely healthy)
  no obligations          -> overdue rate 0
  no renewal/notice date  -> renewal rate 0
  no active policy        -> deviation rate 0, but `active_policies` is
                             reported so "0 deviations" is never mistaken for
                             "compliant"
  reviewer-rejected finding -> contributes no risk weight (same rule as the
                             attention score) yet stays in the unreviewed-rate
                             denominator, so reviewing findings can only help
  duplicate/older runs    -> only each contract's LATEST risk-analysis run is
                             read (aggregations.latest_run_findings_q), so a
                             re-analysis can never double count
"""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy.orm import Session

from app.models import Contract, Obligation, Policy
from app.modules.portfolio import aggregations as A

PORTFOLIO_HEALTH_SCORING_VERSION = "1.0"

# Non-terminal obligation states (mirrors service/aggregations).
OPEN_OBLIGATION_STATUSES = ("UNCONFIRMED", "CONFIRMED", "ACTIVE", "OVERDUE")
ACTIVE_OBLIGATION_STATUSES = ("UNCONFIRMED", "CONFIRMED", "ACTIVE")
# Reviewer decisions that remove a finding's risk weight (mirrors
# service.REVIEWED_EXCLUDE_PRIORITY).
REJECTED_DECISIONS = ("reject",)

RENEWAL_WINDOW_DAYS = 30

# Weights sum to exactly 1.0 — asserted by tests. Changing any of these,
# a denominator, a band or the rounding rule requires a version bump.
WEIGHTS: dict[str, float] = {
    "critical": 0.30,   # unresolved critical findings / analyzed contracts
    "high": 0.15,       # unresolved high findings / analyzed contracts
    "unreviewed": 0.15,  # unreviewed findings / all latest-run findings
    "overdue": 0.15,    # overdue obligations / open obligations
    "renewal": 0.10,    # (renewals + notice deadlines) ≤30d / active contracts
    "deviation": 0.15,  # contracts with a deviation / analyzed contracts
}

LABELS: dict[str, str] = {
    "critical": "Unresolved critical findings",
    "high": "Unresolved high findings",
    "unreviewed": "Unreviewed findings",
    "overdue": "Overdue obligations",
    "renewal": "Renewals & notice deadlines ≤30 days",
    "deviation": "Contracts with policy deviations",
}

RATE_BASIS: dict[str, str] = {
    "critical": "unresolved critical findings ÷ analyzed contracts (capped at 1)",
    "high": "unresolved high findings ÷ analyzed contracts (capped at 1)",
    "unreviewed": "unreviewed findings ÷ all latest-run findings",
    "overdue": "overdue obligations ÷ open obligations",
    "renewal": "(renewals + notice deadlines) within 30 days ÷ active contracts",
    "deviation": "contracts with ≥1 unresolved policy deviation ÷ analyzed contracts",
}

DISCLAIMER = (
    "Operational prioritization derived from stored analysis — not a "
    "legal-risk probability, not an ML confidence and not legal advice."
)


def _rate(count: int, base: int) -> float:
    """count/base clamped to [0, 1]. An empty denominator with a non-empty
    count is maximally bad (1.0); both empty is 0.0."""
    if base <= 0:
        return 1.0 if count > 0 else 0.0
    return min(1.0, max(0.0, count / base))


def _label(score: int | None, n: int, analyzed: int) -> str:
    if n == 0:
        return "No contracts"
    if analyzed == 0:
        return "No analysis yet"
    if score is None:
        return "No contracts"
    if score >= 90:
        return "Healthy"
    if score >= 75:
        return "Low attention"
    if score >= 60:
        return "Moderate attention"
    if score >= 40:
        return "High attention"
    if score >= 1:
        return "Severe attention"
    return "Critical attention"


def _round_half_up(x: float) -> int:
    return int(round(x, 6) + 0.5)


def portfolio_health(db: Session, org_id: uuid.UUID, *,
                     today: str | None = None) -> dict:
    """Deterministic whole-portfolio health record with full drill-down.

    Pure aggregation over authoritative tables: no recomputation of risk,
    no duplicated domain logic, no frontend-visible formula.
    """
    today = today or A.today_iso()

    contracts = (
        db.query(Contract)
        .filter(Contract.organization_id == org_id,
                Contract.is_archived.is_(False))
        .all()
    )
    n = len(contracts)
    by_status: dict[str, int] = {}
    for c in contracts:
        by_status[c.analysis_status] = by_status.get(c.analysis_status, 0) + 1
    # "analyzed" = the app's own honest rollup (classification + risk run
    # both complete, not stale/failed/partial): risk_engine.state.
    analyzed = sum(1 for c in contracts if c.analysis_status == "complete")

    # --- findings: latest risk run only, reviewer-rejected carry no weight ---
    findings = A.latest_run_findings_q(db, org_id).all()
    reviews = A.latest_reviews(db, org_id)
    critical = high = medium = low = unreviewed = 0
    deviation_findings = 0
    deviation_contracts: set[str] = set()
    review_contracts: set[str] = set()
    for f in findings:
        rev = reviews.get(str(f.id))
        # same state rule as service.contract_attention (never merged)
        if A.review_state(rev.decision if rev else None) == "unreviewed":
            unreviewed += 1
            review_contracts.add(str(f.contract_id))
        if rev is not None and rev.decision in REJECTED_DECISIONS:
            continue  # shown to reviewers; contributes no risk weight
        if f.priority == "critical":
            critical += 1
        elif f.priority == "high":
            high += 1
        elif f.priority == "medium":
            medium += 1
        else:
            low += 1
        if f.finding_type == "policy_deviation":
            deviation_findings += 1
            deviation_contracts.add(str(f.contract_id))

    # --- obligations: dates are ISO strings, windows are inclusive ---------
    obs = (
        db.query(Obligation)
        .filter(Obligation.organization_id == org_id,
                Obligation.status.in_(OPEN_OBLIGATION_STATUSES))
        .all()
    )
    end = (datetime.date.fromisoformat(today)
           + datetime.timedelta(days=RENEWAL_WINDOW_DAYS)).isoformat()
    overdue = sum(1 for o in obs if o.deadline and o.deadline < today)
    active_obs = [o for o in obs if o.status in ACTIVE_OBLIGATION_STATUSES]
    renewals = sum(1 for o in active_obs
                   if o.renewal_date and today <= o.renewal_date <= end)
    notices = sum(1 for o in active_obs
                  if o.notice_deadline and today <= o.notice_deadline <= end)
    active_policies = (
        db.query(Policy)
        .filter(Policy.organization_id == org_id, Policy.is_active.is_(True))
        .count()
    )

    # --- rates ------------------------------------------------------------
    rates = {
        "critical": _rate(critical, analyzed),
        "high": _rate(high, analyzed),
        "unreviewed": _rate(unreviewed, len(findings)),
        "overdue": _rate(overdue, len(obs)),
        "renewal": _rate(renewals + notices, n),
        "deviation": _rate(len(deviation_contracts), analyzed),
    }
    burden = round(sum(WEIGHTS[k] * rates[k] for k in WEIGHTS), 10)
    raw = min(100.0, max(0.0, round(100 * (1 - burden), 6)))
    coverage = (analyzed / n) if n else 0.0
    score = _round_half_up(raw * coverage) if n else None

    coverage_pct = _round_half_up(coverage * 100) if n else None
    raw_score = _round_half_up(raw) if n else None
    label = _label(score, n, analyzed)

    components = [
        {
            "key": k,
            "label": LABELS[k],
            "weight": WEIGHTS[k],
            "rate": round(rates[k], 6),
            "points": round(100 * WEIGHTS[k] * rates[k], 2),
            "numerator": {
                "critical": critical,
                "high": high,
                "unreviewed": unreviewed,
                "overdue": overdue,
                "renewal": renewals + notices,
                "deviation": len(deviation_contracts),
            }[k],
            "denominator": {
                "critical": analyzed,
                "high": analyzed,
                "unreviewed": len(findings),
                "overdue": len(obs),
                "renewal": n,
                "deviation": analyzed,
            }[k],
            "basis": RATE_BASIS[k],
        }
        for k in WEIGHTS
    ]

    if n == 0:
        explanation = (
            "No active contracts in this organization — there is nothing to "
            "score yet. Upload and analyze a contract to get a Portfolio "
            "Health score."
        )
    elif analyzed == 0:
        explanation = (
            f"{n} active contract(s) but none finished analysis, so no health "
            f"can be claimed: coverage is 0% and the score is 0 by definition. "
            f"Run analysis first. ({DISCLAIMER})"
        )
    else:
        explanation = (
            f"{score}/100 (higher is healthier), computed {today} by backend "
            f"formula v{PORTFOLIO_HEALTH_SCORING_VERSION}: 100 minus each "
            f"component's weight × rate = {raw_score}/100 for the analyzed "
            f"contracts, then × coverage ({analyzed} of {n} analyzed = "
            f"{coverage_pct}%). {DISCLAIMER}"
        )

    return {
        "scoring_version": PORTFOLIO_HEALTH_SCORING_VERSION,
        "computed_on": today,
        "formula": ("score = round_half_up((100 − Σ(weight × rate)) × coverage); "
                    "weights sum to 1.0; rates are capped at 1"),
        "portfolio_health": {
            "score": score,
            "raw_score": raw_score,
            "label": label,
            "orientation": "higher_is_healthier",
            "analysis_coverage": coverage_pct,
            "explanation": explanation,
        },
        "drivers": {
            "critical_findings": critical,
            "high_findings": high,
            "medium_findings": medium,
            "low_findings": low,
            "total_findings": len(findings),
            "unreviewed_findings": unreviewed,
            "unresolved_high_critical": critical + high,
            "policy_deviations": deviation_findings,
            "contracts_with_policy_deviations": len(deviation_contracts),
            "overdue_obligations": overdue,
            "renewals_next_30_days": renewals,
            "notice_deadlines_next_30_days": notices,
            "contracts_needing_review": len(review_contracts),
            "contracts_needing_analysis": n - analyzed,
            "active_contracts": sum(
                1 for c in contracts if c.processing_status == "processed"),
            "total_contracts": n,
            "analyzed_contracts": analyzed,
            "open_obligations": len(obs),
            "active_policies": active_policies,
        },
        "coverage": {
            "analysis_coverage": coverage_pct,
            "analyzed_contracts": analyzed,
            "total_contracts": n,
            "unanalyzed_contracts": n - analyzed,
            "stale_contracts": by_status.get("stale", 0),
            "failed_contracts": by_status.get("failed", 0),
            "by_status": by_status,
        },
        "components": components,
        "disclaimer": DISCLAIMER,
    }
