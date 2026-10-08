"""Paired statistical comparison (Phase 14.4). Same seeds ⇒ paired design:
compare per-seed differences, not independent means.

Reports mean difference, std of differences, min/max improvement, and a 95%
CI for the mean difference using the paired t-interval (exact t critical
values hardcoded for df<=30, normal fallback beyond). With n=5 the interval
is honestly wide — that is the point. A CI excluding zero is reported as
"consistent directional evidence", never as proof, and never as legal
significance. The t-interval assumes approximately normal differences; with
tiny n this is stated as a limitation, not a guarantee.
"""
from __future__ import annotations

import math
import statistics

# Two-sided t_{0.975} critical values for df=1..30.
_T975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


def _metric_of(report: dict, scope: str):
    s = report.get("summary", {})
    if scope == "accuracy":
        return s.get("accuracy")
    return (s.get(scope) or {}).get("f1")


def paired_compare(baseline: dict[str, dict], candidate: dict[str, dict],
                   scope: str = "macro") -> dict:
    """Inputs map seed -> full_report dict. Paired t-interval on per-seed
    differences; requires >=2 shared seeds."""
    seeds = sorted(set(baseline) & set(candidate))
    if len(seeds) < 2:
        raise ValueError("Need >=2 shared seeds for a paired comparison.")
    diffs = []
    for sd in seeds:
        b, c = _metric_of(baseline[sd], scope), _metric_of(candidate[sd], scope)
        if b is None or c is None:
            raise ValueError(f"Missing {scope} metric for seed {sd}.")
        diffs.append(float(c) - float(b))
    n = len(diffs)
    mean = statistics.fmean(diffs)
    std = statistics.pstdev(diffs) if n > 1 else 0.0
    tcrit = _T975.get(n - 1, 1.96)
    half = tcrit * (std / math.sqrt(n)) if n > 1 else 0.0
    return {
        "scope": scope,
        "seeds": seeds,
        "n": n,
        "mean_difference": round(mean, 4),
        "std_difference": round(std, 4),
        "min_improvement": round(min(diffs), 4),
        "max_improvement": round(max(diffs), 4),
        "ci95": [round(mean - half, 4), round(mean + half, 4)],
        "ci_excludes_zero": (mean - half) > 0,
        "method": "paired t-interval on per-seed differences "
                  f"(t_{{0.975,df={n - 1}}}={tcrit}); appropriate because the "
                  "same seeds pair baseline and candidate runs",
    }
