"""Review endpoints (Phase 8.14). Nested finding routes mirror the
explanations router; org-wide /reviews + /feedback/export live here too.

Submit: admin/legal_reviewer/contract_manager (same trigger convention as
classify/risk/compare). Read: all members. Cross-org → 404 (finding lookup
is org-scoped first, so B can neither read nor write A's reviews).
"""
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import (Clause, Contract, FeedbackLedger, ReviewDecision, RiskFinding,
                        User)
from app.modules.review import schemas
from app.modules.review.decisions import DECISIONS, derive_status
from app.modules.review.service import (
    create_review,
    feedback_for_finding,
    latest_review,
    review_history,
)

router = APIRouter(tags=["reviews"])
_REVIEWERS = ("admin", "legal_reviewer", "contract_manager")


def _finding(db: Session, contract_id: str, finding_id: str, org_id) -> RiskFinding:
    try:
        cid, fid = uuid.UUID(contract_id), uuid.UUID(finding_id)
    except ValueError:
        raise HTTPException(404, "Finding not found")
    contract = db.query(Contract).filter(
        Contract.id == cid, Contract.organization_id == org_id).first()
    if contract is None:
        raise HTTPException(404, "Contract not found")
    finding = db.query(RiskFinding).filter(
        RiskFinding.id == fid, RiskFinding.contract_id == cid).first()
    if finding is None:
        raise HTTPException(404, "Finding not found")
    return finding


def _reviewer_email(db: Session, user_id) -> str | None:
    u = db.get(User, user_id)
    return u.email if u else None


def _out(db: Session, r: ReviewDecision) -> dict:
    return {
        "id": str(r.id), "finding_id": str(r.finding_id),
        "reviewer_id": str(r.reviewer_id),
        "reviewer_email": _reviewer_email(db, r.reviewer_id),
        "reviewer_role": _member_role(db, r.reviewer_id, r.organization_id),
        "decision": r.decision, "comment": r.comment,
        "correction_type": r.correction_type,
        "corrected_category": r.corrected_category,
        "corrected_priority": r.corrected_priority,
        "explanation_id": str(r.explanation_id) if r.explanation_id else None,
        "explanation_version": r.explanation_version,
        "finding_snapshot": json.loads(r.finding_snapshot or "{}"),
        "created_at": r.created_at.isoformat(),
    }


def _member_role(db: Session, user_id, org_id) -> str | None:
    from app.models import OrganizationMember

    m = db.query(OrganizationMember).filter(
        OrganizationMember.user_id == user_id,
        OrganizationMember.organization_id == org_id).first()
    return m.role if m else None


def _feedback_out(f: FeedbackLedger) -> dict:
    return {
        "id": str(f.id), "finding_id": str(f.finding_id), "review_id": str(f.review_id),
        "reviewer_role": f.reviewer_role, "original_category": f.original_category,
        "original_priority": f.original_priority, "original_rule": f.original_rule,
        "original_classifier_prediction": f.original_classifier_prediction,
        "original_classifier_confidence": f.original_classifier_confidence,
        "original_policy_version": f.original_policy_version,
        "explanation_id": str(f.explanation_id) if f.explanation_id else None,
        "explanation_version": f.explanation_version, "decision": f.decision,
        "correction_type": f.correction_type, "corrected_category": f.corrected_category,
        "corrected_priority": f.corrected_priority, "reviewer_comment": f.reviewer_comment,
        "created_at": f.created_at.isoformat(),
    }


def _state(db: Session, finding: RiskFinding) -> dict:
    latest = latest_review(db, finding.id)
    if latest is None:
        return {"status": "unreviewed", "decision": None,
                "reviewer_email": None, "created_at": None}
    return {"status": derive_status(latest.decision), "decision": latest.decision,
            "reviewer_email": _reviewer_email(db, latest.reviewer_id),
            "created_at": latest.created_at.isoformat()}


@router.post("/contracts/{contract_id}/findings/{finding_id}/reviews",
             response_model=schemas.ReviewOut)
def submit(contract_id: str, finding_id: str, body: schemas.ReviewIn,
           member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _REVIEWERS:
        raise HTTPException(403, "Viewers cannot submit reviews")
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    review, _ = create_review(
        db, organization_id=member.organization_id, finding=finding,
        reviewer_id=member.user_id, reviewer_role=member.role,
        decision=body.decision, comment=body.comment,
        correction_type=body.correction_type,
        corrected_category=body.corrected_category,
        corrected_priority=body.corrected_priority)
    return _out(db, review)


@router.get("/contracts/{contract_id}/findings/{finding_id}/reviews",
            response_model=list[schemas.ReviewOut])
def history(contract_id: str, finding_id: str,
            member=Depends(get_current_membership), db: Session = Depends(get_db)):
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    return [_out(db, r) for r in review_history(db, finding.id)]


@router.get("/contracts/{contract_id}/findings/{finding_id}/review",
            response_model=schemas.FindingWithReviewOut)
def current(contract_id: str, finding_id: str,
            member=Depends(get_current_membership), db: Session = Depends(get_db)):
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    return {
        "risk_finding": {"id": str(finding.id), "priority": finding.priority,
                         "category": finding.risk_category, "rule_id": finding.rule_id,
                         "title": finding.title},
        "review": _state(db, finding),
    }


@router.get("/contracts/{contract_id}/findings/{finding_id}/feedback",
            response_model=list[schemas.FeedbackOut])
def feedback(contract_id: str, finding_id: str,
             member=Depends(get_current_membership), db: Session = Depends(get_db)):
    finding = _finding(db, contract_id, finding_id, member.organization_id)
    return [_feedback_out(f) for f in feedback_for_finding(db, finding.id)]


@router.get("/reviews", response_model=list[schemas.ReviewOut])
def list_reviews(member=Depends(get_current_membership), db: Session = Depends(get_db),
                 decision: str | None = Query(default=None),
                 category: str | None = Query(default=None),
                 priority: str | None = Query(default=None),
                 reviewer: str | None = Query(default=None),
                 contract_id: str | None = Query(default=None)):
    if decision is not None and decision not in DECISIONS:
        raise HTTPException(400, f"Invalid decision {decision!r}.")
    q = (db.query(ReviewDecision, RiskFinding)
         .join(RiskFinding, RiskFinding.id == ReviewDecision.finding_id)
         .filter(ReviewDecision.organization_id == member.organization_id))
    if decision:
        q = q.filter(ReviewDecision.decision == decision)
    if category:
        q = q.filter(RiskFinding.risk_category == category)
    if priority:
        q = q.filter(RiskFinding.priority == priority)
    if reviewer:
        try:
            q = q.filter(ReviewDecision.reviewer_id == uuid.UUID(reviewer))
        except ValueError:
            raise HTTPException(400, "Invalid reviewer id.")
    if contract_id:
        try:
            q = q.filter(RiskFinding.contract_id == uuid.UUID(contract_id))
        except ValueError:
            raise HTTPException(400, "Invalid contract id.")
    return [_out(db, r) for r, _ in q.order_by(ReviewDecision.created_at.desc()).limit(200).all()]


@router.get("/reviews/summary", response_model=schemas.ReviewSummaryOut)
def summary(contract_id: str | None = Query(default=None),
            member=Depends(get_current_membership), db: Session = Depends(get_db)):
    fq = db.query(RiskFinding).filter(RiskFinding.organization_id == member.organization_id)
    if contract_id:
        try:
            fq = fq.filter(RiskFinding.contract_id == uuid.UUID(contract_id))
        except ValueError:
            raise HTTPException(400, "Invalid contract id.")
    findings = fq.all()
    fids = [f.id for f in findings]
    latest_by_finding: dict[str, ReviewDecision] = {}
    if fids:
        for r in (db.query(ReviewDecision)
                  .filter(ReviewDecision.finding_id.in_(fids))
                  .order_by(ReviewDecision.created_at.desc()).all()):
            latest_by_finding.setdefault(str(r.finding_id), r)
    by_decision: dict[str, int] = {}
    by_correction: dict[str, int] = {}
    reviewed = needs = rejected = hc_unreviewed = 0
    for f in findings:
        latest = latest_by_finding.get(str(f.id))
        if latest is None:
            if f.priority in ("high", "critical"):
                hc_unreviewed += 1
            continue
        st = derive_status(latest.decision)
        if st == "needs_more_review":
            needs += 1
        else:
            reviewed += 1
        by_decision[latest.decision] = by_decision.get(latest.decision, 0) + 1
        if latest.decision == "reject":
            rejected += 1
        if latest.correction_type:
            by_correction[latest.correction_type] = by_correction.get(latest.correction_type, 0) + 1
    return {
        "total_findings": len(findings), "unreviewed": len(findings) - reviewed - needs,
        "reviewed": reviewed, "needs_more_review": needs, "by_decision": by_decision,
        "high_critical_unreviewed": hc_unreviewed, "reviewer_rejected": rejected,
        "by_correction_type": by_correction,
    }


@router.get("/feedback/export")
def export(member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """Org-scoped JSONL evaluation dataset (admin/reviewer roles only)."""
    if member.role not in ("admin", "legal_reviewer"):
        raise HTTPException(403, "Feedback export is restricted to admin/reviewer roles")
    rows = (db.query(FeedbackLedger, RiskFinding, Clause)
            .join(RiskFinding, RiskFinding.id == FeedbackLedger.finding_id)
            .join(Clause, Clause.id == RiskFinding.clause_id)
            .filter(FeedbackLedger.organization_id == member.organization_id)
            .order_by(FeedbackLedger.created_at).all())
    lines = []
    for fb, finding, clause in rows:
        lines.append(json.dumps({
            "clause_id": str(finding.clause_id),
            "clause_text": clause.text if clause else None,
            "original_classifier_prediction": fb.original_classifier_prediction,
            "original_classifier_confidence": fb.original_classifier_confidence,
            "original_category": fb.original_category, "original_rule": fb.original_rule,
            "original_priority": fb.original_priority,
            "original_policy_version": fb.original_policy_version,
            "decision": fb.decision, "correction_type": fb.correction_type,
            "corrected_category": fb.corrected_category,
            "corrected_priority": fb.corrected_priority,
            "reviewer_comment": fb.reviewer_comment,
            "reviewer_role": fb.reviewer_role,
            "explanation_version": fb.explanation_version,
            "created_at": fb.created_at.isoformat()}))
    return Response(content="\n".join(lines), media_type="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=feedback-export.jsonl"})
