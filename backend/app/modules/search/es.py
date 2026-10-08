"""Elasticsearch client access — the ONLY place a cluster URL is resolved.

Contract discovery (Part 2). Rules:
  * Configuration comes from app.core.config (ELASTICSEARCH_*); no URL is
    written anywhere else in the codebase.
  * Bounded timeouts, no transport retries: search must degrade to a clear
    503 quickly instead of hanging a request (or an indexing hook).
  * Every cluster failure is translated into SearchUnavailableError so the
    API can answer 503 with a search-specific message.
  * The client library is imported lazily: a missing/broken install must not
    stop the API from booting (the rest of the application does not need it).
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings


class SearchUnavailableError(RuntimeError):
    """Elasticsearch is not configured, unreachable, or answered an error."""


def es_url() -> str:
    """Cluster URL. Single access point so tests can redirect the client."""
    return get_settings().ELASTICSEARCH_URL


def index_name() -> str:
    """Alias every read/write uses (points at the versioned physical index)."""
    return get_settings().ELASTICSEARCH_INDEX


@lru_cache(maxsize=8)
def _client_for(url: str, timeout_s: float):
    from elasticsearch import Elasticsearch  # lazy import (see module docstring)

    # max_retries=0 + retry_on_timeout=False: one bounded attempt. A slow or
    # dead cluster must not stall an upload hook or a search request.
    return Elasticsearch(
        url, request_timeout=timeout_s, retry_on_timeout=False, max_retries=0
    )


def client():
    """Return a client, raising SearchUnavailableError if unusable."""
    url = es_url()
    if not url:
        raise SearchUnavailableError(
            "Elasticsearch is not configured (ELASTICSEARCH_URL is empty)."
        )
    try:
        return _client_for(url, get_settings().ELASTICSEARCH_TIMEOUT_S)
    except ImportError as e:  # package missing → still a *clear* failure
        raise SearchUnavailableError(
            f"the 'elasticsearch' Python client is not installed: {e}"
        ) from e


def _wrap(e: Exception) -> SearchUnavailableError:
    return SearchUnavailableError(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- read path


def cluster_info() -> dict:
    """GET / — raises SearchUnavailableError when the cluster is unreachable."""
    try:
        return client().info()
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001 — transport/HTTP errors are all "unavailable"
        raise _wrap(e) from e


def search(index: str, **kwargs) -> dict:
    """Run a search request against the alias.

    Body keys use the REST field names, but `from` is a Python keyword, so a
    pagination window is translated to the client's `from_` argument here.
    """
    if "from" in kwargs:
        kwargs["from_"] = kwargs.pop("from")
    try:
        return client().search(index=index, **kwargs)
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


def count(index: str) -> int:
    try:
        resp = client().count(index=index)
        return int(resp.get("count", 0))
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


# --------------------------------------------------------------- index admin


def index_exists(name: str) -> bool:
    try:
        return bool(client().indices.exists(index=name))
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


def create_index(name: str, settings: dict, mappings: dict) -> None:
    try:
        client().indices.create(index=name, settings=settings, mappings=mappings)
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


def delete_index(name: str) -> None:
    """Drop an index if present (404 ignored)."""
    try:
        client().options(ignore_status=404).indices.delete(index=name)
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        if "index_not_found" not in str(e) and "404" not in str(e):
            raise _wrap(e) from e


def alias_targets(alias: str) -> list[str]:
    """Indices the alias currently points at ([] when absent)."""
    try:
        resp = client().indices.get_alias(name=alias)
        return sorted(resp.keys())
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001 — 404 = alias does not exist yet
        if "index_not_found" in str(e) or "404" in str(e):
            return []
        raise _wrap(e) from e


def repoint_alias(alias: str, index: str) -> None:
    """Atomically point `alias` at `index`, removing it from any older index
    (the versioned reindex strategy: callers only ever use the alias)."""
    targets = alias_targets(alias)
    if targets == [index]:
        return  # already correct — no cluster write
    actions: list[dict] = [
        {"remove": {"index": old, "alias": alias}} for old in targets if old != index
    ]
    actions.append({"add": {"index": index, "alias": alias}})
    try:
        client().indices.update_aliases(body={"actions": actions})
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


def refresh(index: str) -> None:
    """Force a refresh so freshly written documents are searchable."""
    try:
        client().indices.refresh(index=index)
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


# --------------------------------------------------------------- write path


def index_document(index: str, doc_id: str, document: dict) -> None:
    try:
        # refresh=True: contract metadata changes must be visible to the very
        # next keystroke-level query, not up to 1s later.
        client().index(index=index, id=doc_id, document=document, refresh=True)
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


def delete_document(index: str, doc_id: str) -> None:
    """Delete by id; a missing document is success (idempotent)."""
    try:
        client().delete(index=index, id=doc_id, refresh=True)
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        if "not_found" in str(e) or "404" in str(e):
            return
        raise _wrap(e) from e


def bulk_index(actions: list[dict]) -> tuple[int, list]:
    """Bulk upsert. Returns (indexed_count, per-document errors)."""
    from elasticsearch.helpers import bulk  # lazy, same package

    if not actions:
        return 0, []
    try:
        # raise_on_error=False: report partial failures instead of aborting,
        # so reindex can answer "indexed N, failed M".
        indexed, errors = bulk(
            client(), actions, raise_on_error=False, raise_on_exception=False,
            refresh=True,
        )
        return int(indexed), list(errors or [])
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e


def delete_by_query(index: str, query: dict) -> int:
    """Delete documents matching `query`; returns the deleted count."""
    try:
        resp = client().delete_by_query(index=index, query=query, refresh=True)
        return int(resp.get("deleted", 0))
    except SearchUnavailableError:
        raise
    except Exception as e:  # noqa: BLE001
        raise _wrap(e) from e
