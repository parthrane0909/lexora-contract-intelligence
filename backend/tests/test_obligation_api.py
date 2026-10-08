"""Obligation API: extract, lifecycle, events, review, feedback, RBAC,
isolation, dashboard, override, migration chain. Uses deterministic
classifications inserted directly (matching risk-test conventions).
"""
import json
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import Clause, ClauseClassification, LifecycleFeedback, Obligation

PAGES = [
    ["1. Payment Terms",
     "Client shall pay all fees within 90 days of invoice."],
    ["10. Termination",
     "Either party may terminate with 60 days written notice."],
    ["12. Renewal",
     "This agreement shall automatically renew for successive one-year terms "
     "unless either party provides 60 days notice before expiration."],
    ["20. Term",
     "This agreement expires on December 31, 2027 and is effective January 1, 2025."],
    ["14. Insurance",
     "Vendor shall maintain liability insurance of at least $2,000,000."],
]
CATS = {0: ("payment", 0.9), 1: ("termination", 0.9), 2: ("renewal", 0.9),
        3: ("other", 0.8), 4: ("insurance", 0.9)}


def _setup(client, email):
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    h = auth(token)
    r = client.post("/api/v1/contracts/upload", headers=h,
                    files={"file": ("c.pdf", make_pdf(PAGES), "application/pdf")})
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        assert len(clauses) == 5, [c.title for c in clauses]
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
    return token, cid


def test_extract_lifecycle_events_traceability(client):
    token, cid = _setup(client, "ob@x.com")
    h = auth(token)
    r = client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h).json()
    assert r["status"] == "ready" and r["obligations"] >= 5
    obs = client.get(f"/api/v1/contracts/{cid}/obligations", headers=h).json()
    by_type = {}
    for o in obs:
        by_type.setdefault(o["type"], []).append(o)
    assert by_type["PAYMENT"][0]["period_days"] == 90
    assert by_type["PAYMENT"][0]["trigger"] == "invoice_received"
    assert by_type["PAYMENT"][0]["deadline"] is None  # no anchor invented
    assert by_type["TERMINATION"][0]["notice_period_days"] == 60
    ren = by_type["RENEWAL"][0]
    assert ren["auto_renew"] is True and ren["notice_period_days"] == 60
    assert ren["renewal_date"] == "2028-12-30"  # 2027-12-31 + 365
    assert ren["notice_deadline"] == "2028-10-31"
    assert by_type["EXPIRY"][0]["expiration_date"] == "2027-12-31"
    assert by_type["EFFECTIVE"][0]["effective_date"] == "2025-01-01"
    assert by_type["INSURANCE"][0]["amount"] == 2000000.0
    for o in obs:
        assert o["status"] == "UNCONFIRMED" and o["extraction_method"] == "deterministic_rule"
        assert o["clause_id"] and o["source_text"]  # provenance mandatory
        one = client.get(f"/api/v1/contracts/{cid}/obligations/{o['id']}", headers=h).json()
        assert one["id"] == o["id"]
    # lifecycle summary + events
    lc = client.get(f"/api/v1/contracts/{cid}/lifecycle", headers=h).json()
    assert lc["status"] in ("ACTIVE", "EXPIRING")  # date-dependent, never UNKNOWN here
    assert lc["expiration_date"] == "2027-12-31"
    assert lc["renewal"]["renewal_type"] == "AUTO"
    assert lc["open_obligations"] == len(obs)
    evs = client.get(f"/api/v1/contracts/{cid}/events", headers=h).json()
    types = {e["event_type"] for e in evs}
    assert {"CONTRACT_EXPIRY", "CONTRACT_START", "RENEWAL", "RENEWAL_NOTICE"} <= types
    assert all(e["source_clause_id"] for e in evs)
    # re-extract replaces unreviewed rows without duplicating
    r2 = client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h).json()
    assert r2["obligations"] == r["obligations"]
    obs2 = client.get(f"/api/v1/contracts/{cid}/obligations", headers=h).json()
    assert len(obs2) == len(obs)
    keys = [(o["clause_id"], o["type"], o["rule_id"]) for o in obs2]
    assert len(set(keys)) == len(keys)


def test_review_workflow_and_immutability(client):
    token, cid = _setup(client, "rev@x.com")
    h = auth(token)
    client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h)
    pay = next(o for o in client.get(f"/api/v1/contracts/{cid}/obligations", headers=h).json()
               if o["type"] == "PAYMENT")
    base = f"/api/v1/obligations/{pay['id']}"
    # confirm
    r = client.post(base + "/reviews", headers=h, json={"decision": "confirm"}).json()
    assert r["decision"] == "confirm"
    assert client.get(base, headers=h).json()["status"] == "CONFIRMED"
    # modify with correction (original stays 90)
    r2 = client.post(base + "/reviews", headers=h,
                     json={"decision": "modify", "comment": "Actually 60.",
                           "correction_type": "WRONG_NOTICE_PERIOD",
                           "corrected_notice_days": 60}).json()
    assert r2["corrected_notice_days"] == 60
    cur = client.get(base, headers=h).json()
    assert cur["period_days"] == 90  # original immutable
    assert cur["status"] == "CONFIRMED"
    fb = client.get(base + "/feedback", headers=h).json()
    assert len(fb) == 2
    mod = next(f for f in fb if f["decision"] == "modify")
    assert (mod["original_notice_days"], mod["corrected_notice_days"]) == (None, 60)
    assert mod["original_type"] == "PAYMENT" and mod["reviewer_comment"] == "Actually 60."
    hist = client.get(base + "/reviews", headers=h).json()
    assert [x["decision"] for x in hist] == ["modify", "confirm"]  # newest first, both kept
    # reject → NOT_APPLICABLE, validation enforced
    assert client.post(base + "/reviews", headers=h,
                       json={"decision": "reject"}).status_code == 400
    assert client.post(base + "/reviews", headers=h,
                       json={"decision": "bogus"}).status_code == 400
    assert client.post(base + "/reviews", headers=h,
                       json={"decision": "modify", "comment": "x"}).status_code == 400
    r3 = client.post(base + "/reviews", headers=h,
                     json={"decision": "reject", "comment": "Not an obligation."}).json()
    assert client.get(base, headers=h).json()["status"] == "NOT_APPLICABLE"
    assert r3["decision"] == "reject"


def test_rbac_and_isolation(client):
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    token, cid = _setup(client, "iso@x.com")
    other = register(client, "isoB@x.com", "password123", "OrgIsoB")
    ho, ha = auth(other), auth(token)
    client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=ha)
    oid = client.get(f"/api/v1/contracts/{cid}/obligations", headers=ha).json()[0]["id"]
    for method, url, body in [
        ("POST", f"/api/v1/contracts/{cid}/obligations/extract", None),
        ("GET", f"/api/v1/contracts/{cid}/obligations", None),
        ("GET", f"/api/v1/contracts/{cid}/lifecycle", None),
        ("GET", f"/api/v1/contracts/{cid}/events", None),
        ("POST", f"/api/v1/obligations/{oid}/reviews", {"decision": "confirm"}),
        ("GET", f"/api/v1/obligations/{oid}/reviews", None),
        ("GET", "/api/v1/events/upcoming", None),
        ("GET", "/api/v1/events/overdue", None),
        ("GET", "/api/v1/lifecycle/dashboard", None),
    ]:
        if method == "POST":
            resp = client.post(url, headers=ho, json=body)
        else:
            resp = client.get(url, headers=ho)
        if url in ("/api/v1/events/upcoming", "/api/v1/events/overdue"):
            assert resp.status_code == 200 and resp.json() == [], url  # org lists: empty, not 404
        elif url == "/api/v1/lifecycle/dashboard":
            assert resp.status_code == 200
            assert all(v == 0 for v in resp.json().values()), url
        else:
            assert resp.status_code == 404, (method, url)
    assert client.get("/api/v1/events/upcoming", headers=ho).json() == []
    # same-org viewer: reads OK, writes forbidden
    db = TestingSession()
    try:
        user = User(email="wv@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(_C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(c.organization_id), "viewer")
    finally:
        db.close()
    hv = auth(vtok)
    assert client.get(f"/api/v1/contracts/{cid}/obligations", headers=hv).status_code == 200
    assert client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=hv).status_code == 403
    assert client.post(f"/api/v1/obligations/{oid}/reviews", headers=hv,
                       json={"decision": "confirm"}).status_code == 403


def test_dashboard_override_and_filters(client):
    token, cid = _setup(client, "dash@x.com")
    h = auth(token)
    client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h)
    d0 = client.get("/api/v1/lifecycle/dashboard", headers=h).json()
    assert d0["unconfirmed_obligations"] >= 5
    assert d0["upcoming_obligations"] + d0["overdue_obligations"] >= 1
    assert client.get(f"/api/v1/contracts/{cid}/obligations?status=UNCONFIRMED",
                      headers=h).json()
    assert client.get("/api/v1/contracts/{cid}/obligations?type=PAYMENT",
                      headers=h).json()
    assert client.get("/api/v1/events/upcoming?within_days=30", headers=h).status_code == 200
    assert client.get("/api/v1/events/upcoming?within_days=99", headers=h).status_code == 400
    # manual override wins; invalid rejected; cleared back to computed
    assert client.post(f"/api/v1/contracts/{cid}/lifecycle/override", headers=h,
                       json={"status": "BOGUS"}).status_code == 400
    client.post(f"/api/v1/contracts/{cid}/lifecycle/override", headers=h,
                json={"status": "TERMINATED"})
    assert client.get(f"/api/v1/contracts/{cid}/lifecycle", headers=h).json()["status"] == "TERMINATED"
    client.post(f"/api/v1/contracts/{cid}/lifecycle/override", headers=h,
                json={"status": None})
    assert client.get(f"/api/v1/contracts/{cid}/lifecycle", headers=h).json()["status"] != "TERMINATED"


def test_malformed_and_empty_contracts(client):
    token = register(client, "edge@x.com", "password123", "OrgEdge")
    h = auth(token)
    r = client.post("/api/v1/contracts/upload", headers=h,
                    files={"file": ("e.pdf", make_pdf([["Title Only", "General provisions apply."]]),
                                     "application/pdf")})
    cid = r.json()["id"]
    db = TestingSession()
    try:
        from app.modules.document.pipeline import process_contract

        process_contract(db, uuid.UUID(cid))
    finally:
        db.close()
    out = client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h).json()
    assert out["obligations"] == 0 and out["status"] == "partial"
    lc = client.get(f"/api/v1/contracts/{cid}/lifecycle", headers=h).json()
    assert lc["status"] == "UNKNOWN"  # honest about missing dates
    assert client.post(f"/api/v1/contracts/{cid}/obligations/{uuid.uuid4()}/reviews",
                       headers=h, json={"decision": "confirm"}).status_code == 404


def test_migration_chain_0001_to_0009():
    import alembic.config
    import alembic.script

    cfg = alembic.config.Config("alembic.ini")
    cfg.set_main_option("script_location", "alembic")
    script = alembic.script.ScriptDirectory.from_config(cfg)
    chain, rev = [], "0009_phase9_lifecycle"
    seen = set()
    revs = {s.revision: s for s in script.walk_revisions()}
    while rev:
        assert rev not in seen
        seen.add(rev)
        chain.append(rev)
        downs = revs[rev].down_revision
        rev = downs if isinstance(downs, str) else None
    chain.reverse()  # base -> head
    assert chain[0] == "0001_phase1_foundation" and chain[-1].startswith("0009")
    assert len(chain) == 9
    # ORM metadata covers every migrated table
    from app.db.base import Base

    tables = set(Base.metadata.tables)
    for t in ("obligations", "lifecycle_events", "obligation_reviews",
              "lifecycle_feedback", "risk_findings", "clauses"):
        assert t in tables, t
