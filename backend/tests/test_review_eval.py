"""Deterministic §26 eval: AI analysis vs human judgment stay separate.

Fixture: LIAB-003 / HIGH → reviewer MODIFY (WRONG_PRIORITY → MEDIUM).
Asserts: original priority HIGH everywhere it matters, MEDIUM only in the
human-owned fields, finding row untouched, history append-only.
"""
import uuid

from conftest import TestingSession, auth
from test_reviews import _setup

from app.models import FeedbackLedger, ReviewDecision, RiskFinding


def test_ai_human_separation_eval(client):
    token, cid, liab = _setup(client, "eval26@x.com")
    h = auth(token)
    base = f"/api/v1/contracts/{cid}/findings/{liab['id']}"
    assert liab["priority"] == "high" and liab["rule_id"] == "LIAB-003"

    # REJECT leaves everything untouched
    client.post(base + "/reviews", headers=h,
                json={"decision": "reject", "comment": "Negotiated."})
    # MODIFY records MEDIUM without touching HIGH
    rev = client.post(base + "/reviews", headers=h,
                      json={"decision": "modify", "comment": "Medium suffices.",
                            "correction_type": "WRONG_PRIORITY",
                            "corrected_priority": "medium"}).json()
    # ACCEPT leaves everything untouched
    client.post(base + "/reviews", headers=h, json={"decision": "accept"})

    db = TestingSession()
    try:
        f = db.get(RiskFinding, uuid.UUID(liab["id"]))
        assert (f.priority, f.risk_category, f.rule_id) == ("high", "liability", "LIAB-003")
        reviews = db.query(ReviewDecision).filter(
            ReviewDecision.finding_id == uuid.UUID(liab["id"])).all()
        assert len(reviews) == 3
        mod = next(r for r in reviews if r.id == uuid.UUID(rev["id"]))
        assert mod.corrected_priority == "medium"
        assert json_snapshot(mod)["priority"] == "high"
        fb = db.query(FeedbackLedger).filter(
            FeedbackLedger.review_id == uuid.UUID(rev["id"])).one()
        assert (fb.original_priority, fb.corrected_priority) == ("high", "medium")
        assert fb.original_rule == "LIAB-003"
    finally:
        db.close()


def json_snapshot(review):
    import json

    return json.loads(review.finding_snapshot)
