"""Classification API: trigger, retrieve, history, isolation, failure states.

Uses a tiny local Transformer bundle (MODEL_DIR override) so the full
Clause → Tokenizer → Transformer → Prediction → DB path runs in-process.
"""
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import CONTRACT_PDF_PAGES, make_pdf
from tiny_model import TINY_LABELS, build_tiny_model

from app.models import ClauseClassification


@pytest.fixture()
def model_env(tmp_path, monkeypatch):
    d = build_tiny_model(str(tmp_path / "clf"), TINY_LABELS)
    monkeypatch.setenv("MODEL_DIR", d)
    from app.modules.ml_nlp.classifier import reset_classifier

    reset_classifier()
    yield d
    reset_classifier()


def _processed_contract(client, email):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": ("c.pdf", make_pdf(CONTRACT_PDF_PAGES), "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
    finally:
        db.close()
    return token, cid


def test_classify_end_to_end_and_history(client, model_env):
    token, cid = _processed_contract(client, "clf@x.com")
    h = auth(token)
    # No classification yet → explicit 404 (not empty 200).
    clauses = client.get(f"/api/v1/contracts/{cid}/clauses", headers=h).json()
    assert clauses
    first = clauses[0]["id"]
    r0 = client.get(f"/api/v1/contracts/{cid}/clauses/{first}/classification", headers=h)
    assert r0.status_code == 404

    r1 = client.post(f"/api/v1/contracts/{cid}/classify", headers=h)
    assert r1.status_code == 200, r1.text
    assert r1.json()["classified"] == len(clauses)
    assert r1.json()["model_version"] == "tiny-v0"

    got = client.get(f"/api/v1/contracts/{cid}/clauses/{first}/classification", headers=h).json()
    assert got["predicted_category"] in TINY_LABELS
    assert 0.0 <= got["confidence"] <= 1.0 and got["analysis_run_id"] == r1.json()["run_id"]
    allcls = client.get(f"/api/v1/contracts/{cid}/classifications", headers=h).json()
    assert len(allcls) == len(clauses)

    # Re-run appends history; latest wins.
    r2 = client.post(f"/api/v1/contracts/{cid}/classify", headers=h)
    assert r2.json()["run_id"] != r1.json()["run_id"]
    db = TestingSession()
    try:
        n = db.query(ClauseClassification).filter(
            ClauseClassification.clause_id == uuid.UUID(first)).count()
        assert n == 2
    finally:
        db.close()
    assert client.get(f"/api/v1/contracts/{cid}/clauses/{first}/classification",
                      headers=h).json()["analysis_run_id"] == r2.json()["run_id"]


def test_classification_isolated(client, model_env):
    token_a, cid = _processed_contract(client, "isoA@x.com")
    token_b = register(client, "isoB@x.com", "password123", "OrgB")
    client.post(f"/api/v1/contracts/{cid}/classify", headers=auth(token_a))
    assert client.get(f"/api/v1/contracts/{cid}/classifications",
                      headers=auth(token_b)).status_code == 404
    assert client.post(f"/api/v1/contracts/{cid}/classify",
                       headers=auth(token_b)).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/clauses/{uuid.uuid4()}/classification",
                      headers=auth(token_a)).status_code == 404


def test_classify_no_model_503(client, tmp_path, monkeypatch):
    from app.modules.ml_nlp.classifier import reset_classifier

    monkeypatch.setenv("MODEL_DIR", str(tmp_path / "empty"))
    reset_classifier()
    try:
        token = register(client, "nom@x.com", "password123", "OrgNom")
        r = client.post("/api/v1/contracts/upload", headers=auth(token),
                        files={"file": ("c.pdf", make_pdf(CONTRACT_PDF_PAGES), "application/pdf")})
        cid = r.json()["id"]
        resp = client.post(f"/api/v1/contracts/{cid}/classify", headers=auth(token))
        assert resp.status_code == 503
        assert "No trained model" in resp.text
    finally:
        reset_classifier()


def test_classify_viewer_forbidden(client, model_env):
    _token, cid = _processed_contract(client, "own@x.com")
    from conftest import TestingSession as TS

    from app.core.security import create_access_token
    from app.models import Contract as C
    from app.models import OrganizationMember, User

    db = TS()
    try:
        user = User(email="view@x.com", password_hash="x", display_name="v")
        db.add(user)
        db.flush()
        c = db.get(C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id, role="viewer"))
        db.commit()
        v = db.query(User).filter(User.email == "view@x.com").first()
        m = db.query(OrganizationMember).filter(OrganizationMember.user_id == v.id).first()
        vtok = create_access_token(str(v.id), str(m.organization_id), "viewer")
    finally:
        db.close()
    assert client.post(f"/api/v1/contracts/{cid}/classify",
                       headers=auth(vtok)).status_code == 403
