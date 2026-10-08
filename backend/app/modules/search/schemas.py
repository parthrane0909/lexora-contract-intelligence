"""Response shapes for contract discovery search (Part 2 Step 10).

The frontend receives application DTOs — never a raw Elasticsearch hit
(_index/_type/_score/_source/shard diagnostics stay server-side).
"""
from pydantic import BaseModel, Field


class ContractSearchHit(BaseModel):
    """One contract the query matched, with fields read back from PostgreSQL."""

    contract_id: str
    filename: str
    counterparty: str | None = None
    contract_type: str | None = None
    # Part 5: never-null category ("Other / Unspecified" when unset) and the
    # user-controlled star flag — both read from PostgreSQL, not the index.
    contract_category: str = "Other / Unspecified"
    is_important: bool = False
    processing_status: str
    analysis_status: str
    semantic_index_status: str = "pending"
    is_archived: bool = False
    created_at: str | None = None
    # Relevance only — NOT a legal or risk score (risk findings are the
    # authority for risk; the portfolio health score is a separate concept).
    score: float = 0.0


class ContractSearchOut(BaseModel):
    query: str
    total: int
    results: list[ContractSearchHit]
    # Server-applied page size: one page of the result set is returned.
    limit: int
    # Window actually requested (mirrors the listing's page/limit pair).
    offset: int = 0
    # Ordering actually applied ("relevance" when the caller asked for none).
    sort: str = "relevance"
    # Server-side search time in milliseconds (Elasticsearch `took`).
    took_ms: int | None = None
    index: str | None = None


class ReindexOut(BaseModel):
    index: str
    index_version: int
    contracts_in_postgres: int
    indexed: int
    removed: int = Field(description="Stale index documents deleted for this org")
    failed: int
    errors: list[dict] = Field(default_factory=list)


class SearchStatusOut(BaseModel):
    configured: bool
    available: bool
    index: str | None = None
    index_version: int | None = None
    physical_index: str | None = None
    server_version: str | None = None
    documents: int | None = None
    detail: str | None = None
