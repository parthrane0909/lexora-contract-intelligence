"""Typed policy schemas. Params are validated per category — the engine
only accepts known keys so typos fail loud instead of being silently ignored.
"""
from pydantic import BaseModel

# category → allowed param keys (incl. shared "impact": standard|high)
POLICY_SHAPES: dict[str, set[str]] = {
    "liability": {"max_amount", "currency", "require_cap", "impact"},
    "payment": {"max_days", "impact"},
    "termination": {"min_notice_days", "impact"},
    "renewal": {"allow_auto_renew", "impact"},
    "governing_law": {"preferred_jurisdictions", "impact"},
    "jurisdiction": {"preferred_jurisdictions", "impact"},
    "insurance": {"min_coverage", "currency", "impact"},
}

POLICY_CATEGORIES = sorted(POLICY_SHAPES)


class PolicyIn(BaseModel):
    category: str
    params: dict


class PolicyOut(BaseModel):
    id: str
    category: str
    params: dict
    version: int
    is_active: bool
    created_by: str | None = None
    created_at: str
