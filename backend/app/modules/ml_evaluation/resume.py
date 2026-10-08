"""Resume-safe Phase 14 helpers (stage 1c). Read-then-act primitives that make it
impossible to retrain a completed run or touch the frozen test split.

- plan_resume: classify expected specs into completed / needs_finalize / missing.
- finalize_without_retraining: comparison + experiment-row update ONLY (B45 case).
- disk_ok: free-space guard before starting a training run.

No function here trains, promotes, or evaluates on test. All refuse test split.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid

from sqlalchemy.orm import Session

#: Splits that may be used for selection-related work. "test" is deliberately
#: absent: the frozen test set is final-reporting only.
ALLOWED_EVAL_SPLITS = ("train", "validation")


def model_version_for(prefix: str, cell: str, seed: int) -> str:
    """Mirror run_matrix naming so resume matches existing rows exactly."""
    return f"{prefix}-{cell}-s{seed}"


def _artifact_ok(artifact_location: str | None) -> bool:
    return bool(artifact_location) and os.path.exists(
        os.path.join(artifact_location, "model.safetensors"))


def _validation_reports(db: Session, model_version: str) -> list:
    from app.models import MLEvaluationRun

    return (db.query(MLEvaluationRun)
            .filter(MLEvaluationRun.model_version == model_version,
                    MLEvaluationRun.split == "validation")
            .order_by(MLEvaluationRun.created_at).all())


def plan_resume(db: Session, specs: list[dict]) -> dict:
    """Classify each spec {cell, preset, seed, model_version, matrix_id?}.

    - completed: experiment row completed AND model registered AND final
      artifact present.
    - needs_finalize: not completed BUT final artifact + validation eval +
      model row all present (interrupted after training/eval, e.g. B45).
    - missing: everything else (never started, failed without artifact, etc.).
    """
    from app.models import MLExperiment

    from app.modules.ml_evaluation import registry as reg

    out = {"completed": [], "needs_finalize": [], "missing": []}
    for spec in specs:
        exp = None
        if spec.get("matrix_id"):
            exp = (db.query(MLExperiment)
                   .filter(MLExperiment.matrix_id == spec["matrix_id"],
                           MLExperiment.name == spec["name"])
                   .first()) if spec.get("name") else None
        row = reg.get_model(db, reg.DEFAULT_MODEL_NAME, spec["model_version"])
        art_ok = _artifact_ok(row.artifact_location if row else None)
        if exp is not None and exp.status == "completed" and row is not None and art_ok:
            out["completed"].append(spec)
        elif row is not None and art_ok and _validation_reports(db, spec["model_version"]):
            out["needs_finalize"].append(spec)
        else:
            out["missing"].append(spec)
    return out


def finalize_without_retraining(db: Session, experiment_id: uuid.UUID, *,
                                eval_split: str = "validation") -> dict:
    """Finish an interrupted experiment whose training + validation eval
    already happened (B45 pattern). Runs ONLY the comparison step on the
    existing artifact, then updates the experiment row. Never trains, never
    touches MLflow, never promotes, never evaluates on test."""
    if eval_split == "test":
        raise ValueError("Refusing test split: finalization uses validation only.")
    if eval_split not in ALLOWED_EVAL_SPLITS:
        raise ValueError(f"Unknown eval_split {eval_split!r}.")
    from app.models import MLExperiment

    from app.modules.ml_evaluation import comparisons as cmp_mod
    from app.modules.ml_evaluation import registry as reg
    from app.modules.ml_evaluation import service as svc

    exp = db.get(MLExperiment, experiment_id)
    if exp is None:
        raise ValueError("Experiment not found.")
    if exp.status == "completed":
        raise ValueError("Experiment already completed; refusing to overwrite finished work.")
    cfg = json.loads(exp.config or "{}")["resolved"]
    if cfg.get("dataset_version") is None:
        raise ValueError("Experiment has no dataset version; cannot finalize.")
    row = reg.get_model(db, reg.DEFAULT_MODEL_NAME, cfg["model_version"])
    if row is None:
        raise ValueError(f"Model {cfg['model_version']!r} not registered; cannot finalize.")
    if not _artifact_ok(row.artifact_location):
        raise ValueError("Final artifact missing; retraining required (refusing here).")
    reports = _validation_reports(db, cfg["model_version"])
    if not reports:
        raise ValueError("No validation evaluation recorded; cannot finalize.")
    try:
        summary = json.loads(row.metrics or "{}")
        eval_n = summary.get("n")
    except Exception:
        summary, eval_n = {}, None
    if not summary:
        raise ValueError("Registered metrics empty; cannot finalize without measured numbers.")
    data_dir = _dataset_dir(db, exp.organization_id, cfg["dataset_version"])
    comparison = cmp_mod.compare_against_baseline(
        db, candidate_version=cfg["model_version"],
        dataset_path=data_dir, split=eval_split)
    exp.status = "completed"
    exp.candidate_version = cfg["model_version"]
    exp.metrics = json.dumps(summary)
    exp.costs = json.dumps({
        "train_duration_s": None,
        "recovered_run": True,
        "eval_split": eval_split,
        "eval_n": eval_n,
        "note": "train duration unavailable: process died after training; not fabricated",
    })
    db.commit()
    return {"experiment_id": str(exp.id), "status": "completed",
            "eval_split": eval_split, "comparison": comparison}


def _dataset_dir(db: Session, organization_id, dataset_version: str) -> str:
    from app.models import MLDataset

    row = db.query(MLDataset).filter(
        MLDataset.organization_id == organization_id,
        MLDataset.version == dataset_version).first()
    if row is None or not row.artifact_path:
        raise ValueError(f"Dataset {dataset_version!r} not found for this org.")
    return row.artifact_path


def disk_ok(path: str, *, required_bytes: int, floor_bytes: int) -> dict:
    """True only if starting a run leaves at least floor_bytes free."""
    free = shutil.disk_usage(path).free
    return {"ok": (free - required_bytes) >= floor_bytes,
            "free_bytes": free, "required_bytes": required_bytes,
            "floor_bytes": floor_bytes}
