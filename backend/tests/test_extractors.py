"""Extractor units: deterministic facts from clause text (no ML, no DB)."""
from app.modules.risk_engine.extractors import (
    extract_facts,
    extract_governing_law,
    extract_indemnity,
    extract_insurance,
    extract_liability,
    extract_payment,
    extract_renewal,
    extract_termination,
    parse_money,
)


def test_parse_money():
    assert parse_money("capped at $1,000,000 for all claims") == (1000000.0, "USD")
    assert parse_money("limited to $2.5 million in fees") == (2500000.0, "USD")
    assert parse_money("no money mentioned here") == (None, None)
    assert parse_money("within 30 days") == (None, None)  # bare numbers are not money


def test_liability_cap_and_unlimited():
    f = extract_liability("Supplier liability shall be capped at $500,000.")
    assert f["cap_amount"] == 500000.0 and not f["unlimited"]
    f2 = extract_liability("Supplier shall be liable for all losses without limitation.")
    assert f2["cap_amount"] is None  # no amount → missing-term signal, not a cap
    f3 = extract_liability("Liability is unlimited under this agreement.")
    assert f3["unlimited"] is True
    f4 = extract_liability("In no event liable for indirect or consequential damages.")
    assert f4["excludes_consequential"] is True and f4["cap_amount"] is None


def test_payment_days():
    assert extract_payment("Client shall pay within 60 days of invoice.")["period_days"] == 60
    assert extract_payment("Net 45 payment terms apply.")["period_days"] == 45
    assert extract_payment("Payment is due upon receipt.")["period_days"] is None


def test_termination_notice_and_convenience():
    f = extract_termination("Either party may terminate with 30 days written notice.")
    assert f["notice_days"] == 30 and not f["for_convenience"]
    f2 = extract_termination("Client may terminate for convenience without cause.")
    assert f2["for_convenience"] is True and f2["notice_days"] is None


def test_renewal_auto_and_notice():
    f = extract_renewal("This agreement shall automatically renew for successive one-year terms.")
    assert f["auto_renew"] is True
    f2 = extract_renewal("Renewal requires 60 days notice before expiry.")
    assert f2["auto_renew"] is False and f2["notice_days"] == 60


def test_governing_law_jurisdiction():
    f = extract_governing_law("This agreement shall be governed by the laws of Delaware.")
    assert f["jurisdiction"] and "Delaware" in f["jurisdiction"]
    assert extract_governing_law("General provisions apply.")["jurisdiction"] is None


def test_insurance_amounts():
    f = extract_insurance("Vendor shall maintain liability insurance of at least $2,000,000.")
    assert f["min_coverage"] == 2000000.0
    assert extract_insurance("Vendor shall maintain insurance.")["min_coverage"] is None


def test_indemnity_one_sided():
    f = extract_indemnity("Vendor shall indemnify and hold harmless Client against all claims.")
    assert f["one_sided"] is True
    f2 = extract_indemnity("Each party shall indemnify the other party against third-party claims.")
    assert f2["one_sided"] is False


def test_extract_facts_dispatch():
    assert "cap_amount" in extract_facts("liability", "capped at $10.")
    assert extract_facts("intellectual_property", "anything") == {}  # no v1 extractor
    assert extract_facts("nonsense", "anything") == {}
