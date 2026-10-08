"""DOCX extraction: Heading styles, numbered paras, tables, single logical page."""
from doc_builders import CONTRACT_DOCX_BLOCKS, make_docx
from doc_helpers import norm, upload_and_process


def test_docx_extraction_headings_tables_clauses(client):
    token, cid, result = upload_and_process(
        client, make_docx(CONTRACT_DOCX_BLOCKS), "svc.docx", "docx@x.com")
    from conftest import auth

    assert result["status"] == "processed"
    h = auth(token)
    doc = client.get(f"/api/v1/contracts/{cid}/document", headers=h).json()
    # DOCX has no native pagination → single logical page (honest, documented).
    assert doc["page_count"] == 1 and len(doc["pages"]) == 1
    assert "$10,000" in doc["full_text"]  # table content preserved
    assert "45 days" in doc["full_text"]
    nums = [s["section_number"] for s in doc["sections"]]
    assert "1" in nums and "2" in nums

    clauses = client.get(f"/api/v1/contracts/{cid}/clauses", headers=h).json()
    assert len(clauses) >= 3
    # Sub-numbered paragraph opens its own clause.
    assert any(c["section_number"] == "8.2.1" for c in clauses)
    flat_full = norm(doc["full_text"])
    for c in clauses:
        assert norm(c["text"]) in flat_full
        assert c["page_start"] == 1 and c["page_end"] == 1
