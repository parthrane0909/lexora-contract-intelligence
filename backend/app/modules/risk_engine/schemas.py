"""Typed risk schemas (spec §7-8)."""
from pydantic import BaseModel


class FindingOut(BaseModel):
    id: str
    contract_id: str
    clause_id: str
    analysis_run_id: str | None = None
    risk_category: str
    finding_type: str
    priority: str
    title: str
    reason: str
    evidence: str
    section_number: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    rule_id: str
    rule_version: str
    policy_id: str | None = None
    policy_version: int | None = None
    contract_value: dict = {}
    expected_value: dict = {}
    priority_reasons: list[str] = []
    # Phase 4/5 additions: where it came from, what to check, why.
    extracted_facts: dict = {}
    clause_title: str | None = None
    clause_text: str | None = None
    requires_attention: bool = False
    attention_reason: str = ""
    explanation: dict = {}
    classification_model: str | None = None
    classification_version: str | None = None
    classification_confidence: float | None = None
    status: str
    created_at: str


class RiskRunOut(BaseModel):
    status: str
    run_id: str
    clauses: int
    unclassified: int
    findings: int
    engine_version: str
