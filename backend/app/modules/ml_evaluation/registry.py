"""Model registry (Phase 12.12) + production resolution (Phase 12.16).

Statuses: candidate → evaluated → approved → production; rejected/archived
terminal-ish. Exactly one `production` row per task — enforced here, never
assumed. The classifier consults resolve_production_model_dir() with total
fallback to legacy MODEL_DIR behavior, so existing inference is untouched
when no explicit pin exists.
"""
from __future__ import annotations

import json
import os
import uuid

from sqlalchemy.orm import Session

from app.models import MLModelVersion

TASK = "clause_classification"
DEFAULT_MODEL_NAME = "clause-classifier"
VALID_STATUSES = ("candidate", "evaluated", "approved", "production",
                  "rejected", "archived")


def register_model(db: Session, *, name: str = DEFAULT_MODEL_NAME, version: str,
                   task: str = TASK, dataset_version: str | None = None,
                   base_model: str | None = None, metrics: dict | None = None,
                   artifact_location: str | None = None,
                   status: str = "candidate",
                   mlflow_run_id: str | None = None) -> MLModelVersion:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}")
    existing = db.query(MLModelVersion).filter(
        MLModelVersion.name == name, MLModelVersion.version == version).first()
    if existing:
        raise ValueError(f"Model {name}/{version} already registered.")
    if status == "production":
        _demote_production(db, task, exclude_id=None)
    row = MLModelVersion(
        name=name, version=version, task=task, dataset_version=dataset_version,
        base_model=base_model, metrics=json.dumps(metrics or {}),
        artifact_location=artifact_location, status=status,
        mlflow_run_id=mlflow_run_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _demote_production(db: Session, task: str, exclude_id) -> list:
    demoted = []
    for r in db.query(MLModelVersion).filter(
            MLModelVersion.task == task, MLModelVersion.status == "production").all():
        if exclude_id is not None and r.id == exclude_id:
            continue
        r.status = "archived"
        demoted.append(r)
    return demoted


def get_model(db: Session, name: str, version: str) -> MLModelVersion | None:
    return db.query(MLModelVersion).filter(
        MLModelVersion.name == name, MLModelVersion.version == version).first()


def list_models(db: Session, task: str | None = None) -> list[MLModelVersion]:
    q = db.query(MLModelVersion).order_by(MLModelVersion.created_at.desc())
    if task:
        q = q.filter(MLModelVersion.task == task)
    return q.all()


def production_model(db: Session, task: str = TASK) -> MLModelVersion | None:
    rows = db.query(MLModelVersion).filter(
        MLModelVersion.task == task, MLModelVersion.status == "production").all()
    if len(rows) > 1:
        raise ValueError(f"Multiple production models for task {task!r} — "
                         f"registry corrupt, refusing to choose.")
    return rows[0] if rows else None


def ensure_baseline_registered(db: Session) -> dict:
    """Idempotent bootstrap: register the current MODEL_DIR bundle as the
    production baseline if the registry has no production model yet."""

    if production_model(db) is not None:
        prod = production_model(db)
        return {"registered": False, "version": prod.version}
    # Raw legacy fallback (NOT default_model_dir(): that consults the registry).
    from app.core.config import get_settings

    model_dir = os.environ.get("MODEL_DIR") or get_settings().MODEL_DIR
    info_path = os.path.join(model_dir, "model_info.json")
    if not os.path.exists(info_path):
        raise ValueError(f"Cannot seed baseline: no model_info.json at {model_dir!r}")
    with open(info_path) as f:
        info = json.load(f)
    row = register_model(
        db, version=info.get("model_version", "unknown"),
        dataset_version=info.get("dataset_version"),
        base_model=info.get("base_model"), metrics={},
        artifact_location=os.path.abspath(model_dir), status="production",
        mlflow_run_id=info.get("mlflow_run_id"))
    return {"registered": True, "version": row.version}


def resolve_production_model_dir() -> str | None:
    """Return the artifact dir for the explicitly pinned production model, or
    None when no pin exists (caller falls back to legacy behavior). Any
    failure → None: inference must never break because of the registry."""
    pinned = os.environ.get("ACTIVE_MODEL_VERSION")
    if not pinned:
        return None
    try:
        from app.db.session import get_session_factory

        db = get_session_factory()()
        try:
            row = db.query(MLModelVersion).filter(
                MLModelVersion.status == "production").first()
            if row is not None and row.version == pinned and row.artifact_location \
                    and os.path.exists(os.path.join(row.artifact_location,
                                                    "model_info.json")):
                return row.artifact_location
            return None
        finally:
            db.close()
    except Exception:
        return None
