"""Typed portfolio schemas. Scores always ship with their breakdown and
scoring version; review state is a sibling of risk data, never merged.
"""
from pydantic import BaseModel


class AttentionItem(BaseModel):
    contract_id: str
    contract_name: str
    contract_type: str | None = None
    counterparty: str | None = None
    status: str
    attention_score: int
    score_breakdown: dict = {}
    scoring_version: str
    high_risk_count: int = 0
    critical_risk_count: int = 0
    unreviewed_count: int = 0
    overdue_obligation_count: int = 0
    upcoming_event_count: int = 0
    renewal_date: str | None = None
    policy_deviation_count: int = 0


class SummaryOut(BaseModel):
    """Dashboard KPI source (Part 5 Step 12 documents the semantics):

        total_contracts                        active (non-archived) contracts
        contracts_with_high_critical_unresolved DISTINCT contracts with ≥1
                                               unresolved high/critical finding
        upcoming_renewals_30d                  renewals from REAL known dates
                                               in the next 30 days (obligation
                                               level — one contract can appear
                                               more than once)
        contracts_renewing_30d                 DISTINCT contracts renewing in
                                               the next 30 days (the number a
                                               "contracts renewing" KPI shows)
        important_contracts                    active contracts with
                                               is_important = true
    All are computed organization-scoped.
    """

    total_contracts: int = 0
    active_contracts: int = 0
    contracts_with_high_critical_unresolved: int = 0
    important_contracts: int = 0
    open_obligations: int = 0
    overdue_obligations: int = 0
    upcoming_renewals_30d: int = 0
    contracts_renewing_30d: int = 0
    contracts_requiring_review: int = 0
    findings_by_review_state: dict = {}
    unresolved_high_critical: int = 0
    scoring_version: str


class RiskPortfolioOut(BaseModel):
    by_priority: dict = {}
    by_category: dict = {}
    by_contract: dict = {}
    by_review_state: dict = {}
    total: int = 0


class ObligationPortfolioOut(BaseModel):
    total: int = 0
    by_status: dict = {}
    by_type: dict = {}
    overdue_ids: list[str] = []
    upcoming_ids: list[str] = []
    obligations: list[dict] = []


class RenewalItem(BaseModel):
    contract_id: str
    contract_name: str
    renewal_date: str | None = None
    notice_deadline: str | None = None
    auto_renew: bool | None = None
    obligation_id: str
    risk_count: int = 0


class RenewalPortfolioOut(BaseModel):
    window_days: int
    renewals: list[RenewalItem] = []
    notice_deadlines: list[dict] = []


class PolicyDeviationOut(BaseModel):
    total: int = 0
    by_contract: dict = {}
    by_category: dict = {}
    by_risk_category: dict = {}
    contract_status: dict = {}


class ComparisonPortfolioOut(BaseModel):
    materiality_totals: dict = {}
    by_contract: list[dict] = []


class CounterpartyOut(BaseModel):
    counterparty: str
    contract_count: int = 0
    active_contract_count: int = 0
    risk_count: int = 0
    high_risk_count: int = 0
    overdue_obligation_count: int = 0
    upcoming_renewal_count: int = 0
    contract_ids: list[str] = []


class CounterpartyPortfolioOut(BaseModel):
    counterparties: list[CounterpartyOut] = []
    counterparty_data_available: bool = False


class TrendPoint(BaseModel):
    bucket: str
    count: int = 0
    versions: list[str] = []


class TrendsOut(BaseModel):
    granularity: str
    risk_findings: list[TrendPoint] = []
    policy_deviations: list[TrendPoint] = []
    reviews: list[TrendPoint] = []
    obligation_reviews: list[TrendPoint] = []


class FeedbackAnalyticsOut(BaseModel):
    total: int = 0
    by_decision: dict = {}
    by_correction_type: dict = {}
    reviewer_rejected: int = 0
    wrong_category: int = 0
    wrong_priority: int = 0
    extraction_errors: int = 0
    explanation_problems: int = 0


class NotificationAnalyticsOut(BaseModel):
    upcoming_events: int = 0
    overdue_events: int = 0
    completed_instances: int = 0
    notifications_by_status: dict = {}
    failed_deliveries: int = 0


class ContractDrilldownOut(BaseModel):
    attention: AttentionItem
    obligations: ObligationPortfolioOut
    renewals: RenewalPortfolioOut
    policy_deviations: PolicyDeviationOut


class HealthComponent(BaseModel):
    """One term of the portfolio-health formula (drill-down + hand
    reproduction: 100 − Σ points = raw score)."""
    key: str
    label: str
    weight: float
    rate: float
    points: float
    numerator: int
    denominator: int
    basis: str


class PortfolioHealthBlock(BaseModel):
    score: int | None = None
    raw_score: int | None = None
    label: str
    orientation: str = "higher_is_healthier"
    analysis_coverage: int | None = None
    explanation: str = ""


class CoverageDetail(BaseModel):
    analysis_coverage: int | None = None
    analyzed_contracts: int = 0
    total_contracts: int = 0
    unanalyzed_contracts: int = 0
    stale_contracts: int = 0
    failed_contracts: int = 0
    by_status: dict = {}


class PortfolioHealthOut(BaseModel):
    """Whole-portfolio health (Part 1). Separate from the per-contract
    attention score: `score` is 0-100 and higher is HEALTHIER."""
    scoring_version: str
    computed_on: str
    formula: str
    portfolio_health: PortfolioHealthBlock
    drivers: dict = {}
    coverage: CoverageDetail
    components: list[HealthComponent] = []
    disclaimer: str = ""
