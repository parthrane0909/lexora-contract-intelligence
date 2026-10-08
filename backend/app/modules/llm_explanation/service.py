"""Explanation workflow (Phase 7.19). Finding → evidence → context → LLM →
validate → persist. Regeneration appends; history is never overwritten.

Failure policy: LLM/validation errors persist a `failed` row (auditable)
and raise — callers return explicit errors, never fake explanations. No
contract text in logs (counts/ids only).
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.models import RiskExplanation, RiskFinding
from app.modules.audit.service import record
from app.modules.llm_explanation.context import build_context
from app.modules.llm_explanation.evidence import collect_evidence
from app.modules.llm_explanation.prompts import PROMPT_VERSION, build_messages
from app.modules.llm_explanation.providers import (
    LLMUnavailableError,
    get_llm_provider,
)
from app.modules.llm_explanation.validator import GroundingError, validate_explanation

log = logging.getLogger("explanations")


def _snapshot(sources: list[dict]) -> list[dict]:
    """Immutable provenance: full source texts + versions at generation time."""
    return [{k: s.get(k) for k in (
        "evidence_id", "source_type", "source_id", "organization_id",
        "contract_id", "clause_id", "template_id", "title", "text",
        "category", "page", "section", "similarity_score", "provenance",
        "retrieval_method", "rank", "metadata")} for s in sources]


def _coerce_text_fields(validated: dict) -> dict:
    """Real models sometimes return nested objects for text sections.
    Normalize to strings (JSON) so the persisted schema stays stable;
    citation/grounding validation already ran on the raw structure."""
    import json as _json

    out = dict(validated)
    for key, value in out.items():
        if key == "source_references":
            continue
        if not isinstance(value, str):
            out[key] = _json.dumps(value)
    return out
    """Immutable provenance: full source texts + versions at generation time."""
    return [{k: s.get(k) for k in (
        "evidence_id", "source_type", "source_id", "organization_id",
        "contract_id", "clause_id", "template_id", "title", "text",
        "category", "page", "section", "similarity_score", "provenance",
        "retrieval_method", "rank", "metadata")} for s in sources]


def generate_explanation(db: Session, finding_id: uuid.UUID, *,
                         triggered_by: uuid.UUID | None,
                         provider=None, store=None, retrieval_provider=None) -> RiskExplanation:
    finding = db.get(RiskFinding, finding_id)
    if finding is None:
        raise ValueError("Risk finding not found")
    llm = provider or get_llm_provider()
    started = time.monotonic()
    try:
        sources = collect_evidence(db, finding, provider=retrieval_provider, store=store)
        context = build_context(finding, sources)
        raw = llm.generate_structured(build_messages(finding, context))
        validated = validate_explanation(raw, context["sources"], finding.organization_id)
        validated = _coerce_text_fields(validated)
        latency_ms = int((time.monotonic() - started) * 1000)
        row = RiskExplanation(
            organization_id=finding.organization_id, finding_id=finding.id,
            analysis_run_id=finding.analysis_run_id, contract_id=finding.contract_id,
            clause_id=finding.clause_id, provider=type(llm).__name__,
            model=llm.model_name, model_version=llm.model_version,
            prompt_version=PROMPT_VERSION, evidence_snapshot=json.dumps(_snapshot(sources)),
            explanation=json.dumps(validated), status="completed",
            latency_ms=latency_ms)
        db.add(row)
        db.commit()
        db.refresh(row)
        record(db, action="finding.explained", organization_id=finding.organization_id,
               user_id=triggered_by, object_type="risk_explanation", object_id=str(row.id),
               detail=f"finding={finding.id} sources={len(sources)} "
                      f"model={llm.model_name} prompt={PROMPT_VERSION} {latency_ms}ms")
        log.info("explanation completed finding=%s sources=%d model=%s prompt=%s %dms",
                 finding.id, len(sources), llm.model_name, PROMPT_VERSION, latency_ms)
        return row
    except (LLMUnavailableError, GroundingError, ValueError) as e:
        db.rollback()
        row = RiskExplanation(
            organization_id=finding.organization_id, finding_id=finding.id,
            analysis_run_id=finding.analysis_run_id, contract_id=finding.contract_id,
            clause_id=finding.clause_id, provider=type(llm).__name__,
            model=getattr(llm, "model_name", "?"),
            model_version=getattr(llm, "model_version", "?"),
            prompt_version=PROMPT_VERSION, evidence_snapshot=json.dumps([]),
            explanation=json.dumps({}), status="failed",
            error=f"{type(e).__name__}: {e}"[:2000],
            latency_ms=int((time.monotonic() - started) * 1000))
        db.add(row)
        db.commit()
        db.refresh(row)
        record(db, action="finding.explanation_failed", organization_id=finding.organization_id,
               user_id=triggered_by, object_type="risk_finding", object_id=str(finding.id),
               detail=str(e)[:500])
        log.warning("explanation failed finding=%s reason=%s", finding.id, e)
        raise


def latest_explanation(db: Session, finding_id: uuid.UUID) -> RiskExplanation | None:
    return (db.query(RiskExplanation)
            .filter(RiskExplanation.finding_id == finding_id,
                    RiskExplanation.status == "completed")
            .order_by(RiskExplanation.created_at.desc()).first())


def explanation_history(db: Session, finding_id: uuid.UUID) -> list[RiskExplanation]:
    return (db.query(RiskExplanation)
            .filter(RiskExplanation.finding_id == finding_id)
            .order_by(RiskExplanation.created_at.desc()).all())
