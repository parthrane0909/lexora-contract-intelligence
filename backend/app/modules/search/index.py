"""Contract search index: versioned index/alias, mapping, analyzers, documents.

Versioned reindex strategy (Part 2 Step 4)
------------------------------------------
    physical index : contract_search_v<N>   (INDEX_VERSION, currently 2)
    alias          : contract_search      (settings.ELASTICSEARCH_INDEX)
Every read and write goes through the ALIAS. To change the mapping, bump
INDEX_VERSION, reindex into <alias>_v2 (POST /api/v1/search/reindex-contracts)
and the alias is repointed atomically — callers never need to know.

What is indexed (Part 2 Step 4, extended in Part 5)
---------------------------------------------------
Only fields that already exist on app.models.Contract — nothing is invented
here. Part 5 added the two metadata fields the Contracts page needs:

    contract_category   keyword  never-null copy of contract_type, so an
                                 absent category is the explicit value
                                 "Other / Unspecified" and a term filter
                                 means the same thing as PostgreSQL's
                                 `contract_type IS NULL` check
    is_important        boolean  the user-controlled star flag

Search-as-you-type strategy (Part 2 Step 5)
------------------------------------------
    filename              search_as_you_type  → bool_prefix queries
                          (raw filename, separators are token boundaries)
    filename_normalized   text(norm)           → exact / phrase / all-words
    filename_normalized.edge  edge_ngram 1..24 → prefix ("vend" → vendor)
                          **search_analyzer=norm**: only INDEXING produces
                          n-grams. If the query were analyzed with the edge
                          analyzer too, a nonsense word like "nonexistent"
                          would collapse to "n" and match "nda" — measured
                          and fixed during implementation.
    .raw (keyword)        exact normalized filename, biggest ranking boost
Normalization (index.py:normalize_filename) is applied identically at index
and query time, so `_`, `-`, `.`, spaces and `.pdf` never have to be typed:
    '02_balanced_saas_agreement.pdf' → '02 balanced saas agreement'
Fuzziness (typo tolerance) is applied ONLY to filename_normalized — it is
deliberately not spread over every field.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from app.models import Contract
from app.modules.contracts.category import UNSPECIFIED
from app.modules.search import es

# Bump when MAPPING/SETTINGS change incompatibly, then reindex (new physical
# index, alias repointed by ensure_index()).
# v2 (Part 5): + contract_category (keyword), + is_important (boolean).
INDEX_VERSION = 2

# --- analysis -------------------------------------------------------------
# edge_ngram min_gram=1: results must update from the FIRST keystroke ("b").
# max_gram=24: covers realistic filename tokens; preserve_original keeps the
# whole token as well.
_FILENAME_EDGE_FILTER = {
    "type": "edge_ngram",
    "min_gram": 1,
    "max_gram": 24,
    "preserve_original": True,
}

SETTINGS = {
    "number_of_shards": 1,          # single-node local cluster
    "number_of_replicas": 0,        # no replicas to allocate on one node
    "analysis": {
        "filter": {"filename_edge": _FILENAME_EDGE_FILTER},
        "analyzer": {
            # Query/index analyzer for human-normalized text.
            "filename_norm": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding"],
            },
            # INDEX-TIME ONLY: adds prefix tokens (search_analyzer overrides
            # it at query time — see module docstring).
            "filename_norm_edge": {
                "type": "custom",
                "tokenizer": "standard",
                "filter": ["lowercase", "asciifolding", "filename_edge"],
            },
        },
    },
}

MAPPING = {
    # Reject unknown fields loudly instead of silently mapping junk.
    "dynamic": "strict",
    "properties": {
        "contract_id": {"type": "keyword"},
        # Part 2 Step 6: EVERY document carries its org and EVERY query
        # filters on it. Never removed, never optional.
        "org_id": {"type": "keyword"},
        # Raw filename, as the user sees it (search_as_you_type).
        "filename": {"type": "search_as_you_type"},
        "filename_normalized": {
            "type": "text",
            "analyzer": "filename_norm",
            "fields": {
                "edge": {
                    "type": "text",
                    "analyzer": "filename_norm_edge",
                    "search_analyzer": "filename_norm",
                },
                "raw": {"type": "keyword", "normalizer": "lowercase"},
            },
        },
        # Member-supplied metadata (Phase 11) — real columns, useful to find a
        # contract by counterparty or type. Prefix/fuzzy is NOT applied here.
        "counterparty": {"type": "text", "analyzer": "filename_norm"},
        "contract_type": {
            "type": "text",
            "analyzer": "filename_norm",
            "fields": {"raw": {"type": "keyword"}},  # structured filter target
        },
        # Part 5: filter/sort targets for the Contracts page.
        # contract_category is the never-null copy of contract_type (missing
        # category == the literal "Other / Unspecified"), so a term filter on
        # it behaves exactly like PostgreSQL's category filter.
        "contract_category": {"type": "keyword"},
        # User-controlled star flag — organizational, never a risk score.
        "is_important": {"type": "boolean"},
        # Status copies: used for FILTERING only; displayed values always
        # come from PostgreSQL (service.search_contracts).
        "analysis_status": {"type": "keyword"},
        "processing_status": {"type": "keyword"},
        "semantic_index_status": {"type": "keyword"},
        # Archive state is indexed and filtered (not deleted), matching the
        # application's own semantics: archived rows still exist in PG.
        "is_archived": {"type": "boolean"},
        "created_at": {"type": "date"},
        "updated_at": {"type": "date"},
    },
}


def normalize_filename(filename: str) -> str:
    """Human-search form of a filename — applied identically when indexing
    and when querying, so separators and the extension are never typed.

        '02_balanced_saas_agreement.pdf' -> '02 balanced saas agreement'
        'Risk-Vendor (final).PDF'        -> 'risk vendor final'
    """
    text = (filename or "").strip()
    text = re.sub(r"\.(pdf|docx)$", "", text, flags=re.IGNORECASE)
    # Any run of non-word characters (and `_`, which \W excludes) → one space.
    text = re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().lower()


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def document_for(contract: Contract) -> dict:
    """The search copy of one contract row. PostgreSQL stays authoritative:
    this document is only ever used to answer 'which contract matches?'."""
    return {
        "contract_id": str(contract.id),
        "org_id": str(contract.organization_id),
        "filename": contract.filename,
        "filename_normalized": normalize_filename(contract.filename),
        "counterparty": contract.counterparty or "",
        "contract_type": contract.contract_type or "",
        # Part 5: never-null category copy for exact term filters (see MAPPING).
        "contract_category": contract.contract_type or UNSPECIFIED,
        "is_important": bool(contract.is_important),
        "analysis_status": contract.analysis_status or "pending",
        "processing_status": contract.processing_status or "uploaded",
        "semantic_index_status": contract.semantic_index_status or "pending",
        "is_archived": bool(contract.is_archived),
        "created_at": _iso(contract.created_at),
        "updated_at": _iso(contract.updated_at),
    }


def physical_index_name(alias: str | None = None) -> str:
    return f"{alias or es.index_name()}_v{INDEX_VERSION}"


def ensure_index(force: bool = False) -> str:
    """Create the versioned index + alias if missing (idempotent).

    force=True drops the current physical index first — test fixtures only.
    Returns the alias every caller should keep using.
    """
    alias = es.index_name()
    physical = physical_index_name(alias)
    if force:
        es.delete_index(physical)
    if not es.index_exists(physical):
        es.create_index(physical, SETTINGS, MAPPING)
    # (Re)point the alias at the current version — no-op when already correct.
    es.repoint_alias(alias, physical)
    return alias
