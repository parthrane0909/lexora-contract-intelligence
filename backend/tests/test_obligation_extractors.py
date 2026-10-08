"""Extractor + date-parser units, incl. the §32 eval fixtures."""
from app.modules.obligations.dates import (
    add_period,
    classify_event,
    notice_deadline,
    parse_date,
    renewal_date,
)
from app.modules.obligations.extractors import (
    get_extractor,
    infer_party,
    infer_trigger,
    parse_explicit_date,
    parse_period,
)
from app.modules.obligations.taxonomy import OBLIGATION_TYPES, OBLIGATION_TAXONOMY_VERSION

ext = get_extractor()


def test_eval_fixture_1_payment():
    obs = ext.extract("Payment is due within 30 days of receipt of invoice.", "payment")
    pay = next(o for o in obs if o["type"] == "PAYMENT")
    assert pay["period_days"] == 30 and pay["trigger"] == "invoice_received"
    assert pay["deadline"] is None  # no anchor date invented


def test_eval_fixture_2_termination():
    obs = ext.extract("Either party may terminate upon 60 days written notice.", "termination")
    term = next(o for o in obs if o["type"] == "TERMINATION")
    assert term["notice_period_days"] == 60


def test_eval_fixture_3_renewal():
    obs = ext.extract(
        "Agreement automatically renews for successive one-year terms "
        "unless notice is provided 90 days before expiration.", "renewal")
    ren = next(o for o in obs if o["type"] == "RENEWAL")
    assert ren["auto_renew"] is True and ren["notice_period_days"] == 90


def test_eval_fixture_4_expiration():
    obs = ext.extract("Agreement expires on December 31, 2027.", None)
    exp = next(o for o in obs if o["type"] == "EXPIRY")
    assert exp["expiration_date"] == "2027-12-31"


def test_eval_fixture_5_ambiguous_no_fabrication():
    obs = ext.extract("The parties shall cooperate in good faith.", None)
    assert obs == []  # nothing established → nothing emitted
    obs2 = ext.extract("Vendor shall maintain insurance coverage.", "insurance")
    ins = next(o for o in obs2 if o["type"] == "INSURANCE")
    assert ins["amount"] is None and ins["deadline"] is None


def test_insurance_amount_and_party():
    obs = ext.extract(
        "Vendor shall maintain liability insurance of at least $1,000,000.", "insurance")
    ins = next(o for o in obs if o["type"] == "INSURANCE")
    assert ins["amount"] == 1000000.0 and ins["party"] == "VENDOR"


def test_termination_cure_and_convenience_split():
    obs = ext.extract(
        "Either party may terminate for convenience with 30 days notice. "
        "Material breach allows termination with 15 days cure period.", "termination")
    terms = [o for o in obs if o["type"] == "TERMINATION"]
    assert terms and terms[0]["notice_period_days"] == 30
    assert any(o["type"] == "NOTICE" and o["notice_period_days"] == 15 for o in obs)


def test_confidentiality_audit_reporting():
    conf = next(o for o in ext.extract(
        "Confidentiality obligations survive for 2 years after termination.", "confidentiality")
        if o["type"] == "CONFIDENTIALITY")
    assert conf["period_days"] == 730  # 2 years -> days
    aud = ext.extract("Client may audit vendor books annually with 10 days notice.", None)
    assert any(o["type"] == "AUDIT" for o in aud)
    rep = ext.extract("Vendor shall submit quarterly security reports.", None)
    r = next(o for o in rep if o["type"] == "REPORTING")
    assert r["frequency"] == "quarterly" and r["period_days"] == 91 and r["period_approximate"]


def test_empty_and_malformed_clauses():
    assert ext.extract("", "payment") == []
    assert ext.extract("   ", None) == []
    assert ext.extract("!!! ??? ...", None) == []  # no crash, no fabrication
    assert ext.extract("Payment payment payment", "payment") != []  # signal still fires


def test_explicit_date_formats_and_guards():
    assert parse_explicit_date("expires on December 31, 2027") is not None
    d = parse_explicit_date("expires on December 31, 2027")
    assert (d.year, d.month, d.day) == (2027, 12, 31)
    assert parse_explicit_date("effective 2027-01-15") is not None
    assert parse_explicit_date("dated 03/25/2026") is not None
    assert parse_explicit_date("no date here") is None
    assert parse_explicit_date("meeting on December 45, 2027") is None  # invalid day
    assert parse_explicit_date("signed in year 3000") is None  # year guard


def test_period_parsing():
    assert parse_period("within 30 days")[0] == 30
    assert parse_period("60 days before expiration")[0] == 60
    assert parse_period("within 10 business days") == (10, False, True)
    days, approx, _ = parse_period("each quarter")
    assert (days, approx) == (91, True)
    assert parse_period("annually")[0] == 365
    assert parse_period("no period mentioned")[0] is None


def test_trigger_and_party_honesty():
    assert infer_trigger("pay within 30 days of invoice") == "invoice_received"
    assert infer_trigger("60 days before expiration") == "contract_expiration"
    assert infer_trigger("vague cooperation text") == "unspecified"
    assert infer_party("Vendor shall deliver the goods") == "VENDOR"
    assert infer_party("Client shall pay") == "CLIENT"
    assert infer_party("Vendor shall pay Client") is None  # ambiguous → None
    assert infer_party("General provisions apply") is None


def test_taxonomy_support_flags():
    assert OBLIGATION_TAXONOMY_VERSION == "1.0"
    assert OBLIGATION_TYPES["PAYMENT"]["supported"] is True
    assert OBLIGATION_TYPES["DELIVERY"]["supported"] is False
    assert OBLIGATION_TYPES["DATA_PROTECTION"]["supported"] is False


def test_date_math():
    import datetime

    assert renewal_date("2027-12-31", 365) == "2028-12-30"
    assert renewal_date(None, 365) is None
    assert renewal_date("2027-12-31", None) is None
    assert notice_deadline("2028-12-30", 60) == "2028-10-31"
    assert notice_deadline(None, 60) is None
    assert add_period(datetime.date(2027, 1, 31), months=1) == datetime.date(2027, 2, 28)
    assert classify_event("2000-01-01") == "OVERDUE"
    assert classify_event("2999-01-01") == "UPCOMING"
    assert classify_event(None) == "UPCOMING"  # dateless stays visible
