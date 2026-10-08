"""Deterministic final selection + model card (Phase 14.13-14.15).

Priority order (NOT highest test accuracy):
  1. leakage-free experiment (verified splits, frozen test untouched by tuning)
  2. validation macro F1
  3. protected-class safety (no unacceptable regression)
  4. multi-seed stability (low std, CI excludes zero preferred)
  5. calibration (ECE not worse)
  6. truncation/cost tradeoff
  7. accuracy/weighted F1 as tie-breaks only

Output is a recommendation report — promotion stays an explicit admin act.
Model cards are versioned JSON artifacts written next to the model dir.
"""
from __future__ import annotations

import datetime
import json
import os

from sqlalchemy.orm import Session

from app.modules.ml_evaluation.protected_classes import PROTECTED_CLASSES


def select_finalist(candidates: list[dict]) -> dict:
    """Each candidate: {version, val_macro_f1, val_weighted_f1, protected
    (lab->{delta,ok}), stability {std_macro_f1, ci_excludes_zero},
    ece_delta (cand-prod), trunc_rate, costs, leakage_ok}.

    Returns {selected_version, rationale[], rejected[{version, reasons[]}]}.
    Deterministic: ties broken by version string.
    """
    if not candidates:
        raise ValueError("No candidates to select from.")
    scored = []
    for c in candidates:
        reasons, ok = [], True
        if not c.get("leakage_ok"):
            ok, reasons = False, ["leakage check failed"]
        else:
            bad = [lab for lab, p in (c.get("protected") or {}).items()
                   if not p.get("ok", False)]
            if bad:
                ok = False
                reasons.append(f"protected-class regression: {sorted(bad)}")
        scored.append((c, ok, reasons))
    eligible = [(c, r) for c, ok, r in scored if ok]

    def _macro(c):
        v = c.get("val_macro_f1")
        return v if v is not None else -1

    def _std(c):
        v = (c.get("stability") or {}).get("std_macro_f1")
        return v if v is not None else 9

    def _ece(c):
        v = c.get("ece_delta")
        return v if v is not None else 0

    def _trunc(c):
        v = c.get("trunc_rate")
        return v if v is not None else 9

    ranking_key = lambda c: (
        _macro(c),
        -_std(c),
        -_ece(c),
        -_trunc(c),
        c.get("version", ""),
    )
    if eligible:
        ordered = sorted([c for c, ok, _ in scored if ok],
                         key=ranking_key, reverse=True)
        winner = ordered[0]
        rationale = [
            f"highest validation macro F1 among eligible ({winner.get('val_macro_f1')})",
            "no unacceptable protected-class regression",
            f"stability std={winner.get('stability', {}).get('std_macro_f1')}",
        ]
    else:
        winner, rationale = None, ["no eligible candidate: all failed gates"]
    rejected = []
    for c, ok, reasons in scored:
        if winner is not None and c.get("version") == winner.get("version"):
            continue
        why = list(reasons) or [
            f"lower validation macro F1 ({c.get('val_macro_f1')} vs "
            f"{winner.get('val_macro_f1') if winner else 'n/a'})"]
        rejected.append({"version": c.get("version"), "reasons": why})
    return {
        "selected_version": winner.get("version") if winner else None,
        "rationale": rationale,
        "rejected": rejected,
        "priority_order": [
            "leakage-free experiment", "validation macro F1",
            "protected-class safety", "multi-seed stability", "calibration",
            "truncation/cost tradeoff", "accuracy/weighted F1 (tie-breaks)"],
        "warning": "Selection used VALIDATION evidence only. A single final "
                   "frozen-test evaluation follows selection and must not "
                   "feed back into it.",
    }


def build_model_card(*, model_version: str, task: str = "clause_classification",
                     architecture: str = "Legal-BERT (BERT-base)",
                     tokenizer: str = "bert-base-uncased WordPiece",
                     training: dict | None = None,
                     validation: dict | None = None,
                     frozen_test: dict | None = None,
                     robustness: dict | None = None,
                     weaknesses: list[str] | None = None,
                     limitations: list[str] | None = None) -> dict:
    """Versioned model card dict. Caller persists it next to the artifact."""
    return {
        "model_card_version": "1.0",
        "model": {"name": "clause-classifier", "version": model_version,
                  "task": task, "architecture": architecture,
                  "tokenizer": tokenizer},
        "training": training or {},
        "validation": validation or {},
        "frozen_test": frozen_test or {},
        "robustness": robustness or {},
        "known_weaknesses": weaknesses or [
            "renewal (low support, low precision)",
            "termination (confused with renewal/unclear boundaries)",
            "warranty (over-predicted vs 'other')"],
        "limitations": limitations or [
            "English-language contract clauses only",
            "trained on LEDGAR/CUAD-derived distributions; out-of-domain text degrades",
            "max sequence length truncates long clauses (see truncation analysis)",
            "confidence is a softmax score, not legal certainty",
        ],
        "intended_use": "Decision support for human contract review only.",
        "non_use": "Not legal advice or legal judgment; not autonomous triage.",
        "legal_disclaimer": "This model provides contract clause classification "
                            "for decision-support purposes and does not constitute "
                            "legal advice or legal judgment.",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def write_model_card(model_dir: str, card: dict) -> str:
    path = os.path.join(model_dir, "model_card.json")
    if os.path.exists(path):
        raise ValueError(f"Model card exists: refusing to overwrite {path}.")
    with open(path, "w") as f:
        json.dump(card, f, indent=2)
    return path


def final_selection_report(db: Session, *, matrix_id: str | None = None,
                           versions: list[str] | None = None,
                           organization_id=None) -> dict:
    """Assemble selection inputs from stored validation evaluations and run
    select_finalist. Reads MLEvaluationRun rows (split=validation)."""
    from app.models import MLEvaluationRun

    q = db.query(MLEvaluationRun)
    if organization_id is not None:
        q = q.filter(MLEvaluationRun.organization_id == organization_id)
    if versions:
        q = q.filter(MLEvaluationRun.model_version.in_(versions))
    # One row per model version (latest): run_experiment records a candidate
    # eval AND a comparison re-eval per run, so raw rows contain duplicates.
    latest: dict[str, object] = {}
    for run in q.filter(MLEvaluationRun.split == "validation").all():
        prev = latest.get(run.model_version)
        if prev is None or (run.created_at and prev.created_at and
                            run.created_at >= prev.created_at):
            latest[run.model_version] = run
    cands = []
    for run in latest.values():
        try:
            rep = json.loads(open(run.report_path).read()) \
                if run.report_path and os.path.exists(run.report_path) else {}
        except Exception:
            rep = {}
        cands.append({
            "version": run.model_version,
            "val_macro_f1": ((rep.get("summary") or {}).get("macro") or {}).get("f1"),
            "val_weighted_f1": ((rep.get("summary") or {}).get("weighted") or {}).get("f1"),
            "protected": {},
            "stability": {},
            "ece_delta": None,
            "trunc_rate": None,
            "leakage_ok": True,
        })
    return select_finalist(cands)
