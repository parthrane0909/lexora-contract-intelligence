"""Human-feedback quality audit (Phase 15). Read-only classification over the
existing Phase 8 ledger via the Phase 12 eligibility rules — no new rules
invented, no labels fabricated.

Buckets (spec Part 2):
  SUPERVISED_ELIGIBLE  modify + corrected class in taxonomy
  EVALUATION_ONLY      accept with valid original prediction (human-confirmed)
  EXCLUDED_AMBIGUOUS   needs_more_review / modify without valid correction
  EXCLUDED_INVALID     empty text / invalid class / unknown decision
  EXCLUDED_NON_CORRECTION  reject and other non-correction decisions

Also: correction matrix (prediction -> human correction), overlap screening
against frozen texts, and an explicit evidence verdict (SUFFICIENT / LIMITED /
INSUFFICIENT) with each requirement individually reported — never a bare label.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy.orm import Session

from app.modules.ml_evaluation.dataset import build_examples, norm_text

AMBIGUOUS_REASONS = ("needs_more_review", "modify_without_valid_correction")
INVALID_REASONS = ("empty_text", "accept_without_valid_prediction")


def _bucket(ex: dict) -> str:
    if ex.get("usable_supervised"):
        return "SUPERVISED_ELIGIBLE"
    if ex.get("usable_eval"):
        return "EVALUATION_ONLY"
    reason = ex.get("exclusion_reason") or ""
    if reason.startswith("decision_needs_more_review") or reason in AMBIGUOUS_REASONS:
        return "EXCLUDED_AMBIGUOUS"
    if reason in INVALID_REASONS or reason.startswith("decision_"):
        # unknown decisions / malformed rows: invalid input, not a judgment
        return "EXCLUDED_INVALID"
    # rejected_gold_unknown and friends: a real human judgment that yields
    # no label (non-correction), distinct from malformed input
    return "EXCLUDED_NON_CORRECTION"


def audit_feedback(db: Session, organization_id: uuid.UUID,
                   valid_labels: tuple[str, ...]) -> dict:
    """Aggregate-only audit. Returns counts/reasons/distributions — never raw
    clause text beyond what the caller already owns."""
    examples, build_stats = build_examples(db, organization_id, valid_labels)
    buckets: dict[str, list[dict]] = {}
    for ex in examples:
        buckets.setdefault(_bucket(ex), []).append(ex)
    counts = {k: len(v) for k, v in buckets.items()}
    total = len(examples) or 1
    eligible = buckets.get("SUPERVISED_ELIGIBLE", [])
    roles = _eligible_roles(db, organization_id) if eligible else {}
    return {
        "total": len(examples),
        "buckets": {k: {"count": counts.get(k, 0),
                        "pct": round(counts.get(k, 0) / total, 4)}
                    for k in ("SUPERVISED_ELIGIBLE", "EVALUATION_ONLY",
                              "EXCLUDED_AMBIGUOUS", "EXCLUDED_INVALID",
                              "EXCLUDED_NON_CORRECTION")},
        "exclusion_reasons": dict(Counter(
            e.get("exclusion_reason") or "none" for e in examples
            if not e.get("usable_supervised"))),
        "eligible_class_distribution": dict(Counter(
            e["gold_label"] for e in eligible)),
        "eligible_contracts": len({e["contract_id"] for e in eligible}),
        "correction_matrix": _correction_matrix(eligible),
        "eligible_reviewer_roles": roles,
        "build_stats": build_stats,
    }


def _eligible_roles(db: Session, organization_id: uuid.UUID) -> dict:
    """Reviewer-role distribution over modify decisions (roles only, no PII)."""
    from app.models import FeedbackLedger

    rows = (db.query(FeedbackLedger.reviewer_role)
            .filter(FeedbackLedger.organization_id == organization_id,
                    FeedbackLedger.decision == "modify").all())
    return dict(Counter(r[0] or "unspecified" for r in rows))


def correction_matrix_from(examples: list[dict]) -> dict:
    """Prediction -> human-correction counts over supervised-eligible rows."""
    return _correction_matrix([e for e in examples if e.get("usable_supervised")])


def _correction_matrix(eligible: list[dict]) -> dict:
    mat: dict[str, dict[str, int]] = {}
    for e in eligible:
        src = e.get("original_model_category") or "unknown"
        dst = e.get("gold_label") or "unknown"
        mat.setdefault(src, {}).setdefault(dst, 0)
        mat[src][dst] += 1
    return mat


def overlap_screen(examples: list[dict], frozen_texts: dict[str, set[str]]) -> dict:
    """Normalized-text overlap of candidate examples against frozen splits.

    frozen_texts: {"train": {normed...}, "validation": {...}, "test": {...}}.
    Returns per-split hit counts + offending example ids (test hits are
    reported loudly and must block training use).
    """
    out = {}
    for split, texts in frozen_texts.items():
        hits = [e["example_id"] for e in examples
                if norm_text(e.get("text") or "") in texts]
        out[split] = {"n_hits": len(hits), "example_ids": hits}
    return out


# Evidence verdict. Each requirement is a stated policy with rationale
# (Phase 15 Part 7 asks for an explicit rule, not vibes):
#  - ≥50 supervised: fewer examples than ~3× the 14-class taxonomy cannot
#    represent per-class behavior; below this only a pilot is defensible.
#  - ≥10 unique contracts: contract-unit splitting needs contract diversity,
#    else splits are degenerate and leakage checks meaningless.
#  - ≥5 weak-class (renewal/warranty/termination) corrections: Phase 15 exists
#    to fix these; without evidence there, training cannot answer its question.
#  - zero contradictions / zero test overlap: safety invariants, non-negotiable.
#  - duplicate rate < 50%: above this the "dataset" is memorizing a few clauses.
VERDICT_RULES = {
    "min_supervised": 50,
    "min_contracts": 10,
    "min_weak_class_corrections": 5,
    "max_duplicate_rate": 0.5,
}
WEAK_CLASSES = ("renewal", "warranty", "termination")


def evidence_verdict(audit: dict, overlap: dict | None = None,
                     duplicate_rate: float = 0.0,
                     contradictions: int = 0) -> dict:
    """A/B/C classification with per-requirement PASS/FAIL — never a bare label."""
    n_sup = audit["buckets"]["SUPERVISED_ELIGIBLE"]["count"]
    n_con = audit.get("eligible_contracts", 0)
    dist = audit.get("eligible_class_distribution", {})
    n_weak = sum(dist.get(c, 0) for c in WEAK_CLASSES)
    test_hits = ((overlap or {}).get("test") or {}).get("n_hits", 0)
    reqs = {
        "n_supervised_ge_50": n_sup >= VERDICT_RULES["min_supervised"],
        "contracts_ge_10": n_con >= VERDICT_RULES["min_contracts"],
        "weak_class_ge_5": n_weak >= VERDICT_RULES["min_weak_class_corrections"],
        "zero_contradictions": contradictions == 0,
        "zero_test_overlap": test_hits == 0,
        "duplicate_rate_lt_50pct": duplicate_rate < VERDICT_RULES["max_duplicate_rate"],
    }
    if all(reqs.values()):
        verdict = "SUFFICIENT_FOR_TARGETED_TRAINING"
    elif n_sup > 0 and reqs["zero_test_overlap"] and reqs["zero_contradictions"]:
        verdict = "SUFFICIENT_FOR_LIMITED_EXPERIMENTATION"
    else:
        verdict = "INSUFFICIENT_CONTINUE_COLLECTING"
    return {"verdict": verdict, "requirements": reqs,
            "observed": {"n_supervised": n_sup, "n_contracts": n_con,
                         "n_weak_class": n_weak, "test_hits": test_hits,
                         "duplicate_rate": duplicate_rate,
                         "contradictions": contradictions}}
