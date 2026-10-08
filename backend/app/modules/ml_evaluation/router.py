"""ML evaluation endpoints (Phase 12.18). Org-scoped datasets; global model
registry with org-scoped evaluation records.

RBAC: viewer → summary reads; legal_reviewer/contract_manager → reports;
admin → build/train/evaluate/promote/shadow. Raw example texts never leave
the org (dataset detail serves manifest + counts, not examples.jsonl).
"""
import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.models import MLDataset
from app.modules.ml_evaluation import promotion as promo_mod
from app.modules.ml_evaluation import registry as reg
from app.modules.ml_evaluation import schemas, service
from app.modules.ml_evaluation.training import CandidateConfig, train_candidate

router = APIRouter(prefix="/ml-evaluation", tags=["ml-evaluation"])


def _dataset_or_404(db: Session, org_id: uuid.UUID, version: str) -> MLDataset:
    row = db.query(MLDataset).filter(
        MLDataset.organization_id == org_id, MLDataset.version == version).first()
    if row is None:
        raise HTTPException(404, f"Dataset {version!r} not found.")
    return row


def _model_or_404(db: Session, model_id: str):
    try:
        uid = uuid.UUID(model_id)
    except ValueError:
        raise HTTPException(404, "Model not found.")
    from app.models import MLModelVersion

    row = db.query(MLModelVersion).filter(MLModelVersion.id == uid).first()
    if row is None:
        raise HTTPException(404, "Model not found.")
    return row


def _dataset_out(row: MLDataset) -> dict:
    try:
        manifest = json.loads(row.manifest or "{}")
    except Exception:
        manifest = {}
    manifest.pop("quality", None)  # quality has its own endpoint
    return {"id": str(row.id), "version": row.version,
            "created_from": row.created_from, "source_count": row.source_count,
            "usable_count": row.usable_count, "excluded_count": row.excluded_count,
            "artifact_path": row.artifact_path, "manifest": manifest,
            "stats": manifest.get("org_stats", {}),
            "created_at": row.created_at.isoformat() if row.created_at else None}


def _model_out(row) -> dict:
    try:
        metrics = json.loads(row.metrics or "{}")
    except Exception:
        metrics = {}
    return {"id": str(row.id), "name": row.name, "version": row.version,
            "task": row.task, "dataset_version": row.dataset_version,
            "base_model": row.base_model, "metrics": metrics,
            "artifact_location": row.artifact_location, "status": row.status,
            "mlflow_run_id": row.mlflow_run_id,
            "created_at": row.created_at.isoformat() if row.created_at else None}


@router.get("/datasets", response_model=list[schemas.DatasetOut])
def list_datasets(member=Depends(get_current_membership),
                 db: Session = Depends(get_db)):
    rows = db.query(MLDataset).filter(
        MLDataset.organization_id == member.organization_id).all()
    return [_dataset_out(r) for r in rows]


@router.post("/datasets/build", response_model=schemas.DatasetOut,
             dependencies=[Depends(require_roles("admin"))])
def build_dataset(body: schemas.DatasetBuildIn,
                  member=Depends(get_current_membership),
                  db: Session = Depends(get_db)):
    try:
        built = service.build_dataset_version(
            db, member.organization_id, version=body.version, seed=body.seed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    row = db.query(MLDataset).filter(MLDataset.id == uuid.UUID(built["id"])).one()
    return _dataset_out(row)


@router.get("/datasets/{version}", response_model=schemas.DatasetOut)
def get_dataset(version: str, member=Depends(get_current_membership),
                db: Session = Depends(get_db)):
    return _dataset_out(_dataset_or_404(db, member.organization_id, version))


@router.get("/datasets/{version}/quality")
def dataset_quality(version: str, member=Depends(get_current_membership),
                    db: Session = Depends(get_db)):
    row = _dataset_or_404(db, member.organization_id, version)
    try:
        quality = json.loads(row.manifest or "{}").get("quality", {})
    except Exception:
        quality = {}
    base = {k: v for k, v in (json.loads(row.manifest or "{}") or {}).items()
            if k != "quality"}
    return {"version": version, "quality": quality, "manifest": base}


@router.get("/models", response_model=list[schemas.ModelOut])
def list_models(member=Depends(get_current_membership),
                db: Session = Depends(get_db)):
    return [_model_out(r) for r in reg.list_models(db)]


@router.get("/models/{model_id}", response_model=schemas.ModelOut)
def get_model(model_id: str, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    return _model_out(_model_or_404(db, model_id))


@router.post("/models/train", dependencies=[Depends(require_roles("admin"))])
def train_model(body: schemas.TrainIn, member=Depends(get_current_membership),
                db: Session = Depends(get_db)):
    from app.core.config import get_settings

    ds = _dataset_or_404(db, member.organization_id, body.dataset_version)
    cfg = CandidateConfig(
        base_model=body.base_model, dataset_version=body.dataset_version,
        learning_rate=body.learning_rate, batch_size=body.batch_size,
        epochs=body.epochs, seq_len=body.seq_len, seed=body.seed,
        class_weighting=body.class_weighting, weight_decay=body.weight_decay,
        fp16=body.fp16, model_version=body.model_version)
    try:
        cfg.validate()
    except ValueError as e:
        raise HTTPException(400, str(e))
    if reg.get_model(db, reg.DEFAULT_MODEL_NAME, body.model_version):
        raise HTTPException(400, f"Model version {body.model_version!r} already registered.")
    data_dir = ds.artifact_path
    out_dir = os.path.join(service.artifact_base(), "models", body.model_version)
    if os.path.exists(os.path.join(out_dir, "model_info.json")):
        raise HTTPException(400, f"Artifact for {body.model_version!r} already exists.")
    try:
        result = train_candidate(
            cfg, data_dir, out_dir,
            os.environ.get("MLFLOW_TRACKING_URI") or get_settings().MLFLOW_TRACKING_URI)
    except ValueError as e:
        raise HTTPException(400, str(e))
    row = reg.register_model(
        db, version=body.model_version, dataset_version=body.dataset_version,
        base_model=body.base_model, metrics=result.get("val_metrics", {}),
        artifact_location=os.path.abspath(out_dir), status="candidate",
        mlflow_run_id=result.get("mlflow_run_id"))
    return {"model": _model_out(row), "training": result}


@router.post("/models/evaluate", dependencies=[Depends(require_roles("admin"))])
def evaluate_model(body: schemas.EvaluateIn, member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    ds = _dataset_or_404(db, member.organization_id, body.dataset_version)
    if body.model_dir:
        model_dir, version = body.model_dir, None
    elif body.model_version:
        row = reg.get_model(db, reg.DEFAULT_MODEL_NAME, body.model_version)
        if row is None or not row.artifact_location:
            raise HTTPException(404, f"Model {body.model_version!r} unknown or has no artifact.")
        model_dir, version = row.artifact_location, row.version
    else:
        raise HTTPException(400, "Provide model_version or model_dir.")
    try:
        result = service.evaluate_bundle(
            db, organization_id=member.organization_id, model_dir=model_dir,
            dataset_path=ds.artifact_path, split=body.split,
            model_version=version, batch_size=body.batch_size)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if version:
        row = reg.get_model(db, reg.DEFAULT_MODEL_NAME, version)
        if row:
            # Record measured metrics on the registry row so promotion gates
            # compare real numbers (status still only advances explicitly).
            rep = result["report"]
            row.metrics = json.dumps({"summary": rep.get("summary", {}),
                                      "per_class": rep.get("per_class", {})})
            if row.status == "candidate":
                row.status = "evaluated"
            db.commit()
    return {"evaluation_id": result["evaluation_id"], "report": result["report"]}


@router.post("/models/{model_id}/shadow-evaluate",
             dependencies=[Depends(require_roles("admin"))])
def shadow(model_id: str, body: schemas.ShadowIn,
           member=Depends(get_current_membership), db: Session = Depends(get_db)):
    row = _model_or_404(db, model_id)
    model_dir = body.model_dir or row.artifact_location
    if not model_dir:
        raise HTTPException(400, "Candidate has no artifact to shadow-run.")
    if not body.texts or len(body.texts) > 50:
        raise HTTPException(400, "Provide 1..50 texts.")
    try:
        return service.shadow_evaluate(model_dir, body.texts)
    except Exception as e:
        raise HTTPException(503, f"Shadow evaluation failed: {e}")


@router.get("/models/{model_id}/errors")
def model_errors(model_id: str, member=Depends(get_current_membership),
                 db: Session = Depends(get_db)):
    if member.role not in ("admin", "legal_reviewer", "contract_manager"):
        raise HTTPException(403, "Insufficient role")
    from app.models import MLEvaluationRun

    row = _model_or_404(db, model_id)
    run = (db.query(MLEvaluationRun)
           .filter(MLEvaluationRun.model_version == row.version)
           .order_by(MLEvaluationRun.created_at.desc()).first())
    if run is None:
        raise HTTPException(404, "No evaluation recorded for this model yet.")
    try:
        with open(run.report_path) as f:
            report = json.load(f)
    except Exception as e:
        raise HTTPException(500, f"Cannot read evaluation report: {e}")
    return {"model_version": row.version, "errors": report.get("errors"),
            "truncation": report.get("truncation"),
            "evaluation_id": str(run.id)}


@router.get("/models/{model_id}/calibration")
def model_calibration(model_id: str, member=Depends(get_current_membership),
                      db: Session = Depends(get_db)):
    if member.role not in ("admin", "legal_reviewer", "contract_manager"):
        raise HTTPException(403, "Insufficient role")
    from app.models import MLEvaluationRun

    row = _model_or_404(db, model_id)
    run = (db.query(MLEvaluationRun)
           .filter(MLEvaluationRun.model_version == row.version)
           .order_by(MLEvaluationRun.created_at.desc()).first())
    if run is None:
        raise HTTPException(404, "No evaluation recorded for this model yet.")
    try:
        with open(run.report_path) as f:
            report = json.load(f)
    except Exception as e:
        raise HTTPException(500, f"Cannot read evaluation report: {e}")
    return {"model_version": row.version, "calibration": report.get("calibration"),
            "evaluation_id": str(run.id)}


@router.post("/models/{model_id}/promote", dependencies=[Depends(require_roles("admin"))])
def promote(model_id: str, body: schemas.PromoteIn,
            member=Depends(get_current_membership), db: Session = Depends(get_db)):
    row = _model_or_404(db, model_id)
    try:
        return promo_mod.promote(
            db, model_name=row.name, version=row.version,
            decided_by=member.user_id, organization_id=member.organization_id,
            reason=body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/lineage")
def lineage(member=Depends(get_current_membership), db: Session = Depends(get_db),
            dataset_version: str | None = None, model_version: str | None = None):
    if member.role not in ("admin", "legal_reviewer", "contract_manager"):
        raise HTTPException(403, "Insufficient role")
    if dataset_version:
        _dataset_or_404(db, member.organization_id, dataset_version)
    return service.lineage(db, organization_id=member.organization_id,
                           dataset_version=dataset_version,
                           model_version=model_version)


@router.get("/feedback/audit")
def feedback_audit(member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    """Phase 15: aggregate-only human-feedback audit (no raw clause texts).
    Org-scoped; read roles mirror lineage (admin/reviewer/manager)."""
    if member.role not in ("admin", "legal_reviewer", "contract_manager"):
        raise HTTPException(403, "Insufficient role")
    from app.modules.ml_evaluation import feedback_audit as fa_mod
    from app.modules.ml_nlp.taxonomy import MODEL_LABELS

    audit = fa_mod.audit_feedback(db, member.organization_id, tuple(MODEL_LABELS))
    verdict = fa_mod.evidence_verdict(audit)
    return {"audit": audit, "verdict": verdict}


def _experiment_out(row) -> dict:
    try:
        config = json.loads(row.config or "{}")
    except Exception:
        config = {}
    try:
        metrics = json.loads(row.metrics or "{}")
    except Exception:
        metrics = {}
    try:
        costs = json.loads(row.costs or "{}")
    except Exception:
        costs = {}
    return {"id": str(row.id), "name": row.name, "kind": row.kind,
            "config": config, "dataset_version": row.dataset_version,
            "seed": row.seed, "status": row.status,
            "candidate_version": row.candidate_version, "metrics": metrics,
            "costs": costs, "mlflow_run_id": row.mlflow_run_id,
            "error": row.error,
            "created_at": row.created_at.isoformat() if row.created_at else None}


@router.post("/classification/experiments", dependencies=[Depends(require_roles("admin"))])
def create_experiment(body: schemas.ExperimentIn,
                      member=Depends(get_current_membership),
                      db: Session = Depends(get_db)):
    from app.modules.ml_evaluation import experiments as exp_mod

    try:
        row = exp_mod.register_experiment(
            db, organization_id=member.organization_id, name=body.name,
            preset=body.preset, dataset_version=body.dataset_version,
            model_version=body.model_version, seed=body.seed,
            overrides=body.overrides, created_by=member.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _experiment_out(row)


@router.get("/classification/experiments")
def list_experiments(member=Depends(get_current_membership),
                     db: Session = Depends(get_db)):
    from app.modules.ml_evaluation import experiments as exp_mod

    return [_experiment_out(r) for r in exp_mod.list_experiments(db, member.organization_id)]


@router.get("/classification/experiments/matrix")
def list_matrix(member=Depends(get_current_membership),
                db: Session = Depends(get_db)):
    from sqlalchemy import func as _func

    from app.models import MLExperiment

    rows = (db.query(MLExperiment.matrix_id, _func.count(MLExperiment.id))
            .filter(MLExperiment.organization_id == member.organization_id,
                    MLExperiment.matrix_id.is_not(None))
            .group_by(MLExperiment.matrix_id).all())
    return [{"matrix_id": m, "n_runs": n} for m, n in rows]


@router.get("/classification/experiments/matrix/{matrix_id}")
def get_matrix(matrix_id: str, member=Depends(get_current_membership),
               db: Session = Depends(get_db)):
    from app.models import MLExperiment

    rows = (db.query(MLExperiment)
            .filter(MLExperiment.organization_id == member.organization_id,
                    MLExperiment.matrix_id == matrix_id)
            .order_by(MLExperiment.created_at).all())
    if not rows:
        raise HTTPException(404, "Matrix not found.")
    return {"matrix_id": matrix_id, "experiments": [_experiment_out(r) for r in rows]}


@router.get("/classification/experiments/{experiment_id}")
def get_experiment(experiment_id: str, member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    from app.modules.ml_evaluation import experiments as exp_mod

    try:
        uid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(404, "Experiment not found.")
    row = exp_mod.get_experiment(db, uid)
    if row is None or row.organization_id != member.organization_id:
        raise HTTPException(404, "Experiment not found.")
    return _experiment_out(row)


@router.post("/classification/experiments/{experiment_id}/run",
             dependencies=[Depends(require_roles("admin"))])
def run_experiment(experiment_id: str, member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    """Execute a registered experiment (trains + evaluates + compares).
    Long-running: production deployments should move this to a job runner."""
    from app.modules.ml_evaluation import experiments as exp_mod

    try:
        uid = uuid.UUID(experiment_id)
    except ValueError:
        raise HTTPException(404, "Experiment not found.")
    row = exp_mod.get_experiment(db, uid)
    if row is None or row.organization_id != member.organization_id:
        raise HTTPException(404, "Experiment not found.")
    try:
        return exp_mod.run_experiment(db, uid)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/classification/experiments/matrix",
             dependencies=[Depends(require_roles("admin"))])
def run_matrix(body: schemas.MatrixIn,
               member=Depends(get_current_membership),
               db: Session = Depends(get_db)):
    """Register + execute a config×seed matrix on the VALIDATION split.
    Long-running: production deployments should move this to a job runner."""
    import uuid as _uuid

    from app.modules.ml_evaluation import experiment_matrix as mx_mod

    try:
        return mx_mod.run_matrix(
            db, matrix_id=f"mx-{_uuid.uuid4().hex[:12]}", name=body.name,
            organization_id=member.organization_id,
            dataset_version=body.dataset_version,
            model_version_prefix=body.model_version_prefix,
            cells=body.cells, seeds=body.seeds,
            lr_multipliers=body.lr_multipliers, epochs_list=body.epochs_list,
            created_by=member.user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/models/{model_id}/model-card")
def model_card(model_id: str, member=Depends(get_current_membership),
               db: Session = Depends(get_db)):
    from app.modules.ml_evaluation import final_selection as fs_mod

    row = _model_or_404(db, model_id)
    base = os.path.join(row.artifact_location or "", "model_card.json") \
        if row.artifact_location else None
    if base and os.path.exists(base):
        with open(base) as f:
            return json.load(f)
    # Fallback: assemble a card from registry data (clearly marked partial).
    return fs_mod.build_model_card(
        model_version=row.version,
        training={"dataset_version": row.dataset_version,
                  "base_model": row.base_model, "note": "partial: assembled live"},
        validation={}, frozen_test={},
        robustness={}, limitations=["assembled live: no stored card artifact"])


@router.get("/models/{model_id}/statistics")
def model_statistics(model_id: str, member=Depends(get_current_membership),
                     db: Session = Depends(get_db)):
    """Multi-seed aggregate for one model version across its tracked runs."""
    from app.models import MLEvaluationRun

    from app.modules.ml_evaluation import multi_seed as ms_mod

    row = _model_or_404(db, model_id)
    runs = (db.query(MLEvaluationRun)
            .filter(MLEvaluationRun.model_version == row.version)
            .order_by(MLEvaluationRun.created_at).all())
    reports = []
    for i, run in enumerate(runs):
        try:
            with open(run.report_path) as f:
                rep = json.load(f)
        except Exception:
            continue
        reports.append({"seed": i,
                        "summary": (rep.get("summary") or {}),
                        "per_class": rep.get("per_class", {})})
    if not reports:
        raise HTTPException(404, "No evaluation reports recorded for this model yet.")
    return {"model_version": row.version,
            "aggregate": ms_mod.aggregate_runs(reports)}


@router.post("/models/{model_id}/rollback", dependencies=[Depends(require_roles("admin"))])
def rollback(model_id: str, body: schemas.RollbackIn,
             member=Depends(get_current_membership), db: Session = Depends(get_db)):
    row = _model_or_404(db, model_id)
    try:
        from app.modules.ml_evaluation import promotion as promo_rb
        return promo_rb.rollback(
            db, model_name=row.name, version=row.version,
            decided_by=member.user_id, organization_id=member.organization_id,
            reason=body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/classification/selection")
def selection(versions: str | None = None,
              member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    """Deterministic final-selection recommendation over VALIDATION evidence
    only (never test). Optional ?versions=a,b to scope the candidate set."""
    from app.modules.ml_evaluation import final_selection as fs_mod

    vlist = [v.strip() for v in (versions or "").split(",") if v.strip()] or None
    try:
        return fs_mod.final_selection_report(
            db, versions=vlist, organization_id=member.organization_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/models/{model_id}/protected")
def model_protected(model_id: str, member=Depends(get_current_membership),
                    db: Session = Depends(get_db)):
    """Protected-class F1 breakdown for one model vs current production,
    from each model's latest stored evaluation report."""
    from app.models import MLEvaluationRun

    from app.modules.ml_evaluation.protected_classes import PROTECTED_CLASSES

    def _latest(version: str):
        run = (db.query(MLEvaluationRun)
               .filter(MLEvaluationRun.model_version == version)
               .order_by(MLEvaluationRun.created_at.desc()).first())
        if run is None or not run.report_path:
            return {}
        try:
            with open(run.report_path) as f:
                return json.load(f).get("per_class", {})
        except Exception:
            return {}

    row = _model_or_404(db, model_id)
    prod = reg.production_model(db, row.task)
    cand_pc, prod_pc = _latest(row.version), _latest(prod.version) if prod else {}
    out = {}
    for lab in PROTECTED_CLASSES:
        cf, pf = (cand_pc.get(lab) or {}).get("f1"), (prod_pc.get(lab) or {}).get("f1")
        out[lab] = {"candidate_f1": cf, "production_f1": pf,
                    "delta": round(cf - pf, 4) if cf is not None and pf is not None else None,
                    "support": (cand_pc.get(lab) or {}).get("support")}
    return {"model_version": row.version,
            "production_version": prod.version if prod else None,
            "protected": out}


@router.get("/promotions/history")
def promotion_history(member=Depends(get_current_membership),
                      db: Session = Depends(get_db)):
    """Org-scoped promotion/rollback audit trail (newest first)."""
    from app.models import MLPromotionEvent

    rows = (db.query(MLPromotionEvent)
            .filter(MLPromotionEvent.organization_id == member.organization_id)
            .order_by(MLPromotionEvent.created_at.desc()).limit(200).all())
    return [{"model_name": r.model_name, "model_version": r.model_version,
             "from_status": r.from_status, "to_status": r.to_status,
             "reason": r.reason,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]
