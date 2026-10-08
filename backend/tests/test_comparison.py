"""Comparison engine: all difference types, value diffs, materiality,
policy linkage, traceability, isolation. Classifications are inserted
directly (deterministic); the matcher/segmenter do the real work.
"""
import json
import uuid

from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import Clause, ClauseClassification, TemplateClause

TPL_PAGES = [
    ["8.1 Limitation of Liability", "Liability is capped at $1,000,000 for all claims."],
    ["9. Payment Terms", "Client shall pay within 30 days of invoice."],
    ["10. Termination", "Either party may terminate with 60 days written notice."],
    ["13. Governing Law", "This agreement is governed by the laws of Delaware."],
    ["99. Legacy Audit", "Template-only audit procedure described here."],
]
CON_PAGES = [
    ["8.1 Limitation of Liability", "Liability is capped at $500,000 for all claims."],
    ["9. Payment Terms", "Client shall pay within 30 days of invoice."],
    ["11. Added Confidentiality", "Contract-only confidentiality duties apply here."],
]

CON_CATS = {0: ("liability", 0.95), 1: ("payment", 0.9), 2: ("other", 0.8)}


def _setup(client, email):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    h = auth(token)
    t = client.post("/api/v1/templates/upload", headers=h,
                    data={"name": "Standard MSA", "contract_type": "MSA"},
                    files={"file": ("t.pdf", make_pdf(TPL_PAGES), "application/pdf")}).json()
    assert t["status"] == "ready", t
    r = client.post("/api/v1/contracts/upload", headers=h,
                    files={"file": ("c.pdf", make_pdf(CON_PAGES), "application/pdf")})
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        assert len(clauses) == 3
        for idx, (cat, conf) in CON_CATS.items():
            db.add(ClauseClassification(
                organization_id=clauses[idx].organization_id,
                contract_id=clauses[idx].contract_id, clause_id=clauses[idx].id,
                analysis_run_id=None, model_name="tiny-test", model_version="tiny-v0",
                predicted_category=cat, confidence=conf,
                probabilities=json.dumps({cat: conf})))
        # Simulate classified template clauses (as if the model had run).
        tclauses = db.query(TemplateClause).filter(
            TemplateClause.template_id == uuid.UUID(t["id"])).order_by(
            TemplateClause.order_index).all()
        for tc, cat in zip(tclauses, ["liability", "payment", "termination",
                                      "governing_law", "other"]):
            tc.predicted_category = cat
        db.commit()
        return token, t["id"], cid, [str(c.id) for c in clauses]
    finally:
        db.close()


def test_all_difference_types_and_materiality(client):
    token, tid, cid, _ = _setup(client, "cmp@x.com")
    h = auth(token)
    comp = client.post(f"/api/v1/contracts/{cid}/compare", headers=h,
                       json={"template_id": tid}).json()
    assert comp["engine_version"] == "1.0"
    findings = client.get(f"/api/v1/comparisons/{comp['id']}/findings", headers=h).json()
    by_type = {}
    for f in findings:
        by_type.setdefault(f["difference_type"], []).append(f)
    assert set(by_type) == {"modified", "unchanged", "added", "missing"}

    mod = [f for f in by_type["modified"] if f["category"] == "liability"][0]
    assert mod["materiality"] == "material"  # changed value in core category
    assert {"field": "cap_amount", "template": 1000000.0, "contract": 500000.0} in mod["value_diff"]
    assert mod["template_clause_id"] and mod["contract_clause_id"]

    assert by_type["unchanged"][0]["materiality"] == "informational"
    assert by_type["added"][0]["materiality"] == "informational"  # 'other' boilerplate
    missing_cats = {f["category"] for f in by_type["missing"]}
    assert missing_cats == {"termination", "governing_law", "other"}
    for f in by_type["missing"]:
        if f["category"] in ("termination", "governing_law"):
            assert f["materiality"] == "potentially_material"
        assert f["contract_clause_id"] is None and f["template_clause_id"]

    # Traceability: every finding resolves to real source rows.
    db = TestingSession()
    try:
        from app.models import ComparisonFinding
        for f in findings:
            row = db.get(ComparisonFinding, uuid.UUID(f["id"]))
            assert row is not None
            if row.contract_clause_id:
                assert db.get(Clause, row.contract_clause_id).text == f["contract_text"]
    finally:
        db.close()

    # Filters + history.
    assert len(client.get(f"/api/v1/comparisons/{comp['id']}/findings?materiality=material",
                          headers=h).json()) >= 1
    assert len(client.get(f"/api/v1/comparisons/{comp['id']}/findings?difference_type=added",
                          headers=h).json()) == 1
    assert client.get(f"/api/v1/comparisons/{comp['id']}/findings?materiality=bogus",
                      headers=h).status_code == 400
    comp2 = client.post(f"/api/v1/contracts/{cid}/compare", headers=h,
                        json={"template_id": tid}).json()
    assert comp2["id"] != comp["id"]
    assert len(client.get(f"/api/v1/contracts/{cid}/comparisons", headers=h).json()) == 2
    assert client.post(f"/api/v1/contracts/{cid}/compare", headers=h,
                       json={"template_id": tid, "matcher": "nope"}).status_code == 400


def test_policy_impact_linkage(client):
    token, tid, cid, _ = _setup(client, "cpol@x.com")
    h = auth(token)
    client.post("/api/v1/policies", headers=h,
                json={"category": "liability",
                      "params": {"max_amount": 100000, "currency": "USD"}})
    assert client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).status_code == 200
    comp = client.post(f"/api/v1/contracts/{cid}/compare", headers=h,
                       json={"template_id": tid}).json()
    findings = client.get(f"/api/v1/comparisons/{comp['id']}/findings", headers=h).json()
    liab = [f for f in findings if f["category"] == "liability" and f["difference_type"] == "modified"][0]
    assert liab["risk_finding_id"]  # linked policy deviation
    assert liab["materiality"] == "material"
    assert any("LIAB-003" in r for r in liab["reasons"])


def test_comparison_isolation_and_unready_template(client):
    token_a, tid, cid, _ = _setup(client, "ca@x.com")
    token_b = register(client, "cb@x.com", "password123", "OrgCB")
    assert client.post(f"/api/v1/contracts/{cid}/compare", headers=auth(token_b),
                       json={"template_id": tid}).status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/comparisons",
                      headers=auth(token_b)).status_code == 404
    # Empty template (failed parse) cannot be compared.
    bad = client.post("/api/v1/templates/upload", headers=auth(token_a),
                      data={"name": "Broken2", "contract_type": "MSA"},
                      files={"file": ("b.pdf", b"%PDF-1.4\n%garbage", "application/pdf")}).json()
    assert bad["status"] == "failed"
    r = client.post(f"/api/v1/contracts/{cid}/compare", headers=auth(token_a),
                    json={"template_id": bad["id"]})
    assert r.status_code == 400
