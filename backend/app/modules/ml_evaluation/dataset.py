"""Feedback → curated examples (Phase 12.1). Deterministic rules over the
Phase 8 ledger; ambiguous reviews are EXCLUDED with reasons, never forced:

- modify + corrected_category in taxonomy  → supervised, gold = corrected
- accept                            → eval-only, gold = original prediction
                                        (human-confirmed, NOT a "correction")
- reject (any)                       → excluded from supervised (gold truly
                                        unknown); flagged eval_fp=True
- needs_more_review / missing text / unknown label → excluded with reason

Accepted ≠ corrected label. Rejected ≠ negative example. Ambiguity stays out.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.orm import Session

from app.models import Clause, Contract, FeedbackLedger, RiskFinding

EVAL_ONLY_DECISIONS = ("accept",)
EXCLUDED_DECISIONS = ("needs_more_review",)


def norm_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def example_id(org_id: uuid.UUID, clause_id: uuid.UUID, review_id: uuid.UUID) -> str:
    raw = f"{org_id}:{clause_id}:{review_id}".encode()
    return f"fb-{hashlib.sha256(raw).hexdigest()[:16]}"


def build_examples(db: Session, organization_id: uuid.UUID,
                   valid_labels: tuple[str, ...]) -> tuple[list[dict], dict]:
    """Returns (examples, stats). Examples carry usability flags + reasons."""
    rows = (db.query(FeedbackLedger, RiskFinding, Clause, Contract)
            .join(RiskFinding, RiskFinding.id == FeedbackLedger.finding_id)
            .join(Clause, Clause.id == RiskFinding.clause_id)
            .join(Contract, Contract.id == RiskFinding.contract_id)
            .filter(FeedbackLedger.organization_id == organization_id)
            .order_by(FeedbackLedger.created_at).all())
    valid = set(valid_labels)
    examples, stats = [], {"total": 0, "accept": 0, "reject": 0, "modify": 0,
                           "needs_more_review": 0, "other": 0}
    for fb, finding, clause, contract in rows:
        stats["total"] += 1
        stats[fb.decision] = stats.get(fb.decision, 0) + 1
        text = (clause.text or "").strip()
        base = {
            "example_id": example_id(fb.organization_id, clause.id, fb.review_id),
            "organization_id": str(fb.organization_id),
            "contract_id": str(finding.contract_id),
            "clause_id": str(clause.id),
            "text": text,
            "original_model_category": fb.original_classifier_prediction,
            "review_decision": fb.decision,
            "correction_type": fb.correction_type,
            "corrected_category": fb.corrected_category,
            "source": "human_review",
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        }
        if not text:
            examples.append({**base, "gold_label": None, "usable_supervised": False,
                             "usable_eval": False, "eval_fp": False,
                             "exclusion_reason": "empty_text"})
        elif fb.decision == "modify" and fb.corrected_category in valid:
            examples.append({**base, "gold_label": fb.corrected_category,
                             "usable_supervised": True, "usable_eval": True,
                             "eval_fp": False, "exclusion_reason": None})
        elif fb.decision == "modify":
            examples.append({**base, "gold_label": None, "usable_supervised": False,
                             "usable_eval": False, "eval_fp": False,
                             "exclusion_reason": "modify_without_valid_correction"})
        elif fb.decision == "accept" and fb.original_classifier_prediction in valid:
            examples.append({**base, "gold_label": fb.original_classifier_prediction,
                             "usable_supervised": False, "usable_eval": True,
                             "eval_fp": False, "exclusion_reason": None,
                             "human_confirmed": True})
        elif fb.decision == "accept":
            examples.append({**base, "gold_label": None, "usable_supervised": False,
                             "usable_eval": False, "eval_fp": False,
                             "exclusion_reason": "accept_without_valid_prediction"})
        elif fb.decision == "reject":
            examples.append({**base, "gold_label": None, "usable_supervised": False,
                             "usable_eval": False, "eval_fp": True,
                             "exclusion_reason": "rejected_gold_unknown"})
        else:
            examples.append({**base, "gold_label": None, "usable_supervised": False,
                             "usable_eval": False, "eval_fp": False,
                             "exclusion_reason": f"decision_{fb.decision}_excluded"})
    return examples, stats
