"""Notification engine tests: windows, prefs, idempotency, retry, read state,
RBAC, isolation, traceability, honest delivery status."""
import uuid

import pytest
from conftest import TestingSession, auth, register
from test_obligation_api import _setup

from app.models import LifecycleEvent, Notification, NotificationDelivery, Obligation
from app.modules.notifications import service as SVC
from app.modules.notifications.providers import FakeFailProvider
from app.modules.obligations import dates as D


def _seed(client, email, *, event_date, event_type="PAYMENT_DEADLINE",
          ob_status="CONFIRMED"):
    token, cid = _setup(client, email)
    h = auth(token)
    out = client.post(f"/api/v1/contracts/{cid}/obligations/extract", headers=h).json()
    assert out["status"] in ("ready", "partial"), out
    db = TestingSession()
    try:
        obs = db.query(Obligation).filter(
            Obligation.contract_id == uuid.UUID(cid)).all()
        assert obs
        for ob in obs:
            ob.status = ob_status  # default CONFIRMED: silence review reminders
        ob = obs[0]
        db.query(LifecycleEvent).filter(
            LifecycleEvent.contract_id == uuid.UUID(cid)).delete()
        ev = LifecycleEvent(
            organization_id=ob.organization_id, contract_id=ob.contract_id,
            obligation_id=ob.id, event_type=event_type, event_date=event_date,
            trigger="test", status="UPCOMING", source_clause_id=ob.clause_id,
            description="seeded")
        db.add(ev)
        db.commit()
        return token, cid, str(ob.id), str(ev.id)
    finally:
        db.close()


def _iso(days):
    import datetime

    return (D.today() + datetime.timedelta(days=days)).isoformat()


def _scan(ob_id, provider=None):
    db = TestingSession()
    try:
        org = db.query(Obligation).filter(
            Obligation.id == uuid.UUID(ob_id)).one().organization_id
        # provider=None -> service resolves via get_provider() (patchable)
        return SVC.run_notification_scan(db, org, provider=provider)
    finally:
        db.close()


def test_windows_30_14_7_1_0(client):
    token, cid, oid, _ = _seed(client, "w30@x.com", event_date=_iso(30))
    s = _scan(oid)
    assert s["notifications_created"] == 1 and s["deliveries_sent"] == 1
    db = TestingSession()
    try:
        notes = db.query(Notification).all()
        assert notes[0].window_days == 30
        assert notes[0].notification_type == "UPCOMING_DEADLINE"
        assert notes[0].status == "SENT" and notes[0].is_read is False
    finally:
        db.close()


def test_due_today_and_overdue(client):
    _, _, oid, _ = _seed(client, "due@x.com", event_date=_iso(0))
    _scan(oid)
    db = TestingSession()
    try:
        types = {n.notification_type for n in db.query(Notification).all()}
        assert "DUE_TODAY" in types
    finally:
        db.close()
    _, _, oid2, _ = _seed(client, "od@x.com", event_date=_iso(-5))
    _scan(oid2)
    db = TestingSession()
    try:
        od = [n for n in db.query(Notification).all() if n.notification_type == "OVERDUE"]
        assert len(od) == 1 and od[0].window_days is None
    finally:
        db.close()


def test_duplicate_scan_idempotent(client):
    _, _, oid, _ = _seed(client, "dup@x.com", event_date=_iso(14))
    s1 = _scan(oid)
    s2 = _scan(oid)
    assert s1["notifications_created"] >= 1
    assert s2["notifications_created"] == 0
    assert s2["notifications_skipped"] >= s1["notifications_created"]
    db = TestingSession()
    try:
        # one notification, one delivery — never duplicated
        assert db.query(NotificationDelivery).count() == s1["deliveries_sent"]
    finally:
        db.close()


def test_window_progression_no_duplicates(client):
    """+5d event enters 30/14/7 windows at once; a repeat scan stays silent."""
    _, _, oid, _ = _seed(client, "prog@x.com", event_date=_iso(5))
    _scan(oid)
    db = TestingSession()
    try:
        wins = sorted(n.window_days for n in db.query(Notification).all()
                      if n.notification_type == "UPCOMING_DEADLINE")
        assert wins == [7, 14, 30], wins
        s2 = _scan(oid)
        assert s2["notifications_created"] == 0
    finally:
        db.close()


def test_different_recipients_and_read_isolation(client):
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    token, cid, oid, _ = _seed(client, "r1@x.com", event_date=_iso(10))
    db = TestingSession()
    try:
        user = User(email="r2@x.com", password_hash="x")
        db.add(user)
        db.flush()
        c = db.get(_C, uuid.UUID(cid))
        db.add(OrganizationMember(organization_id=c.organization_id, user_id=user.id,
                                 role="contract_manager"))
        db.commit()
        tok2 = create_access_token(str(user.id), str(c.organization_id), "contract_manager")
    finally:
        db.close()
    _scan(oid)
    n1 = client.get("/api/v1/notifications", headers=auth(token)).json()
    n2 = client.get("/api/v1/notifications", headers=auth(tok2)).json()
    assert len(n1) >= 1 and len(n2) >= 1
    assert n1[0]["id"] != n2[0]["id"]  # per-recipient rows
    client.post(f"/api/v1/notifications/{n1[0]['id']}/read", headers=auth(token))
    still = client.get("/api/v1/notifications/unread", headers=auth(tok2)).json()
    assert any(n["id"] == n2[0]["id"] for n in still)  # other's read unaffected
    assert all(n["id"] != n1[0]["id"] for n in
               client.get("/api/v1/notifications/unread", headers=auth(token)).json())
    client.post("/api/v1/notifications/read-all", headers=auth(tok2))
    assert client.get("/api/v1/notifications/unread", headers=auth(tok2)).json() == []


def test_preferences_disable_and_validation(client):
    token, cid, oid, _ = _seed(client, "pref@x.com", event_date=_iso(10))
    h = auth(token)
    prefs = client.get("/api/v1/notification-preferences", headers=h).json()
    assert any(p["enabled"] is True for p in prefs)  # default enabled
    r = client.put("/api/v1/notification-preferences", headers=h,
                   json={"notification_type": "UPCOMING_DEADLINE", "enabled": False}).json()
    assert r["enabled"] is False
    s = _scan(oid)
    assert s["notifications_created"] == 0  # disabled → skipped, never created
    assert client.put("/api/v1/notification-preferences", headers=h,
                      json={"notification_type": "NOPE", "enabled": False}).status_code == 400
    assert client.put("/api/v1/notification-preferences", headers=h,
                      json={"notification_type": "UPCOMING_DEADLINE", "enabled": True,
                            "channel": "smoke"}).status_code == 400


def test_failed_delivery_retry_and_ledger(client, monkeypatch):
    import app.modules.notifications.service as svc_mod

    _, _, oid, _ = _seed(client, "fail@x.com", event_date=_iso(30))
    fake = FakeFailProvider(fail_times=1)
    monkeypatch.setattr(svc_mod, "get_provider", lambda *a, **k: fake)
    s = _scan(oid)
    assert s["deliveries_failed"] == 1 and s["deliveries_sent"] == 0
    db = TestingSession()
    try:
        n = db.query(Notification).first()
        assert n.status == "FAILED"
        drows = db.query(NotificationDelivery).filter(
            NotificationDelivery.notification_id == n.id).all()
        assert len(drows) == 1 and drows[0].status == "FAILED"
        assert drows[0].attempt_count == 1 and "simulated failure" in drows[0].error
        r = svc_mod.retry_deliveries(db, n.organization_id)
        assert r == {"retried": 1, "sent": 1}
        drows = db.query(NotificationDelivery).filter(
            NotificationDelivery.notification_id == n.id).order_by(
            NotificationDelivery.attempt_count).all()
        assert [d.status for d in drows] == ["FAILED", "SENT"]  # history preserved
        assert drows[0].error and "simulated" in drows[0].error  # original kept
        assert db.query(Notification).filter(Notification.id == n.id).one().status == "SENT"
        for d in drows:
            d.attempt_count = 99
        db.commit()
        n.status = "FAILED"
        db.commit()
        assert svc_mod.retry_deliveries(db, n.organization_id)["retried"] == 0  # cap
    finally:
        db.close()


def test_no_fake_delivery_status(client, monkeypatch):
    import app.modules.notifications.service as svc_mod

    _, _, oid, _ = _seed(client, "honest@x.com", event_date=_iso(30))
    monkeypatch.setattr(svc_mod, "get_provider",
                        lambda *a, **k: FakeFailProvider(fail_times=99))
    s = _scan(oid)
    assert s["deliveries_sent"] == 0 and s["deliveries_failed"] >= 1
    db = TestingSession()
    try:
        assert all(n.status == "FAILED" for n in db.query(Notification).all())
    finally:
        db.close()


def test_rejected_and_unconfirmed_obligation_routing(client):
    _, _, oid, _ = _seed(client, "rej@x.com", event_date=_iso(10),
                         ob_status="NOT_APPLICABLE")
    s = _scan(oid)
    assert s["notifications_created"] == 0  # rejected → silence
    _, _, oid2, _ = _seed(client, "unc@x.com", event_date=_iso(400),
                          ob_status="UNCONFIRMED")
    _scan(oid2)
    db = TestingSession()
    try:
        types = {n.notification_type for n in db.query(Notification).all()}
        assert types == {"OBLIGATION_REQUIRES_REVIEW"}, types
    finally:
        db.close()


def test_rbac_viewer_and_deliveries_ledger(client):
    from app.core.security import create_access_token

    from app.models import Contract as _C
    from app.models import OrganizationMember, User

    token, cid, oid, _ = _seed(client, "rb@x.com", event_date=_iso(10))
    db = TestingSession()
    try:
        user = User(email="v9@x.com", password_hash="x")
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
    assert client.post("/api/v1/notifications/scan", headers=hv).status_code == 403
    assert client.post("/api/v1/notifications/retry", headers=hv).status_code == 403
    assert client.get("/api/v1/notifications/deliveries", headers=hv).status_code == 403
    assert client.get("/api/v1/notifications", headers=hv).status_code == 200
    assert client.put("/api/v1/notification-preferences", headers=hv,
                      json={"notification_type": "OVERDUE", "enabled": False}).status_code == 200
    _scan(oid)
    dl = client.get("/api/v1/notifications/deliveries", headers=auth(token)).json()
    assert dl and dl[0]["provider"] == "inapp" and dl[0]["attempt_count"] >= 1
    assert client.get("/api/v1/notifications/deliveries?status=SENT",
                      headers=auth(token)).json()


def test_isolation_and_traceability(client):
    token, cid, oid, eid = _seed(client, "ia@x.com", event_date=_iso(10))
    other = register(client, "ib@x.com", "password123", "OrgIB")
    ho = auth(other)
    _scan(oid)
    assert client.get("/api/v1/notifications", headers=ho).json() == []
    assert client.get("/api/v1/notifications/unread", headers=ho).json() == []
    assert client.get("/api/v1/lifecycle/recurrence/upcoming", headers=ho).json() == []
    db = TestingSession()
    try:
        from app.models import Notification as N

        n = db.query(N).first()
        nid = str(n.id)
        assert client.post(f"/api/v1/notifications/{nid}/read", headers=ho).status_code == 404
        assert db.get(Obligation, uuid.UUID(str(n.obligation_id))) is not None
        from app.models import LifecycleEvent as LE

        ev = db.get(LE, uuid.UUID(str(n.lifecycle_event_id)))
        assert ev is not None and str(ev.contract_id) == cid
    finally:
        db.close()
