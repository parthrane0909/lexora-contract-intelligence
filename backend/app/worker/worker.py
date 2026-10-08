"""Background worker (spec Module 30/§28).

Polls Redis and runs two job types on the same loop:

* `analysis_queue`      — document pipeline: parse → structure → segment →
  persist → statuses/audit (Phase 2+; Phase 3+ extends process_contract
  without changing this loop).
* `semantic_index_queue` — automatic semantic indexing (Phase 17B): after a
  contract has been processed, embed its clauses and upsert them to Qdrant
  via the existing `index_contract` service. Runs as a SEPARATE job so the
  Qdrant/embedding work never sits inside the document-processing
  transaction, and so a vector-store failure only flips
  `semantic_index_status` to `failed` (retryable), never processing state.

A periodic recovery sweep re-queues contracts stuck in pending/queued/
processing (Redis outage, worker restart, contract indexed before this
feature existed). Model versions are recorded per run (spec §30/§32).
"""
import time
import uuid

from app.db.base import Base
from app.db.session import get_engine, get_session_factory
from app.models import Contract

ANALYSIS_QUEUE = "analysis_queue"
INDEX_QUEUE = "semantic_index_queue"
RECOVERY_INTERVAL_S = 60


def _connect():
    import redis

    from app.core.config import get_settings

    return redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=2)


def _run_analysis(db, uid: uuid.UUID, cid: str) -> str:
    try:
        c = db.get(Contract, uid)
        if c is None or c.processing_status != "queued":
            return cid  # stale/duplicate delivery: idempotent no-op
        from app.modules.document.pipeline import process_contract

        process_contract(db, uid)
        return cid
    except Exception as e:  # last-resort guard: never kill the loop
        print(f"worker: failed to process {cid}: {e}")
        return cid


def _run_index(db, uid: uuid.UUID, cid: str) -> str:
    try:
        c = db.get(Contract, uid)
        if c is None:
            return cid  # foreign/test id: nothing to index
        if c.processing_status != "processed":
            return cid  # clauses not ready; a later run re-queues it
        from app.modules.retrieval.auto_index import run_semantic_index

        run_semantic_index(db, uid)  # sets indexed/failed itself
    except Exception as e:  # indexing failure must never kill the loop
        print(f"worker: semantic indexing failed for {cid}: {e}")
    return cid


def _recover_stalled(include_inflight: bool = False) -> int:
    from app.modules.retrieval.auto_index import recover_stalled

    db = get_session_factory()()
    try:
        return recover_stalled(db, include_inflight=include_inflight)
    finally:
        db.close()


def _poll_once() -> str | None:
    try:
        r = _connect()
        item = r.blpop([ANALYSIS_QUEUE, INDEX_QUEUE], timeout=5)
    except Exception:
        time.sleep(5)
        return None
    if not item:
        return None
    queue, raw = item
    queue = queue.decode() if isinstance(queue, bytes) else str(queue)
    cid = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        uid = uuid.UUID(cid)
    except ValueError:
        return None
    db = get_session_factory()()
    try:
        if queue == INDEX_QUEUE:
            return _run_index(db, uid, cid)
        return _run_analysis(db, uid, cid)
    finally:
        db.close()


def main():
    Base.metadata.create_all(bind=get_engine())
    print(f"worker: polling {ANALYSIS_QUEUE} + {INDEX_QUEUE} "
          f"(document pipeline, semantic indexing)...")
    last_recovery = 0.0
    first = True
    while True:
        if time.time() - last_recovery >= RECOVERY_INTERVAL_S:
            last_recovery = time.time()
            # First sweep also recovers `queued`/`processing` jobs whose
            # worker died; later sweeps only pick up never-pushed `pending`.
            try:
                n = _recover_stalled(include_inflight=first)
                first = False
                if n:
                    print(f"worker: re-queued {n} unfinished semantic index job(s)")
            except Exception as e:
                print(f"worker: recovery sweep failed: {e}")
                first = False
        _poll_once()


if __name__ == "__main__":
    main()
