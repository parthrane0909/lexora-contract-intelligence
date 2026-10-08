"""Vector-store units: CRUD, filters, isolation, dimensions (in-memory).

Live-Qdrant integration lives in test_vector_store_live.py (skipped when
the daemon is unreachable) — this file never needs a daemon.
"""
import pytest

from app.modules.retrieval.vector_store import (InMemoryVectorStore, VectorStoreError,
                                                org_filter, point_id)


def _pts():
    return [
        {"id": "p1", "vector": [1.0, 0.0], "payload": {"org_id": "A", "object_type": "clause"}},
        {"id": "p2", "vector": [0.0, 1.0], "payload": {"org_id": "A", "object_type": "policy"}},
        {"id": "p3", "vector": [1.0, 0.0], "payload": {"org_id": "B", "object_type": "clause"}},
    ]


def test_point_ids_deterministic_and_scoped():
    a = point_id("orgA", "clause", "c1", "m")
    assert a == point_id("orgA", "clause", "c1", "m")  # idempotent re-index
    assert len({a, point_id("orgB", "clause", "c1", "m"),
                point_id("orgA", "clause", "c2", "m"),
                point_id("orgA", "clause", "c1", "m2")}) == 4


def test_org_filter_always_present():
    assert org_filter("A") == {"org_id": "A"}
    assert org_filter("A", {"category": "payment"}) == {"org_id": "A", "category": "payment"}


def test_upsert_search_delete_filter():
    s = InMemoryVectorStore(dimension=2)
    assert s.health_check() is True
    s.ensure_collection(2)
    assert s.upsert(_pts()) == 3
    hits = s.search([1.0, 0.0], 10, org_filter("A"))
    assert {h["id"] for h in hits} == {"p1", "p2"}  # never B
    assert hits[0]["id"] == "p1" and hits[0]["score"] > 0.99
    only_policy = s.search([0.0, 1.0], 10, org_filter("A", {"object_type": "policy"}))
    assert [h["id"] for h in only_policy] == ["p2"]
    assert s.search([1.0, 0.0], 1, org_filter("A"))[0]["id"] == "p1"  # top-k
    assert s.delete(["p1"]) == 1
    assert [h["id"] for h in s.search([1.0, 0.0], 10, org_filter("A"))] == ["p2"]


def test_delete_where_and_dimension_guard():
    s = InMemoryVectorStore(dimension=2)
    s.upsert(_pts())
    assert s.delete_where({"org_id": "A"}) == 2
    assert s.search([1.0, 0.0], 10, None)[0]["id"] == "p3"
    with pytest.raises(VectorStoreError):
        s.ensure_collection(3)  # refuse to mix geometries
    with pytest.raises(VectorStoreError):
        s.upsert([{"id": "x", "vector": [1.0, 0.0, 0.0], "payload": {}}])
