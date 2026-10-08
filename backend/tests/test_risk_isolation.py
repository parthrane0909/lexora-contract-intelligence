"""Risk isolation + edge cases + real classifier handoff (tiny model)."""
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import CONTRACT_PDF_PAGES, make_pdf
from tiny_model import TINY_LABELS, build_tiny_model

from app.modules.ml_nlp.classifier import reset_classifier


@pytest.fixture()
def model_env(tmp_path, monkeypatch):
    d = build_tiny_model(str(tmp_path / "clf"), TINY_LABELS)
    monkeypatch.setenv("MODEL_DIR", d)
    reset_classifier()
    yield d
    reset_classifier()


def _processed(client, email, pages):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": ("c.pdf", make_pdf(pages), "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
    finally:
        db.close()
    return token, cid


def test_risk_isolation_and_invalid_ids(client, model_env):
    token_a, cid = _processed(client, "ra@x.com", CONTRACT_PDF_PAGES)
    token_b = register(client, "rb@x.com", "password123", "OrgRB")
    h_a, h_b = auth(token_a), auth(token_b)
    client.post(f"/api/v1/contracts/{cid}/classify", headers=h_a)
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h_a).json()
    assert run["status"] == "done"
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h_b).status_code == 404
    assert client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h_b).status_code == 404
    assert client.get(f"/api/v1/contracts/00000000-0000-0000-0000-000000000000/findings",
                      headers=h_a).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/findings/{uuid.uuid4()}",
                      headers=h_a).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/findings?run_id={uuid.uuid4()}",
                      headers=h_a).json() == []  # unknown run → empty, not error


def test_no_classifications_zero_findings(client):
    token, cid = _processed(client, "nc@x.com", CONTRACT_PDF_PAGES)
    h = auth(token)
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["status"] == "done" and run["findings"] == 0 and run["clauses"] == 3
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []


def test_viewer_cannot_run_risk(client, model_env):
    from app.core.security import create_access_token

    from app.models import Contract as C
    from app.models import OrganizationMember, User

    _tok, cid = _processed(client, "own2@x.com", CONTRACT_PDF_PAGES)
    db = TestingSession()
    try:
        user = User(email="rview@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id, role="viewer"))
        db.commit()
        v = db.query(User).filter(User.email == "rview@x.com").first()
        m = db.query(OrganizationMember).filter(OrganizationMember.user_id == v.id).first()
        vtok = create_access_token(str(v.id), str(m.organization_id), "viewer")
    finally:
        db.close()
    assert client.post(f"/api/v1/contracts/{cid}/risk-analysis",
                       headers=auth(vtok)).status_code == 403


def test_tiny_model_handoff_end_to_end(client, model_env):
    """Real path: tiny Transformer → classifications → risk findings (no mocks)."""
    token, cid = _processed(client, "hand@x.com", CONTRACT_PDF_PAGES)
    h = auth(token)
    assert client.post(f"/api/v1/contracts/{cid}/classify", headers=h).status_code == 200
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["status"] == "done"
    findings = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert isinstance(findings, list)  # count depends on random tiny predictions
    for f in findings:
        assert f["classification_version"] == "tiny-v0"  # provenance intact
