"""Deterministic lifecycle eval (§32-33): extraction precision/recall over a
labeled fixture set + human-review separation (90 stays, 60 recorded).
No legal-accuracy claims — these measure the deterministic pipeline only.
"""
import uuid

from conftest import TestingSession, auth
from test_obligation_api import _setup

from app.modules.obligations.extractors import get_extractor

ext = get_extractor()

# (text, category, expected type or None, expected fields)
FIXTURES = [
    ("Payment is due within 30 days of receipt of invoice.", "payment",
     "PAYMENT", {"period_days": 30, "trigger": "invoice_received", "deadline": None}),
    ("Either party may terminate upon 60 days written notice.", "termination",
     "TERMINATION", {"notice_period_days": 60}),
    ("Agreement automatically renews for successive one-year terms unless "
     "notice is provided 90 days before expiration.", "renewal",
     "RENEWAL", {"auto_renew": True, "notice_period_days": 90}),
    ("Agreement expires on December 31, 2027.", None,
     "EXPIRY", {"expiration_date": "2027-12-31"}),
    ("The parties shall cooperate in good faith.", None, None, {}),
    ("Vendor shall maintain insurance of $1,000,000.", "insurance",
     "INSURANCE", {"amount": 1000000.0}),
]


def _match(obs, want_type):
    return [o for o in obs if o["type"] == want_type]


def test_extraction_precision_recall():
    tp = fp = fn = 0
    for text, cat, want, fields in FIXTURES:
        obs = ext.extract(text, cat)
        if want is None:
            if obs:
                fp += len(obs)
            continue
        got = _match(obs, want)
        if not got:
            fn += 1
            continue
        tp += 1
        for k, v in fields.items():
            assert got[0][k] == v, (text, k, got[0][k], v)
        fp += len(obs) - 1  # extra obligations on a labeled clause
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    print(f"\nextraction precision={precision:.3f} recall={recall:.3f} "
          f"(tp={tp} fp={fp} fn={fn})")
    assert precision >= 0.8, "too many spurious obligations"
    assert recall == 1.0, "labeled obligations must all be found"


def test_review_eval_original_preserved(client):
    """§33: original notice 90 stays; corrected 60 recorded; both queryable."""
    from app.models import LifecycleFeedback, Obligation

    token, cid = _setup(client, "eval33@x.com")
    h = auth(token)
    client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h)
    term = next(o for o in client.get(
        f"/api/v1/contracts/{cid}/obligations", headers=h).json()
        if o["type"] == "TERMINATION")
    assert term["notice_period_days"] == 60  # fixture text uses 60
    base = f"/api/v1/obligations/{term['id']}"
    client.post(base + "/reviews", headers=h,
                json={"decision": "modify", "comment": "Period is 45.",
                      "correction_type": "WRONG_NOTICE_PERIOD",
                      "corrected_notice_days": 45})
    cur = client.get(base, headers=h).json()
    assert cur["notice_period_days"] == 60  # original immutable
    fb = client.get(base + "/feedback", headers=h).json()[0]
    assert (fb["original_notice_days"], fb["corrected_notice_days"]) == (60, 45)
    db = TestingSession()
    try:
        ob = db.get(Obligation, uuid.UUID(term["id"]))
        assert ob.notice_period_days == 60
        assert db.query(LifecycleFeedback).filter(
            LifecycleFeedback.obligation_id == ob.id).count() == 1
    finally:
        db.close()
