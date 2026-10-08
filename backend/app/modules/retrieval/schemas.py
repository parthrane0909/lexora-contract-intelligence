"""Typed retrieval schemas."""
from pydantic import BaseModel


class IndexIn(BaseModel):
    scope: str  # contract | template | policy | reindex
    contract_id: str | None = None
    template_id: str | None = None
    policy_id: str | None = None


class SearchIn(BaseModel):
    query: str
    top_k: int = 10
    object_type: str | None = None
    contract_id: str | None = None
    template_id: str | None = None
    category: str | None = None


class SearchHit(BaseModel):
    score: float
    object_type: str | None = None
    object_id: str | None = None
    contract_id: str | None = None
    template_id: str | None = None
    category: str | None = None
    section_number: str | None = None
    page_start: int | None = None
    embedding_model: str | None = None
    text: str = ""


class SearchOut(BaseModel):
    query: str
    top_k: int
    model: str
    results: list[SearchHit]
