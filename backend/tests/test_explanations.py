"""Phase 7: evidence-grounded explanations (fake LLM; no external calls).

Covers: evidence ranking/mandatory-clause/dedupe/isolation, context
budgets, provider modes, grounding validation, persistence/versioning,
API RBAC + isolation, failure states, and the deterministic eval fixture.
"""
import json
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import Clause, ClauseClassification, RiskExplanation, RiskFinding
from app.modules.llm_explanation import evidence as ev_mod
from app.modules.llm_explanation.context import build_context
from app.modules.llm_explanation.providers import (
    FakeProvider,
    LLMUnavailableError,
    reset_llm_provider,
)
from app.modules.llm_explanation.validator import GroundingError, validate_explanation

PAGES = [
    ["8.1 Limitation of Liability",
     "Supplier liability shall be capped at $50,000 for all claims."],
    ["9. Payment Terms",
     "Client shall pay all fees within 90 days of invoice."],
]
TPL_PAGES = [
    ["8.1 Limitation of Liability",
     "Supplier liability shall be capped at $10,000 for all claims."],
    ["9. Payment Terms",
     "Client shall pay all fees within 30 days of invoice."],
]
CATS = {0: ("liability", 0.95), 1: ("payment", 0.90)}


@pytest.fixture()
def llm_fake(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("FAKE_LLM_MODE", "good")
    reset_llm_provider()
    yield
    reset_llm_provider()


@pytest.fixture()
def hermetic(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval import indexer
    from app.modules.retrieval.providers import reset_provider
    from app.modules.retrieval.vector_store import InMemoryVectorStore

    reset_provider()
    store = InMemoryVectorStore(collection="t")
    monkeypatch.setattr(indexer, "_STORE", store)
    yield store
    reset_provider()


def _finding_setup(client, email, with_template=False):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    h = auth(token)
    r = client.post("/api/v1/contracts/upload", headers=h,
                    files={"file": ("r.pdf", make_pdf(PAGES), "application/pdf")})
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        for idx, (cat, conf) in CATS.items():
            db.add(ClauseClassification(
                organization_id=clauses[idx].organization_id,
                contract_id=clauses[idx].contract_id, clause_id=clauses[idx].id,
                analysis_run_id=None, model_name="tiny-test", model_version="tiny-v0",
                predicted_category=cat, confidence=conf,
                probabilities=json.dumps({cat: conf})))
        db.commit()
    finally:
        db.close()
    client.post("/api/v1/policies", headers=h,
                json={"category": "payment", "params": {"max_days": 30}})
    if with_template:
        t = client.post("/api/v1/templates/upload", headers=h,
                        data={"name": "Std", "contract_type": "MSA"},
                        files={"file": ("t.pdf", make_pdf(TPL_PAGES), "application/pdf")}).json()
        assert t["status"] == "ready", t
        comp = client.post(f"/api/v1/contracts/{cid}/compare", headers=h,
                           json={"template_id": t["id"]}).json()
        assert comp["id"]
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["status"] == "done", run
    findings = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert findings, "expected risk findings (PAY-001)"
    return token, cid, findings


# ---------------------------------------------------------- evidence ----

def test_evidence_mandatory_clause_policy_template_ranking(client, llm_fake, hermetic):
    token, cid, findings = _finding_setup(client, "ev@x.com", with_template=True)
    pay = next(f for f in findings if f["risk_category"] == "payment")
    db = TestingSession()
    try:
        finding = db.get(RiskFinding, uuid.UUID(pay["id"]))
        items = ev_mod.collect_evidence(db, finding)
    finally:
        db.close()
    assert items[0]["source_type"] == "contract_clause"  # mandatory, first
    assert "90 days" in items[0]["text"]
    types = [e["source_type"] for e in items]
    assert "policy" in types  # active payment policy retrieved
    assert "template_clause" in types  # latest comparison's template side
    assert [e["rank"] for e in items] == sorted(e["rank"] for e in items)
    assert len({(e["source_type"], e["source_id"]) for e in items}) == len(items)  # deduped
    assert all(e["organization_id"] == items[0]["organization_id"] for e in items)


def test_evidence_missing_policy_and_no_template_ok(client, llm_fake, hermetic):
    token, cid, findings = _finding_setup(client, "ev2@x.com", with_template=False)
    pay = next(f for f in findings if f["risk_category"] == "payment")
    db = TestingSession()
    try:
        # drop the policy: engine must say so, not fabricate
        from app.models import Policy

        f = db.get(RiskFinding, uuid.UUID(pay["id"]))
        db.query(Policy).delete()
        db.commit()
        items = ev_mod.collect_evidence(db, f)
    finally:
        db.close()
    assert items[0]["source_type"] == "contract_clause"
    assert "policy" not in [e["source_type"] for e in items]


def test_evidence_rejects_foreign_clause(client, llm_fake):
    db = TestingSession()
    try:
        from app.models import Organization

        org_b = Organization(name="Other")
        db.add(org_b)
        db.flush()
        fake_finding = RiskFinding(
            organization_id=org_b.id, contract_id=uuid.uuid4(), clause_id=uuid.uuid4(),
            risk_category="payment", finding_type="rule", priority="low",
            title="t", reason="r", rule_id="PAY-002", rule_version="1.0", status="open")
        with pytest.raises(ValueError):
            ev_mod.collect_evidence(db, fake_finding)
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------- context ----

def test_context_boundaries_budget_and_empty(client, llm_fake, hermetic):
    token, cid, findings = _finding_setup(client, "ctx@x.com", with_template=True)
    pay = next(f for f in findings if f["risk_category"] == "payment")
    db = TestingSession()
    try:
        finding = db.get(RiskFinding, uuid.UUID(pay["id"]))
        items = ev_mod.collect_evidence(db, finding)
    finally:
        db.close()
    ctx = build_context(finding, items)
    assert "[1]" in ctx["prompt_block"] and "contract_clause" in ctx["prompt_block"]
    assert ctx["char_count"] <= 6000 + 500
    assert len(ctx["sources"]) >= 2
    with pytest.raises(ValueError):
        build_context(finding, [])
    with pytest.raises(ValueError):
        build_context(finding, [dict(items[1], rank=0)] + items[1:])


# ---------------------------------------------------------- providers ----

def test_fake_provider_modes():
    good = FakeProvider("good").generate_structured([])
    assert set(good) >= {"summary", "source_references"}
    with pytest.raises(LLMUnavailableError):
        FakeProvider("down").generate_structured([])
    with pytest.raises(LLMUnavailableError):
        FakeProvider("malformed").generate_structured([])
    assert FakeProvider("down").health_check() is False
    assert FakeProvider("good").health_check() is True


# ---------------------------------------------------------- validator ----

def _sources(org="org-1"):
    return [
        {"organization_id": org, "text": "Supplier liability shall be capped at $50,000."},
        {"organization_id": org, "text": "Company policy [payment]: max_days = 30."},
    ]


def _good_output():
    return {"summary": "s", "issue": "i", "why_it_matters": "w",
            "evidence_summary": "The clause [1] caps liability at $50,000.",
            "policy_comparison": "p", "template_comparison": "t",
            "historical_context": "h", "recommendation": "r", "limitations": "l",
            "source_references": [{"source_index": 1, "quote": "capped at $50,000"}]}


def test_validator_accepts_and_rejects():
    assert validate_explanation(_good_output(), _sources(), "org-1")["summary"] == "s"
    bad = _good_output()
    bad["source_references"] = [{"source_index": 7, "quote": "x"}]
    with pytest.raises(GroundingError):
        validate_explanation(bad, _sources(), "org-1")
    foreign = _sources(org="org-2")
    with pytest.raises(GroundingError):
        validate_explanation(_good_output(), foreign, "org-1")
    nocite = _good_output()
    nocite["source_references"] = [{"source_index": 2, "quote": "max_days"}]
    with pytest.raises(GroundingError):  # clause [1] never cited
        validate_explanation(nocite, _sources(), "org-1")
    banned = _good_output()
    banned["summary"] = "This contract is illegal."
    with pytest.raises(GroundingError):
        validate_explanation(banned, _sources(), "org-1")
    missing = {k: v for k, v in _good_output().items() if k != "limitations"}
    with pytest.raises(GroundingError):
        validate_explanation(missing, _sources(), "org-1")
    fakequote = _good_output()
    fakequote["source_references"] = [{"source_index": 1, "quote": "statute 12-B requires X"}]
    with pytest.raises(GroundingError):
        validate_explanation(fakequote, _sources(), "org-1")


# ---------------------------------------------------------- API ----

def test_generate_persist_regenerate_and_read(client, llm_fake, hermetic):
    token, cid, findings = _finding_setup(client, "api@x.com", with_template=True)
    h = auth(token)
    pay = next(f for f in findings if f["risk_category"] == "payment")
    base = f"/api/v1/contracts/{cid}/findings/{pay['id']}"
    assert client.get(f"{base}/explanation", headers=h).status_code == 404  # not_generated

    r1 = client.post(f"{base}/explanation", headers=h)
    assert r1.status_code == 200, r1.text
    e1 = r1.json()
    assert e1["status"] == "completed" and e1["prompt_version"] == "v1"
    assert e1["evidence_count"] >= 2 and e1["source_references"]
    assert e1["model"] == "fake-test-llm"

    # deterministic finding untouched by the LLM
    f_again = client.get(f"/api/v1/contracts/{cid}/findings/{pay['id']}", headers=h).json()
    assert (f_again["priority"], f_again["risk_category"], f_again["rule_id"]) == (
        pay["priority"], pay["risk_category"], pay["rule_id"])

    r2 = client.post(f"{base}/explanation", headers=h)  # regenerate appends
    assert r2.status_code == 200 and r2.json()["id"] != e1["id"]
    hist = client.get(f"{base}/explanations", headers=h).json()
    assert len(hist) == 2
    assert client.get(f"{base}/explanation", headers=h).json()["id"] == r2.json()["id"]

    # evidence snapshot frozen on the first row
    db = TestingSession()
    try:
        row = db.get(RiskExplanation, uuid.UUID(e1["id"]))
        snap = json.loads(row.evidence_snapshot)
        assert snap and snap[0]["source_type"] == "contract_clause"
        assert "90 days" in snap[0]["text"]
    finally:
        db.close()

    # evidence preview needs no LLM call
    ev = client.get(f"{base}/evidence", headers=h).json()
    assert ev and ev[0]["source_type"] == "contract_clause"


def test_explanation_rbac_isolation_and_states(client, llm_fake, hermetic):
    token, cid, findings = _finding_setup(client, "rb@x.com")
    pay = next(f for f in findings if f["risk_category"] == "payment")
    base = f"/api/v1/contracts/{cid}/findings/{pay['id']}"
    other = register(client, "other@x.com", "password123", "OrgOther")
    assert client.get(base + "/explanation", headers=auth(other)).status_code == 404
    assert client.post(base + "/explanation", headers=auth(other)).status_code == 404
    assert client.get(base + "/evidence", headers=auth(other)).status_code == 404

    # same-org viewer reads but cannot generate
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email="view@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(_C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(c.organization_id), "viewer")
    finally:
        db.close()
    assert client.post(base + "/explanation", headers=auth(vtok)).status_code == 403
    assert client.get(base + "/evidence", headers=auth(vtok)).status_code == 200

    # LLM down → explicit 503 + persisted failed row, never a fake
    from app.modules.llm_explanation import providers as prov_mod

    old = prov_mod._PROVIDER
    prov_mod._PROVIDER, prov_mod._PROVIDER_KEY = FakeProvider("down"), "fake"
    try:
        r = client.post(base + "/explanation", headers=auth(token))
        assert r.status_code == 503, r.text
    finally:
        prov_mod._PROVIDER, prov_mod._PROVIDER_KEY = old, "fake"
    db = TestingSession()
    try:
        failed = [e for e in db.query(RiskExplanation).filter(
            RiskExplanation.finding_id == uuid.UUID(pay["id"])).all()
            if e.status == "failed"]
        assert failed and failed[0].error
    finally:
        db.close()
    hist = client.get(base + "/explanations", headers=auth(token)).json()
    assert any(h["status"] == "failed" for h in hist)


def test_bad_citation_provider_rejected_and_recorded(client, llm_fake, hermetic, monkeypatch):
    token, cid, findings = _finding_setup(client, "bad@x.com")
    pay = next(f for f in findings if f["risk_category"] == "payment")
    monkeypatch.setenv("FAKE_LLM_MODE", "bad_citation")
    reset_llm_provider()
    try:
        r = client.post(f"/api/v1/contracts/{cid}/findings/{pay['id']}/explanation",
                        headers=auth(token))
        assert r.status_code == 422, r.text
    finally:
        reset_llm_provider()


def test_nested_sections_coerced_to_schema(client, llm_fake, hermetic, monkeypatch):
    """Real models sometimes nest text sections: persisted schema stays stable."""
    token, cid, findings = _finding_setup(client, "nest@x.com")
    pay = next(f for f in findings if f["risk_category"] == "payment")
    monkeypatch.setenv("FAKE_LLM_MODE", "nested")
    reset_llm_provider()
    try:
        r = client.post(f"/api/v1/contracts/{cid}/findings/{pay['id']}/explanation",
                        headers=auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert isinstance(body["evidence_summary"], str)
        assert isinstance(body["policy_comparison"], str)
        assert body["status"] == "completed"
    finally:
        reset_llm_provider()
