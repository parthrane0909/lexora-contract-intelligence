"""False-negative-first error analysis (Phase 12.6). Only patterns present
in the evaluation data are reported — never fabricated. Prioritizes misses
on important categories without claiming legal-harm probability.
"""
from __future__ import annotations

from collections import Counter


def analyze(y_true: list[int], y_pred: list[int], labels: list[str],
            confidences: list[float] | None = None,
            refs: list[dict] | None = None,
            focus: tuple[str, ...] = ("renewal", "termination", "warranty",
                                      "confidentiality", "liability",
                                      "indemnification", "governing_law",
                                      "payment")) -> dict:
    confidences = confidences or [0.0] * len(y_true)
    refs = refs or [{}] * len(y_true)
    per_class: dict[str, dict] = {}
    pair_counts: Counter[tuple[str, str]] = Counter()
    for i, lab in enumerate(labels):
        fp, fn = [], []
        for j in range(len(y_true)):
            if y_pred[j] == i and y_true[j] != i:
                fp.append(j)
            elif y_true[j] == i and y_pred[j] != i:
                fn.append(j)
                pair_counts[(labels[y_true[j]], labels[y_pred[j]])] += 1
        per_class[lab] = {
            "false_positives": len(fp), "false_negatives": len(fn),
            "fp_conf_mean": _mean([confidences[j] for j in fp]),
            "fn_conf_mean": _mean([confidences[j] for j in fn]),
            "fn_examples": [
                {"index": j, "predicted": labels[y_pred[j]],
                 "confidence": round(confidences[j], 4),
                 **{k: refs[j].get(k) for k in
                    ("clause_id", "contract_id", "text") if refs[j].get(k) is not None}}
                for j in fn[:10]],
        }
    pairs = [{"actual": a, "predicted": p, "count": c}
             for (a, p), c in pair_counts.most_common(15)]
    focus_misses = {lab: per_class[lab]["false_negatives"]
                    for lab in focus if lab in per_class}
    return {"per_class": per_class,
            "misclassification_pairs": pairs,
            "focus_false_negatives": focus_misses,
            "total_errors": sum(1 for j in range(len(y_true)) if y_true[j] != y_pred[j])}


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def truncation_report(token_lengths: list[int], max_len: int,
                      correct: list[bool]) -> dict:
    """How much input exceeds the classifier's sequence length, and whether
    truncated examples err more often (comparison, not a change mandate)."""
    over = [i for i, L in enumerate(token_lengths) if L > max_len]
    import statistics

    def rate(idxs):
        return round(sum(1 for i in idxs if not correct[i]) / len(idxs), 4) if idxs else None

    all_idx = list(range(len(token_lengths)))
    under = [i for i in all_idx if i not in set(over)]
    dist = sorted(token_lengths)
    q = lambda p: dist[min(int(p * len(dist)), len(dist) - 1)] if dist else 0
    return {"max_len": max_len, "n": len(token_lengths),
            "n_truncated": len(over),
            "pct_truncated": round(len(over) / len(token_lengths), 4) if token_lengths else 0.0,
            "length_p50": q(0.5), "length_p90": q(0.9), "length_max": max(dist) if dist else 0,
            "error_rate_truncated": rate(over), "error_rate_untruncated": rate(under)}
