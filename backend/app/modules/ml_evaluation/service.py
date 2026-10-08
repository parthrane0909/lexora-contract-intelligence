"""Orchestration (Phase 12): dataset builds, evaluation, comparison, shadow,
lineage. Training itself lives in training.py; promotion in promotion.py;
registry in registry.py. This layer wires them to the DB with org scoping.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import uuid

from sqlalchemy.orm import Session

from app.models import MLEvaluationRun, MLModelVersion
from app.modules.ml_evaluation import calibration as calib_mod
from app.modules.ml_evaluation import error_analysis as err_mod
from app.modules.ml_evaluation import metrics as metrics_mod
from app.modules.ml_evaluation import registry as reg
from app.modules.ml_evaluation.dataset import build_examples
from app.modules.ml_evaluation.dataset_builder import build_versioned_dataset
from app.modules.ml_evaluation.promotion import check_gates
from app.modules.ml_nlp.taxonomy import MODEL_LABELS

ARTIFACT_BASE_ENV = "ML_EVAL_DIR"
DEFAULT_ARTIFACT_BASE = "./ml_artifacts/ml_evaluation"


def artifact_base() -> str:
    return os.environ.get(ARTIFACT_BASE_ENV) or DEFAULT_ARTIFACT_BASE


def build_dataset_version(db: Session, organization_id: uuid.UUID, *,
                          version: str, seed: int = 42) -> dict:
    """Curate feedback → immutable versioned artifact + MLDataset row."""
    from app.models import MLDataset

    if db.query(MLDataset).filter(
            MLDataset.organization_id == organization_id,
            MLDataset.version == version).first():
        raise ValueError(f"Dataset version {version!r} already exists.")
    examples, stats = build_examples(db, organization_id, tuple(MODEL_LABELS))
    built = build_versioned_dataset(
        examples, base_dir=os.path.join(artifact_base(), "datasets"),
        version=f"{organization_id.hex[:8]}_{version}", seed=seed,
        valid_labels=tuple(MODEL_LABELS))
    row = MLDataset(
        organization_id=organization_id, version=version,
        created_from="feedback", source_count=stats["total"],
        usable_count=built["manifest"]["usable_examples"],
        excluded_count=stats["total"] - built["manifest"]["usable_examples"],
        manifest=json.dumps({**built["manifest"], "org_stats": stats}),
        artifact_path=built["path"])
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": str(row.id), "version": version, "path": built["path"],
            "manifest": built["manifest"], "stats": stats,
            "leakage": built["leakage"], "split_counts": built["split_counts"]}


def _load_split_rows(dataset_path: str, split: str) -> list[dict]:
    if split not in ("train", "validation", "test"):
        raise ValueError(f"Unknown split {split!r}.")
    path = os.path.join(dataset_path, f"{split}.jsonl")
    if not os.path.exists(path):
        raise ValueError(f"Split file missing: {path}")
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def predict_bundle(model_dir: str, texts: list[str], seq_len: int = 128,
                   batch_size: int = 32) -> tuple[list[int], list[float], dict]:
    """Run a model bundle WITHOUT touching the production singleton.
    Returns (pred_idx, confidences, bundle_info)."""
    import torch

    from app.modules.ml_nlp.classifier import load_model_bundle

    bundle = load_model_bundle(model_dir)
    labels = bundle["labels"]
    seq_len = bundle.get("seq_len", seq_len) or seq_len
    model, tok = bundle["model"], bundle["tokenizer"]
    preds, confs = [], []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            enc = tok(texts[i:i + batch_size], truncation=True, padding=True,
                      max_length=seq_len, return_tensors="pt").to(model.device)
            probs = torch.softmax(model(**enc).logits, dim=-1).cpu()
            confs.extend(probs.max(dim=1).values.tolist())
            preds.extend(probs.argmax(dim=1).tolist())
    return preds, [float(c) for c in confs], {
        "labels": labels, "model_version": bundle["model_version"],
        "base_model": bundle["base_model"], "seq_len": seq_len}


def evaluate_bundle(db: Session, *, organization_id: uuid.UUID | None,
                    model_dir: str, dataset_path: str, split: str,
                    model_version: str | None = None,
                    batch_size: int = 32) -> dict:
    """Evaluate a bundle on a frozen split; persist MLEvaluationRun + report
    artifacts next to the model dir. Returns the full report."""
    rows = _load_split_rows(dataset_path, split)
    if not rows:
        raise ValueError(f"Split {split!r} is empty — nothing to evaluate.")
    from app.modules.ml_nlp.taxonomy import MODEL_LABELS as _LABELS

    labels = _LABELS
    label2id = {l: i for i, l in enumerate(labels)}
    bad = [r for r in rows if r["label"] not in label2id]
    if bad:
        raise ValueError(f"{len(bad)} rows carry labels outside MODEL_LABELS.")
    y_true = [label2id[r["label"]] for r in rows]
    preds, confs, info = predict_bundle(model_dir, [r["text"] for r in rows],
                                        batch_size=batch_size)
    correct = [p == t for p, t in zip(preds, y_true)]
    report = metrics_mod.full_report(y_true, preds, labels)
    report["model"] = {"artifact_dir": os.path.abspath(model_dir), **info}
    report["dataset"] = {"path": os.path.abspath(dataset_path), "split": split,
                         "n": len(rows)}
    report["calibration"] = {
        "stats": calib_mod.confidence_stats(confs, correct),
        "reliability": calib_mod.reliability(confs, correct)}
    refs = [{"clause_id": None, "contract_id": r.get("contract_id"),
             "text": r["text"][:300]} for r in rows]
    report["errors"] = err_mod.analyze(y_true, preds, labels, confs, refs)
    tok_lens = _token_lengths(model_dir, [r["text"] for r in rows], info["seq_len"])
    report["truncation"] = err_mod.truncation_report(tok_lens, info["seq_len"], correct)
    out_dir = os.path.join(model_dir, "eval_reports")
    os.makedirs(out_dir, exist_ok=True)
    tag = f"{split}"
    report_path = os.path.join(out_dir, f"report_{tag}.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    cm_path = os.path.join(out_dir, f"confusion_{tag}.csv")
    with open(cm_path, "w") as f:
        f.write("," + ",".join(labels) + "\n")
        for lab, row in zip(labels, report["confusion"]["matrix"]):
            f.write(lab + "," + ",".join(map(str, row)) + "\n")
    run = MLEvaluationRun(
        organization_id=organization_id, model_version=model_version or info["model_version"],
        dataset_version=_manifest_version(dataset_path), split=split,
        metrics=json.dumps(report["summary"]), report_path=report_path,
        confusion_path=cm_path)
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"evaluation_id": str(run.id), "report": report}


def _manifest_version(dataset_path: str) -> str | None:
    try:
        with open(os.path.join(dataset_path, "manifest.json")) as f:
            return json.load(f).get("dataset_version")
    except Exception:
        return None


def _token_lengths(model_dir: str, texts: list[str], seq_len: int) -> list[int]:
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir)
    return [len(tok.encode(t, truncation=False)) for t in texts]


def compare_models(db: Session, *, candidate_version: str,
                   dataset_path: str, split: str = "test",
                   protected=None, max_regression: float | None = None) -> dict:
    """Evaluate candidate vs pinned production on the SAME frozen split and
    run promotion gates. Advisory only — never promotes."""
    reg.ensure_baseline_registered(db)
    cand = db.query(MLModelVersion).filter(
        MLModelVersion.version == candidate_version).first()
    if cand is None or not cand.artifact_location:
        raise ValueError(f"Candidate {candidate_version!r} unknown or has no artifact.")
    prod = reg.production_model(db)
    prod_eval = evaluate_bundle(
        db, organization_id=None, model_dir=prod.artifact_location,
        dataset_path=dataset_path, split=split, model_version=prod.version) \
        if prod and prod.artifact_location else {"report": {}}
    cand_eval = evaluate_bundle(
        db, organization_id=None, model_dir=cand.artifact_location,
        dataset_path=dataset_path, split=split, model_version=cand.version)
    from app.modules.ml_evaluation.promotion import (
        MAX_PROTECTED_CLASS_REGRESSION,
        PROTECTED_CLASSES,
    )

    gates = check_gates(
        cand_eval["report"], prod_eval["report"], protected or PROTECTED_CLASSES,
        MAX_PROTECTED_CLASS_REGRESSION if max_regression is None else max_regression)
    return {"candidate": {"version": cand.version,
                          "evaluation_id": cand_eval["evaluation_id"],
                          "summary": cand_eval["report"]["summary"]},
            "production": {"version": prod.version if prod else None,
                           "summary": prod_eval["report"].get("summary", {})},
            "gates": gates}


def shadow_evaluate(model_dir: str, texts: list[str],
                    production_texts: list[str] | None = None) -> dict:
    """Candidate vs production predictions side-by-side. Writes nothing to
    production tables; labels every row explicitly."""
    from app.modules.ml_nlp.classifier import default_model_dir

    prod_dir = default_model_dir()
    prod_preds, prod_confs, prod_info = predict_bundle(prod_dir, texts)
    cand_preds, cand_confs, cand_info = predict_bundle(model_dir, texts)
    pl, cl = prod_info["labels"], cand_info["labels"]
    return {"production_prediction": [
        {"category": pl[i], "confidence": round(c, 4)} for i, c in zip(prod_preds, prod_confs)],
        "candidate_prediction": [
        {"category": cl[i], "confidence": round(c, 4)} for i, c in zip(cand_preds, cand_confs)],
        "production_model": prod_info["model_version"],
        "candidate_model": cand_info["model_version"],
        "note": "Shadow output only — production predictions unchanged."}


def lineage(db: Session, *, organization_id: uuid.UUID,
            dataset_version: str | None = None,
            model_version: str | None = None) -> dict:
    """feedback → dataset → split → runs → models → promotion, by identifiers."""
    from app.models import MLDataset, MLPromotionEvent

    chain: dict = {"datasets": [], "models": [], "evaluations": [], "promotions": []}
    dq = db.query(MLDataset).filter(MLDataset.organization_id == organization_id)
    if dataset_version:
        dq = dq.filter(MLDataset.version == dataset_version)
    for d in dq.all():
        man = {}
        try:
            man = json.loads(d.manifest or "{}")
        except Exception:
            pass
        chain["datasets"].append({
            "version": d.version, "source_count": d.source_count,
            "usable_count": d.usable_count, "artifact_path": d.artifact_path,
            "splits_sha256": (man.get("splits_sha256") or "")[:16],
            "created_at": d.created_at.isoformat() if d.created_at else None})
    mq = db.query(MLModelVersion)
    if model_version:
        mq = mq.filter(MLModelVersion.version == model_version)
    for m in mq.all():
        chain["models"].append({
            "name": m.name, "version": m.version, "status": m.status,
            "dataset_version": m.dataset_version, "base_model": m.base_model,
            "artifact_location": m.artifact_location,
            "mlflow_run_id": m.mlflow_run_id})
    eq = db.query(MLEvaluationRun).filter(
        (MLEvaluationRun.organization_id == organization_id) |
        (MLEvaluationRun.organization_id.is_(None)))
    if model_version:
        eq = eq.filter(MLEvaluationRun.model_version == model_version)
    for e in eq.order_by(MLEvaluationRun.created_at.desc()).all():
        chain["evaluations"].append({
            "id": str(e.id), "model_version": e.model_version,
            "dataset_version": e.dataset_version, "split": e.split,
            "report_path": e.report_path})
    pq = db.query(MLPromotionEvent)
    if model_version:
        pq = pq.filter(MLPromotionEvent.model_version == model_version)
    for p in pq.order_by(MLPromotionEvent.created_at.desc()).all():
        chain["promotions"].append({
            "model": f"{p.model_name}/{p.model_version}",
            "from_status": p.from_status, "to_status": p.to_status,
            "decided_by": str(p.decided_by) if p.decided_by else None,
            "created_at": p.created_at.isoformat() if p.created_at else None})
    return chain


def dataset_fingerprint(path: str) -> str:
    h = hashlib.sha256()
    for name in ("examples.jsonl", "train.jsonl", "validation.jsonl",
                 "test.jsonl", "splits.json", "manifest.json"):
        p = os.path.join(path, name)
        if os.path.exists(p):
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()[:16]
