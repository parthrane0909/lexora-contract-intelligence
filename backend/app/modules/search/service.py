"""Contract discovery search: query building, ranking, verification, status.

Flow (Part 2 Steps 10/11/7)
----------------------------
    GET /api/v1/search/contracts?q=...   (org-scoped, backend-enforced)
        → build_search_body()  →  Elasticsearch (which contract + what order)
        → PostgreSQL            (what the contract actually IS)
        → application DTOs      (never a raw Elasticsearch response)

Elasticsearch decides *which* documents match and *in what order*; every
returned row is then re-read from org-scoped PostgreSQL (service.py
_verify_against_postgres). Consequences:

  * a deleted contract can never be served from a stale index entry;
  * another organization's document can never be served even if the index
    were corrupted (org_id term filter + org-scoped re-query);
  * displayed statuses/filenames are always current, never index copies.

No fallback algorithm: if Elasticsearch is unavailable the endpoint raises
SearchUnavailableError → HTTP 503 → the UI shows a search-specific error.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Contract
from app.modules.contracts.category import UNSPECIFIED
from app.modules.search import es, index as search_index

# --------------------------------------------------------------------------
# Ranking ladder. Boosts are per-clause and scores are ADDED, so only the
# sum matters. Because exact ⊇ prefix ⊃ whole-word ⊇ fuzzy (a document that
# matches a stricter clause always matches the looser ones too), a true
# match always outranks a weaker one:
#
#   1000  exact normalized filename          '02_balanced_saas_agreement.pdf'
#    500  every query word = prefix of a filename word   'vend' → vendor
#    200  search-as-you-type prefix sequence on raw filename ('balan…')
#    100  query words present in order (phrase)          'low risk'
#     60  every query word present as a whole word       'risk vendor'
#     30  counterparty word
#     20  contract type
#     15  typo-tolerant (fuzzy) — deliberately LAST, so a fuzzy-only hit can
#          never outrank (or be confused with) a real match
RANK = {
    "exact_filename": 1000.0,
    "token_prefix": 500.0,
    "as_you_type": 200.0,
    "phrase": 100.0,
    "all_words": 60.0,
    "counterparty": 30.0,
    "contract_type": 20.0,
    "fuzzy": 15.0,
}

# Part 2 Step 17 / Part 5 Step 6: structured filters. Every entry is applied
# as an exact term filter next to the mandatory org scope, and the SAME
# vocabulary is accepted by the PostgreSQL listing (contracts/router) — one
# filter semantics, two indexes. Adding a filter later = adding a row here.
FILTER_FIELDS = {
    "analysis_status": "analysis_status",
    "processing_status": "processing_status",
    "semantic_index_status": "semantic_index_status",
    "contract_type": "contract_type.raw",
    # Part 5: contract-level category (never-null keyword) and star flag.
    "category": "contract_category",
    "is_important": "is_important",
    "is_archived": "is_archived",
}


def _scope_filters(org_id, include_archived: bool, filters: dict | None) -> list[dict]:
    """Mandatory org scope + archive policy + optional structured filters."""
    clauses: list[dict] = [{"term": {"org_id": str(org_id)}}]
    if not include_archived:
        # Same policy as GET /api/v1/contracts: archived is hidden by default.
        clauses.append({"term": {"is_archived": False}})
    for key, value in (filters or {}).items():
        if value is None or value == "":
            continue
        if key == "contract_ids":
            # Part 7: KPI-derived constraints (high risk / renewing soon) have
            # no index field — they are verified against PostgreSQL only, see
            # _matches_filters.
            continue
        field = FILTER_FIELDS.get(key)
        if field is None:
            raise ValueError(f"Unsupported filter: {key}")
        clauses.append({"term": {field: value}})
    return clauses


# Result orderings accepted by the search endpoint. They mirror the PostgreSQL
# listing's `sort` vocabulary (contracts/router SORTS) so the two paths order
# the same query the same way; "relevance" is search-only.
SEARCH_SORTS: dict[str, list[dict | str]] = {
    "relevance": ["_score",
                  {"created_at": {"order": "desc", "unmapped_type": "date"}}],
    "newest": [{"created_at": {"order": "desc", "unmapped_type": "date"}},
               "_score"],
    "oldest": [{"created_at": {"order": "asc", "unmapped_type": "date"}},
               "_score"],
    # filename_normalized.raw is the lowercase keyword twin of the stored name
    # (the `filename` field itself is search-as-you-type and cannot sort).
    "filename": [{"filename_normalized.raw": {"order": "asc"}},
                 {"created_at": {"order": "desc", "unmapped_type": "date"}}],
    # contract_category is a never-null keyword: "Other / Unspecified" sorts
    # exactly where the PostgreSQL coalesce() puts it.
    "category": [{"contract_category": {"order": "asc"}},
                 {"filename_normalized.raw": {"order": "asc"}}],
}


def build_search_body(
    query: str,
    *,
    org_id,
    limit: int,
    offset: int = 0,
    sort: str = "relevance",
    include_archived: bool = False,
    filters: dict | None = None,
) -> dict:
    """Elasticsearch request body for contract discovery (see RANK)."""
    normalized = search_index.normalize_filename(query)
    should: list[dict] = [
        # 1. exact normalized filename
        {"term": {"filename_normalized.raw": {"value": normalized,
                                              "boost": RANK["exact_filename"]}}},
        # 2. every query word is a prefix of some filename word. operator=and
        #    keeps multi-word queries precise ("risk vendor" → vendor files,
        #    not every file that merely contains "risk") and stops a single
        #    shared letter from matching unrelated documents.
        {"match": {"filename_normalized.edge": {"query": normalized,
                                                "operator": "and",
                                                "boost": RANK["token_prefix"]}}},
        # 3. search-as-you-type on the raw filename (works while typing a
        #    partial word: "bala" → "balanced").
        {"multi_match": {"type": "bool_prefix", "query": query,
                         "fields": ["filename", "filename._2gram",
                                    "filename._3gram", "filename._index_prefix"],
                         "boost": RANK["as_you_type"]}},
        # 4. all words, in order
        {"match_phrase": {"filename_normalized": {"query": normalized,
                                                  "boost": RANK["phrase"]}}},
        # 5. all words, any order (whole tokens only)
        {"match": {"filename_normalized": {"query": normalized,
                                           "operator": "and",
                                           "boost": RANK["all_words"]}}},
        # 6/7. member-supplied metadata
        {"match": {"counterparty": {"query": query, "boost": RANK["counterparty"]}}},
        {"match": {"contract_type": {"query": query, "boost": RANK["contract_type"]}}},
        # 8. typo tolerance — the ONLY clause with fuzziness, and the weakest.
        {"multi_match": {"query": query, "fields": ["filename_normalized^2"],
                         "operator": "or", "fuzziness": "AUTO",
                         "boost": RANK["fuzzy"]}},
    ]
    return {
        "size": limit,
        "from": max(0, int(offset)),
        "query": {
            "bool": {
                "filter": _scope_filters(org_id, include_archived, filters),
                "should": should,
                # Required: with a filter present the default is 0.
                "minimum_should_match": 1,
            }
        },
        # Deterministic order: relevance, then newest first (same tie-break as
        # the contract list endpoint) — or an explicit metadata ordering when
        # the caller asked for one.
        "sort": SEARCH_SORTS.get(sort, SEARCH_SORTS["relevance"]),
        "track_total_hits": True,
    }


def _matches_filters(contract: Contract, filters: dict | None) -> bool:
    """Re-apply the structured filters to the PostgreSQL row itself.

    Elasticsearch decides WHICH ids match and in what order; re-checking the
    filters here means a momentarily stale index copy can never contradict
    PostgreSQL (the same defence the archive policy already applies below).
    """
    for key, value in (filters or {}).items():
        if key == "contract_ids":
            # KPI constraint (high risk / renewing soon): derived from
            # PostgreSQL, never from the index — see portfolio.service.
            if contract.id not in value:
                return False
        elif key == "category":
            if (contract.contract_type or UNSPECIFIED) != value:
                return False
        elif key == "is_important":
            if bool(contract.is_important) != bool(value):
                return False
        elif key == "analysis_status":
            if (contract.analysis_status or "pending") != value:
                return False
        elif key == "processing_status":
            if contract.processing_status != value:
                return False
        elif key == "semantic_index_status":
            if (contract.semantic_index_status or "pending") != value:
                return False
        elif key == "is_archived":
            if bool(contract.is_archived) != bool(value):
                return False
        else:
            return False  # unknown keys never reach here (_scope_filters)
    return True


def _verify_against_postgres(
    db: Session, org_id, ordered_ids: list[str], scores: dict,
    include_archived: bool, filters: dict | None = None,
) -> list[Contract]:
    """Re-read every hit from org-scoped PostgreSQL, preserving ES order.

    This is the guarantee that PostgreSQL — not the index — decides whether a
    contract exists, who owns it and what its fields are (Step 7).
    """
    if not ordered_ids:
        return []
    try:
        uuids = [uuid.UUID(str(i)) for i in ordered_ids]
    except (ValueError, TypeError):
        return []
    rows = (
        db.query(Contract)
        .filter(Contract.organization_id == org_id, Contract.id.in_(uuids))
        .all()
    )
    by_id = {str(r.id): r for r in rows}
    verified = []
    for cid in ordered_ids:
        row = by_id.get(str(cid))
        if row is None:
            continue  # deleted (or never ours): drop it
        if row.is_archived and not include_archived:
            continue  # same archive policy as the list endpoint
        if not _matches_filters(row, filters):
            continue  # stale index copy: PostgreSQL has the final say
        verified.append(row)
    return verified


def search_contracts(
    db: Session,
    *,
    org_id,
    query: str,
    limit: int | None = None,
    offset: int = 0,
    sort: str = "relevance",
    include_archived: bool = False,
    filters: dict | None = None,
) -> dict:
    """Search contracts for ONE organization. Returns an application DTO."""
    q = (query or "").strip()
    if not q:
        # Step 15: an empty query is never sent to Elasticsearch — the caller
        # restores the normal PostgreSQL-backed listing instead.
        raise ValueError("Query must not be empty.")
    settings = get_settings()
    size = limit or settings.ELASTICSEARCH_SEARCH_LIMIT
    size = max(1, min(int(size), settings.ELASTICSEARCH_SEARCH_MAX_LIMIT))
    start = max(0, int(offset))
    if sort not in SEARCH_SORTS:
        raise ValueError(
            f"Unknown sort {sort!r}. "
            f"Expected one of: {', '.join(sorted(SEARCH_SORTS))}")

    # `total` must be PostgreSQL-verified (a stale or deleted document may not
    # be counted), so the whole window [0, offset+size) is fetched and checked
    # whenever it fits inside the server's max window. Deep pages beyond that
    # window fall back to asking Elasticsearch for page + exact hit count,
    # which is reported as an index-derived lower bound.
    exact_window = start + size <= settings.ELASTICSEARCH_SEARCH_MAX_LIMIT
    fetch = (start + size) if exact_window else size
    page_from = 0 if exact_window else start

    alias = search_index.ensure_index()
    body = build_search_body(
        q, org_id=org_id, limit=fetch, offset=page_from, sort=sort,
        include_archived=include_archived, filters=filters,
    )
    resp = es.search(index=alias, **body)

    hits = (resp.get("hits") or {}).get("hits") or []
    raw_total = (resp.get("hits") or {}).get("total") or {}
    es_total = int(raw_total.get("value") if isinstance(raw_total, dict)
                   else raw_total or 0)
    ordered_ids = [h.get("_id") for h in hits]
    scores = {h.get("_id"): h.get("_score") for h in hits}
    rows = _verify_against_postgres(db, org_id, ordered_ids, scores,
                                    include_archived, filters)

    if exact_window:
        # When Elasticsearch answered the window in full (fewer hits than
        # requested) it has nothing left to give: PostgreSQL's verified count
        # IS the exact total, so a stale or deleted document is never counted.
        # Otherwise matches continue past the window and the index count is
        # corrected by whatever verification had to drop here.
        dropped = len(hits) - len(rows)
        total = (len(rows) if len(hits) < fetch
                 else max(len(rows), es_total - dropped))
        rows = rows[start:start + size]
    else:
        dropped = len(hits) - len(rows)
        total = max(start + len(rows), es_total - dropped)

    results = [
        {
            "contract_id": str(r.id),
            "filename": r.filename,
            "counterparty": r.counterparty,
            "contract_type": r.contract_type,
            # Never-null category (Part 5) + star flag, read back from
            # PostgreSQL — the index only decides WHICH contracts match.
            "contract_category": r.contract_type or UNSPECIFIED,
            "is_important": bool(r.is_important),
            "processing_status": r.processing_status,
            "analysis_status": r.analysis_status,
            "semantic_index_status": r.semantic_index_status or "pending",
            "is_archived": bool(r.is_archived),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "score": round(float(scores.get(str(r.id)) or 0.0), 4),
        }
        for r in rows
    ]
    return {
        "query": q,
        "total": total,
        "results": results,
        # Server-applied page size (settings clamp), reported so the UI/docs
        # never overstate how much was fetched.
        "limit": size,
        "offset": start,
        "sort": sort,
        "took_ms": resp.get("took"),
        "index": alias,
    }


def search_status() -> dict:
    """Diagnostic for GET /api/v1/search/status (search health, nothing else)."""
    settings = get_settings()
    alias = es.index_name()
    if not es.es_url():
        return {
            "configured": False, "available": False, "index": None,
            "index_version": None, "physical_index": None,
            "server_version": None, "documents": None,
            "detail": "ELASTICSEARCH_URL is empty — contract search is not configured.",
        }
    try:
        info = es.cluster_info()
    except es.SearchUnavailableError as e:
        return {
            "configured": True, "available": False, "index": alias,
            "index_version": search_index.INDEX_VERSION,
            "physical_index": search_index.physical_index_name(alias),
            "server_version": None, "documents": None, "detail": str(e),
        }
    documents = None
    try:
        if es.index_exists(alias):
            documents = es.count(alias)
    except es.SearchUnavailableError:
        documents = None
    return {
        "configured": True,
        "available": True,
        "index": alias,
        "index_version": search_index.INDEX_VERSION,
        "physical_index": search_index.physical_index_name(alias),
        "server_version": (info.get("version") or {}).get("number"),
        "documents": documents,
        "detail": None,
    }
