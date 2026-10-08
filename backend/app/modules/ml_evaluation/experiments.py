"""Experiment runner (Phase 13). One experiment = one resolved config +
frozen dataset + seed, tracked in ml_experiments, producing at most one
registered candidate. Train/evaluate cores are injectable so the whole
lifecycle is unit-testable without GPUs; production runs use the real cores.

The frozen test set is for FINAL measurement only — tuning decisions use
validation metrics. Nothing here promotes anything.
"""
from __future__ import annotations

import datetime
import json
import os
import time
import uuid

from sqlalchemy.orm import Session

from app.models import MLExperiment
from app.modules.ml_evaluation import service as svc
from app.modules.ml_evaluation.training import CandidateConfig, train_candidate
from app.modules.ml_evaluation.training_configs import diff_configs, resolve_config


def register_experiment(db: Session, *, organization_id: uuid.UUID | None,
                        name: str, preset: str, dataset_version: str,
                        model_version: str, seed: int = 42,
                        overrides: dict | None = None,
                        created_by: uuid.UUID | None = None) -> MLExperiment:
    cfg = resolve_config(preset, dataset_version, model_version, overrides)
    if seed != 42:
        # Seeds are first-class experimental variables, recorded explicitly.
        cfg.seed = seed
        cfg.validate()
    row = MLExperiment(
        organization_id=organization_id, name=name, kind="classification",
        config=json.dumps({"preset": preset, "overrides": overrides or {},
                           "resolved": _config_dict(cfg)}),
        dataset_version=dataset_version, seed=cfg.seed, status="registered",
        created_by=created_by)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _config_dict(cfg: CandidateConfig) -> dict:
    from dataclasses import asdict

    return asdict(cfg)


def run_experiment(db: Session, experiment_id: uuid.UUID, *,
                   _train=None, _evaluate=None,
                   eval_split: str = "test") -> dict:
    """Execute a registered experiment end-to-end. Returns the comparison
    report (candidate vs production baseline on the SAME split).

    Frozen-test discipline: pass eval_split="validation" for tuning/selection
    runs; "test" is reserved for the single final frozen evaluation. The split
    used is recorded on the experiment row and in the returned report."""
    from app.modules.ml_evaluation import registry as reg

    if eval_split not in ("train", "validation", "test"):
        raise ValueError(f"Unknown eval_split {eval_split!r}.")
    exp = db.get(MLExperiment, experiment_id)
    if exp is None:
        raise ValueError("Experiment not found.")
    if exp.status not in ("registered", "failed"):
        raise ValueError(f"Experiment is {exp.status!r}; only registered/failed runs.")
    exp.status = "running"
    db.commit()
    started = time.monotonic()
    try:
        cfg_dict = json.loads(exp.config or "{}")
        cfg = CandidateConfig(**cfg_dict["resolved"]).validate()
        data_dir = _dataset_dir(db, exp.organization_id, cfg.dataset_version)
        out_dir = os.path.join(svc.artifact_base(), "models", cfg.model_version)
        mlflow_uri = os.environ.get("MLFLOW_TRACKING_URI", "./mlruns")
        train = _train or train_candidate
        result = train(cfg, data_dir, out_dir, mlflow_uri)
        evaluate = _evaluate or svc.evaluate_bundle
        test_eval = evaluate(
            db, organization_id=exp.organization_id, model_dir=out_dir,
            dataset_path=data_dir, split=eval_split, model_version=cfg.model_version)
        from app.modules.ml_evaluation import comparisons as cmp_mod

        reg.register_model(
            db, version=cfg.model_version, dataset_version=cfg.dataset_version,
            base_model=cfg.base_model, metrics=test_eval["report"]["summary"],
            artifact_location=os.path.abspath(out_dir), status="candidate",
            mlflow_run_id=result.get("mlflow_run_id"))
        comparison = cmp_mod.compare_against_baseline(
            db, candidate_version=cfg.model_version,
            dataset_path=data_dir, split=eval_split, _evaluate=evaluate)
        exp.status = "completed"
        exp.candidate_version = cfg.model_version
        exp.metrics = json.dumps(test_eval["report"]["summary"])
        exp.costs = json.dumps({
            "train_duration_s": result.get("duration_s"),
            "eval_split": eval_split,
            "eval_n": test_eval["report"]["summary"].get("n")})
        db.commit()
        return {"experiment_id": str(exp.id), "status": "completed",
                "duration_s": round(time.monotonic() - started, 1),
                "eval_split": eval_split,
                "comparison": comparison}
    except Exception as e:
        db.rollback()
        exp = db.get(MLExperiment, experiment_id)
        exp.status = "failed"
        exp.error = f"{type(e).__name__}: {e}"[:2000]
        db.commit()
        raise


def _dataset_dir(db: Session, organization_id, dataset_version: str) -> str:
    from app.models import MLDataset

    row = db.query(MLDataset).filter(
        MLDataset.organization_id == organization_id,
        MLDataset.version == dataset_version).first()
    if row is None or not row.artifact_path:
        raise ValueError(f"Dataset {dataset_version!r} not found for this org.")
    return row.artifact_path


def get_experiment(db: Session, experiment_id: uuid.UUID) -> MLExperiment | None:
    return db.get(MLExperiment, experiment_id)


def list_experiments(db: Session, organization_id) -> list[MLExperiment]:
    return (db.query(MLExperiment)
            .filter(MLExperiment.organization_id == organization_id)
            .order_by(MLExperiment.created_at.desc()).all())


# ---------------------------------------------------------------------------
# Phase 14: canonical implementations live in dedicated modules
# (experiment_matrix.py, final_selection.py, search.py). Re-exported here so
# legacy `experiments.*` call sites keep working with zero behavior change
# and a single source of truth. experiments.py owns ONLY register/run/get.
# ---------------------------------------------------------------------------
from app.modules.ml_evaluation.experiment_matrix import (  # noqa: F401
    DEFAULT_SEEDS,
    MATRIX_CELLS,
    build_matrix,
    run_matrix,
    run_multi_seed,
)
from app.modules.ml_evaluation.final_selection import (  # noqa: F401
    build_model_card,
    final_selection_report,
    select_finalist,
    write_model_card,
)
from app.modules.ml_evaluation.search import (  # noqa: F401
    summarize_epoch_sensitivity,
    summarize_lr_sensitivity,
)
