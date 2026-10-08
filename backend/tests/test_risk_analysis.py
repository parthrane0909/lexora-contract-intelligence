"""End-to-end: contract → (deterministic) classifications → risk analysis.

Classifications are inserted directly with chosen categories/confidences so
rule behavior is fully deterministic — the Transformer is NOT re-run here
(its path is covered by Phase 3 tests). One test uses the tiny model to
prove the real handoff; the rest control every input.
"""
import json
import uuid

from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import ClauseClassification

PAGES = [
    ["8.1 Limitation of Liability",
     "Supplier liability shall be capped at $50,000 for all claims."],
    ["8.2 Indemnification",
     "Vendor shall indemnify and hold harmless Client against all claims."],
    ["9. Payment Terms",
     "Client shall pay all fees within 90 days of invoice."],
    ["10. Termination",
     "Either party may terminate this agreement at any time."],
    ["11. Convenience Termination",
     "Client may terminate for convenience without cause."],
    ["12. Renewal",
     "This agreement shall automatically renew for successive terms."],
    ["13. Governing Law",
     "This agreement shall be governed by the laws of Delaware."],
    ["14. Insurance",
     "Vendor shall maintain insurance coverage."],
    ["15. Entire Agreement",
     "This is the entire agreement between the parties."],
]

CATS = {  # clause index → (category, confidence)
    0: ("liability", 0.95), 1: ("indemnification", 0.90), 2: ("payment", 0.85),
    3: ("termination", 0.88), 4: ("termination", 0.91), 5: ("renewal", 0.92),
    6: ("governing_law", 0.99), 7: ("insurance", 0.30),  # low confidence
    # 8 left unclassified on purpose
}


def _contract(client, email):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": ("r.pdf", make_pdf(PAGES), "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
        from app.models import Clause

        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        assert len(clauses) == 9, [c.title for c in clauses]
        for idx, (cat, conf) in CATS.items():
            db.add(ClauseClassification(
                organization_id=clauses[idx].organization_id, contract_id=clauses[idx].contract_id,
                clause_id=clauses[idx].id, analysis_run_id=None, model_name="tiny-test",
                model_version="tiny-v0", predicted_category=cat, confidence=conf,
                probabilities=json.dumps({cat: conf})))
        db.commit()
        return token, cid, [str(c.id) for c in clauses]
    finally:
        db.close()


def _set_policy(client, token, category, params):
    r = client.post("/api/v1/policies", headers=auth(token),
                    json={"category": category, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_risk_run_with_policies(client):
    token, cid, _ = _contract(client, "risk@x.com")
    h = auth(token)
    _set_policy(client, token, "liability", {"max_amount": 10000, "currency": "USD"})
    _set_policy(client, token, "payment", {"max_days": 30, "impact": "high"})
    _set_policy(client, token, "renewal", {"allow_auto_renew": False})
    _set_policy(client, token, "governing_law", {"preferred_jurisdictions": ["New York"]})

    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["status"] == "done" and run["engine_version"] == "1.0"
    assert run["unclassified"] == 1  # clause 8 never classified — counted, not hidden

    findings = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    by_rule = {f["rule_id"]: f for f in findings}
    assert set(by_rule) == {"LIAB-003", "IND-001", "PAY-001", "TERM-002", "TERM-003",
                            "REN-001", "REN-002", "GOV-001", "INS-002", "CONFIDENCE-001"}

    # Priority + reasons are inspectable.
    assert by_rule["PAY-001"]["priority"] == "high"  # medium base + high-impact bump
    assert any("Bumped" in r for r in by_rule["PAY-001"]["priority_reasons"])
    assert by_rule["REN-001"]["priority"] == "high"
    assert by_rule["TERM-002"]["priority"] == "low"

    # Policy deviations carry both sides of the comparison + policy provenance.
    liab = by_rule["LIAB-003"]
    assert liab["finding_type"] == "policy_deviation"
    assert liab["contract_value"]["cap_amount"] == 50000.0
    assert liab["expected_value"]["max_amount"] == 10000.0
    assert liab["policy_version"] == 1 and liab["policy_id"]

    # Every finding links back: clause, section/page snapshot, evidence, classifier.
    for f in findings:
        assert f["clause_id"] and f["evidence"] and f["rule_version"] == "1.0"
        assert f["classification_model"] == "tiny-test"
        assert f["classification_version"] == "tiny-v0"
        assert f["status"] == "open"
        one = client.get(f"/api/v1/contracts/{cid}/findings/{f['id']}", headers=h).json()
        assert one["reason"] == f["reason"]
    assert by_rule["GOV-001"]["section_number"] == "13"
    assert by_rule["GOV-001"]["page_start"] == 7

    # Low-confidence clause gets the honesty finding alongside the real one.
    ins = [f for f in findings if f["risk_category"] == "insurance"]
    assert {f["rule_id"] for f in ins} == {"INS-002", "CONFIDENCE-001"}

    # Filters.
    assert len(client.get(f"/api/v1/contracts/{cid}/findings?priority=high", headers=h).json()) == 3  # LIAB-003, PAY-001 (bumped), REN-001
    assert len(client.get(f"/api/v1/contracts/{cid}/findings?category=liability", headers=h).json()) == 1
    assert len(client.get(f"/api/v1/contracts/{cid}/findings?finding_type=policy_deviation",
                          headers=h).json()) == 4
    assert client.get(f"/api/v1/contracts/{cid}/findings?priority=extreme",
                      headers=h).status_code == 400
    # Clause-scoped findings.
    per_clause = client.get(f"/api/v1/contracts/{cid}/clauses/{by_rule['REN-001']['clause_id']}"
                            f"/findings", headers=h).json()
    assert {f["rule_id"] for f in per_clause} == {"REN-001", "REN-002"}

    # Re-run after a policy change → new run, history preserved, default = latest.
    _set_policy(client, token, "payment", {"max_days": 120})
    run2 = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run2["run_id"] != run["run_id"]
    latest = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert "PAY-001" not in {f["rule_id"] for f in latest}  # 90d now within policy
    hist = client.get(f"/api/v1/contracts/{cid}/findings?run_id={run['run_id']}",
                      headers=h).json()
    assert "PAY-001" in {f["rule_id"] for f in hist}  # old run intact


def test_no_policies_no_deviations_but_rules_fire(client):
    token, cid, _ = _contract(client, "nopol@x.com")
    h = auth(token)
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["findings"] > 0
    findings = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert all(f["finding_type"] != "policy_deviation" for f in findings)
    assert {f["rule_id"] for f in findings} == {"IND-001", "TERM-002", "TERM-003",
                                                "REN-002", "INS-002", "CONFIDENCE-001"}
