"""Indexing workflow: first index, idempotent repeat, model-change reindex,
empty-skip, policy objects, Qdrant-down behavior (hermetic backend)."""
import uuid

import pytest
from doc_builders import make_pdf
from doc_helpers import upload_and_process

from app.models import SemanticIndex
from app.modules.retrieval import indexer
from app.modules.retrieval.vector_store import InMemoryVectorStore


@pytest.fixture()
def hermetic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval.providers import reset_provider

    reset_provider()
    store = InMemoryVectorStore(collection="t")
    monkeypatch.setattr(indexer, "_STORE", store)
    yield store
    reset_provider()


PAGES = [
    ["1. Payment Terms", "Client shall pay all fees within 30 days of invoice."],
    ["8.2 Limitation of Liability", "Supplier liability is capped at $500,000."],
]


def _contract(client, email="idx@x.com"):
    token, cid, result = upload_and_process(client, make_pdf(PAGES), "c.pdf", email)
    assert result["status"] == "processed"
    return token, cid


def _rows(client, org_id):
    from conftest import TestingSession

    db = TestingSession()
    try:
        return db.query(SemanticIndex).filter(
            SemanticIndex.organization_id == uuid.UUID(org_id)).all()
    finally:
        db.close()


def test_first_index_and_idempotent_repeat(client, hermetic):
    from conftest import TestingSession, auth

    token, cid = _contract(client)
    from app.models import Contract

    db = TestingSession()
    try:
        org = str(db.query(Contract).filter(Contract.id == uuid.UUID(cid)).one().organization_id)
    finally:
        db.close()
    r1 = client.post("/api/v1/retrieval/index", headers=auth(token),
                     json={"scope": "contract", "contract_id": cid}).json()
    assert r1["indexed"] == 2 and r1["skipped_empty"] == 0
    vids1 = sorted(r.vector_id for r in _rows(client, org))
    assert len(vids1) == 2 and all(vids1)
    r2 = client.post("/api/v1/retrieval/index", headers=auth(token),
                     json={"scope": "contract", "contract_id": cid}).json()
    assert r2["indexed"] == 2
    assert sorted(r.vector_id for r in _rows(client, org)) == vids1  # same points, no dupes
    assert len(hermetic._points) == 2
    st = client.get("/api/v1/retrieval/status", headers=auth(token)).json()
    assert st["objects"] == 2 and st["by_status"] == {"indexed": 2}


def test_reindex_new_model_drops_stale(client, hermetic):
    from conftest import TestingSession, auth

    from app.models import Contract
    from app.modules.retrieval import indexer as idx_mod

    token, cid = _contract(client, "reidx@x.com")
    client.post("/api/v1/retrieval/index", headers=auth(token),
                json={"scope": "contract", "contract_id": cid})
    db = TestingSession()
    try:
        org = db.query(Contract).filter(Contract.id == uuid.UUID(cid)).one().organization_id
        # simulate a previous model generation's leftovers (in contract scope)
        stale = SemanticIndex(organization_id=org, object_type="clause", object_id="ghost",
                              contract_id=uuid.UUID(cid),
                              embedding_model="old-model", embedding_version="v0",
                              dimension=4, status="indexed", vector_id="ghost-id")
        db.add(stale)
        db.commit()
    finally:
        db.close()
    out = idx_mod.reindex_scope(db := TestingSession(), org,
                                contract_id=uuid.UUID(cid),
                                provider=__import__("app.modules.retrieval.providers",
                                                    fromlist=["get_provider"]).get_provider(),
                                store=hermetic)
    db.close()
    assert out["dropped_state_rows"] == 1
    rows = _rows(client, str(org))
    assert {r.embedding_model for r in rows} == {"hash-test"}  # no mixed geometries


def test_empty_clause_skipped_and_policy_indexed(client, hermetic):
    from conftest import TestingSession, auth

    from app.models import Clause

    token, cid = _contract(client, "empty@x.com")
    db = TestingSession()
    try:
        db.add(Clause(organization_id=db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).first().organization_id,
            contract_id=uuid.UUID(cid), text="   ", page_start=1,
            char_start=0, char_end=0, order_index=99,
            segmenter_name="t", segmenter_version="t"))
        db.commit()
    finally:
        db.close()
    r = client.post("/api/v1/retrieval/index", headers=auth(token),
                    json={"scope": "contract", "contract_id": cid}).json()
    assert r["skipped_empty"] == 1 and r["indexed"] == 2
    p = client.post("/api/v1/policies", headers=auth(token),
                    json={"category": "payment", "params": {"max_days": 30}}).json()
    rp = client.post("/api/v1/retrieval/index", headers=auth(token),
                     json={"scope": "policy", "policy_id": p["id"]}).json()
    assert rp["indexed"] == 1


def test_qdrant_down_is_503_not_silent(client, monkeypatch):
    from conftest import auth

    from app.modules.retrieval import indexer as idx_mod
    from app.modules.retrieval.vector_store import VectorStoreError

    class Dead:
        def __getattr__(self, name):
            raise VectorStoreError("down")

    monkeypatch.setattr(idx_mod, "_STORE", Dead())
    token, cid = _contract(client, "down@x.com")
    r = client.post("/api/v1/retrieval/index", headers=auth(token),
                    json={"scope": "contract", "contract_id": cid})
    assert r.status_code == 503
    assert client.get("/api/v1/retrieval/health", headers=auth(token)).json() == {
        "qdrant_reachable": False}


def test_index_validation_and_rbac(client, hermetic):
    from conftest import auth, register

    token, _ = _contract(client, "val@x.com")
    h = auth(token)
    assert client.post("/api/v1/retrieval/index", headers=h,
                       json={"scope": "bogus"}).status_code == 400
    assert client.post("/api/v1/retrieval/index", headers=h,
                       json={"scope": "contract",
                             "contract_id": "00000000-0000-0000-0000-000000000000"}
                       ).status_code == 404
    viewer = register(client, "v@x.com", "password123", "Org-v@x.com")
    # register() makes an admin of a NEW org — add a same-org viewer properly:
    from app.core.security import create_access_token
    from conftest import TestingSession

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email="sameorg-view@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.query(_C).filter(_C.filename == "c.pdf").first()
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(c.organization_id), "viewer")
    finally:
        db.close()
    assert client.post("/api/v1/retrieval/index", headers=auth(vtok),
                       json={"scope": "bogus"}).status_code == 403
    # ...while viewers MAY search:
    assert client.post("/api/v1/retrieval/search", headers=auth(vtok),
                       json={"query": "payment terms"}).status_code == 200
