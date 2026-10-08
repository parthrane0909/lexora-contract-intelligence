"""Org isolation for Phase 2 endpoints: document, clauses, clause detail."""
from doc_builders import CONTRACT_PDF_PAGES, make_pdf
from doc_helpers import upload_and_process


def test_document_endpoints_isolated(client):
    from conftest import auth, register

    token_a, cid, result = upload_and_process(
        client, make_pdf(CONTRACT_PDF_PAGES), "a.pdf", "a2@x.com")
    assert result["status"] == "processed"
    token_b = register(client, "b2@x.com", "password123", "OrgB")
    h_b, h_a = auth(token_b), auth(token_a)

    assert client.get(f"/api/v1/contracts/{cid}/document", headers=h_b).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/clauses", headers=h_b).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/clauses/00000000-0000-0000-0000-000000000000",
                      headers=h_b).status_code == 404

    clauses = client.get(f"/api/v1/contracts/{cid}/clauses", headers=h_a).json()
    assert len(clauses) > 0
    real_id = clauses[0]["id"]
    # Unknown clause id, and clause id under a different contract id → 404.
    assert client.get(f"/api/v1/contracts/{cid}/clauses/00000000-0000-0000-0000-000000000000",
                      headers=h_a).status_code == 404
    assert client.get(f"/api/v1/contracts/00000000-0000-0000-0000-000000000000/clauses/{real_id}",
                      headers=h_a).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/clauses/{real_id}",
                      headers=h_b).status_code == 404
