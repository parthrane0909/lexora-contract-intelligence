"""Clause-classification inference (Phase 3.6).

Architecture: Clause → Tokenizer → Transformer → Classifier → Prediction → DB.
Pure Transformer inference — classification is NEVER delegated to an LLM.

Explicit, auditable errors: missing model bundle, empty clause text,
unknown clause, inference failure. The API maps these to status codes;
nothing degrades silently into a fake prediction.
"""
from __future__ import annotations

import json
import os
import uuid

import torch
from sqlalchemy.orm import Session

from app.models import AnalysisRun, Clause, ClauseClassification, Contract
from app.modules.audit.service import record


class ModelUnavailableError(Exception):
    """No trained model bundle at MODEL_DIR (train first: train.py)."""


class EmptyClauseError(Exception):
    """Clause has no text to classify."""


class InferenceError(Exception):
    """The model failed on a non-empty clause."""


def default_model_dir() -> str:
    from app.core.config import get_settings

    # Explicit production pin (Phase 12.16). Any failure falls back to
    # legacy behavior so inference never breaks because of the registry.
    try:
        from app.modules.ml_evaluation.registry import resolve_production_model_dir

        pinned = resolve_production_model_dir()
        if pinned:
            return pinned
    except Exception:
        pass
    return os.environ.get("MODEL_DIR") or get_settings().MODEL_DIR


def load_model_bundle(model_dir: str) -> dict:
    """Load tokenizer + fine-tuned model + label map. Shared by API and evaluate.py."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    info_path = os.path.join(model_dir, "model_info.json")
    map_path = os.path.join(model_dir, "label_map.json")
    if not (os.path.exists(info_path) and os.path.exists(map_path)):
        raise ModelUnavailableError(
            f"No trained model at {model_dir!r} (need model_info.json + label_map.json). "
            "Train one with: python -m app.modules.ml_nlp.train --help")
    labels = json.load(open(map_path))["labels"]
    info = json.load(open(info_path))
    try:
        tok = AutoTokenizer.from_pretrained(model_dir)
        model = AutoModelForSequenceClassification.from_pretrained(model_dir)
    except Exception as e:
        raise ModelUnavailableError(f"Could not load model from {model_dir!r}: {e}") from e
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()
    return {"model": model, "tokenizer": tok, "labels": labels,
            "model_version": info.get("model_version", "unknown"),
            "base_model": info.get("base_model", "unknown"),
            "seq_len": int(info.get("seq_len", 256))}


_CLASSIFIER: dict | None = None
_CLASSIFIER_DIR: str | None = None


def get_classifier(model_dir: str | None = None) -> dict:
    """Process-wide singleton (model stays in GPU/CPU memory across requests)."""
    global _CLASSIFIER, _CLASSIFIER_DIR
    d = model_dir or default_model_dir()
    if _CLASSIFIER is None or _CLASSIFIER_DIR != d:
        _CLASSIFIER = load_model_bundle(d)
        _CLASSIFIER_DIR = d
    return _CLASSIFIER


def reset_classifier() -> None:
    """Test hook: drop the cached bundle so MODEL_DIR switches take effect."""
    global _CLASSIFIER, _CLASSIFIER_DIR
    _CLASSIFIER, _CLASSIFIER_DIR = None, None


def classify_clause(text: str, model_dir: str | None = None) -> dict:
    """Classify one clause. Returns {category, confidence, probabilities, model_version}."""
    if not (text or "").strip():
        raise EmptyClauseError("Clause text is empty; nothing to classify.")
    b = get_classifier(model_dir)
    try:
        enc = b["tokenizer"](text, truncation=True, padding=True,
                             max_length=b["seq_len"], return_tensors="pt").to(b["model"].device)
        with torch.no_grad():
            probs = torch.softmax(b["model"](**enc).logits, dim=-1)[0].cpu().tolist()
    except Exception as e:
        raise InferenceError(f"Model inference failed: {type(e).__name__}") from e
    top = max(range(len(probs)), key=probs.__getitem__)
    return {"category": b["labels"][top], "confidence": float(probs[top]),
            "probabilities": {b["labels"][i]: float(p) for i, p in enumerate(probs)},
            "model_version": b["model_version"], "base_model": b["base_model"]}


def classify_contract(db: Session, contract_id: uuid.UUID, *, triggered_by: uuid.UUID | None,
                      model_dir: str | None = None) -> dict:
    """Classify every non-empty clause; persist rows (append-only) + run record."""
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    b = get_classifier(model_dir)  # raises ModelUnavailableError before any writes
    run = AnalysisRun(organization_id=contract.organization_id, contract_id=contract.id,
                      kind="clause_classification", model_name=b["base_model"],
                      model_version=b["model_version"], status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        clauses = db.query(Clause).filter(Clause.contract_id == contract.id)\
            .order_by(Clause.order_index).all()
        classified, skipped = 0, 0
        for cl in clauses:
            if not (cl.text or "").strip():
                skipped += 1
                continue
            pred = classify_clause(cl.text, model_dir or _CLASSIFIER_DIR)
            db.add(ClauseClassification(
                organization_id=contract.organization_id, contract_id=contract.id,
                clause_id=cl.id, analysis_run_id=run.id, model_name=b["base_model"],
                model_version=b["model_version"], predicted_category=pred["category"],
                confidence=pred["confidence"], probabilities=json.dumps(pred["probabilities"])))
            classified += 1
        run.status = "done"
        run.stats = json.dumps({"clauses": len(clauses), "classified": classified, "skipped": skipped})
        db.commit()
        record(db, action="contract.classified", organization_id=contract.organization_id,
               user_id=triggered_by, object_type="contract", object_id=str(contract.id),
               detail=f"{classified} clauses, model {b['model_version']}")
        from app.modules.risk_engine.state import refresh_analysis_status

        refresh_analysis_status(db, contract)
        return {"status": "done", "run_id": str(run.id), "clauses": len(clauses),
                "classified": classified, "skipped": skipped,
                "model_version": b["model_version"]}
    except Exception as e:
        db.rollback()
        run = db.get(AnalysisRun, run.id)
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"[:2000]
        db.commit()
        record(db, action="contract.classify_failed", organization_id=contract.organization_id,
               user_id=triggered_by, object_type="contract", object_id=str(contract.id),
               detail=run.error[:500])
        from app.modules.risk_engine.state import refresh_analysis_status

        refresh_analysis_status(db, contract)
        raise
