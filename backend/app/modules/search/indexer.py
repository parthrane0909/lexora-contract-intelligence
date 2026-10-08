"""Indexing lifecycle: how a contract enters, updates and leaves the index.

Events (Part 2 Step 8) and the call site that publishes each one:

    UPLOAD            contracts/service.create_contract   → index_contract()
    ANALYSIS QUEUED   contracts/service.enqueue_analysis  → index_contract()
    METADATA UPDATE   contracts/router.update_metadata    → index_contract()
    ARCHIVE           contracts/router.archive            → index_contract()
      (an archived contract stays INDEXED with is_archived=true and is
       filtered out of default results — the row still exists in PostgreSQL,
       so indexing the state matches the application's own semantics)
    STATUS CHANGE     risk_engine/state.refresh_analysis_status → index_contract()
                      document/pipeline._fail                    → index_contract()
    RENAME            no rename endpoint exists (filename is immutable);
                      it would publish through index_contract() like PATCH
    HARD DELETE       no hard-delete endpoint exists today — remove_contract()
                      is ready for one, and the search API's PostgreSQL
                      verification already drops stale ids (defence in depth)

Every hook is BEST-EFFORT and never raises: an Elasticsearch outage must not
fail an upload, a metadata edit, an analysis write — and must never touch
PostgreSQL. Nothing here writes to PostgreSQL or to Qdrant.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Contract
from app.modules.search import es, index as search_index


def index_contract(contract: Contract) -> bool:
    """Upsert ONE contract's search document. Returns True when published."""
    contract_id = "?"
    try:
        contract_id = str(contract.id)
        alias = search_index.ensure_index()
        es.index_document(alias, contract_id, search_index.document_for(contract))
        return True
    except es.SearchUnavailableError as e:
        # Not configured at all is a normal local-dev state — stay quiet.
        # Configured-but-failing is an operational problem worth logging.
        if es.es_url():
            print(f"search: index update failed for {contract_id}: {e}")
        return False
    except Exception as e:  # noqa: BLE001 — indexing must never break a write
        print(f"search: index update failed for {contract_id}: {e}")
        return False


def remove_contract(contract_id, org_id) -> bool:
    """Drop ONE contract's search document (hard-delete path; idempotent)."""
    try:
        alias = search_index.ensure_index()
        es.delete_document(alias, str(contract_id))
        return True
    except es.SearchUnavailableError as e:
        if es.es_url():
            print(f"search: index delete failed for {contract_id}: {e}")
        return False
    except Exception as e:  # noqa: BLE001
        print(f"search: index delete failed for {contract_id}: {e}")
        return False


def _bulk_errors(errors: list, limit: int = 20) -> list[dict]:
    out = []
    for err in errors[:limit]:
        payload = {}
        for op in ("index", "create", "update", "delete"):
            if isinstance(err, dict) and op in err:
                payload = err[op] or {}
                break
        if not payload and isinstance(err, dict):
            payload = err
        reason = payload.get("error")
        if isinstance(reason, dict):
            reason = reason.get("reason") or reason.get("type")
        out.append({"contract_id": payload.get("_id"), "reason": str(reason)})
    return out


def reindex_org(db: Session, org_id) -> dict:
    """Idempotent, organization-scoped backfill for the EXISTING portfolio.

    * reads every contract row of THIS organization from PostgreSQL;
    * bulk-upserts them (contract id = document id → running it twice changes
      nothing and cannot duplicate);
    * then deletes index documents of this organization whose contract no
      longer exists in PostgreSQL (reconciliation, so a missed delete cannot
      haunt search forever).

    Never writes to PostgreSQL, never touches Qdrant, never reads another
    organization's rows.
    """
    alias = search_index.ensure_index()
    contracts = (
        db.query(Contract).filter(Contract.organization_id == org_id).all()
    )
    actions = [
        {"_index": alias, "_id": str(c.id), "_source": search_index.document_for(c)}
        for c in contracts
    ]
    indexed, errors = es.bulk_index(actions)
    current_ids = [str(c.id) for c in contracts]
    stale_query: dict = {"bool": {"filter": [{"term": {"org_id": str(org_id)}}]}}
    if current_ids:
        stale_query["bool"]["must_not"] = [{"ids": {"values": current_ids}}]
    removed = es.delete_by_query(alias, stale_query)
    return {
        "index": alias,
        "index_version": search_index.INDEX_VERSION,
        "contracts_in_postgres": len(contracts),
        "indexed": int(indexed),
        "removed": int(removed),
        "failed": len(errors),
        "errors": _bulk_errors(errors),
    }
