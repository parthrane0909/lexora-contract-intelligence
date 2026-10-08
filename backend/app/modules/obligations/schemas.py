"""Typed obligation/lifecycle schemas."""
from pydantic import BaseModel


class ObligationOut(BaseModel):
    id: str
    contract_id: str
    clause_id: str
    type: str
    title: str
    description: str | None = None
    party: str | None = None
    trigger: str | None = None
    frequency: str | None = None
    period_days: int | None = None
    period_approximate: bool = False
    notice_period_days: int | None = None
    cure_period_days: int | None = None
    deadline: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    renewal_period_days: int | None = None
    auto_renew: bool | None = None
    renewal_date: str | None = None
    notice_deadline: str | None = None
    amount: float | None = None
    currency: str | None = None
    status: str
    confidence: float | None = None
    extraction_method: str
    model_version: str | None = None
    rule_id: str | None = None
    source_text: str | None = None
    risk_finding_id: str | None = None
    policy_expectation: dict | None = None
    created_at: str


class EventOut(BaseModel):
    id: str
    contract_id: str
    obligation_id: str | None = None
    event_type: str
    event_date: str | None = None
    trigger: str | None = None
    status: str
    source_clause_id: str | None = None
    description: str | None = None


class LifecycleOut(BaseModel):
    contract_id: str
    status: str
    reason: str = ""
    effective_date: str | None = None
    expiration_date: str | None = None
    renewal: dict = {}
    termination_windows: list[dict] = []
    open_obligations: int = 0
    upcoming_events: int = 0
    overdue_events: int = 0
    obligations: list[ObligationOut] = []
    events: list[EventOut] = []


class ExtractOut(BaseModel):
    status: str
    obligations: int
    preserved: int = 0
    engine: str


class ReviewIn(BaseModel):
    decision: str  # confirm | reject | modify
    comment: str | None = None
    correction_type: str | None = None
    corrected_type: str | None = None
    corrected_date: str | None = None
    corrected_notice_days: int | None = None
    corrected_party: str | None = None
    corrected_trigger: str | None = None
    corrected_status: str | None = None


class ReviewOut(BaseModel):
    id: str
    obligation_id: str
    reviewer_id: str
    reviewer_email: str | None = None
    decision: str
    comment: str | None = None
    correction_type: str | None = None
    corrected_type: str | None = None
    corrected_date: str | None = None
    corrected_notice_days: int | None = None
    corrected_party: str | None = None
    corrected_trigger: str | None = None
    corrected_status: str | None = None
    snapshot: dict = {}
    created_at: str


class FeedbackOut(BaseModel):
    id: str
    obligation_id: str
    review_id: str
    reviewer_role: str | None = None
    original_type: str | None = None
    original_deadline: str | None = None
    original_notice_days: int | None = None
    original_party: str | None = None
    original_trigger: str | None = None
    original_confidence: float | None = None
    extraction_method: str | None = None
    decision: str
    correction_type: str | None = None
    corrected_type: str | None = None
    corrected_date: str | None = None
    corrected_notice_days: int | None = None
    reviewer_comment: str | None = None
    created_at: str


class OverrideIn(BaseModel):
    status: str | None = None  # null clears the override


class DashboardOut(BaseModel):
    contracts_expiring_soon: int = 0
    renewals_approaching: int = 0
    upcoming_obligations: int = 0
    overdue_obligations: int = 0
    unconfirmed_obligations: int = 0
    upcoming_notice_deadlines: int = 0
