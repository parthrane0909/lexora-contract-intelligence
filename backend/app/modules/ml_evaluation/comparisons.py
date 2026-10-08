"""Candidate-vs-baseline comparison builder (Phase 13.5). Deltas, error
shifts, truncation/cost contrasts, protected-class table — all from measured
reports, never from claims. A regression is shown as loudly as an improvement.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.ml_evaluation import service as svc
from app.modules.ml_evaluation.protected_classes import PROTECTED_CLASSES
from app.modules.ml_evaluation.training_configs import diff_configs


def _get(d: dict, *path, default=None):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def compare_against_baseline(db: Session, *, candidate_version: str,
                             dataset_path: str, split: str = "test",
                             _evaluate=None) -> dict:
    """Evaluate the candidate on the frozen split, diff against the stored
    production baseline evaluation (re-measuring production if absent)."""
    from app.models import MLEvaluationRun
    from app.modules.ml_evaluation import registry as reg

    ev = _evaluate or svc.evaluate_bundle
    cand_eval = ev(
        db, organization_id=None, model_dir=_artifact_of(db, candidate_version),
        dataset_path=dataset_path, split=split, model_version=candidate_version)
    prod = reg.production_model(db)
    if prod is None:
        raise ValueError("No production model registered; cannot compare.")
    base_eval = ev(
        db, organization_id=None, model_dir=prod.artifact_location,
        dataset_path=dataset_path, split=split, model_version=prod.version)
    return build_comparison(baseline_report=base_eval["report"],
                            candidate_report=cand_eval["report"],
                            baseline_version=prod.version,
                            candidate_version=candidate_version)


def _artifact_of(db: Session, version: str) -> str:
    from app.modules.ml_evaluation import registry as reg

    row = reg.get_model(db, reg.DEFAULT_MODEL_NAME, version)
    if row is None or not row.artifact_location:
        raise ValueError(f"Model {version!r} unknown or has no artifact.")
    return row.artifact_location


def build_comparison(*, baseline_report: dict, candidate_report: dict,
                     baseline_version: str, candidate_version: str) -> dict:
    """Pure diff of two full_report dicts (+ identity). Unit-testable."""
    b, c = baseline_report, candidate_report
    out = {
        "baseline_version": baseline_version,
        "candidate_version": candidate_version,
        "overall": {},
        "per_class": {},
        "protected_classes": {},
        "error_shift": {},
        "truncation": {"baseline": b.get("truncation"),
                       "candidate": c.get("truncation")},
        "calibration": {"baseline_ece": _get(b, "calibration", "reliability", "ece"),
                        "candidate_ece": _get(c, "calibration", "reliability", "ece")},
    }
    for scope in ("accuracy", "micro", "macro", "weighted"):
        if scope == "accuracy":
            bv, cv = _get(b, "summary", "accuracy"), _get(c, "summary", "accuracy")
        else:
            bv, cv = _get(b, "summary", scope, "f1"), _get(c, "summary", scope, "f1")
        out["overall"][scope] = {
            "baseline": bv, "candidate": cv,
            "delta": (round(cv - bv, 4) if bv is not None and cv is not None else None)}
    bpc, cpc = b.get("per_class", {}), c.get("per_class", {})
    for lab in sorted(set(bpc) | set(cpc)):
        bf = (bpc.get(lab) or {}).get("f1")
        cf = (cpc.get(lab) or {}).get("f1")
        out["per_class"][lab] = {
            "baseline_f1": bf, "candidate_f1": cf,
            "delta": (round(cf - bf, 4) if bf is not None and cf is not None else None)}
        if lab in PROTECTED_CLASSES:
            out["protected_classes"][lab] = out["per_class"][lab]
    berr = len((b.get("errors") or {}).get("misclassification_pairs", []))
    cerr = len((c.get("errors") or {}).get("misclassification_pairs", []))
    out["error_shift"] = {
        "baseline_pair_types": berr, "candidate_pair_types": cerr,
        "note": "Pair lists compared by (actual, predicted); counts only, "
                "patterns listed in each report."}
    return out
