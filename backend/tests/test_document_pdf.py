"""PDF extraction + page/source mapping + clause traceability."""
from doc_builders import CONTRACT_PDF_PAGES, make_pdf
from doc_helpers import norm, upload_and_process


def test_pdf_extraction_pages_sections_clauses(client):
    token, cid, result = upload_and_process(
        client, make_pdf(CONTRACT_PDF_PAGES), "msa.pdf", "pdf@x.com")
    from conftest import auth

    assert result["status"] == "processed"
    assert result["pages"] == 3 and result["clauses"] >= 3

    h = auth(token)
    detail = client.get(f"/api/v1/contracts/{cid}", headers=h).json()
    assert detail["processing_status"] == "processed"
    assert detail["page_count"] == 3 and detail["clause_count"] >= 3
    assert detail["processing_error"] is None
    assert "pypdf" in (detail["model_versions"] or "")

    doc = client.get(f"/api/v1/contracts/{cid}/document", headers=h).json()
    assert doc["page_count"] == 3
    assert [p["page_number"] for p in doc["pages"]] == [1, 2, 3]
    # Page slices reconstruct full_text exactly (source mapping invariant).
    for p in doc["pages"]:
        assert doc["full_text"][p["char_start"]:p["char_end"]] == p["text"]
    assert "shall pay the Vendor" in doc["full_text"]
    # Sections preserved with numbers + page mapping.
    nums = [s["section_number"] for s in doc["sections"]]
    assert nums == ["1", "2", "8.2"]  # "30 days…" body line must not split
    for s in doc["sections"]:
        assert 1 <= (s["page_start"] or 1) <= (s["page_end"] or 3) <= 3

    clauses = client.get(f"/api/v1/contracts/{cid}/clauses", headers=h).json()
    assert [c["order_index"] for c in clauses] == list(range(len(clauses)))
    assert len({c["id"] for c in clauses}) == len(clauses)  # stable unique IDs
    flat_full = norm(doc["full_text"])
    for c in clauses:
        assert norm(c["text"]) in flat_full  # traceable to source
        assert 1 <= (c["page_start"] or 1) <= (c["page_end"] or 3) <= 3
        assert c["char_start"] < c["char_end"]
        one = client.get(f"/api/v1/contracts/{cid}/clauses/{c['id']}", headers=h).json()
        assert one["text"] == c["text"]
