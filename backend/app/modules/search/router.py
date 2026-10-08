"""Contract discovery search routes (Part 2 Steps 9/10).

    GET  /api/v1/search/contracts?q=...      live/fuzzy filename search
         &category= &analysis_status= &processing_status= &important=
                                             structured filters (Part 5),
                                             combinable with `q`
    POST /api/v1/search/reindex-contracts    idempotent org-scoped backfill
    GET  /api/v1/search/status               search-backend diagnostics

Every endpoint is organization-scoped on the backend via
get_current_membership: the org filter is part of the Elasticsearch query
itself (service._scope_filters) and results are re-verified against
org-scoped PostgreSQL rows. The frontend never filters across organizations.

Filter values are validated against the SAME vocabularies the PostgreSQL
listing uses (contracts/category.CATEGORIES, risk_engine.state.
ANALYSIS_STATUSES), so search and listing can never disagree on what a
filter means. `q` stays required: an empty query keeps using the normal
listing (which supports the identical filters) instead of a match-all
Elasticsearch query.

POSTGRES IS THE SOURCE OF TRUTH — these routes only ask the index *which*
contracts match and answer with DTOs (see app/modules/search/service.py).
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.modules.contracts.category import parse_category
from app.modules.contracts.service import PROCESSING_STATUSES
from app.modules.portfolio.service import kpi_contract_ids
from app.modules.risk_engine.state import ANALYSIS_STATUSES
from app.modules.search import schemas, service
from app.modules.search.es import SearchUnavailableError
from app.modules.search.indexer import reindex_org

router = APIRouter(prefix="/search", tags=["search"])


def _filter(name: str, value, allowed: tuple[str, ...]) -> str:
    """Validate one structured filter value against its vocabulary."""
    if value not in allowed:
        raise HTTPException(
            400, f"Unknown {name} {value!r}. "
                 f"Expected one of: {', '.join(allowed)}")
    return value


@router.get("/contracts", response_model=schemas.ContractSearchOut)
def search_contracts(
    q: str = Query(..., min_length=1, max_length=256,
                   description="Free text: filename, counterparty or type "
                               "(partial words and typos are fine)"),
    limit: int | None = Query(default=None, ge=1, description="Max results"),
    offset: int = Query(default=0, ge=0,
                        description="Result offset (pagination window)"),
    sort: str = Query(default="relevance",
                      description="relevance | newest | oldest | filename | "
                                  "category — mirrors the listing's `sort`"),
    include_archived: bool = Query(default=False),
    category: str | None = Query(default=None),
    analysis_status: str | None = Query(default=None),
    processing_status: str | None = Query(default=None),
    important: bool | None = Query(default=None),
    high_risk: bool = Query(default=False,
                            description="KPI filter: contracts with a "
                                        "qualifying unresolved high/critical "
                                        "finding"),
    renewing_within_days: int | None = Query(default=None, ge=1, le=365,
                                             description="KPI filter: stored "
                                                         "renewal date within "
                                                         "N days"),
    member=Depends(get_current_membership),
    db: Session = Depends(get_db),
):
    """Search contracts of the caller's organization, optionally narrowed by
    structured metadata filters (ANDed with the text query).

    Empty `q` is rejected (422) — an empty string is never run through
    Elasticsearch; the frontend restores the normal listing instead, which
    accepts the very same filters.

    `offset`/`sort` give search the same pagination and ordering vocabulary
    as the listing. The KPI filters (`high_risk`, `renewing_within_days`) have
    no index field: their contract-id set is derived from PostgreSQL and
    re-applied during verification, so both endpoints agree on what they mean.
    """
    filters: dict = {}
    if category is not None:
        try:
            filters["category"] = parse_category(category)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if analysis_status is not None:
        filters["analysis_status"] = _filter(
            "analysis status", analysis_status, ANALYSIS_STATUSES)
    if processing_status is not None:
        filters["processing_status"] = _filter(
            "processing status", processing_status, PROCESSING_STATUSES)
    if important is not None:
        filters["is_important"] = bool(important)
    kpi_ids = kpi_contract_ids(db, member.organization_id, high_risk=high_risk,
                               renewing_within_days=renewing_within_days)
    if kpi_ids is not None:
        filters["contract_ids"] = kpi_ids
    try:
        return service.search_contracts(
            db, org_id=member.organization_id, query=q, limit=limit,
            offset=offset, sort=sort,
            include_archived=include_archived, filters=filters or None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except SearchUnavailableError as e:
        # Detail stays server-side (it can contain internal endpoints).
        print(f"search: /search/contracts unavailable: {e}")
        raise HTTPException(
            503,
            "Contract search is temporarily unavailable. Retry shortly — "
            "the contract list itself is unaffected.",
        )


@router.get("/status", response_model=schemas.SearchStatusOut)
def search_status(member=Depends(get_current_membership)):
    """Whether contract discovery search is configured and reachable."""
    return service.search_status()


@router.post("/reindex-contracts", response_model=schemas.ReindexOut,
             dependencies=[Depends(require_roles("admin", "contract_manager"))])
def reindex_contracts(member=Depends(get_current_membership),
                      db: Session = Depends(get_db)):
    """Backfill/reconcile the search index for the caller's organization.

    Idempotent (document id = contract id), organization safe, duplicate safe.
    Reports indexed / removed / failed counts. Touches neither PostgreSQL
    contract rows nor Qdrant.
    """
    try:
        return reindex_org(db, member.organization_id)
    except SearchUnavailableError as e:
        print(f"search: reindex unavailable: {e}")
        raise HTTPException(
            503,
            "Reindex failed: the search backend is unavailable. Nothing was "
            "changed in PostgreSQL.",
        )
