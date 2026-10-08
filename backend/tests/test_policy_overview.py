"""Policy overview (derived, never duplicated logic) + restore/deactivate."""
import json
import uuid

from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import Clause, ClauseClassification

PAGES = [
    ["8.1 Limitation of Liability", "Supplier liability is capped at $50,000."],
    ["9. Payment Terms", "Client shall pay within 90 days of invoice."],
]


def _setup(client, email):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    h = auth(token)
    cid = client.post("/api/v1/contracts/upload", headers=h,
                      files={"file": ("c.pdf", make_pdf(PAGES), "application/pdf")}).json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        for cl, (cat, conf) in zip(clauses, [("liability", 0.95), ("payment", 0.9)]):
            db.add(ClauseClassification(
                organization_id=cl.organization_id, contract_id=cl.contract_id,
                clause_id=cl.id, analysis_run_id=None, model_name="t", model_version="v0",
                predicted_category=cat, confidence=conf, probabilities=json.dumps({cat: conf})))
        db.commit()
        return token, cid
    finally:
        db.close()


def _pol(client, token, category, params):
    r = client.post("/api/v1/policies", headers=auth(token),
                    json={"category": category, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_overview_deviation_within_no_data(client):
    token, cid = _setup(client, "ov@x.com")
    h = auth(token)
    _pol(client, token, "liability", {"max_amount": 1000000, "currency": "USD"})
    _pol(client, token, "payment", {"max_days": 30})
    _pol(client, token, "termination", {"min_notice_days": 30})
    assert client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).status_code == 200

    ov = client.get(f"/api/v1/contracts/{cid}/policy-overview", headers=h).json()
    rows = {r["category"]: r for r in ov["rows"]}
    assert set(rows) == {"liability", "payment", "termination"}

    liab, pay, term = rows["liability"], rows["payment"], rows["termination"]
    assert liab["status"] == "within_policy"  # $50k cap under $1M
    assert liab["policy_version"] == 1 and liab["finding_ids"] == []
    assert pay["status"] == "deviation"  # 90d > 30d
    assert pay["contract_summary"] == "90 days" and pay["policy_summary"] == "≤ 30 days"
    assert len(pay["finding_ids"]) == 1
    assert term["status"] == "no_data"  # policy set, no termination clause reviewed

    # Tighten liability → deviation appears without touching risk logic.
    _pol(client, token, "liability", {"max_amount": 10000, "currency": "USD"})
    assert client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).status_code == 200
    rows2 = {r["category"]: r
             for r in client.get(f"/api/v1/contracts/{cid}/policy-overview", headers=h).json()["rows"]}
    assert rows2["liability"]["status"] == "deviation"
    assert rows2["liability"]["contract_summary"] == "$50,000"
    assert rows2["liability"]["policy_version"] == 2


def test_overview_isolation_and_no_run(client):
    token, cid = _setup(client, "ov2@x.com")
    other = register(client, "ov3@x.com", "password123", "OrgOV3")
    assert client.get(f"/api/v1/contracts/{cid}/policy-overview",
                      headers=auth(other)).status_code == 404
    _pol(client, token, "payment", {"max_days": 30})
    ov = client.get(f"/api/v1/contracts/{cid}/policy-overview", headers=auth(token)).json()
    assert ov["rows"][0]["status"] == "no_data"  # no risk run yet


def test_restore_and_deactivate(client):
    token = register(client, "rst@x.com", "password123", "OrgRst")
    h = auth(token)
    p1 = _pol(client, token, "payment", {"max_days": 30})
    p2 = _pol(client, token, "payment", {"max_days": 60})
    assert p2["version"] == 2
    # Restore v1 → new v3 with v1's params (append-only history).
    r = client.post(f"/api/v1/policies/{p1['id']}/restore", headers=h).json()
    assert r["version"] == 3 and r["params"]["max_days"] == 30
    hist = client.get("/api/v1/policies/history?category=payment", headers=h).json()
    assert [p["version"] for p in hist] == [3, 2, 1]
    assert r["created_by"] == "rst@x.com"  # who changed it is visible
    # Deactivate removes the position; re-deactivate 404s.
    assert client.delete("/api/v1/policies/payment", headers=h).status_code == 200
    assert client.get("/api/v1/policies", headers=h).json() == []
    assert client.delete("/api/v1/policies/payment", headers=h).status_code == 404
    # Restoring still works after deactivation (creates a fresh active version).
    r2 = client.post(f"/api/v1/policies/{p1['id']}/restore", headers=h).json()
    assert r2["version"] == 4 and r2["is_active"] is True
