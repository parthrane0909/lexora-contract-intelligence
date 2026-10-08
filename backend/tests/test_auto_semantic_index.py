"""Automatic semantic indexing (Phase 17B): queue → worker → index_contract
→ Qdrant, with its own status lifecycle.

Covers: enqueue only AFTER processing, no indexing before processing,
success, failure (processing stays `processed`), retry, idempotent repeats,
stale-point pruning on re-process, organization isolation, correct
contract/clause provenance, queue outage safety, and the recovery sweep.
Hermetic: hash embeddings + InMemoryVectorStore + fake Redis.
"""
import uuid

import pytest
from doc_builders import make_pdf
from doc_helpers import upload_and_process

from app.models import Clause, Contract, SemanticIndex
from app.modules.retrieval import auto_index, indexer
from app.modules.retrieval.vector_store import InMemoryVectorStore, VectorStoreError

PAGES = [
    ["1. Payment Terms", "Client shall pay all fees within 30 days of invoice."],
    ["8.2 Limitation of Liability", "Supplier liability is capped at $500,000."],
]


class _FakeRedis:
    def __init__(self, sink: list):
        self.sink = sink

    def lpush(self, name, *values):
        self.sink.extend((name, str(v)) for v in values)
        return len(values)


@pytest.fixture()
def hermetic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval.providers import reset_provider

    reset_provider()
    store = InMemoryVectorStore(collection="auto")
    queued: list = []
    monkeypatch.setattr(indexer, "_STORE", store)
    monkeypatch.setattr(auto_index, "_redis", lambda: _FakeRedis(queued))
    yield store, queued
    reset_provider()


def _contract(cid: str) -> Contract:
    from conftest import TestingSession

    db = TestingSession()
    try:
        return db.get(Contract, uuid.UUID(cid))
    finally:
        db.close()


def _run(cid: str):
    from conftest import TestingSession

    db = TestingSession()
    try:
        return auto_index.run_semantic_index(db, uuid.UUID(cid))
    finally:
        db.close()


def _rows(cid: str) -> list:
    from conftest import TestingSession

    db = TestingSession()
    try:
        return db.query(SemanticIndex).filter(
            SemanticIndex.contract_id == uuid.UUID(cid)).all()
    finally:
        db.close()


def _clause_ids(cid: str) -> set:
    from conftest import TestingSession

    db = TestingSession()
    try:
        return {str(c.id) for c in db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).all()}
    finally:
        db.close()


def _process(client, email):
    return upload_and_process(client, make_pdf(PAGES), "c.pdf", email)


# ------------------------------------------------------------- lifecycle ---


def test_processing_enqueues_index_job(client, hermetic):
    store, queued = hermetic
    token, cid, result = _process(client, "enqueue@x.com")
    assert result["status"] == "processed"
    assert queued == [(auto_index.INDEX_QUEUE, cid)]  # queued, not run inline
    c = _contract(cid)
    assert c.processing_status == "processed"
    assert c.semantic_index_status == "queued"
    assert _rows(cid) == [] and store._points == {}  # nothing embedded yet


def test_no_indexing_before_processing(client, hermetic):
    from conftest import TestingSession, auth, register

    store, queued = hermetic
    token = register(client, "unproc@x.com", "password123", "Org-unproc")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": ("u.pdf", make_pdf(PAGES), "application/octet-stream")})
    cid = r.json()["id"]
    c = _contract(cid)
    assert c.processing_status == "uploaded" and c.semantic_index_status == "pending"
    assert queued == []  # never queued before clauses exist

    out = _run(cid)  # manual attempt on an unprocessed contract
    assert out["status"] == "skipped"
    assert _contract(cid).semantic_index_status == "pending"
    assert _rows(cid) == [] and store._points == {}


def test_worker_indexes_after_processing(client, hermetic):
    from conftest import TestingSession

    from app.worker.worker import _run_index

    store, queued = hermetic
    token, cid, _ = _process(client, "worker@x.com")
    db = TestingSession()
    try:
        assert _run_index(db, uuid.UUID(cid), cid) == cid
    finally:
        db.close()
    c = _contract(cid)
    assert c.semantic_index_status == "indexed" and c.semantic_index_error is None
    assert c.processing_status == "processed"  # never touched by indexing
    rows = _rows(cid)
    assert len(rows) == 2 and all(r.status == "indexed" and r.vector_id for r in rows)
    assert len(store._points) == 2
    assert {p["payload"]["contract_id"] for p in store._points.values()} == {cid}
    assert {p["payload"]["org_id"] for p in store._points.values()} == {
        str(_contract(cid).organization_id)}


def test_api_exposes_index_status(client, hermetic):
    from conftest import auth

    token, cid, _ = _process(client, "apistatus@x.com")
    d = client.get(f"/api/v1/contracts/{cid}", headers=auth(token)).json()
    assert d["processing_status"] == "processed"
    assert d["semantic_index_status"] == "queued"
    listed = client.get("/api/v1/contracts", headers=auth(token)).json()
    assert listed[0]["semantic_index_status"] == "queued"


# ------------------------------------------------------ failure + retry ---


class _BrokenStore(InMemoryVectorStore):
    def upsert(self, points):
        raise VectorStoreError("Qdrant upsert failed (test)")


def test_index_failure_is_isolated_and_observable(client, hermetic, monkeypatch):
    store, _ = hermetic
    token, cid, _ = _process(client, "fail@x.com")
    broken = _BrokenStore(collection="broken")
    monkeypatch.setattr(indexer, "_STORE", broken)
    with pytest.raises(VectorStoreError):
        _run(cid)
    c = _contract(cid)
    assert c.processing_status == "processed"  # processing NOT rolled back
    # Indexing failure must not fail the analysis rollup either: nothing has
    # been classified or risk-analyzed yet, so the honest state is "pending"
    # (a vector-store outage never touches the risk pipeline).
    assert c.analysis_status == "pending"
    assert c.semantic_index_status == "failed"
    assert "Qdrant upsert failed" in (c.semantic_index_error or "")
    rows = _rows(cid)
    assert rows and all(r.status == "failed" and r.vector_id is None for r in rows)
    assert broken._points == {} and store._points == {}


def test_retry_after_failure_succeeds(client, hermetic, monkeypatch):
    store, _ = hermetic
    token, cid, _ = _process(client, "retry@x.com")
    monkeypatch.setattr(indexer, "_STORE", _BrokenStore(collection="broken"))
    with pytest.raises(VectorStoreError):
        _run(cid)
    assert _contract(cid).semantic_index_status == "failed"

    monkeypatch.setattr(indexer, "_STORE", store)  # backend restored → retry
    out = _run(cid)
    assert out["indexed"] == 2
    c = _contract(cid)
    assert c.semantic_index_status == "indexed" and c.semantic_index_error is None
    assert len(_rows(cid)) == 2 and all(r.status == "indexed" for r in _rows(cid))
    assert len(store._points) == 2


def test_failed_index_keeps_http_503_for_manual_retry(client, hermetic, monkeypatch):
    from conftest import auth

    token, cid, _ = _process(client, "retry503@x.com")
    monkeypatch.setattr(indexer, "_STORE", _BrokenStore(collection="broken"))
    r = client.post("/api/v1/retrieval/index", headers=auth(token),
                    json={"scope": "contract", "contract_id": cid})
    assert r.status_code == 503  # observable failure, not a silent 200
    assert _contract(cid).semantic_index_status == "failed"

    monkeypatch.setattr(indexer, "_STORE", hermetic[0])
    r2 = client.post("/api/v1/retrieval/index", headers=auth(token),
                     json={"scope": "contract", "contract_id": cid})
    assert r2.status_code == 200 and r2.json()["indexed"] == 2
    assert _contract(cid).semantic_index_status == "indexed"


def test_queue_outage_does_not_fail_processing(client, hermetic, monkeypatch):
    def boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr(auto_index, "_redis", boom)
    token, cid, result = _process(client, "redisdown@x.com")
    assert result["status"] == "processed"
    c = _contract(cid)
    assert c.processing_status == "processed"
    assert c.semantic_index_status == "pending"  # deferred, not failed


# ---------------------------------------------------------- idempotency ---


def test_repeated_indexing_creates_no_duplicates(client, hermetic):
    store, _ = hermetic
    token, cid, _ = _process(client, "idem@x.com")
    first = _run(cid)
    vids1 = sorted(r.vector_id for r in _rows(cid))
    assert first["indexed"] == 2 and len(store._points) == 2

    for _ in range(2):
        out = _run(cid)
        assert out["indexed"] == 2
    rows = _rows(cid)
    assert len(rows) == 2  # no duplicate semantic_index records
    assert sorted(r.vector_id for r in rows) == vids1  # same point ids
    assert len(store._points) == 2  # no duplicate Qdrant points
    assert _contract(cid).semantic_index_status == "indexed"


def test_reprocess_replaces_points_without_orphans(client, hermetic):
    from conftest import TestingSession

    from app.modules.document.pipeline import process_contract

    store, _ = hermetic
    token, cid, _ = _process(client, "reproc@x.com")
    _run(cid)
    old_clause_ids = _clause_ids(cid)
    assert len(old_clause_ids) == 2

    db = TestingSession()  # re-run the pipeline: clauses get NEW ids
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
    finally:
        db.close()
    new_clause_ids = _clause_ids(cid)
    assert new_clause_ids and not (new_clause_ids & old_clause_ids)

    _run(cid)
    assert len(_rows(cid)) == 2  # stale rows pruned
    assert len(store._points) == 2  # stale points pruned, not duplicated
    point_clause_ids = {p["payload"]["object_id"] for p in store._points.values()}
    assert point_clause_ids == new_clause_ids


# ----------------------------------------------------- org isolation etc. ---


def test_org_isolation_and_provenance(client, hermetic):
    from conftest import TestingSession, auth

    from app.modules.retrieval.search import semantic_search

    store, _ = hermetic
    token_a, cid_a, _ = _process(client, "own@x.com")
    _run(cid_a)
    token_b, cid_b, _ = upload_and_process(client, make_pdf(PAGES), "d.pdf",
                                            "other@x.com")
    _run(cid_b)
    assert len(store._points) == 4  # both orgs share one collection

    db = TestingSession()
    try:
        org_a = _contract(cid_a).organization_id
        org_b = _contract(cid_b).organization_id
        a = semantic_search(db, org_id=org_a, query="supplier liability capped",
                            top_k=10)
        b = semantic_search(db, org_id=org_b, query="supplier liability capped",
                            top_k=10)
    finally:
        db.close()

    assert {r["contract_id"] for r in a["results"]} == {cid_a}  # no leakage
    assert {r["contract_id"] for r in b["results"]} == {cid_b}
    assert len(a["results"]) == 2 and len(b["results"]) == 2
    assert all(r["object_id"] in _clause_ids(cid_a) for r in a["results"])
    assert all(r["contract_filename"] == "c.pdf" for r in a["results"])

    # Same guarantees through the HTTP API (membership-scoped org filter).
    for token, cid in ((token_a, cid_a), (token_b, cid_b)):
        r = client.post("/api/v1/retrieval/search", headers=auth(token),
                        json={"query": "supplier liability capped", "top_k": 10})
        assert r.status_code == 200
        assert {x["contract_id"] for x in r.json()["results"]} == {cid}


def test_category_filter_requires_classifications(client, hermetic):
    from conftest import TestingSession

    from app.modules.retrieval.search import semantic_search

    _, _ = hermetic
    token, cid, _ = _process(client, "cat@x.com")
    _run(cid)
    db = TestingSession()
    try:
        org = _contract(cid).organization_id
        unfiltered = semantic_search(db, org_id=org, query="payment terms", top_k=5)
        liability = semantic_search(db, org_id=org, query="payment terms",
                                    top_k=5, category="liability")
        payment = semantic_search(db, org_id=org, query="payment terms",
                                  top_k=5, category="payment")
    finally:
        db.close()
    assert len(unfiltered["results"]) == 2
    # No clause_classifications rows → no category on the points → an explicit
    # category filter honestly excludes them (same reason category=liability
    # returned 0 in the audit). Filtering is never loosened to force hits.
    assert liability["results"] == [] and payment["results"] == []


def test_recovery_sweep_requeues_unfinished_jobs(client, hermetic):
    store, queued = hermetic
    token, cid, _ = _process(client, "sweep@x.com")
    queued.clear()

    from conftest import TestingSession

    db = TestingSession()
    try:
        c = db.get(Contract, uuid.UUID(cid))
        c.semantic_index_status = "pending"  # e.g. Redis was down at processing time
        db.commit()
        assert auto_index.recover_stalled(db) == 1
        assert (auto_index.INDEX_QUEUE, cid) in queued
        assert c.semantic_index_status == "queued"

        # A live worker already owns `queued`/`processing`: the periodic sweep
        # must not pile up duplicate jobs for them...
        for state in ("queued", "processing"):
            queued.clear()
            c.semantic_index_status = state
            db.commit()
            assert auto_index.recover_stalled(db) == 0
            assert queued == []
            # ...but a restart lost those jobs, so the startup sweep re-queues.
            assert auto_index.recover_stalled(db, include_inflight=True) == 1
            assert (auto_index.INDEX_QUEUE, cid) in queued

        # `failed` is never auto-retried — it waits for an explicit retry.
        queued.clear()
        c.semantic_index_status = "failed"
        db.commit()
        assert auto_index.recover_stalled(db) == 0
        assert auto_index.recover_stalled(db, include_inflight=True) == 0
        assert queued == []
    finally:
        db.close()


def test_worker_index_job_ignores_foreign_ids(client, hermetic):
    from conftest import TestingSession

    from app.worker.worker import _run_index

    _, _ = hermetic
    db = TestingSession()
    try:
        foreign = str(uuid.uuid4())
        assert _run_index(db, uuid.UUID(foreign), foreign) == foreign
    finally:
        db.close()
