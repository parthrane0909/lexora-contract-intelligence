"""Recurrence: frequencies, month-end, horizon, idempotency, complete/skip,
overdue, RBAC, isolation. Uses controlled anchors (no PDF needed)."""
import uuid

import pytest
from conftest import TestingSession, auth, register

from app.models import Obligation, ObligationEventInstance, RecurrenceDefinition
from app.modules.notifications import recurrence as RC
from app.modules.obligations import dates as D


@pytest.fixture()
def ob(client):
    token = register(client, "rc@x.com", "password123", "OrgRC")
    h = auth(token)
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        user = db.query(User).filter(User.email == "rc@x.com").one()
        member = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == user.id).one()
        from app.models import Clause, Contract as _C

        contract = _C(organization_id=member.organization_id, filename="r.pdf",
                      storage_key="k", uploaded_by=user.id)
        db.add(contract)
        db.flush()
        clause = Clause(organization_id=member.organization_id, contract_id=contract.id,
                        text="Vendor shall submit monthly reports.", page_start=1,
                        char_start=0, char_end=10, order_index=0,
                        segmenter_name="t", segmenter_version="t")
        db.add(clause)
        db.flush()
        ob = Obligation(
            organization_id=member.organization_id, contract_id=contract.id,
            clause_id=clause.id, type="REPORTING", title="Reports",
            status="CONFIRMED", confidence=0.9, extraction_method="deterministic_rule",
            model_version="1.0/1.0", source_text="x")
        db.add(ob)
        db.commit()
        yield token, ob.organization_id, str(ob.id), str(contract.id)
    finally:
        db.close()


def _post_rec(client, token, oid, body):
    return client.post(f"/api/v1/obligations/{oid}/recurrence",
                       headers=auth(token), json=body)


def test_monthly_month_end_series():
    series = RC.upcoming_series("2027-01-31", "MONTHLY", 1, horizon_months=12,
                                ref=D.parse_date("2027-01-01"))
    assert series[:3] == ["2027-02-28", "2027-03-31", "2027-04-30"]
    assert all(D.parse_date(d) for d in series)  # no invalid dates
    assert len(series) == 11  # Feb..Dec 2027 within the 12-month horizon


def test_quarterly_weekly_daily_business():
    q = RC.upcoming_series("2027-01-15", "QUARTERLY", 1, horizon_months=12,
                           ref=D.parse_date("2027-01-01"))
    assert q[:3] == ["2027-04-15", "2027-07-15", "2027-10-15"]
    w = RC.upcoming_series("2027-01-04", "WEEKLY", 1, horizon_months=1,
                           ref=D.parse_date("2027-01-01"))
    assert w == ["2027-01-11", "2027-01-18", "2027-01-25", "2027-02-01"]  # horizon inclusive
    b = RC.upcoming_series("2027-01-01", "BUSINESS_DAYS", 1, horizon_months=1,
                           ref=D.parse_date("2027-01-01"))
    assert b[0] == "2027-01-04"  # Friday -> Monday skip weekend
    assert all(D.parse_date(d).weekday() < 5 for d in b)
    y = RC.upcoming_series("2027-03-01", "YEARLY", 1, horizon_months=24,
                           ref=D.parse_date("2027-01-01"))
    assert y == ["2028-03-01"]  # 2029-03-01 is past the 24-month horizon
    assert RC.upcoming_series("2027-01-31", "MONTHLY", 1, horizon_months=4,
                              ref=D.parse_date("2027-01-01")) == [
        "2027-02-28", "2027-03-31", "2027-04-30"]  # 05-31 past horizon


def test_horizon_and_validation(client, ob):
    token, _, oid, _ = ob
    h = auth(token)
    assert _post_rec(client, token, oid, {"frequency": "NOPE"}).status_code == 400
    assert _post_rec(client, token, oid, {
        "frequency": "MONTHLY", "horizon_months": 99}).status_code in (400, 422)
    r = _post_rec(client, token, oid, {
        "frequency": "MONTHLY", "anchor_date": (D.today() + __import__(
            "datetime").timedelta(days=10)).isoformat(),
        "horizon_months": 4}).json()
    assert r["instances_created"] == 3 and r["next_due_date"]
    inst = client.get(f"/api/v1/obligations/{oid}/instances", headers=h).json()
    assert len(inst) == 3 and inst[0]["due_date"] == r["next_due_date"]
    # regenerate: idempotent, old definition deactivated
    import datetime as _dt

    anchor = (D.today() + _dt.timedelta(days=10)).isoformat()
    r2 = _post_rec(client, token, oid, {
        "frequency": "MONTHLY", "anchor_date": anchor,
        "horizon_months": 4}).json()
    assert r2["instances_created"] == 3  # new definition, fresh instances
    db = TestingSession()
    try:
        defs = db.query(RecurrenceDefinition).filter(
            RecurrenceDefinition.obligation_id == uuid.UUID(oid)).all()
        assert len(defs) == 2
        assert sum(d.active for d in defs) == 1
    finally:
        db.close()


def test_complete_skip_and_audit(client, ob):
    token, _, oid, _ = ob
    h = auth(token)
    _post_rec(client, token, oid, {"frequency": "WEEKLY", "anchor_date": "2027-01-04",
                                   "horizon_months": 24})
    inst = client.get(f"/api/v1/obligations/{oid}/instances", headers=h).json()
    first = inst[0]["id"]
    assert client.post(f"/api/v1/obligations/{oid}/instances/{first}/complete",
                       headers=h).json() == {"ok": True, "status": "COMPLETED"}
    done = next(i for i in client.get(f"/api/v1/obligations/{oid}/instances",
                                      headers=h).json() if i["id"] == first)
    assert done["status"] == "COMPLETED" and done["completed_at"]
    assert client.post(f"/api/v1/obligations/{oid}/instances/{first}/complete",
                       headers=h).status_code == 409  # already completed
    second = inst[1]["id"]
    assert client.post(f"/api/v1/obligations/{oid}/instances/{second}/skip",
                       headers=h).json()["status"] == "SKIPPED"
    db = TestingSession()
    try:
        from app.models import AuditLog

        assert db.query(AuditLog).filter(
            AuditLog.action == "obligation.instance.completed").count() == 1
        assert db.query(AuditLog).filter(
            AuditLog.action == "obligation.instance.skipped").count() == 1
    finally:
        db.close()
    # original obligation unchanged
    assert db.query(Obligation).filter(
        Obligation.id == uuid.UUID(oid)).one().status == "CONFIRMED"


def test_overdue_instances_and_rbac_isolation(client, ob):
    token, _, oid, _ = ob
    h = auth(token)
    _post_rec(client, token, oid, {"frequency": "MONTHLY", "anchor_date": "2027-01-31",
                                   "horizon_months": 24})
    inst = client.get(f"/api/v1/obligations/{oid}/instances", headers=h).json()
    assert inst
    # simulate time passage: backdate one instance, then re-read
    db = TestingSession()
    try:
        row = db.query(ObligationEventInstance).filter(
            ObligationEventInstance.id == uuid.UUID(inst[0]["id"])).one()
        row.due_date = "2020-02-29"
        db.commit()
    finally:
        db.close()
    inst = client.get(f"/api/v1/obligations/{oid}/instances", headers=h).json()
    assert inst[0]["status"] == "OVERDUE" and inst[0]["due_date"] == "2020-02-29"
    other = register(client, "orb@x.com", "password123", "OrgRB")
    ho = auth(other)
    assert client.get(f"/api/v1/obligations/{oid}/instances", headers=ho).status_code == 404
    assert client.post(f"/api/v1/obligations/{oid}/instances/{inst[0]['id']}/complete",
                       headers=ho).status_code == 404
    from app.core.security import create_access_token

    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = User(email="vw@x.com", password_hash="x")
        db.add(user)
        db.flush()
        obrow = db.query(Obligation).filter(Obligation.id == uuid.UUID(oid)).one()
        db.add(OrganizationMember(organization_id=obrow.organization_id, user_id=user.id,
                                 role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(obrow.organization_id), "viewer")
    finally:
        db.close()
    hv = auth(vtok)
    assert client.get(f"/api/v1/obligations/{oid}/instances", headers=hv).status_code == 200
    assert client.post(f"/api/v1/obligations/{oid}/recurrence", headers=hv,
                       json={"frequency": "MONTHLY"}).status_code == 403
    assert client.post(f"/api/v1/obligations/{oid}/instances/{inst[0]['id']}/complete",
                       headers=hv).status_code == 403
    assert client.get(f"/api/v1/obligations/{uuid.uuid4()}/instances",
                      headers=h).status_code == 404
