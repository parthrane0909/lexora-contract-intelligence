"""Typed template schemas (spec §5.3)."""
from pydantic import BaseModel


class TemplateOut(BaseModel):
    id: str
    name: str
    contract_type: str | None = None
    version: int
    is_active: bool
    status: str
    filename: str
    page_count: int = 0
    clause_count: int = 0
    processing_error: str | None = None
    created_by: str | None = None
    created_at: str


class TemplateClauseOut(BaseModel):
    id: str
    section_number: str | None = None
    title: str | None = None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    order_index: int = 0
    predicted_category: str | None = None
    classification_confidence: float | None = None
