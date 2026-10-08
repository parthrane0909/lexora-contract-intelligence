"""Review workflow service (Phase 8.15). One transaction creates the review
decision + feedback ledger row + audit event. The original finding, policy,
template, classifier output, and explanation are never touched.

Reviewer comments never go into logs (ids/decisions only).
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import FeedbackLedger, ReviewDecision, RiskFinding
from app.modules.audit.service import record
from app.modules.llm_explanation.service import latest_explanation
from app.modules.review.decisions import (
    COMMENT_MAX_CHARS,
    COMMENT_REQUIRED,
    CORRECTABLE_PRIORITIES,
    CORRECTION_TYPES,
    DECISIONS,
)

log = logging.getLogger("reviews")


def validate_input(decision: str, comment: str | None, correction_type: str | None,
                   corrected_category: str | None, corrected_priority: str | None) -> str:
    if decision not in DECISIONS:
        raise HTTPException(400, f"Invalid decision {decision!r}. Allowed: {list(DECISIONS)}")
    comment = (comment or "").strip()
    if decision in COMMENT_REQUIRED and not comment:
        raise HTTPException(400, f"A comment is required for {decision}.")
    if len(comment) > COMMENT_MAX_CHARS:
        raise HTTPException(400, f"Comment exceeds {COMMENT_MAX_CHARS} characters.")
    if decision == "modify":
        if correction_type not in CORRECTION_TYPES:
            raise HTTPException(
                400, f"MODIFY requires correction_type, one of {list(CORRECTION_TYPES)}.")
    elif correction_type is not None and correction_type not in CORRECTION_TYPES:
        raise HTTPException(400, f"Invalid correction_type {correction_type!r}.")
    if corrected_category is not None:
        from app.modules.ml_nlp.taxonomy import CATEGORIES

        if corrected_category not in CATEGORIES:
            raise HTTPException(400, f"Invalid corrected_category {corrected_category!r}.")
    if corrected_priority is not None and corrected_priority not in CORRECTABLE_PRIORITIES:
        raise HTTPException(400, f"Invalid corrected_priority {corrected_priority!r}.")
    return comment


def _snapshot(finding: RiskFinding, explanation) -> dict:
    return {
        "risk_category": finding.risk_category, "priority": finding.priority,
        "title": finding.title, "reason": finding.reason,
        "finding_type": finding.finding_type, "rule_id": finding.rule_id,
        "rule_version": finding.rule_version, "policy_id": str(finding.policy_id)
        if finding.policy_id else None, "policy_version": finding.policy_version,
        "classification_model": finding.classification_model,
        "classification_version": finding.classification_version,
        "classification_confidence": finding.classification_confidence,
        "analysis_run_id": str(finding.analysis_run_id) if finding.analysis_run_id else None,
        "clause_id": str(finding.clause_id), "contract_id": str(finding.contract_id),
        "explanation_id": str(explanation.id) if explanation else None,
        "explanation_model": explanation.model if explanation else None,
        "explanation_prompt": explanation.prompt_version if explanation else None,
    }


def create_review(db: Session, *, organization_id: uuid.UUID, finding: RiskFinding,
                  reviewer_id: uuid.UUID, reviewer_role: str | None, decision: str,
                  comment: str | None, correction_type: str | None = None,
                  corrected_category: str | None = None,
                  corrected_priority: str | None = None):
    """Persist review + ledger atomically. Returns (review, ledger)."""
    comment = validate_input(decision, comment, correction_type,
                             corrected_category, corrected_priority)
    explanation = latest_explanation(db, finding.id)
    snap = _snapshot(finding, explanation)
    review = ReviewDecision(
        organization_id=organization_id, finding_id=finding.id, reviewer_id=reviewer_id,
        decision=decision, comment=comment or None, correction_type=correction_type,
        corrected_category=corrected_category, corrected_priority=corrected_priority,
        finding_snapshot=json.dumps(snap),
        explanation_id=explanation.id if explanation else None,
        explanation_version=(f"{explanation.model_version}@prompt-{explanation.prompt_version}"
                             if explanation else None))
    db.add(review)
    db.flush()
    ledger = FeedbackLedger(
        organization_id=organization_id, finding_id=finding.id, review_id=review.id,
        reviewer_id=reviewer_id, reviewer_role=reviewer_role,
        original_category=finding.risk_category, original_priority=finding.priority,
        original_rule=finding.rule_id,
        original_classifier_prediction=_classifier_prediction(db, finding),
        original_classifier_confidence=finding.classification_confidence,
        original_policy_version=finding.policy_version,
        explanation_id=explanation.id if explanation else None,
        explanation_version=review.explanation_version,
        decision=decision, correction_type=correction_type,
        corrected_category=corrected_category, corrected_priority=corrected_priority,
        reviewer_comment=comment or None)
    db.add(ledger)
    db.commit()
    db.refresh(review)
    db.refresh(ledger)
    record(db, action="finding.reviewed", organization_id=organization_id,
           user_id=reviewer_id, object_type="review_decision", object_id=str(review.id),
           detail=f"finding={finding.id} decision={decision}")
    log.info("review recorded finding=%s decision=%s reviewer=%s",
             finding.id, decision, reviewer_id)
    return review, ledger


def _classifier_prediction(db: Session, finding: RiskFinding) -> str | None:
    from app.models import ClauseClassification

    row = (db.query(ClauseClassification)
           .filter(ClauseClassification.clause_id == finding.clause_id)
           .order_by(ClauseClassification.created_at.desc()).first())
    return row.predicted_category if row else None


def review_history(db: Session, finding_id: uuid.UUID):
    return (db.query(ReviewDecision)
            .filter(ReviewDecision.finding_id == finding_id)
            .order_by(ReviewDecision.created_at.desc()).all())


def latest_review(db: Session, finding_id: uuid.UUID):
    rows = review_history(db, finding_id)
    return rows[0] if rows else None


def feedback_for_finding(db: Session, finding_id: uuid.UUID):
    return (db.query(FeedbackLedger)
            .filter(FeedbackLedger.finding_id == finding_id)
            .order_by(FeedbackLedger.created_at.desc()).all())
