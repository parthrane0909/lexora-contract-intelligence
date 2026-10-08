"""Fine-tune the Transformer clause classifier (Phase 3.2).

Clause text → tokenizer → Transformer → 14-way softmax. Class-weighted loss
handles the long tail (LEDGAR is imbalanced; caps alone don't fix it).

Every run is logged to MLflow (params, metrics, artifacts) and saves:
  model.safetensors/config, tokenizer, label_map.json, model_info.json
so inference and evaluation can reproduce it exactly.

Protocol: train here, tune on validation, report FINAL numbers from
evaluate.py on the held-out test split (never peek during development).

Usage:
  python -m app.modules.ml_nlp.train --data ml/data/clause_v1 \\
      --out ml_artifacts/clause_classifier_v1 --epochs 3 --model-version ledgar15-cuad-v1
"""
from __future__ import annotations

import argparse
import datetime
import json
import os

import torch


def load_rows(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def compute_class_weights(labels: list[int], n_classes: int) -> torch.Tensor:
    from collections import Counter

    counts = Counter(labels)
    total = len(labels)
    return torch.tensor([total / (n_classes * counts.get(i, 1)) for i in range(n_classes)],
                        dtype=torch.float)


def main() -> None:
    ap = argparse.ArgumentParser(description="Train clause classifier")
    ap.add_argument("--data", required=True, help="dataset dir with train/validation.jsonl + manifest")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model-name", default=os.environ.get("TRAIN_MODEL_NAME", "nlpaueb/legal-bert-base-uncased"))
    ap.add_argument("--model-version", default="ledgar15-cuad-v1")
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--seq-len", type=int, default=128)
    ap.add_argument("--weight-decay", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--class-weight", choices=["balanced", "none"], default="balanced")
    ap.add_argument("--fp16", action="store_true", default=True)
    ap.add_argument("--no-fp16", dest="fp16", action="store_false")
    ap.add_argument("--mlflow-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "./mlruns"))
    args = ap.parse_args()

    import mlflow
    from transformers import (AutoTokenizer, AutoModelForSequenceClassification,
                               Trainer, TrainingArguments)

    from app.modules.ml_nlp.taxonomy import MODEL_LABELS

    train_rows = load_rows(os.path.join(args.data, "train.jsonl"))
    val_rows = load_rows(os.path.join(args.data, "validation.jsonl"))
    manifest = json.load(open(os.path.join(args.data, "manifest.json")))
    label2id = {l: i for i, l in enumerate(MODEL_LABELS)}
    assert all(r["label"] in label2id for r in train_rows + val_rows), "dataset has labels outside MODEL_LABELS"

    tok = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name, num_labels=len(MODEL_LABELS))

    def tok_rows(rows):
        enc = tok([r["text"] for r in rows], truncation=True, padding=False, max_length=args.seq_len)
        enc["labels"] = [label2id[r["label"]] for r in rows]
        return enc

    from datasets import Dataset
    train_ds = Dataset.from_dict(tok_rows(train_rows))
    val_ds = Dataset.from_dict(tok_rows(val_rows))

    weights = (compute_class_weights(train_ds["labels"], len(MODEL_LABELS))
               if args.class_weight == "balanced" else None)

    from torch.nn import CrossEntropyLoss

    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            loss = (CrossEntropyLoss(weight=weights.to(model.device))(outputs.logits, labels)
                    if weights is not None else CrossEntropyLoss()(outputs.logits, labels))
            return (loss, outputs) if return_outputs else loss

    def compute_metrics(p):
        import numpy as np
        from sklearn.metrics import accuracy_score, f1_score

        preds = np.argmax(p.predictions, axis=1)
        return {"accuracy": accuracy_score(p.label_ids, preds),
                "macro_f1": f1_score(p.label_ids, preds, average="macro", zero_division=0)}

    targs = TrainingArguments(
        output_dir=os.path.join(args.out, "checkpoints"),
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="macro_f1", num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch, per_device_eval_batch_size=args.batch * 4,
        learning_rate=args.lr, weight_decay=args.weight_decay, fp16=args.fp16,
        seed=args.seed, logging_steps=50, save_total_limit=1, report_to="none",
    )
    trainer = WeightedTrainer(model=model, args=targs, train_dataset=train_ds,
                              eval_dataset=val_ds, compute_metrics=compute_metrics,
                              processing_class=tok)

    mlflow.set_tracking_uri(args.mlflow_uri)
    mlflow.set_experiment("clause-classification")
    with mlflow.start_run(run_name=args.model_version) as run:
        mlflow.log_params({"base_model": args.model_name, "model_version": args.model_version,
                           "epochs": args.epochs, "lr": args.lr, "batch": args.batch,
                           "seq_len": args.seq_len, "weight_decay": args.weight_decay,
                           "seed": args.seed, "class_weight": args.class_weight,
                           "dataset_version": manifest.get("dataset_version"),
                           "train_cap": manifest.get("train_cap"),
                           "n_train": len(train_rows), "n_val": len(val_rows),
                           "labels": ",".join(MODEL_LABELS)})
        trainer.train()
        val_metrics = trainer.evaluate()
        mlflow.log_metrics({f"val_{k.replace('eval_', '')}": float(v)
                            for k, v in val_metrics.items() if isinstance(v, (int, float))})

        os.makedirs(args.out, exist_ok=True)
        trainer.save_model(args.out)
        tok.save_pretrained(args.out)
        with open(os.path.join(args.out, "label_map.json"), "w") as f:
            json.dump({"labels": MODEL_LABELS}, f, indent=2)
        info = {"base_model": args.model_name, "model_version": args.model_version,
                "labels": MODEL_LABELS, "seq_len": args.seq_len,
                "dataset_version": manifest.get("dataset_version"),
                "mlflow_run_id": run.info.run_id,
                "trained_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        with open(os.path.join(args.out, "model_info.json"), "w") as f:
            json.dump(info, f, indent=2)
        mlflow.log_artifacts(args.out, artifact_path="model")
        print(json.dumps({"val": val_metrics, "out": args.out, "run": run.info.run_id}, indent=2))


if __name__ == "__main__":
    main()
