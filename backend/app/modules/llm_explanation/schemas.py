"""Typed explanation schemas (Phase 7.13). The LLM may ONLY fill these
fields — category/priority/type/rule/policy stay authoritative on the
finding and are never part of the generated payload.
"""
from pydantic import BaseModel


class SourceReferenceOut(BaseModel):
    source_index: int
    quote: str


class ExplanationOut(BaseModel):
    id: str
    finding_id: str
    status: str
    summary: str = ""
    issue: str = ""
    why_it_matters: str = ""
    evidence_summary: str = ""
    policy_comparison: str = ""
    template_comparison: str = ""
    historical_context: str = ""
    recommendation: str = ""
    limitations: str = ""
    source_references: list[SourceReferenceOut] = []
    model: str = ""
    model_version: str = ""
    prompt_version: str = ""
    evidence_count: int = 0
    latency_ms: int | None = None
    error: str | None = None
    created_at: str


class EvidenceItemOut(BaseModel):
    evidence_id: str
    source_type: str
    source_id: str
    title: str
    category: str | None = None
    page: int | None = None
    section: str | None = None
    similarity_score: float | None = None
    rank: int = 0
    excerpt: str = ""
    contract_id: str | None = None
    clause_id: str | None = None
    template_id: str | None = None
