"""Part 4 — Contract Summary endpoint: descriptive, grounded, risk-free.

The summary answers "what is this agreement as a whole about?" using only
contract metadata, the parsed document and (optionally) clause
classifications. It must:

  * describe SaaS / NDA / employment documents sensibly (A–C),
  * carry NO risk counts, findings, rule IDs or recommendations (D–F),
  * keep working before any risk analysis exists (G),
  * never hallucinate parties or renewal terms that the document does not
    state (H–I),
  * stay organization-isolated (J, plus the empty-contract baseline).
"""
import json
import re
import uuid

from conftest import TestingSession, auth, register
from doc_builders import make_pdf

# ------------------------------------------------------------ fixtures -----

SAAS_PAGES = [
    ["SaaS Subscription Agreement",
     "This agreement is entered into between Provider and Customer."],
    ["1. Subscription",
     "Provider grants Customer a non-exclusive right to access the hosted "
     "application during the subscription term."],
    ["2. Fees",
     "Invoices are payable within thirty (30) days of receipt. Customer may "
     "dispute an invoice in writing within fifteen (15) days."],
    ["3. Renewal",
     "The subscription renews for one-year periods unless either party gives "
     "at least thirty (30) days written notice before expiration."],
    ["4. Confidentiality",
     "Each party shall protect the other party's confidential information "
     "using reasonable safeguards."],
    ["5. Termination",
     "Either party may terminate this agreement for an uncured material "
     "breach after thirty (30) days written notice."],
]

NDA_PAGES = [
    ["Mutual Non-Disclosure Agreement",
     "This agreement is made between the parties."],
    ["1. Purpose",
     "The parties may exchange confidential business and technical "
     "information solely to evaluate a potential commercial relationship."],
    ["2. Confidentiality Duty",
     "Each party shall use the other party's confidential information only "
     "for the Purpose and shall protect it using reasonable safeguards."],
    ["3. Duration",
     "The confidentiality obligations remain in effect for two (2) years "
     "after the last disclosure."],
    ["4. Governing Law",
     "This agreement is governed by the laws of Maharashtra, India."],
]

EMPLOYMENT_PAGES = [
    ["Employment Agreement",
     "This agreement is made between the Company and the Employee."],
    ["1. Position",
     "The Employee will serve as Senior Software Engineer and perform "
     "duties reasonably assigned by the Company."],
    ["2. Compensation",
     "The Employee will receive a monthly salary, subject to applicable "
     "deductions."],
    ["3. Confidentiality",
     "The Employee shall keep the Company's confidential information "
     "confidential during employment."],
    ["4. Termination",
     "The Company may terminate employment immediately for breach of policy."],
]

# No stated parties, no recognisable type — everything must stay neutral.
NEUTRAL_PAGES = [
    ["Project Framework Agreement",
     "This document sets out terms between the parties."],
    ["1. Cooperation",
     "The parties shall cooperate in good faith on the project."],
    ["2. Payment",
     "Fees are payable on invoice within fourteen (14) days."],
]

# Duration/renewal wording appears nowhere: the summary must not invent it.
NO_RENEWAL_PAGES = [
    ["Service Agreement",
     "This agreement is made between Provider and Customer."],
    ["1. Services",
     "Provider shall deliver the services described in the statement of work."],
    ["2. Fees",
     "Fees are payable within thirty (30) days of invoice."],
    ["3. Termination",
     "Either party may terminate this agreement with sixty (60) days "
     "written notice."],
]

# Strings that must NEVER appear anywhere in a summary payload.
FORBIDDEN = (
    "risk_summary", "findings_by_priority", "high_risk", "medium_risk",
    "low_risk", "no_detected_risk", "needs_review", "needs_attention",
    "analyzed_clauses", "analysis_status", '"risks"', '"analysis"',
    "n_findings", '"priority"', '"rule_id"',
)


# ------------------------------------------------------------ helpers ------


def _processed(client, email, pages, filename="c.pdf"):
    """Register → upload → run the document pipeline. NO classification,
    NO risk analysis: the contract is 'processed' and nothing else."""
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": (filename, make_pdf(pages), "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
    finally:
        db.close()
    return token, cid


def _summary(client, token, cid):
    r = client.get(f"/api/v1/contracts/{cid}/summary", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def _sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _descriptive_only(s):
    """No risk content of any kind — counts, findings, rules, priorities."""
    blob = json.dumps(s, sort_keys=True)
    for bad in FORBIDDEN:
        assert bad not in blob, f"summary carries risk content: {bad}"
    assert "no LLM" in s["generated_from"]


def _cats(s):
    return {a["category"] for a in s["agreement_areas"]}


def _add_risk_rows(email, cid):
    """Persist classifications + a risk finding the summary must IGNORE."""
    db = TestingSession()
    try:
        from app.models import (Clause, ClauseClassification, Contract,
                                OrganizationMember, RiskFinding, User)

        u = db.query(User).filter(User.email == email).one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
        c = db.query(Contract).filter(Contract.id == uuid.UUID(cid)).one()
        clause = (db.query(Clause).filter(Clause.contract_id == c.id)
                  .order_by(Clause.order_index).first())
        db.add(ClauseClassification(
            organization_id=org_id, contract_id=c.id, clause_id=clause.id,
            model_version="test-v1", predicted_category="payment",
            confidence=0.9))
        db.add(RiskFinding(
            organization_id=org_id, contract_id=c.id, clause_id=clause.id,
            risk_category="payment", finding_type="rule", priority="high",
            title="Late payment exceeded policy", reason="Terms exceed policy.",
            rule_id="PAY-001", rule_version="1.0"))
        db.commit()
    finally:
        db.close()


# ------------------------------------------------------- A. SaaS summary ----


def test_summary_saas_describes_the_whole_agreement(client):
    token, cid = _processed(client, "saas@x.com", SAAS_PAGES,
                            "02_balanced_saas_agreement.pdf")
    s = _summary(client, token, cid)
    _descriptive_only(s)

    assert s["contract_type"] == "SaaS Subscription Agreement"
    assert "SaaS Subscription Agreement" in s["overview"]
    assert 2 <= len(_sentences(s["overview"])) <= 5
    # Human-readable, not "contains 13 clauses" bookkeeping.
    assert not re.search(r"\d+\s+clauses", s["overview"], re.I)
    assert "Provider" in s["parties"]["roles"]
    assert "Customer" in s["parties"]["roles"]
    assert "purpose" in s and s["purpose"]
    assert "thirty (30) days" in s["commercial_terms"]
    assert "renew" in s["term_and_renewal"].lower()
    assert {"service", "payment", "term_renewal", "confidentiality",
            "termination"} <= _cats(s)
    assert 5 <= len(s["agreement_areas"]) <= 8
    assert all(a["summary"] and a["label"] for a in s["agreement_areas"])
    assert s["key_responsibilities"], "expected extractive duty sentences"


# ---------------------------------------------------------- B. NDA summary --


def test_summary_nda_explains_confidentiality_purpose(client):
    token, cid = _processed(client, "nda@x.com", NDA_PAGES, "03_nda.pdf")
    s = _summary(client, token, cid)
    _descriptive_only(s)

    assert s["contract_type"] == "Non-Disclosure Agreement"
    assert "Non-Disclosure Agreement" in s["overview"]
    assert "confidential" in s["overview"].lower()
    # Purpose comes from the document's own Purpose clause.
    assert "evaluate a potential commercial relationship" in s["purpose"]
    assert "confidentiality" in _cats(s)
    assert "two (2) years" in s["term_and_renewal"]
    assert s["commercial_terms"] is None  # no fee clause exists — none shown


# -------------------------------------------------- C. employment summary ---


def test_summary_employment_explains_the_relationship(client):
    token, cid = _processed(client, "emp@x.com", EMPLOYMENT_PAGES,
                            "04_employment_agreement.pdf")
    s = _summary(client, token, cid)
    _descriptive_only(s)

    assert s["contract_type"] == "Employment Agreement"
    assert "employment relationship" in s["overview"]
    assert 2 <= len(_sentences(s["overview"])) <= 5
    assert {"Employee", "Company"} <= set(s["parties"]["roles"])
    assert "monthly salary" in s["commercial_terms"]
    assert "salary" in s["purpose"] or "employment" in s["purpose"]
    assert {"service", "payment", "confidentiality", "termination"} <= _cats(s)


# ------------------------------------ D/E/F. no risk content even with rows --


def test_summary_contains_no_risk_counts(client):
    token, cid = _processed(client, "cnt@x.com", SAAS_PAGES)
    _add_risk_rows("cnt@x.com", cid)
    s = _summary(client, token, cid)
    _descriptive_only(s)
    blob = json.dumps(s).lower()
    for word in ("high risk", "medium risk", "low risk", "needs attention",
                 "needs review", "findings"):
        assert word not in blob, word


def test_summary_contains_no_findings(client):
    token, cid = _processed(client, "fnd@x.com", SAAS_PAGES)
    _add_risk_rows("fnd@x.com", cid)
    s = _summary(client, token, cid)
    _descriptive_only(s)
    assert "findings" not in json.dumps(s).lower()
    assert "Late payment exceeded policy" not in json.dumps(s)


def test_summary_contains_no_risk_rule_ids(client):
    token, cid = _processed(client, "rul@x.com", SAAS_PAGES)
    _add_risk_rows("rul@x.com", cid)
    s = _summary(client, token, cid)
    _descriptive_only(s)
    blob = json.dumps(s)
    assert "PAY-001" not in blob
    assert '"rule_id"' not in blob
    assert '"priority"' not in blob


# -------------------------------------------- G. independent of risk engine --


def test_summary_works_before_risk_analysis(client):
    """MANDATORY independence: processed + clauses, NO classification, NO
    risk run, NO findings — the summary is still useful and descriptive."""
    token, cid = _processed(client, "pre@x.com", SAAS_PAGES)
    h = auth(token)

    detail = client.get(f"/api/v1/contracts/{cid}", headers=h).json()
    life = detail["analysis_lifecycle"]
    assert life["processing"]["state"] == "complete"
    assert life["risk_analysis"]["state"] == "not_run"
    assert life["overall"] == "not_started"
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []
    ra = client.get(f"/api/v1/contracts/{cid}/risk-assessment", headers=h).json()
    assert ra["summary"]["findings"] == 0

    s = _summary(client, token, cid)
    _descriptive_only(s)
    assert s["contract_type"] == "SaaS Subscription Agreement"
    assert len(_sentences(s["overview"])) >= 2
    assert len(s["agreement_areas"]) >= 4
    assert s["purpose"] and s["commercial_terms"] and s["term_and_renewal"]
    assert s["key_responsibilities"]


# ------------------------------------------------------- H. no hallucinated


def test_summary_does_not_hallucinate_parties(client):
    token, cid = _processed(client, "neutral@x.com", NEUTRAL_PAGES,
                            "framework.pdf")
    s = _summary(client, token, cid)
    _descriptive_only(s)

    assert s["contract_type"] is None  # no recognised type — not guessed
    assert s["parties"]["roles"] == []
    assert s["counterparty"] is None
    assert s["parties"]["description"] == (
        "The agreement governs the relationship between the parties.")
    assert "between the parties" in s["overview"]
    assert "between the parties" in s["purpose"]
    blob = json.dumps(s)
    for fake in ("Acme", "Vendor X", "LLC", "Ltd"):
        assert fake not in blob, fake


# ------------------------------------------------------- I. no invented term


def test_summary_does_not_hallucinate_renewal(client):
    token, cid = _processed(client, "norenew@x.com", NO_RENEWAL_PAGES,
                            "service_agreement.pdf")
    s = _summary(client, token, cid)
    _descriptive_only(s)

    assert s["term_and_renewal"] is None
    assert "renew" not in s["overview"].lower()
    assert "renew" not in s["purpose"].lower()
    assert "term_renewal" not in _cats(s)  # key name aside, no renewal claim
    assert "termination" in _cats(s)  # what the document does have


# ---------------------------------------------- J. isolation + empty base --


def test_summary_empty_contract(client):
    token = register(client, "s@x.com", "password123", "OrgS")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": ("c.pdf", b"%PDF-1.4 fake contract bytes",
                                    "application/pdf")})
    cid = r.json()["id"]
    s = _summary(client, token, cid)
    _descriptive_only(s)
    assert s["contract_id"] == cid
    assert isinstance(s["overview"], str) and s["overview"]
    assert s["agreement_areas"] == [] and s["key_responsibilities"] == []
    assert s["commercial_terms"] is None and s["term_and_renewal"] is None


def test_summary_cross_org_forbidden(client):
    ta = register(client, "a@x.com", "password123", "OrgA")
    tb = register(client, "b@x.com", "password123", "OrgB")
    _, cid = _processed(client, "iso1@x.com", SAAS_PAGES)
    r = client.get(f"/api/v1/contracts/{cid}/summary", headers=auth(tb))
    assert r.status_code == 404
    assert client.get(f"/api/v1/contracts/{cid}/summary").status_code in (401, 403)
