"""Pipeline robustness: corrupt/scan/unsupported inputs fail gracefully with
records (processing_status=failed + processing_error), never silent 500s.
Also: re-runs are idempotent, results cleared before re-persist."""
import uuid

import pytest
from conftest import TestingSession, auth, register

from app.models import Clause, ParsedDocument
from app.modules.document.errors import DocumentParseError
from app.modules.document.parsers import parse_document
from app.modules.document.pipeline import process_contract
from doc_builders import CONTRACT_PDF_PAGES, make_blank_pdf, make_pdf


def _upload(client, data: bytes, filename: str, email: str):
    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": (filename, data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    return token, r.json()["id"]


def test_corrupt_pdf_fails_gracefully(client):
    token, cid = _upload(client, b"%PDF-1.4\n%truncated-garbage-xyz", "bad.pdf", "bad@x.com")
    db = TestingSession()
    try:
        result = process_contract(db, uuid.UUID(cid))
    finally:
        db.close()
    assert result["status"] == "failed"
    h = auth(token)
    detail = client.get(f"/api/v1/contracts/{cid}", headers=h).json()
    assert detail["processing_status"] == "failed"
    assert detail["processing_error"]  # useful message, no traceback leak
    assert "Traceback" not in detail["processing_error"]
    # Nothing half-persisted; viewer endpoints report cleanly.
    assert client.get(f"/api/v1/contracts/{cid}/document", headers=h).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/clauses", headers=h).json() == []


def test_scanned_pdf_reports_no_text(client):
    token, cid = _upload(client, make_blank_pdf(), "scan.pdf", "scan@x.com")
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "failed"
    finally:
        db.close()
    detail = client.get(f"/api/v1/contracts/{cid}", headers=auth(token)).json()
    assert "No extractable text" in (detail["processing_error"] or "")


def test_unsupported_type_rejected_at_parse():
    with pytest.raises(DocumentParseError):
        parse_document("notes.txt", b"hello")


def test_rerun_replaces_previous_results(client):
    from doc_helpers import upload_and_process

    token, cid, first = upload_and_process(
        client, make_pdf(CONTRACT_PDF_PAGES), "re.pdf", "re@x.com")
    assert first["status"] == "processed"
    db = TestingSession()
    try:
        second = process_contract(db, uuid.UUID(cid))
        assert second["status"] == "processed"
        assert db.query(ParsedDocument).filter(ParsedDocument.contract_id == uuid.UUID(cid)).count() == 1
        assert db.query(Clause).filter(Clause.contract_id == uuid.UUID(cid)).count() == second["clauses"]
    finally:
        db.close()


def test_unknown_contract_raises():
    db = TestingSession()
    try:
        with pytest.raises(ValueError):
            process_contract(db, uuid.uuid4())
    finally:
        db.close()
