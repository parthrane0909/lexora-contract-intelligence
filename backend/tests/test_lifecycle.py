"""Lifecycle computation units: status, renewal math, events, states."""
from app.modules.obligations import lifecycle as LC
from app.modules.obligations.taxonomy import (
    EVENT_STATES,
    LIFECYCLE_ENGINE_VERSION,
    LIFECYCLE_STATUSES,
)


def test_status_unknown_without_expiration():
    s = LC.contract_lifecycle(None, None)
    assert s["status"] == "UNKNOWN"  # never auto-expires on missing data
    s = LC.contract_lifecycle(None, None, override="ACTIVE")
    assert s["status"] == "ACTIVE" and "Manual" in s["reason"]


def test_status_derivation():
    assert LC.contract_lifecycle("2020-01-01", "2000-06-01")["status"] == "EXPIRED"
    assert LC.contract_lifecycle("2020-01-01", "2999-06-01")["status"] == "ACTIVE"
    assert LC.contract_lifecycle(None, None, terminated=True)["status"] == "TERMINATED"
    assert LC.contract_lifecycle(None, None, archived=True)["status"] == "ARCHIVED"
    assert LC.contract_lifecycle("2999-01-01", None)["status"] == "DRAFT"


def test_expiring_window():
    import datetime

    from app.modules.obligations import dates as D

    soon = (D.today() + datetime.timedelta(days=30)).isoformat()
    assert LC.contract_lifecycle("2020-01-01", soon)["status"] == "EXPIRING"


def test_renewal_intelligence_no_fabrication():
    assert LC.renewal_intelligence(None, None, None, None) == {}
    r = LC.renewal_intelligence(True, 365, 60, "2027-12-31")
    assert r["renewal_type"] == "AUTO" and r["renewal_date"] == "2028-12-30"
    assert r["notice_deadline"] == "2028-10-31"  # 60 days before renewal
    r2 = LC.renewal_intelligence(True, 365, 60, None)  # no anchor
    assert r2["renewal_date"] is None and r2["notice_deadline"] is None
    r3 = LC.renewal_intelligence(False, None, 30, "2027-12-31")
    assert r3["renewal_type"] == "MANUAL" and r3["renewal_date"] is None
    assert r3["notice_deadline"] == "2027-12-01"  # falls back to expiration anchor


def test_termination_window():
    assert LC.termination_window(None, None) == {}
    assert LC.termination_window(30, 15) == {
        "termination_notice_days": 30, "cure_period_days": 15}


def test_events_for_obligation():
    evs = LC.events_for_obligation({"type": "EXPIRY", "expiration_date": "2027-12-31"})
    assert evs == [{"event_type": "CONTRACT_EXPIRY", "event_date": "2027-12-31",
                    "description": "Contract expiration extracted."}]
    evs = LC.events_for_obligation({"type": "RENEWAL", "renewal_date": "2028-12-30",
                                    "notice_deadline": "2028-10-31"})
    assert {e["event_type"] for e in evs} == {"RENEWAL", "RENEWAL_NOTICE"}
    assert LC.events_for_obligation({"type": "PAYMENT"}) == []  # no date → no event
    assert LC.events_for_obligation({"type": "OTHER"}) == []


def test_versions_and_status_vocab():
    assert LIFECYCLE_ENGINE_VERSION == "1.0"
    assert set(LIFECYCLE_STATUSES) >= {"ACTIVE", "EXPIRING", "EXPIRED", "UNKNOWN"}
    assert set(EVENT_STATES) >= {"UPCOMING", "OVERDUE"}
