"""Phase 8: human review workflow + feedback ledger.

Deterministic throughout: classifications inserted directly, fake LLM not
needed (reviews don't touch explanations). Covers decisions, validation,
history, snapshots, ledger linkage, RBAC, isolation, audit, concurrency,
and the AI-vs-human separation eval (§26).
"""
import json
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import (AuditLog, Clause, ClauseClassification, FeedbackLedger,
                        ReviewDecision, RiskFinding)

PAGES = [
    ["8.1 Limitation of Liability",
     "Supplier liability shall be capped at $50,000 for all claims."],
    ["9. Payment Terms",
     "Client shall pay all fees within 90 days of invoice."],
]
CATS = {0: ("liability", 0.95), 1: ("payment", 0.90)}


def _setup(client, email):
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
                json={"category": "liability", "params": {"max_amount": 10000}})
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["status"] == "done", run
    findings = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert findings, "expected LIAB-003 finding"
    liab = next(f for f in findings if f["risk_category"] == "liability")
    return token, cid, liab


def _base(client, token, cid, fid):
    return f"/api/v1/contracts/{cid}/findings/{fid}"


# ------------------------------------------------------- A. creation ----

def test_accept_reject_modify_needs_review(client):
    token, cid, liab = _setup(client, "dec@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    cases = [
        ("accept", {"decision": "accept"}, 200),
        ("reject", {"decision": "reject", "comment": "Negotiated exception."}, 200),
        ("modify", {"decision": "modify", "comment": "Cap is fine.",
                    "correction_type": "WRONG_PRIORITY",
                    "corrected_priority": "medium"}, 200),
        ("needs_more_review", {"decision": "needs_more_review",
                               "comment": "Awaiting counsel."}, 200),
    ]
    for name, body, _ in cases:
        r = client.post(base + "/reviews", headers=h, json=body)
        assert r.status_code == 200, (name, r.text)
        assert r.json()["decision"] == body["decision"]
    hist = client.get(base + "/reviews", headers=h).json()
    assert len(hist) == 4  # append-only, nothing overwritten


def test_latest_review_state_and_finding_untouched(client):
    token, cid, liab = _setup(client, "state@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    assert client.get(base + "/review", headers=h).json()["review"]["status"] == "unreviewed"
    client.post(base + "/reviews", headers=h,
                json={"decision": "modify", "comment": "Lower it.",
                      "correction_type": "WRONG_PRIORITY", "corrected_priority": "medium"})
    cur = client.get(base + "/review", headers=h).json()
    assert cur["review"] == {"status": "reviewed", "decision": "modify",
                             "reviewer_email": "state@x.com",
                             "created_at": cur["review"]["created_at"]}
    assert cur["risk_finding"] == {"id": liab["id"], "priority": liab["priority"],
                                   "category": liab["risk_category"],
                                   "rule_id": liab["rule_id"], "title": liab["title"]}
    # original finding row byte-identical
    db = TestingSession()
    try:
        f = db.get(RiskFinding, uuid.UUID(liab["id"]))
        assert (f.priority, f.risk_category, f.rule_id) == (
            liab["priority"], liab["risk_category"], liab["rule_id"])
    finally:
        db.close()


# ------------------------------------------------------- B. validation ----

def test_review_validation(client):
    token, cid, liab = _setup(client, "val@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    bad = [
        ({"decision": "approve-it"}, 400),  # invalid decision
        ({"decision": "reject"}, 400),  # comment required
        ({"decision": "reject", "comment": "   "}, 400),
        ({"decision": "modify", "comment": "x"}, 400),  # correction required
        ({"decision": "modify", "comment": "x", "correction_type": "NOPE"}, 400),
        ({"decision": "modify", "comment": "x", "correction_type": "OTHER",
          "corrected_priority": "extreme"}, 400),
        ({"decision": "modify", "comment": "x", "correction_type": "OTHER",
          "corrected_category": "nope"}, 400),
        ({"decision": "accept", "comment": "x" * 2001}, 400),  # length limit
        ({"decision": "accept"}, 200),  # accept w/o comment is fine
    ]
    for body, code in bad:
        assert client.post(base + "/reviews", headers=h, json=body).status_code == code, body
    assert client.post(
        f"/api/v1/contracts/{cid}/findings/{uuid.uuid4()}/reviews",
        headers=h, json={"decision": "accept"}).status_code == 404


# ------------------------------------------------------- C/D. history + snapshot ----

def test_history_order_and_snapshot_freeze(client):
    token, cid, liab = _setup(client, "hist@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    client.post(base + "/reviews", headers=h,
                json={"decision": "reject", "comment": "First take."})
    client.post(base + "/reviews", headers=h,
                json={"decision": "modify", "comment": "On reflection, medium.",
                      "correction_type": "WRONG_PRIORITY", "corrected_priority": "medium"})
    hist = client.get(base + "/reviews", headers=h).json()
    assert [r["decision"] for r in hist] == ["modify", "reject"]  # newest first
    assert hist[1]["comment"] == "First take."  # historical row unchanged
    snap = hist[0]["finding_snapshot"]
    assert snap["priority"] == liab["priority"] and snap["rule_id"] == liab["rule_id"]
    assert snap["policy_version"] == liab["policy_version"]
    assert snap["clause_id"] and snap["analysis_run_id"]
    # latest reflects the newest decision
    assert client.get(base + "/review", headers=h).json()["review"]["decision"] == "modify"


def test_review_binds_explanation_version(client):
    token, cid, liab = _setup(client, "exp@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    r = client.post(base + "/reviews", headers=h, json={"decision": "accept"}).json()
    assert r["explanation_id"] is None  # no explanation exists yet
    db = TestingSession()
    try:
        from app.models import RiskExplanation

        frow = db.get(RiskFinding, uuid.UUID(liab["id"]))
        exp = RiskExplanation(
            organization_id=frow.organization_id,
            finding_id=uuid.UUID(liab["id"]), contract_id=frow.contract_id,
            clause_id=frow.clause_id, provider="t", model="m",
            model_version="m-v9", prompt_version="v7",
            evidence_snapshot="[]", explanation="{}", status="completed")
        db.add(exp)
        db.commit()
        exp_id = str(exp.id)
    finally:
        db.close()
    r2 = client.post(base + "/reviews", headers=h, json={"decision": "accept"}).json()
    assert r2["explanation_id"] == exp_id
    assert r2["explanation_version"] == "m-v9@prompt-v7"  # pinned, not retroactive


# ------------------------------------------------------- E. ledger ----

def test_feedback_ledger_linkage_and_content(client):
    token, cid, liab = _setup(client, "led@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    rev = client.post(base + "/reviews", headers=h,
                      json={"decision": "modify", "comment": "Should be medium.",
                            "correction_type": "WRONG_PRIORITY",
                            "corrected_priority": "medium"}).json()
    fb = client.get(base + "/feedback", headers=h).json()
    assert len(fb) == 1
    row = fb[0]
    assert row["review_id"] == rev["id"] and row["decision"] == "modify"
    assert (row["original_category"], row["original_priority"],
            row["original_rule"]) == ("liability", liab["priority"], liab["rule_id"])
    assert row["original_classifier_prediction"] == "liability"
    assert row["original_classifier_confidence"] == pytest.approx(0.95)
    assert row["corrected_priority"] == "medium"
    assert row["reviewer_comment"] == "Should be medium."
    assert row["reviewer_role"] in ("admin", "legal_reviewer", "contract_manager")
    db = TestingSession()
    try:
        assert db.query(FeedbackLedger).filter(
            FeedbackLedger.review_id == uuid.UUID(rev["id"])).count() == 1
    finally:
        db.close()


# ------------------------------------------------------- F/G. RBAC + isolation ----

def _viewer_token(client, cid, email):
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email=email, password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(_C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="viewer"))
        db.commit()
        return create_access_token(str(user.id), str(c.organization_id), "viewer")
    finally:
        db.close()


def test_viewer_reads_but_cannot_submit(client):
    token, cid, liab = _setup(client, "own@x.com")
    vtok = _viewer_token(client, cid, "v@x.com")
    base = _base(client, token, cid, liab["id"])
    assert client.post(base + "/reviews", headers=auth(vtok),
                       json={"decision": "accept"}).status_code == 403
    assert client.get(base + "/reviews", headers=auth(vtok)).status_code == 200
    assert client.get(base + "/review", headers=auth(vtok)).status_code == 200
    assert client.get(base + "/feedback", headers=auth(vtok)).status_code == 200


def test_cross_org_isolation(client):
    token, cid, liab = _setup(client, "oa@x.com")
    other = register(client, "ob@x.com", "password123", "OrgOB")
    base = _base(client, token, cid, liab["id"])
    assert client.post(base + "/reviews", headers=auth(other),
                       json={"decision": "accept"}).status_code == 404
    assert client.get(base + "/reviews", headers=auth(other)).status_code == 404
    assert client.get(base + "/review", headers=auth(other)).status_code == 404
    assert client.get(base + "/feedback", headers=auth(other)).status_code == 404
    assert client.get("/api/v1/reviews", headers=auth(other)).json() == []
    # other-org admin gets an empty export of THEIR org (200), never A's rows
    assert client.get("/api/v1/feedback/export", headers=auth(other)).text == ""


# ------------------------------------------------------- H. audit ----

def test_review_creates_audit_event(client):
    token, cid, liab = _setup(client, "aud@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    rev = client.post(base + "/reviews", headers=h,
                      json={"decision": "reject", "comment": "Exception applies."}).json()
    db = TestingSession()
    try:
        events = db.query(AuditLog).filter(
            AuditLog.action == "finding.reviewed",
            AuditLog.object_id == rev["id"]).all()
        assert len(events) == 1
        assert events[0].user_id is not None and "reject" in (events[0].detail or "")
        # no contract text in audit detail
        assert "Exception applies" not in (events[0].detail or "")
    finally:
        db.close()


# ------------------------------------------------------- I. concurrency ----

def test_concurrent_reviewers_append(client):
    token, cid, liab = _setup(client, "con@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    # second reviewer in the same org
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email="second@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(_C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="legal_reviewer"))
        db.commit()
        tok2 = create_access_token(str(user.id), str(c.organization_id), "legal_reviewer")
    finally:
        db.close()
    r1 = client.post(base + "/reviews", headers=h, json={"decision": "accept"}).json()
    r2 = client.post(base + "/reviews", headers=auth(tok2),
                     json={"decision": "reject", "comment": "Disagree."}).json()
    assert r1["id"] != r2["id"]
    hist = client.get(base + "/reviews", headers=h).json()
    assert len(hist) == 2
    assert {r["reviewer_email"] for r in hist} == {"con@x.com", "second@x.com"}


# ------------------------------------------------------- dashboard/export ----

def test_summary_and_filters_and_export(client):
    token, cid, liab = _setup(client, "dash@x.com")
    h, base = auth(token), _base(client, token, cid, liab["id"])
    s0 = client.get("/api/v1/reviews/summary", headers=h).json()
    assert s0["total_findings"] >= 1 and s0["unreviewed"] == s0["total_findings"]
    assert s0["high_critical_unreviewed"] >= 1  # LIAB-003 is high
    client.post(base + "/reviews", headers=h,
                json={"decision": "modify", "comment": "Medium suffices.",
                      "correction_type": "WRONG_PRIORITY", "corrected_priority": "medium"})
    s1 = client.get("/api/v1/reviews/summary", headers=h).json()
    assert s1["reviewed"] == s0["reviewed"] + 1
    assert s1["by_decision"] == {"modify": 1}
    assert s1["by_correction_type"] == {"WRONG_PRIORITY": 1}
    assert s1["unreviewed"] == s0["unreviewed"] - 1
    lst = client.get("/api/v1/reviews?decision=modify", headers=h).json()
    assert len(lst) == 1 and lst[0]["finding_id"] == liab["id"]
    assert client.get("/api/v1/reviews?decision=bogus", headers=h).status_code == 400
    assert client.get(f"/api/v1/reviews?contract_id={cid}", headers=h).json()
    exp = client.get("/api/v1/feedback/export", headers=h)
    assert exp.status_code == 200
    lines = [json.loads(line) for line in exp.text.strip().split("\n")]
    assert len(lines) == 1
    row = lines[0]
    assert row["decision"] == "modify" and row["corrected_priority"] == "medium"
    assert row["original_priority"] == liab["priority"]
    assert "50,000" in (row["clause_text"] or "")
    # contract_manager may review, but export stays admin/reviewer-gated
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email="mgr@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(_C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="contract_manager"))
        db.commit()
        mtok = create_access_token(str(user.id), str(c.organization_id), "contract_manager")
    finally:
        db.close()
    mh = auth(mtok)
    assert client.post(base + "/reviews", headers=mh,
                       json={"decision": "accept"}).status_code == 200
    assert client.get("/api/v1/feedback/export", headers=mh).status_code == 403
