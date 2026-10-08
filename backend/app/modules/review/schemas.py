"""Typed review schemas (Phase 8.28). The response keeps the authoritative
risk finding and the human review as siblings — corrections never merge
into the finding object.
"""
from pydantic import BaseModel


class ReviewIn(BaseModel):
    decision: str
    comment: str | None = None
    correction_type: str | None = None
    corrected_category: str | None = None
    corrected_priority: str | None = None


class ReviewOut(BaseModel):
    id: str
    finding_id: str
    reviewer_id: str
    reviewer_email: str | None = None
    reviewer_role: str | None = None
    decision: str
    comment: str | None = None
    correction_type: str | None = None
    corrected_category: str | None = None
    corrected_priority: str | None = None
    explanation_id: str | None = None
    explanation_version: str | None = None
    finding_snapshot: dict = {}
    created_at: str


class ReviewStateOut(BaseModel):
    status: str  # unreviewed | reviewed | needs_more_review
    decision: str | None = None
    reviewer_email: str | None = None
    created_at: str | None = None


class FindingWithReviewOut(BaseModel):
    risk_finding: dict
    review: ReviewStateOut


class FeedbackOut(BaseModel):
    id: str
    finding_id: str
    review_id: str
    reviewer_role: str | None = None
    original_category: str | None = None
    original_priority: str | None = None
    original_rule: str | None = None
    original_classifier_prediction: str | None = None
    original_classifier_confidence: float | None = None
    original_policy_version: int | None = None
    explanation_id: str | None = None
    explanation_version: str | None = None
    decision: str
    correction_type: str | None = None
    corrected_category: str | None = None
    corrected_priority: str | None = None
    reviewer_comment: str | None = None
    created_at: str


class ReviewSummaryOut(BaseModel):
    total_findings: int
    unreviewed: int
    reviewed: int
    needs_more_review: int
    by_decision: dict = {}
    high_critical_unreviewed: int
    reviewer_rejected: int
    by_correction_type: dict = {}
