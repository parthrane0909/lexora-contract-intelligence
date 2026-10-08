"""Live Qdrant integration (skipped when the daemon is unreachable).

Proves the QdrantVectorStore honors the same contract as the in-memory
backend: isolation filters, idempotent upserts, dimension guard.
"""
import pytest

from app.modules.retrieval.vector_store import QdrantVectorStore, VectorStoreError, org_filter


@pytest.fixture()
def live():
    try:
        store = QdrantVectorStore(collection="test_contract_clauses_phase6")
    except VectorStoreError as e:
        pytest.skip(f"Qdrant unreachable: {e}")
    try:
        store.ensure_collection(2)
    except VectorStoreError as e:
        pytest.skip(f"Qdrant setup failed: {e}")
    yield store
    try:
        store._client.delete_collection("test_contract_clauses_phase6")
    except Exception:
        pass


def test_live_upsert_search_isolation(live):
    import uuid

    a1, b1 = str(uuid.uuid4()), str(uuid.uuid4())
    live.upsert([
        {"id": a1, "vector": [1.0, 0.0],
         "payload": {"org_id": "A", "object_type": "clause", "embedding_model": "m"}},
        {"id": b1, "vector": [1.0, 0.0],
         "payload": {"org_id": "B", "object_type": "clause", "embedding_model": "m"}},
    ])
    hits = live.search([1.0, 0.0], 10, org_filter("A"))
    assert [h["id"] for h in hits] == [a1]
    assert hits[0]["score"] > 0.99
    # idempotent re-upsert: same ID, no duplicates
    live.upsert([{"id": a1, "vector": [1.0, 0.0],
                  "payload": {"org_id": "A", "object_type": "clause", "embedding_model": "m"}}])
    assert len(live.search([1.0, 0.0], 10, org_filter("A"))) == 1


def test_live_dimension_guard(live):
    with pytest.raises(VectorStoreError):
        live.ensure_collection(999)
