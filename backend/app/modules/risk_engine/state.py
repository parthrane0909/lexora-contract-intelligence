"""Deterministic analysis-state derivation (Part 1, Phase 2/3/6/8).

The system never stores a "risk answer" — it stores clauses, predictions,
analysis runs and findings. This module derives the *honest* state of those
artifacts so a contract can never report "analysis complete" when its clauses
were never classified, and a clause that was never analyzed can never look
like a clause with no detected risk.

Derived (nothing invented; the only persisted output is the
`contracts.analysis_status` rollup written by `refresh_analysis_status`):

  processing      ← contracts.processing_status
  classification  ← clauses + clause_classifications + latest classify run
  risk analysis   ← clauses + risk_findings + latest risk run (staleness aware)
  clause state    ← classification ∩ findings, per clause
  risk summary    ← counts over clause states (Phase 8 shape)

Clause states (Phase 3) — exactly five:
  high_risk        rules fired at critical/high priority
  medium_risk      rules fired at medium priority
  low_risk         rules fired at low priority
  no_detected_risk classification + complete risk run + no rule matched
  needs_review     NOT analyzed (unclassified, or analysis absent/failed/stale)

`no_detected_risk` and `needs_review` are never interchangeable: the former
is an analytical result, the latter means no result exists.

Staleness: a run only describes the rows that existed when it ran. Deleting
clauses (re-processing) cascades away classifications and findings, so a run
that finished *before* the newest clause/prediction row no longer describes
the contract and is reported as `stale`, never as success or as "0 findings".
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AnalysisRun, Clause, ClauseClassification, Contract, RiskFinding
from app.modules.risk_engine.taxonomy import PRIORITIES

ANALYSIS_STATES_VERSION = "1.0"

# Engine priorities (critical/high/medium/low) group into the five reviewer
# states. `critical` reports as high_risk because it needs attention first;
# the exact engine value stays on `top_priority` and on each finding.
_PRIORITY_STATE = {"critical": "high_risk", "high": "high_risk",
                   "medium": "medium_risk", "low": "low_risk"}
RISK_STATES = ("high_risk", "medium_risk", "low_risk", "no_detected_risk", "needs_review")

# contracts.analysis_status rollup values (kept coarse: per-stage detail
# lives in analysis_lifecycle(), not in this column). "stale" means earlier
# results no longer describe this contract (it was re-processed or
# re-classified afterwards) and must be re-run.
ANALYSIS_STATUSES = ("pending", "queued", "classified", "partial", "stale",
                     "complete", "failed")


def _json(text: str | None) -> dict:
    try:
        return json.loads(text) if text else {}
    except (ValueError, TypeError):
        return {}


def _newest(db: Session, model, contract_id: uuid.UUID):
    """Newest created_at among a contract's rows of `model` (None if none)."""
    return db.query(func.max(model.created_at)).filter(
        model.contract_id == contract_id).scalar()


def _stale(run, reference) -> bool:
    """True when `run` finished before `reference` was written, i.e. the run
    no longer describes the data it claims to describe."""
    if run is None or run.created_at is None or reference is None:
        return False
    try:
        return run.created_at < reference
    except TypeError:  # defensive: never crash on naive/aware timestamps
        return False


def _latest_run(db: Session, contract_id: uuid.UUID, kind: str):
    return (db.query(AnalysisRun)
            .filter(AnalysisRun.contract_id == contract_id,
                    AnalysisRun.kind == kind)
            .order_by(AnalysisRun.created_at.desc()).first())


def latest_risk_run(db: Session, contract_id: uuid.UUID):
    """Most recent risk run (any status) — the one whose findings are current."""
    return _latest_run(db, contract_id, "risk_analysis")


def contract_findings(db: Session, contract_id: uuid.UUID) -> list[RiskFinding]:
    """Findings of the current risk run, restricted to clauses that still
    exist for this contract.

    Scoped to the latest run so a failed/stale re-run can never be reported
    alongside the previous run's rows as if both were current, and to live
    clauses so a finding can always be traced to the text it came from.
    Contracts with findings but no run at all (hand-seeded rows) keep every
    row — there is no run to scope them to.
    """
    run = latest_risk_run(db, contract_id)
    q = db.query(RiskFinding).filter(RiskFinding.contract_id == contract_id)
    if run is not None:
        q = q.filter(RiskFinding.analysis_run_id == run.id)
    live = {str(c.id) for c in contract_clauses(db, contract_id)}
    return [f for f in q.order_by(RiskFinding.created_at).all()
            if str(f.clause_id) in live]


def latest_classifications(db: Session, contract_id: uuid.UUID) -> dict[str, ClauseClassification]:
    """Latest prediction per clause (append-only history → first hit wins)."""
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id == contract_id)
            .order_by(ClauseClassification.created_at.desc()).all())
    out: dict[str, ClauseClassification] = {}
    for r in rows:
        out.setdefault(str(r.clause_id), r)
    return out


def contract_clauses(db: Session, contract_id: uuid.UUID) -> list[Clause]:
    return (db.query(Clause).filter(Clause.contract_id == contract_id)
            .order_by(Clause.order_index).all())


def _rank(priority: str | None) -> int:
    """Lower is worse (critical=0 … low=3); unknown priorities rank last."""
    return PRIORITIES.index(priority) if priority in PRIORITIES else len(PRIORITIES)


def analysis_lifecycle(db: Session, contract: Contract) -> dict:
    """Processing / classification / risk-analysis states, derived."""
    clauses = contract_clauses(db, contract.id)
    eligible = [c for c in clauses if (c.text or "").strip()]
    cls_rows = latest_classifications(db, contract.id)
    cls_run = _latest_run(db, contract.id, "clause_classification")
    risk_run = _latest_run(db, contract.id, "risk_analysis")

    if contract.processing_status == "failed":
        processing = {"state": "failed", "error": contract.processing_error}
    elif contract.processing_status == "processed":
        processing = {"state": "complete", "error": None}
    elif contract.processing_status in ("queued", "processing"):
        processing = {"state": "running", "error": None}
    else:
        processing = {"state": "not_started", "error": None}

    n_clauses = len(eligible)
    n_classified = sum(1 for c in eligible if str(c.id) in cls_rows)
    unclassified = n_clauses - n_classified
    cls_error = cls_run.error if (cls_run is not None and cls_run.status == "failed") else None

    # ---- classification (rows are the truth; runs explain why) ----------
    if processing["state"] != "complete":
        cls_state, cls_stale = "not_ready", False
    elif cls_run is not None and cls_run.status == "running":
        cls_state, cls_stale = "running", False
    elif n_clauses == 0:
        cls_state, cls_stale = ("complete" if cls_run is not None else "not_run"), False
    elif n_classified == n_clauses:
        cls_state, cls_stale = "complete", False
    elif n_classified > 0:
        cls_state, cls_stale = "partial", False
    elif cls_run is not None and cls_run.status == "failed":
        cls_state, cls_stale = "failed", False
    elif cls_run is not None and _stale(cls_run, _newest(db, Clause, contract.id)):
        cls_state, cls_stale = "stale", True  # contract re-processed since
    elif cls_run is not None:
        cls_state, cls_stale = "partial", False  # run exists but wrote nothing
    else:
        cls_state, cls_stale = "not_run", False
    classification = {"state": cls_state, "clauses": n_clauses,
                      "classified": n_classified, "error": cls_error,
                      "stale": cls_stale}

    # ---- risk analysis --------------------------------------------------
    n_findings = len(contract_findings(db, contract.id))
    # A run's own rows are written after it starts, so only clauses and
    # predictions can invalidate it (findings never can).
    risk_stale = (_stale(risk_run, _newest(db, Clause, contract.id))
                  or _stale(risk_run, _newest(db, ClauseClassification, contract.id)))
    risk_error = risk_run.error if (risk_run is not None and risk_run.status == "failed") else None
    if processing["state"] != "complete":
        risk_state = "not_ready"
    elif risk_run is None:
        risk_state = "not_run"
    elif risk_run.status == "running":
        risk_state = "running"
    elif risk_run.status == "failed":
        risk_state = "failed"
    elif risk_stale:
        risk_state = "stale"
    elif unclassified > 0:
        risk_state = "partial"  # engine skipped clauses without a prediction
    else:
        risk_state = "complete"
    risk = {"state": risk_state, "clauses": n_clauses, "unclassified": unclassified,
            "findings": n_findings, "error": risk_error, "stale": risk_stale}

    # ---- overall --------------------------------------------------------
    if processing["state"] == "failed":
        overall = "processing_failed"
    elif processing["state"] != "complete":
        overall = "not_ready"
    elif "failed" in (cls_state, risk_state):
        overall = "failed"
    elif "stale" in (cls_state, risk_state):
        overall = "stale"
    elif cls_state in ("not_run", "not_ready") and risk_state in ("not_run", "not_ready"):
        overall = "not_started"
    elif "running" in (cls_state, risk_state) or "not_ready" in (cls_state, risk_state):
        overall = "running"
    elif cls_state == "complete" and risk_state == "not_run":
        overall = "classified_only"
    elif cls_state == "complete" and risk_state == "complete":
        overall = "complete"
    else:
        overall = "partial"

    return {
        "states_version": ANALYSIS_STATES_VERSION,
        "processing": processing,
        "classification": classification,
        "risk_analysis": risk,
        "overall": overall,
    }


def clause_states(db: Session, contract: Contract, life: dict | None = None) -> dict[str, dict]:
    """Per-clause analysis state, keyed by clause id."""
    life = life or analysis_lifecycle(db, contract)
    cls_rows = latest_classifications(db, contract.id)
    cls_state = life["classification"]["state"]
    cls_failed = bool(life["classification"]["error"])  # latest run failed
    risk_state = life["risk_analysis"]["state"]
    findings: dict[str, list[RiskFinding]] = {}
    for f in contract_findings(db, contract.id):
        findings.setdefault(str(f.clause_id), []).append(f)

    out: dict[str, dict] = {}
    for cl in contract_clauses(db, contract.id):
        cid = str(cl.id)
        pred = cls_rows.get(cid)
        rows = findings.get(cid, [])
        top = min((f.priority for f in rows), key=_rank) if rows else None
        if not (cl.text or "").strip():
            state, reason = "needs_review", "empty_clause"
        elif pred is None:
            # Never analyzed: say why (failed run, contract re-processed, or
            # never classified) — this is NOT "no risk detected".
            state = "needs_review"
            reason = ("classification_failed" if cls_failed
                      else "classification_stale" if cls_state == "stale"
                      else "unclassified")
        elif risk_state in ("not_run", "not_ready"):
            state, reason = "needs_review", "risk_analysis_not_run"
        elif risk_state == "failed":
            state, reason = "needs_review", "risk_analysis_failed"
        elif risk_state == "stale":
            state, reason = "needs_review", "risk_analysis_stale"
        elif rows:
            state, reason = _PRIORITY_STATE.get(top, "needs_review"), "rule_matched"
        else:
            state, reason = "no_detected_risk", "no_rule_matched"
        out[cid] = {
            "clause_id": cid,
            "risk_state": state,
            "state_reason": reason,
            "top_priority": top,
            "finding_ids": [str(f.id) for f in rows],
            "classification": ({"category": pred.predicted_category,
                                "confidence": pred.confidence,
                                "model_name": pred.model_name,
                                "model_version": pred.model_version}
                               if pred is not None else None),
        }
    return out


def risk_summary(db: Session, contract: Contract, life: dict | None = None) -> dict:
    """Contract-level aggregation (Phase 8 shape).

    Counts come from clause states, so they can never disagree with the
    per-clause results; the two lifecycle states are included so "0 findings"
    is always readable next to *why* (not_run / failed / stale / complete)."""
    life = life or analysis_lifecycle(db, contract)
    clauses = contract_clauses(db, contract.id)
    states = clause_states(db, contract, life)
    counts = {s: 0 for s in RISK_STATES}
    classified = 0
    for cl in clauses:
        st = states.get(str(cl.id), {})
        counts[st.get("risk_state", "needs_review")] += 1
        if st.get("classification"):
            classified += 1
    findings = contract_findings(db, contract.id)
    by_priority: dict[str, int] = {}
    categories: list[str] = []
    for f in findings:
        by_priority[f.priority] = by_priority.get(f.priority, 0) + 1
        if f.risk_category not in categories:
            categories.append(f.risk_category)
    return {
        "contract_id": str(contract.id),
        "filename": contract.filename,
        "classification_state": life["classification"]["state"],
        "risk_analysis_state": life["risk_analysis"]["state"],
        "analysis_overall": life["overall"],
        "total_clauses": len(clauses),
        "classified_clauses": classified,
        "unclassified_clauses": len(clauses) - classified,
        "analyzed_clauses": counts["high_risk"] + counts["medium_risk"]
        + counts["low_risk"] + counts["no_detected_risk"],
        "high_risk": counts["high_risk"],
        "medium_risk": counts["medium_risk"],
        "low_risk": counts["low_risk"],
        "no_detected_risk": counts["no_detected_risk"],
        "needs_review": counts["needs_review"],
        # Look first at risky clauses and at anything not yet analyzed.
        "needs_attention": counts["high_risk"] + counts["medium_risk"]
        + counts["needs_review"],
        "findings": len(findings),
        "findings_by_priority": by_priority,
        "risk_categories": sorted(categories),
        "states_version": ANALYSIS_STATES_VERSION,
    }


def compute_analysis_status(db: Session, contract: Contract) -> str:
    """Coarse rollup for `contracts.analysis_status`: never claims success the
    stored data does not support."""
    if contract.processing_status == "failed":
        return "failed"
    if contract.processing_status != "processed":
        return "queued" if contract.processing_status in ("queued", "processing") else "pending"
    life = analysis_lifecycle(db, contract)
    cls_state = life["classification"]["state"]
    risk_state = life["risk_analysis"]["state"]
    if "failed" in (cls_state, risk_state):
        return "failed"
    if cls_state == "complete" and risk_state == "complete":
        return "complete"
    if "stale" in (cls_state, risk_state):
        return "stale"
    if cls_state == "complete" and risk_state == "not_run":
        return "classified"
    if cls_state in ("complete", "partial") or risk_state == "partial":
        return "partial"
    return "pending"


def refresh_analysis_status(db: Session, contract: Contract) -> str:
    """Recompute and persist the rollup after any processing/classification/
    risk write. Bookkeeping only — never raises into the caller."""
    try:
        status = compute_analysis_status(db, contract)
        if contract.analysis_status != status:
            contract.analysis_status = status
            db.commit()
            # Part 2: this is THE choke point for analysis_status writes, so
            # the search copy follows every rollup change (best-effort —
            # index_contract never raises, this function still never raises).
            from app.modules.search.indexer import index_contract

            index_contract(contract)
        return status
    except Exception as e:  # noqa: BLE001
        print(f"state: could not refresh analysis_status for {contract.id}: {e}")
        return contract.analysis_status
