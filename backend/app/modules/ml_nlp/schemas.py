"""Typed classification schemas (spec §7)."""
from pydantic import BaseModel


class ClassificationOut(BaseModel):
    clause_id: str
    predicted_category: str
    confidence: float
    model_name: str | None = None
    model_version: str
    analysis_run_id: str | None = None
    created_at: str


class ClassifyRunOut(BaseModel):
    status: str
    run_id: str
    clauses: int
    classified: int
    skipped: int
    model_version: str
