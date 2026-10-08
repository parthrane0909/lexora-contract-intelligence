"""Promotion gates (Phase 12.13/12.14). Deterministic, configurable, loud.

A candidate passes ONLY if every gate passes. Higher macro F1 alone is
never sufficient. Protected classes (important supported categories) may
regress by at most MAX_PROTECTED_CLASS_REGRESSION (default 0.03).

Gates never promote: promote() below is the ONLY path to production, it
requires admin, and it records an auditable event. Training/evaluation
output ("passes gates") is advisory, not an action.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import MLModelVersion, MLPromotionEvent
from app.modules.ml_evaluation import registry as reg

from app.modules.ml_evaluation.protected_classes import (
    MAX_PROTECTED_CLASS_REGRESSION,
    PROTECTED_CLASSES,
)


def check_gates(candidate_metrics: dict, production_metrics: dict,
                protected: tuple[str, ...] = PROTECTED_CLASSES,
                max_regression: float = MAX_PROTECTED_CLASS_REGRESSION) -> dict:
    """Compare two full_report-style metric dicts. Returns
    {approved, gates: [{name, passed, detail}]}."""
    gates = []

    def f1(m, scope):
        try:
            return float(m["summary"][scope]["f1"])
        except (KeyError, TypeError):
            return None

    cm, pm = f1(candidate_metrics, "macro"), f1(production_metrics, "macro")
    gates.append({
        "name": "macro_f1_no_regression",
        "passed": cm is not None and pm is not None and cm >= pm,
        "detail": f"candidate macro F1 {cm} vs production {pm}",
    })
    cw, pw = f1(candidate_metrics, "weighted"), f1(production_metrics, "weighted")
    gates.append({
        "name": "weighted_f1_no_regression",
        "passed": cw is not None and pw is not None and cw >= pw,
        "detail": f"candidate weighted F1 {cw} vs production {pw}",
    })
    cp = (candidate_metrics.get("per_class") or {})
    pp = (production_metrics.get("per_class") or {})
    worst = None
    for lab in protected:
        cf = (cp.get(lab) or {}).get("f1")
        pf = (pp.get(lab) or {}).get("f1")
        if cf is None or pf is None:
            gates.append({"name": f"protected_{lab}", "passed": False,
                          "detail": f"missing per-class F1 for {lab!r}"})
            continue
        delta = cf - pf
        gates.append({"name": f"protected_{lab}", "passed": delta >= -max_regression,
                      "detail": f"{lab}: candidate F1 {round(cf, 4)} vs production "
                                f"{round(pf, 4)} (delta {round(delta, 4)}, "
                                f"tolerance -{max_regression})"})
        if worst is None or delta < worst[1]:
            worst = (lab, delta)
    approved = all(g["passed"] for g in gates)
    return {"approved": approved, "gates": gates,
            "worst_protected": {"class": worst[0], "delta": round(worst[1], 4)}
            if worst else None}


def _metrics_of(row: MLModelVersion) -> dict:
    try:
        return json.loads(row.metrics or "{}")
    except Exception:
        return {}


def promote(db: Session, *, model_name: str, version: str,
            decided_by: uuid.UUID | None, organization_id: uuid.UUID | None,
            reason: str = "", protected=None, max_regression: float | None = None,
            ) -> dict:
    """Explicit promotion (admin only at the API layer). Validates status,
    re-checks gates against live production metrics, swaps exactly one
    production pointer, records the event. Returns the gate report."""
    row = reg.get_model(db, model_name, version)
    if row is None:
        raise ValueError(f"Model {model_name}/{version} not registered.")
    if row.status not in ("evaluated", "approved"):
        raise ValueError(f"Model {model_name}/{version} has status {row.status!r}; "
                         f"only evaluated/approved candidates can be promoted.")
    prod = reg.production_model(db, row.task)
    prod_metrics = _metrics_of(prod) if prod else {}
    if prod is None:
        gates = {"approved": True, "gates": [{
            "name": "no_incumbent", "passed": True,
            "detail": "No production model recorded; first promotion allowed."}],
            "worst_protected": None}
    else:
        gates = check_gates(
            _metrics_of(row), prod_metrics,
            protected or PROTECTED_CLASSES,
            MAX_PROTECTED_CLASS_REGRESSION if max_regression is None else max_regression)
    if not gates["approved"]:
        db.add(MLPromotionEvent(
            model_name=model_name, model_version=version,
            from_status=row.status, to_status=row.status,
            decided_by=decided_by, organization_id=organization_id,
            gates=json.dumps(gates), reason=f"REJECTED: {reason}" if reason else "REJECTED"))
        db.commit()
        return {"promoted": False, "gates": gates}
    previous = prod.version if prod else None
    reg._demote_production(db, row.task, exclude_id=row.id)
    row.status = "production"
    db.add(MLPromotionEvent(
        model_name=model_name, model_version=version,
        from_status="evaluated", to_status="production",
        decided_by=decided_by, organization_id=organization_id,
        gates=json.dumps(gates),
        reason=f"previous production: {previous}. {reason}".strip()))
    db.commit()
    return {"promoted": True, "previous_production": previous, "gates": gates}


def rollback(db: Session, *, model_name: str, version: str,
             decided_by: uuid.UUID | None, organization_id: uuid.UUID | None,
             reason: str = "") -> dict:
    """Restore a previous production model (admin only at the API layer).

    The target must have held production status before (proven by promotion
    history) — never an unvetted candidate. Artifacts and historical rows
    are untouched; exactly one production pointer results; the event is
    audited exactly like a promotion."""
    import os as _os

    row = reg.get_model(db, model_name, version)
    if row is None:
        raise ValueError(f"Model {model_name}/{version} not registered.")
    if row.status == "production":
        raise ValueError(f"Model {model_name}/{version} is already production.")
    held = db.query(MLPromotionEvent).filter(
        MLPromotionEvent.model_name == model_name,
        MLPromotionEvent.model_version == version,
        MLPromotionEvent.to_status == "production").first()
    if held is None:
        raise ValueError(
            f"Model {model_name}/{version} never held production status; "
            f"rollback refuses unevaluated targets. Promote it explicitly instead.")
    if not row.artifact_location or not _os.path.exists(
            _os.path.join(row.artifact_location, "model_info.json")):
        raise ValueError(f"Artifact missing for {model_name}/{version}; rollback refused.")
    current = reg.production_model(db, row.task)
    previous_status = current.status if current else None
    reg._demote_production(db, row.task, exclude_id=row.id)
    row.status = "production"
    db.add(MLPromotionEvent(
        model_name=model_name, model_version=version,
        from_status=previous_status, to_status="production",
        decided_by=decided_by, organization_id=organization_id,
        gates=json.dumps({"rollback": True}),
        reason=f"rollback from {current.version if current else None}. {reason}".strip()))
    db.commit()
    return {"rolled_back": True, "restored_version": version,
            "previous_production": current.version if current else None}
