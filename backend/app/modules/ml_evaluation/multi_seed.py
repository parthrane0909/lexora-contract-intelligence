"""Multi-seed aggregation (Phase 14.2/14.3). Pure functions over per-seed
metric dicts: mean/std/min/max per metric, per-class mean/std where present.
No training, no DB — inputs are measured reports, never claims.
"""
from __future__ import annotations

import statistics


def aggregate_runs(run_metrics: list[dict]) -> dict:
    """Each item: {"seed": int, "summary": {...}, "per_class": {...}}."""
    if not run_metrics:
        raise ValueError("No runs to aggregate.")
    out: dict = {"n_runs": len(run_metrics),
                 "seeds": sorted(r["seed"] for r in run_metrics)}
    for scope in ("accuracy", "micro", "macro", "weighted"):
        vals = []
        for r in run_metrics:
            s = r.get("summary", {})
            v = s.get(scope) if scope == "accuracy" else (s.get(scope) or {}).get("f1")
            if v is not None:
                vals.append(float(v))
        out[scope] = _describe(vals)
    classes: dict[str, list[float]] = {}
    for r in run_metrics:
        for lab, m in (r.get("per_class") or {}).items():
            if m and m.get("f1") is not None:
                classes.setdefault(lab, []).append(float(m["f1"]))
    out["per_class"] = {lab: _describe(v) for lab, v in sorted(classes.items())}
    return out


def _describe(vals: list[float]) -> dict:
    if not vals:
        return {"mean": None, "std": None, "min": None, "max": None, "n": 0}
    return {
        "mean": round(statistics.fmean(vals), 4),
        "std": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "n": len(vals),
    }
