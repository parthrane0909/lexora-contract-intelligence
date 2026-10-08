"""Review decision semantics (Phase 8.4/8.11).

ACCEPT: reviewer agrees the finding warrants attention as presented
  (assessment of the system's finding — NOT a claim of legal danger).
REJECT: finding is not applicable / should not be treated as valid.
MODIFY: real issue, but interpretation/severity/category/explanation needs
  correction (structured correction_type + optional corrected_* + comment).
NEEDS_MORE_REVIEW: no final determination yet (comment required).

Correction taxonomy is fixed values (extensible later), never free text.
"""
from __future__ import annotations

DECISIONS = ("accept", "reject", "modify", "needs_more_review")

CORRECTION_TYPES = (
    "WRONG_CATEGORY",
    "WRONG_PRIORITY",
    "FALSE_POSITIVE",
    "MISSING_CONTEXT",
    "POLICY_MISINTERPRETATION",
    "TEMPLATE_MISMATCH",
    "EXTRACTION_ERROR",
    "CLAUSE_CLASSIFICATION_ERROR",
    "EXPLANATION_PROBLEM",
    "OTHER",
)

# Priorities/categories accepted as corrections (mirror engine taxonomies).
CORRECTABLE_PRIORITIES = ("critical", "high", "medium", "low")

COMMENT_MAX_CHARS = 2000

# comment required for every decision except plain ACCEPT
COMMENT_REQUIRED = ("reject", "modify", "needs_more_review")


def derive_status(latest_decision: str | None) -> str:
    """Review state from the latest decision (separate from risk status)."""
    if latest_decision is None:
        return "unreviewed"
    if latest_decision == "needs_more_review":
        return "needs_more_review"
    return "reviewed"
