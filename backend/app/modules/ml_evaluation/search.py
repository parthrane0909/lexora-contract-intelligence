"""Sensitivity summaries over validation evidence (Phase 14.6/14.7).

Small, controlled comparisons only — learning-rate multipliers and epoch
counts on the SAME dataset/seed. This is sensitivity analysis, not HPO:
no test data is involved, and the output recommends nothing by itself
(final_selection decides, then one frozen-test run).
"""
from __future__ import annotations


def summarize_lr_sensitivity(validation_reports: list[dict]) -> dict:
    """Each item: {lr, lr_multiplier, report}. Returns ordered comparison."""
    rows = []
    for item in validation_reports:
        rep = item.get("report", {})
        s = rep.get("summary", {})
        rows.append({
            "lr": item.get("lr"),
            "lr_multiplier": item.get("lr_multiplier"),
            "val_accuracy": (s.get("accuracy")),
            "val_macro_f1": (s.get("macro") or {}).get("f1"),
            "val_weighted_f1": (s.get("weighted") or {}).get("f1"),
        })
    base = next((r for r in rows if r["lr_multiplier"] in (1, 1.0, None)), None)
    out = {"rows": sorted(rows, key=lambda r: (r["lr"] or 0)),
           "baseline_lr": base["lr"] if base else None,
           "note": "Validation evidence only. Sensitivity, not a search: "
                   "no test data was consulted."}
    return out


def summarize_epoch_sensitivity(validation_reports: list[dict]) -> dict:
    """Each item: {epochs, report}. Flags whether more budget helped."""
    rows = []
    for item in validation_reports:
        rep = item.get("report", {})
        s = rep.get("summary", {})
        rows.append({
            "epochs": item.get("epochs"),
            "val_accuracy": s.get("accuracy"),
            "val_macro_f1": (s.get("macro") or {}).get("f1"),
        })
    rows.sort(key=lambda r: (r["epochs"] or 0))
    macros = [r["val_macro_f1"] for r in rows if r["val_macro_f1"] is not None]
    return {"rows": rows,
            "monotonic_gain": all(b >= a for a, b in zip(macros, macros[1:]))
            if len(macros) > 1 else None,
            "note": "Validation evidence only. More epochs cost more; "
                    "gains must clear protected-class gates to matter."}
