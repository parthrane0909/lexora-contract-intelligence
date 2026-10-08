"""Typed contract schemas (spec §4)."""
from pydantic import BaseModel


class ContractOut(BaseModel):
    id: str
    filename: str
    contract_type: str | None = None
    # Part 5: normalized category for listings/UI — never-null, so a missing
    # contract_type shows up as the explicit "Other / Unspecified" state
    # instead of an empty cell. Same column as `contract_type`.
    contract_category: str = "Other / Unspecified"
    counterparty: str | None = None
    processing_status: str
    analysis_status: str
    semantic_index_status: str = "pending"
    # User-controlled organizational marker (Part 5) — never a risk score.
    is_important: bool = False
    is_archived: bool = False
    created_at: str
    updated_at: str | None = None


class ContractDetailOut(ContractOut):
    storage_key: str
    model_versions: str | None = None
    processing_error: str | None = None
    semantic_index_error: str | None = None
    page_count: int | None = None
    clause_count: int | None = None
    # Derived on read (risk_engine.state.analysis_lifecycle): processing,
    # classification and risk analysis reported separately.
    analysis_lifecycle: dict | None = None


class ContractPatchIn(BaseModel):
    counterparty: str | None = None
    # Free-text legacy label (kept exactly as Part 11 shipped it); "" clears
    # the category back to "Other / Unspecified". See contract_category.
    contract_type: str | None = None
    # Part 5: controlled category. When present it wins over contract_type
    # and MUST be one of contracts.category.CATEGORIES (Unspecified → NULL).
    contract_category: str | None = None
    # Part 5: star/unstar. None = "leave unchanged"; true/false are both
    # accepted and repeated identical PATCHes are idempotent.
    is_important: bool | None = None
