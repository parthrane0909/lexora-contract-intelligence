"""Typed comparison schemas (spec §5.6)."""
from pydantic import BaseModel


class CompareIn(BaseModel):
    template_id: str
    matcher: str = "deterministic"


class ComparisonOut(BaseModel):
    id: str
    contract_id: str
    template_id: str
    template_name: str
    template_version: int
    engine_version: str
    status: str
    stats: dict = {}
    created_at: str


class ComparisonFindingOut(BaseModel):
    id: str
    template_clause_id: str | None = None
    contract_clause_id: str | None = None
    category: str | None = None
    difference_type: str
    materiality: str
    title: str
    template_text: str | None = None
    contract_text: str | None = None
    value_diff: list[dict] = []
    risk_finding_id: str | None = None
    reasons: list[str] = []
