"""Held-out evaluation for the clause classifier (Phase 3.5).

Reports only measured numbers: accuracy, micro/macro/weighted
precision/recall/F1, per-class P/R/F1/support, confusion matrix.
Run on test.jsonl AFTER all tuning decisions are frozen.

Usage:
  python -m app.modules.ml_nlp.evaluate --model-dir ml_artifacts/clause_classifier_v1 \\
      --split ml/data/clause_v1/test.jsonl --out ml_artifacts/clause_classifier_v1/test_metrics.json
"""
from __future__ import annotations

import argparse
import csv
import json

import torch


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate clause classifier on a held-out split")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from app.modules.ml_nlp.classifier import load_model_bundle

    bundle = load_model_bundle(args.model_dir)
    model: AutoModelForSequenceClassification = bundle["model"]
    tok: AutoTokenizer = bundle["tokenizer"]
    labels: list[str] = bundle["labels"]
    seq_len: int = bundle.get("seq_len", 256)
    model.eval()

    with open(args.split) as f:
        rows = [json.loads(line) for line in f if line.strip()]

    from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,
                                  precision_recall_fscore_support)

    y_true, y_pred = [], []
    for i in range(0, len(rows), args.batch):
        batch = rows[i:i + args.batch]
        enc = tok([r["text"] for r in batch], truncation=True, padding=True,
                  max_length=seq_len, return_tensors="pt").to(model.device)
        with torch.no_grad():
            logits = model(**enc).logits
        y_pred.extend(logits.argmax(-1).cpu().tolist())
        y_true.extend(labels.index(r["label"]) for r in batch)

    acc = accuracy_score(y_true, y_pred)
    per_p, per_r, per_f, support = precision_recall_fscore_support(
        y_true, y_pred, labels=list(range(len(labels))), zero_division=0)
    cm = confusion_matrix(y_true, y_pred, labels=list(range(len(labels)))).tolist()
    metrics = {
        "n": len(rows),
        "accuracy": acc,
        "micro": {"p": float(sum(p == t for p, t in zip(y_pred, y_true)) / len(rows)),
                  "r": float(sum(p == t for p, t in zip(y_pred, y_true)) / len(rows)),
                  "f1": float(f1_score(y_true, y_pred, average="micro", zero_division=0))},
        "macro": {"f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0))},
        "weighted": {"f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0))},
        "per_class": {labels[i]: {"precision": float(per_p[i]), "recall": float(per_r[i]),
                                   "f1": float(per_f[i]), "support": int(support[i])}
                      for i in range(len(labels))},
    }
    out = args.out or "metrics.json"
    with open(out, "w") as f:
        json.dump(metrics, f, indent=2)
    with open(out.replace(".json", "_confusion.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["true/pred"] + labels)
        for i, row in enumerate(cm):
            w.writerow([labels[i]] + row)

    print(f"n={len(rows)} accuracy={acc:.4f} macro_f1={metrics['macro']['f1']:.4f} "
          f"micro_f1={metrics['micro']['f1']:.4f} weighted_f1={metrics['weighted']['f1']:.4f}")
    print(f"{'class':22s} {'P':>7s} {'R':>7s} {'F1':>7s} {'sup':>6s}")
    for lab in labels:
        m = metrics["per_class"][lab]
        print(f"{lab:22s} {m['precision']:7.3f} {m['recall']:7.3f} {m['f1']:7.3f} {m['support']:6d}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
