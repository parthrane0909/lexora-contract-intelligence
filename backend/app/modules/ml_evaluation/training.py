"""Candidate training pipeline (Phase 12.8). Same Legal-BERT architecture
as Phase 3 — never an LLM. Reuses train.py's data/weighting pieces; the
orchestration (config validation, fingerprinting, MLflow candidates
experiment, Phase-12 artifact layout) lives here and is unit-testable
without GPUs via an injectable trainer core.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field

CANDIDATE_EXPERIMENT = "clause-classification-candidates"


@dataclass
class CandidateConfig:
    base_model: str = "nlpaueb/legal-bert-base-uncased"
    dataset_version: str = ""
    learning_rate: float = 2e-5
    batch_size: int = 8
    epochs: float = 3
    seq_len: int = 128
    seed: int = 42
    class_weighting: str = "balanced"
    weight_decay: float = 0.01
    fp16: bool = True
    model_version: str = ""
    task: str = "clause_classification"

    def validate(self) -> "CandidateConfig":
        if not self.dataset_version:
            raise ValueError("dataset_version is required")
        if not self.model_version:
            raise ValueError("model_version is required")
        if not (0 < self.learning_rate < 1):
            raise ValueError(f"learning_rate out of range: {self.learning_rate}")
        if self.batch_size < 1 or self.batch_size > 512:
            raise ValueError(f"batch_size out of range: {self.batch_size}")
        if not (0 < self.epochs <= 50):
            raise ValueError(f"epochs out of range: {self.epochs}")
        if not (8 <= self.seq_len <= 2048):
            raise ValueError(f"seq_len out of range: {self.seq_len}")
        if self.class_weighting not in ("balanced", "none"):
            raise ValueError(f"class_weighting must be balanced|none")
        if not self.base_model or "/" not in self.base_model:
            raise ValueError(f"base_model looks invalid: {self.base_model!r}")
        return self


def config_fingerprint(cfg: CandidateConfig) -> str:
    blob = json.dumps(asdict(cfg), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def mlflow_log_artifacts_enabled() -> bool:
    """Full per-run artifact upload switch. Default ON (historical behavior);
    PHASE14_MLFLOW_LOG_ARTIFACTS=0 disables the ~1.7GB duplication while
    params/metrics/tags logging continues unchanged. Local canonical
    artifacts are always written regardless."""
    return os.environ.get("PHASE14_MLFLOW_LOG_ARTIFACTS", "1") != "0"


def train_candidate(cfg: CandidateConfig, data_dir: str, out_dir: str,
                    mlflow_uri: str, _fit=None) -> dict:
    """Run training (real _fit by default; injectable for tests). Returns
    metadata dict; artifact layout mirrors Phase 3 + candidate provenance."""
    cfg.validate()
    started = time.monotonic()
    fit = _fit or _fit_transformer
    result = fit(cfg, data_dir, out_dir, mlflow_uri)
    result["duration_s"] = round(time.monotonic() - started, 1)
    result["config_fingerprint"] = config_fingerprint(cfg)
    return result


def _fit_transformer(cfg: CandidateConfig, data_dir: str, out_dir: str,
                     mlflow_uri: str) -> dict:
    import mlflow
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer)

    from app.modules.ml_nlp import train as phase3
    from app.modules.ml_nlp.taxonomy import MODEL_LABELS

    train_rows = phase3.load_rows(os.path.join(data_dir, "train.jsonl"))
    val_rows = phase3.load_rows(os.path.join(data_dir, "validation.jsonl"))
    label2id = {l: i for i, l in enumerate(MODEL_LABELS)}
    bad = [r["label"] for r in train_rows + val_rows if r["label"] not in label2id]
    if bad:
        raise ValueError(f"Dataset labels outside MODEL_LABELS: {sorted(set(bad))}")
    tok = AutoTokenizer.from_pretrained(cfg.base_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        cfg.base_model, num_labels=len(MODEL_LABELS))

    def tok_rows(rows):
        enc = tok([r["text"] for r in rows], truncation=True, padding=False,
                  max_length=cfg.seq_len)
        enc["labels"] = [label2id[r["label"]] for r in rows]
        return enc

    from datasets import Dataset
    from transformers import Trainer as HFTrainer
    from transformers import TrainingArguments as HFTA

    train_ds = Dataset.from_dict(tok_rows(train_rows))
    val_ds = Dataset.from_dict(tok_rows(val_rows))
    weights = (phase3.compute_class_weights(train_ds["labels"], len(MODEL_LABELS))
               if cfg.class_weighting == "balanced" else None)
    from torch.nn import CrossEntropyLoss

    class WeightedTrainer(HFTrainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            ce = (CrossEntropyLoss(weight=weights.to(model.device))
                  if weights is not None else CrossEntropyLoss())
            loss = ce(outputs.logits, labels)
            return (loss, outputs) if return_outputs else loss

    def compute_metrics(p):
        import numpy as np
        from sklearn.metrics import accuracy_score, f1_score

        preds = np.argmax(p.predictions, axis=1)
        return {"accuracy": accuracy_score(p.label_ids, preds),
                "macro_f1": f1_score(p.label_ids, preds, average="macro",
                                     zero_division=0)}

    import torch

    targs = HFTA(
        output_dir=os.path.join(out_dir, "checkpoints"),
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="macro_f1", num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.batch_size,
        per_device_eval_batch_size=cfg.batch_size * 4,
        learning_rate=cfg.learning_rate, weight_decay=cfg.weight_decay,
        fp16=cfg.fp16 and torch.cuda.is_available(),
        seed=cfg.seed, logging_steps=50, save_total_limit=1, report_to="none")
    trainer = WeightedTrainer(model=model, args=targs, train_dataset=train_ds,
                              eval_dataset=val_ds, compute_metrics=compute_metrics,
                              processing_class=tok)
    mlflow.set_tracking_uri(mlflow_uri)
    mlflow.set_experiment(CANDIDATE_EXPERIMENT)
    with mlflow.start_run(run_name=cfg.model_version) as run:
        mlflow.log_params({**asdict(cfg), "labels": ",".join(MODEL_LABELS),
                           "n_train": len(train_rows), "n_val": len(val_rows)})
        mlflow.set_tag("phase", "12-candidate")
        trainer.train()
        val_metrics = trainer.evaluate()
        mlflow.log_metrics({f"val_{k.replace('eval_', '')}": float(v)
                            for k, v in val_metrics.items() if isinstance(v, (int, float))})
        os.makedirs(out_dir, exist_ok=True)
        trainer.save_model(out_dir)
        tok.save_pretrained(out_dir)
        with open(os.path.join(out_dir, "label_map.json"), "w") as f:
            json.dump({"labels": MODEL_LABELS}, f, indent=2)
        info = {"base_model": cfg.base_model, "model_version": cfg.model_version,
                "labels": MODEL_LABELS, "seq_len": cfg.seq_len,
                "dataset_version": cfg.dataset_version,
                "candidate_config": asdict(cfg),
                "mlflow_run_id": run.info.run_id,
                "trained_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        with open(os.path.join(out_dir, "model_info.json"), "w") as f:
            json.dump(info, f, indent=2)
        # Full artifact upload is opt-out (see mlflow_log_artifacts_enabled):
        # PHASE14_MLFLOW_LOG_ARTIFACTS=0 skips duplicating the ~1.7GB training
        # dir into MLflow (params/metrics/tags are always logged). The canonical
        # artifact still lands in out_dir and is registered locally.
        if mlflow_log_artifacts_enabled():
            mlflow.log_artifacts(out_dir, artifact_path="model")
        else:
            mlflow.set_tag("artifacts", "local-only")
        return {"model_version": cfg.model_version, "artifact_dir": out_dir,
                "val_metrics": {k.replace("eval_", ""): float(v)
                                for k, v in val_metrics.items()
                                if isinstance(v, (int, float))},
                "mlflow_run_id": run.info.run_id}
