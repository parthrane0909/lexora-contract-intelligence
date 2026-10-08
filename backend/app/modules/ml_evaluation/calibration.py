"""Confidence & calibration analysis (Phase 12.7). Pure functions.

Reports whether classifier confidence is meaningful — it never changes
production thresholds, and nothing here claims confidence equals legal
certainty. ECE uses equal-width bins over max-softmax probabilities.
"""
from __future__ import annotations


def confidence_stats(confidences: list[float], correct: list[bool]) -> dict:
    import statistics

    out = {"n": len(confidences),
           "mean": float(statistics.fmean(confidences)) if confidences else 0.0,
           "min": float(min(confidences)) if confidences else 0.0,
           "max": float(max(confidences)) if confidences else 0.0}
    hist = [0] * 10
    for c in confidences:
        hist[min(int(c * 10), 9)] += 1
    out["histogram_10"] = hist
    cc = [c for c, ok in zip(confidences, correct) if ok]
    ic = [c for c, ok in zip(confidences, correct) if not ok]
    out["mean_correct"] = float(statistics.fmean(cc)) if cc else None
    out["mean_incorrect"] = float(statistics.fmean(ic)) if ic else None
    return out


def reliability(confidences: list[float], correct: list[bool],
                n_bins: int = 10) -> dict:
    """Per-bin accuracy vs mean confidence + Expected Calibration Error."""
    bins = [{"n": 0, "correct": 0, "conf_sum": 0.0} for _ in range(n_bins)]
    for c, ok in zip(confidences, correct):
        i = min(int(c * n_bins), n_bins - 1)
        bins[i]["n"] += 1
        bins[i]["correct"] += 1 if ok else 0
        bins[i]["conf_sum"] += c
    rows, ece, total = [], 0.0, len(confidences) or 1
    for i, b in enumerate(bins):
        if not b["n"]:
            rows.append({"bin": [round(i / n_bins, 2), round((i + 1) / n_bins, 2)],
                         "n": 0, "accuracy": None, "mean_confidence": None})
            continue
        acc = b["correct"] / b["n"]
        mean_c = b["conf_sum"] / b["n"]
        ece += abs(acc - mean_c) * (b["n"] / total)
        rows.append({"bin": [round(i / n_bins, 2), round((i + 1) / n_bins, 2)],
                     "n": b["n"], "accuracy": round(acc, 4),
                     "mean_confidence": round(mean_c, 4)})
    return {"bins": rows, "ece": round(ece, 4), "n_bins": n_bins}
