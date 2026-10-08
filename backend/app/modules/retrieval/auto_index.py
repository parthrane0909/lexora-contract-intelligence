"""Automatic semantic indexing (Phase 17B).

Wires the EXISTING indexing service (`index_contract`) into the normal
contract lifecycle, without ever running a slow Qdrant/embedding call
inside the document-processing transaction:

    process_contract commits  →  enqueue_semantic_index() pushes a job
    →  existing worker pops `semantic_index_queue`  →  run_semantic_index()
    →  embeddings  →  Qdrant upsert  →  contract.semantic_index_status

There is exactly one indexer (`app.modules.retrieval.indexer`); this module
only supplies the queue, the status lifecycle and stale-point pruning.
States: pending → queued → processing → indexed | failed (failed is
retryable via POST /retrieval/index or the worker's recovery sweep).
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Clause, Contract, SemanticIndex
from app.modules.audit.service import record
from app.modules.retrieval.vector_store import org_filter

INDEX_QUEUE = "semantic_index_queue"

#: statuses a STARTUP sweep may re-queue: after a restart, `queued`/`processing`
#: jobs are definitively lost (their worker is gone), and `pending` means the
#: Redis push never happened. `failed` is never auto-retried — it is an
#: explicit, user-visible state with its own Retry action.
STARTUP_RECOVERABLE = ("pending", "queued", "processing")
#: The periodic sweep only re-queues `pending` — jobs that were never pushed.
#: Re-queueing `queued`/`processing` while a live worker still owns them would
#: pile up duplicate jobs for long-running index operations.
PERIODIC_RECOVERABLE = ("pending",)


def _redis():
    import redis

    from app.core.config import get_settings

    return redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1)


def enqueue_semantic_index(db: Session, contract_id: uuid.UUID) -> bool:
    """Queue semantic indexing for a contract. Called only AFTER processing
    has committed its clauses. Never raises: a Redis outage must not fail or
    roll back document processing — the job simply stays `pending` and the
    worker's recovery sweep re-queues it later."""
    contract = db.get(Contract, contract_id)
    if contract is None:
        return False
    if contract.processing_status != "processed":
        return False  # clauses must exist before anything is embedded
    contract.semantic_index_status = "queued"
    contract.semantic_index_error = None
    db.commit()
    pushed = False
    try:
        _redis().lpush(INDEX_QUEUE, str(contract.id))
        pushed = True
    except Exception:
        pass
    if not pushed:
        contract.semantic_index_status = "pending"  # deferred, not failed
        db.commit()
    record(db, action="contract.semantic_index_queued" if pushed
           else "contract.semantic_index_deferred",
           organization_id=contract.organization_id, user_id=contract.uploaded_by,
           object_type="contract", object_id=str(contract.id))
    return pushed


def _prune_stale(db: Session, contract: Contract, *, provider, store) -> dict:
    """Re-processing replaces clause rows with NEW ids, so an earlier index
    run leaves rows/points for clauses that no longer exist. Drop them here
    so re-indexing never leaves duplicate or dangling vectors behind.
    Points are deleted before their state rows: if we die in between, the
    rows survive and the next run retries the same idempotent delete."""
    live = {str(c.id) for c in
            db.query(Clause).filter(Clause.contract_id == contract.id).all()}
    rows = (db.query(SemanticIndex)
            .filter(SemanticIndex.contract_id == contract.id,
                    SemanticIndex.object_type == "clause").all())
    stale_rows = [r for r in rows if r.object_id not in live]
    stale_ids = [r.vector_id for r in stale_rows if r.vector_id]

    # Best-effort sweep for points that outlived their state row (crash
    # between upsert and commit). Failure here is reported by index_contract.
    try:
        store.ensure_collection(provider.dimension)
        hits = store.search([0.0] * provider.dimension, top_k=10000,
                            flt=org_filter(str(contract.organization_id),
                                           {"contract_id": str(contract.id)}))
        known = set(stale_ids)
        stale_ids += [h["id"] for h in hits
                      if h["payload"].get("object_id") not in live
                      and h["id"] not in known]
    except Exception:
        pass

    deleted_points = store.delete(stale_ids) if stale_ids else 0
    deleted_rows = 0
    if stale_rows:
        db.query(SemanticIndex).filter(
            SemanticIndex.id.in_([r.id for r in stale_rows])
        ).delete(synchronize_session=False)
        db.commit()
        deleted_rows = len(stale_rows)
    return {"deleted_points": deleted_points, "deleted_rows": deleted_rows}


def run_semantic_index(db: Session, contract_id: uuid.UUID, *,
                       triggered_by: uuid.UUID | None = None) -> dict:
    """Index one processed contract (worker job + manual retry share this).

    On success `semantic_index_status` becomes `indexed`; on ANY failure it
    becomes `failed` with the reason stored, and the original exception is
    re-raised so the HTTP retry path keeps returning 503 for a dead vector
    store. `processing_status` is never touched."""
    from app.modules.retrieval.indexer import get_provider, get_store, index_contract

    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    if contract.processing_status != "processed":
        return {"status": "skipped", "indexed": 0, "failed": [],
                "skipped_empty": 0, "reason": "Contract is not processed yet."}
    contract.semantic_index_status = "processing"
    contract.semantic_index_error = None
    db.commit()
    try:
        provider = get_provider()
        store = get_store()
        pruned = _prune_stale(db, contract, provider=provider, store=store)
        result = index_contract(db, contract.id, provider=provider, store=store,
                                triggered_by=triggered_by)
    except Exception as e:
        _mark_failed(db, contract_id, e, triggered_by=triggered_by)
        raise
    done = db.get(Contract, contract_id)
    if done is not None:
        done.semantic_index_status = "indexed"
        done.semantic_index_error = None
        db.commit()
    return {**result, **pruned, "index_status": "indexed"}


def _mark_failed(db: Session, contract_id: uuid.UUID, error: Exception,
                 *, triggered_by: uuid.UUID | None = None) -> None:
    """Persist the failure as its own state (never raises — the caller is
    already unwinding with the real error)."""
    try:
        db.rollback()
        contract = db.get(Contract, contract_id)
        if contract is None:
            return
        contract.semantic_index_status = "failed"
        contract.semantic_index_error = str(error)[:2000]
        db.commit()
        record(db, action="contract.semantic_index_failed",
               organization_id=contract.organization_id, user_id=triggered_by,
               object_type="contract", object_id=str(contract.id),
               detail=str(error)[:500])
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def recover_stalled(db: Session, *, include_inflight: bool = False) -> int:
    """Re-queue contracts whose indexing never reached a terminal state.

    * `include_inflight=True` (worker startup): `pending` (Redis push never
      happened), `queued` and `processing` (their worker died) are all
      recoverable — idempotent re-indexing makes the retry safe.
    * periodic (default): only `pending`, because a live worker already owns
      `queued`/`processing` and re-queueing those would create duplicate jobs.

    `failed` is never auto-retried — it stays visible until a human retries.
    """
    states = STARTUP_RECOVERABLE if include_inflight else PERIODIC_RECOVERABLE
    rows = (db.query(Contract)
            .filter(Contract.processing_status == "processed",
                    Contract.semantic_index_status.in_(states))
            .limit(200).all())
    ids = [str(c.id) for c in rows]
    if not ids:
        return 0
    try:
        _redis().lpush(INDEX_QUEUE, *ids)
    except Exception:
        return 0
    for c in rows:
        c.semantic_index_status = "queued"
    db.commit()
    return len(ids)
