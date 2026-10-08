"""Classification metrics (Phase 12.5 + Phase 14 multi-seed/paired stats). Pure functions over label id lists —
no models, no DB. Averages use zero_division=0 and are reported explicitly;
nothing here implies legal correctness.
"""
from __future__ import annotations


def _safe(f, *args, **kwargs):
    from sklearn.exceptions import UndefinedMetricWarning
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UndefinedMetricWarning)
        return f(*args, **kwargs)


def per_class_metrics(y_true: list[int], y_pred: list[int],
                      labels: list[str]) -> dict[str, dict]:
    from sklearn.metrics import precision_recall_fscore_support

    idx = list(range(len(labels)))
    p, r, f, s = _safe(precision_recall_fscore_support,
                       y_true, y_pred, labels=idx, zero_division=0)
    return {labels[i]: {"precision": float(p[i]), "recall": float(r[i]),
                        "f1": float(f[i]), "support": int(s[i])}
            for i in range(len(labels))}


def summary_metrics(y_true: list[int], y_pred: list[int]) -> dict:
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

    if not y_true:
        raise ValueError("Cannot compute metrics on zero examples.")

    out = {"accuracy": float(_safe(accuracy_score, y_true, y_pred)), "n": len(y_true)}
    for avg in ("micro", "macro", "weighted"):
        out[avg] = {
            "precision": float(_safe(precision_score, y_true, y_pred, average=avg,
                                    zero_division=0)),
            "recall": float(_safe(recall_score, y_true, y_pred, average=avg,
                                  zero_division=0)),
            "f1": float(_safe(f1_score, y_true, y_pred, average=avg, zero_division=0)),
        }
    return out


def confusion_matrix(y_true: list[int], y_pred: list[int],
                     labels: list[str]) -> dict:
    from sklearn.metrics import confusion_matrix as _cm

    mat = _cm(y_true, y_pred, labels=list(range(len(labels)))).tolist()
    return {"labels": labels, "matrix": mat}


def full_report(y_true: list[int], y_pred: list[int],
                labels: list[str]) -> dict:
    """Everything the API/MLflow needs in one reproducible dict."""
    return {"summary": summary_metrics(y_true, y_pred),
            "per_class": per_class_metrics(y_true, y_pred, labels),
            "confusion": confusion_matrix(y_true, y_pred, labels)}


# ---------------------------------------------------------------------------
# Phase 14: multi-seed aggregation + paired statistical comparison live in
# multi_seed.py / statistical.py (spec module layout). Re-exported here so
# existing `metrics.*` call sites keep working with zero behavior change.
# ---------------------------------------------------------------------------
from app.modules.ml_evaluation.multi_seed import aggregate_runs  # noqa: F401
from app.modules.ml_evaluation.statistical import paired_compare  # noqa: F401