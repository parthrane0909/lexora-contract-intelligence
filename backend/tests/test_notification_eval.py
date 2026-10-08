"""Notification/delivery/recurrence eval (§34-37). Deterministic service-level
fixtures with explicit reference dates (no time travel hacks in prod code —
`ref` is a service parameter). Reports measured counts, not adjectives.
"""
import uuid

from conftest import TestingSession, auth
from test_notifications import _seed as _seed_event

from app.models import Notification, NotificationDelivery, Obligation
from app.modules.notifications import service as SVC
from app.modules.notifications.providers import FakeFailProvider, InAppNotificationProvider
from app.modules.obligations import dates as D


def _run(oid, ref, provider=None):
    db = TestingSession()
    try:
        org = db.query(Obligation).filter(
            Obligation.id == uuid.UUID(oid)).one().organization_id
        return SVC.run_notification_scan(
            db, org, provider=provider or InAppNotificationProvider(),
            ref=D.parse_date(ref))
    finally:
        db.close()


def _count(oid):
    db = TestingSession()
    try:
        return db.query(Notification).count()
    finally:
        db.close()


def test_eval_notification_windows_no_duplicates(client):
    """§36: event 2027-12-30. Scan 11-30 → 30d only. Rescan → none.
    Scan 12-16 → 14d only (30d already exists)."""
    _, _, oid, _ = _seed_event(client, "ev36@x.com", event_date="2027-12-30")
    s1 = _run(oid, "2027-11-30")
    assert s1["notifications_created"] == 1
    db = TestingSession()
    try:
        assert db.query(Notification).one().window_days == 30
    finally:
        db.close()
    s2 = _run(oid, "2027-11-30")
    assert s2["notifications_created"] == 0  # NO DUPLICATE
    s3 = _run(oid, "2027-12-16")
    assert s3["notifications_created"] == 1
    db = TestingSession()
    try:
        wins = sorted(n.window_days for n in db.query(Notification).all())
        assert wins == [14, 30], wins
    finally:
        db.close()
    print("\n§36 eval: 30d@11-30 ok, rescan silent ok, 14d@12-16 ok")


def test_eval_delivery_ledger_fail_then_success(client):
    """§37: first attempt fails, second succeeds. attempt_count=2, SENT,
    original failure retained, single notification row."""
    _, _, oid, _ = _seed_event(client, "ev37@x.com", event_date="2027-12-30")
    fake = FakeFailProvider(fail_times=1)
    s = _run(oid, "2027-11-30", provider=fake)
    assert s["deliveries_failed"] == 1
    # rescan creates nothing new AND does not redeliver: retry is explicit
    r = _run(oid, "2027-11-30", provider=fake)
    assert r["notifications_created"] == 0
    db = TestingSession()
    try:
        from app.modules.notifications import service as _svc

        n = db.query(Notification).one()
        assert n.status == "FAILED"
        out = _svc.retry_deliveries(db, n.organization_id, provider=fake)
        assert out == {"retried": 1, "sent": 1}
        n = db.query(Notification).one()
        assert n.status == "SENT"
        rows = db.query(NotificationDelivery).filter(
            NotificationDelivery.notification_id == n.id).order_by(
            NotificationDelivery.attempt_count).all()
        assert [d.status for d in rows] == ["FAILED", "SENT"]
        assert rows[1].attempt_count == 2 and rows[0].error
        assert db.query(Notification).count() == 1  # no duplicate record
    finally:
        db.close()
    print("\n§37 eval: fail→retry ok, attempts=2, SENT, history kept")


def test_eval_recurrence_fixtures():
    """§35 fixtures (pure functions, exact dates)."""
    from app.modules.notifications.recurrence import upcoming_series

    m = upcoming_series("2027-01-31", "MONTHLY", 1, horizon_months=4,
                        ref=D.parse_date("2027-01-01"))
    assert m == ["2027-02-28", "2027-03-31", "2027-04-30"]
    q = upcoming_series("2027-01-15", "QUARTERLY", 1, horizon_months=12,
                        ref=D.parse_date("2027-01-01"))
    assert q[:3] == ["2027-04-15", "2027-07-15", "2027-10-15"]
    print("\n§35 eval: monthly month-end ok, quarterly ok, no invalid dates")
