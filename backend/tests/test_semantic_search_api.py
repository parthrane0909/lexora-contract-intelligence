"""Search API + precedent: relevance, top-k, filters, isolation, RBAC."""
import uuid

import pytest
from doc_builders import make_pdf
from doc_helpers import upload_and_process

from app.modules.retrieval import indexer
from app.modules.retrieval.vector_store import InMemoryVectorStore

ORG_A_PAGES = [
    ["8.2 Limitation of Liability", "Liability is unlimited under this agreement."],
    ["9. Payment Terms", "Client shall pay within 30 days of invoice."],
]
ORG_B_PAGES = [
    ["8.2 Limitation of Liability", "Supplier liability is capped at $10,000."],
]


@pytest.fixture()
def hermetic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval.providers import reset_provider

    reset_provider()
    store = InMemoryVectorStore(collection="t")
    monkeypatch.setattr(indexer, "_STORE", store)
    yield store
    reset_provider()


def _idx(client, token, pages, email):
    from conftest import auth

    t, cid, res = upload_and_process(client, make_pdf(pages), "c.pdf", email)
    assert res["status"] == "processed"
    r = client.post("/api/v1/retrieval/index", headers=auth(t),
                    json={"scope": "contract", "contract_id": cid})
    assert r.json()["indexed"] == len(pages), r.text
    return t, cid


def test_search_returns_relevant_with_navigation(client, hermetic):
    from conftest import auth

    token, cid = _idx(client, None, ORG_A_PAGES, "sa@x.com")
    out = client.post("/api/v1/retrieval/search", headers=auth(token),
                      json={"query": "unlimited liability", "top_k": 5}).json()
    assert out["results"], "expected a relevant result"
    top = out["results"][0]
    assert "unlimited" in top["text"].lower()  # relevant clause ranked first
    assert top["contract_id"] == cid and top["page_start"] == 1
    assert "object_id" in top and top["embedding_model"] == "hash-test"
    # top-k honored
    out1 = client.post("/api/v1/retrieval/search", headers=auth(token),
                       json={"query": "contract terms", "top_k": 1}).json()
    assert len(out1["results"]) == 1


def test_search_filters_and_validation(client, hermetic):
    from conftest import auth

    token, cid = _idx(client, None, ORG_A_PAGES, "sf@x.com")
    h = auth(token)
    by_contract = client.post("/api/v1/retrieval/search", headers=h,
                              json={"query": "agreement terms", "contract_id": cid}).json()
    assert all(r["contract_id"] == cid for r in by_contract["results"])
    assert client.post("/api/v1/retrieval/search", headers=h,
                       json={"query": "   "}).status_code == 400
    assert client.post("/api/v1/retrieval/search", headers=h,
                       json={"query": "x", "object_type": "bogus"}).status_code == 400


def test_search_org_isolation(client, hermetic):
    from conftest import auth

    token_a, _ = _idx(client, None, ORG_A_PAGES, "ia@x.com")
    token_b, _ = _idx(client, None, ORG_B_PAGES, "ib@x.com")
    res_a = client.post("/api/v1/retrieval/search", headers=auth(token_a),
                        json={"query": "liability capped", "top_k": 10}).json()
    res_b = client.post("/api/v1/retrieval/search", headers=auth(token_b),
                        json={"query": "liability capped", "top_k": 10}).json()
    assert res_a["results"] and res_b["results"]
    assert all("unlimited" in r["text"].lower() or "30 days" in r["text"]
               for r in res_a["results"])
    assert all("capped at $10,000" in r["text"] for r in res_b["results"])
    # A never sees B's capped clause and vice versa
    assert not any("capped at $10,000" in r["text"] for r in res_a["results"])
    assert not any("unlimited" in r["text"] for r in res_b["results"])


def test_precedent_similar_ranked_and_scoped(client, hermetic):
    import json as _json

    from conftest import TestingSession, auth

    token, cid = _idx(client, None, ORG_A_PAGES, "pr@x.com")
    db = TestingSession()
    try:
        from app.models import Clause, RiskFinding

        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        src, other = clauses
        db.add(RiskFinding(organization_id=src.organization_id, contract_id=src.contract_id,
                           clause_id=other.id, risk_category="payment", finding_type="rule",
                           priority="medium", title="t", reason="r", rule_id="PAY-002",
                           rule_version="1.0", status="open"))
        db.commit()
        src_id = str(src.id)
    finally:
        db.close()
    out = client.get(f"/api/v1/retrieval/precedent?clause_id={src_id}&contract_id={cid}&top_k=5",
                     headers=auth(token)).json()
    assert out["source_clause_id"] == src_id
    assert out["results"], "expected similar historical clause"
    assert all(r["object_id"] != src_id for r in out["results"])  # self excluded
    with_prev = [r for r in out["results"] if "previous_analysis" in r]
    assert with_prev and with_prev[0]["previous_analysis"][0]["rule_id"] == "PAY-002"
    dumped = _json.dumps(out).lower()
    assert "previous analysis" in dumped
    assert "not an approved legal precedent" in dumped  # explicit disclaimer present
    # unknown clause → 404; other org's clause → 404
    assert client.get(f"/api/v1/retrieval/precedent?clause_id={uuid.uuid4()}&contract_id={cid}",
                      headers=auth(token)).status_code == 404
    token_b, _ = _idx(client, None, ORG_B_PAGES, "prb@x.com")
    assert client.get(f"/api/v1/retrieval/precedent?clause_id={src_id}&contract_id={cid}",
                      headers=auth(token_b)).status_code == 404
