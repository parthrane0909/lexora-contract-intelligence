"""Part 7 groundwork — KPI-derived contract filters, search paging/sorting.

The dashboard KPI cards must be clickable filters over the SAME table, so the
number on the card and the number of rows below it can never disagree. That
requires three guarantees, each pinned here:

  * a KPI filter counts DISTINCT CONTRACTS, never findings or obligations;
  * the exact same constraint is enforced by the PostgreSQL listing AND by the
    Elasticsearch search path (org-scoped, re-verified per row);
  * a constraint that matches nothing returns an EMPTY result — it is never
    quietly dropped.

Also covers the paging/sorting vocabulary added to the search endpoint
(`offset`, `sort`) so search and listing paginate identically.

Read-only with respect to risk: no rule, priority or score is created or
altered here beyond the fixture rows these assertions are built on.
"""
import datetime
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import (
    AnalysisRun,
    Clause,
    Obligation,
    ReviewDecision,
    RiskFinding,
)
from app.modules.search import es, index as search_index


def _es_reachable() -> bool:
    try:
        es.cluster_info()
        return True
    except Exception:  # noqa: BLE001 — unreachable means "skip, not fail"
        return False


@pytest.fixture(autouse=True)
def fresh_index():
    """Each test starts from an empty search index (idempotent reset).

    Declared autouse so it runs BEFORE `kpi_org` uploads — resetting after
    the fixture would wipe the documents those uploads just published.
    """
    search_index.ensure_index(force=True)
    yield


# ------------------------------------------------------------- helpers ------

def _upload(client, token, name):
    """Upload + process one contract; returns (contract_id, [clause ids])."""
    from app.modules.document.pipeline import process_contract

    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": (name, make_pdf(
                        [["1. Payment Terms", "Pay within 30 days."],
                         ["8.2 Liability", "Liability is capped."]]),
                        "application/pdf")})
    assert r.status_code == 200, r.text
    cid = uuid.UUID(r.json()["id"])
    db = TestingSession()
    try:
        assert process_contract(db, cid)["status"] == "processed"
        clauses = [c.id for c in db.query(Clause)
                   .filter(Clause.contract_id == cid).all()]
        return cid, clauses
    finally:
        db.close()


def _org_of(email: str):
    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        user = db.query(User).filter(User.email == email).one()
        return db.query(OrganizationMember).filter(
            OrganizationMember.user_id == user.id).one().organization_id
    finally:
        db.close()


def _list(client, token, **params):
    r = client.get("/api/v1/contracts", headers=auth(token), params=params)
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()}, int(r.headers["X-Total-Count"])


def _search(client, token, **params):
    return client.get("/api/v1/search/contracts", headers=auth(token),
                      params=params)


# --------------------------------------------------------------- fixtures ---

@pytest.fixture
def kpi_org(client):
    """Org A: `risky` (2 high/critical findings, one of them rejected, plus
    two renewals inside 30 days) and `clean` (no findings, renewal at 60 days).
    Org B: one contract with one high finding (isolation)."""
    ta = register(client, "a@x.com", "password123", "OrgA")
    tb = register(client, "b@x.com", "password123", "OrgB")
    orga, orgb = _org_of("a@x.com"), _org_of("b@x.com")

    risky, risky_clauses = _upload(client, ta, "a1_risky_alpha.pdf")
    clean, clean_clauses = _upload(client, ta, "b1_clean_alpha.pdf")
    other, other_clauses = _upload(client, tb, "c1_other_alpha.pdf")

    today = datetime.date.today()
    soon = (today + datetime.timedelta(days=20)).isoformat()
    far = (today + datetime.timedelta(days=60)).isoformat()

    db = TestingSession()
    try:
        from app.models import User

        def run(org, cid):
            r = AnalysisRun(organization_id=org, contract_id=cid,
                            kind="risk_analysis", status="done")
            db.add(r)
            db.flush()
            return r

        def finding(org, cid, clause, arun, priority):
            f = RiskFinding(organization_id=org, contract_id=cid,
                            clause_id=clause, analysis_run_id=arun.id,
                            risk_category="liability", finding_type="rule",
                            priority=priority, title=f"{priority} issue",
                            reason="r", rule_id="R-1", rule_version="1.0")
            db.add(f)
            db.flush()
            return f

        run_a, run_b, run_c = run(orga, risky), run(orga, clean), run(orgb, other)
        f_high = finding(orga, risky, risky_clauses[0], run_a, "high")
        f_crit = finding(orga, risky, risky_clauses[0], run_a, "critical")
        finding(orgb, other, other_clauses[0], run_c, "high")

        # A REJECTED finding contributes no risk weight, so `risky` keeps one
        # qualifying finding — the filter must use the same reviewed /
        # unresolved semantics the KPI already uses.
        user_a = db.query(User).filter_by(email="a@x.com").one()
        db.add(ReviewDecision(organization_id=orga, finding_id=f_crit.id,
                              reviewer_id=user_a.id, decision="reject"))
        db.flush()

        def ob(org, cid, clause, **kw):
            defaults = dict(organization_id=org, contract_id=cid,
                            clause_id=clause, type="RENEWAL", title="Renew",
                            status="CONFIRMED", confidence=0.9,
                            extraction_method="deterministic_rule",
                            model_version="1.0/1.0",
                            source_text="Renews annually.")
            defaults.update(kw)
            db.add(Obligation(**defaults))
            db.flush()

        ob(orga, risky, risky_clauses[0], renewal_date=soon)
        ob(orga, risky, risky_clauses[0], renewal_date=soon)   # 2nd in window
        ob(orga, risky, risky_clauses[0], renewal_date=far)    # beyond 30 days
        ob(orga, clean, clean_clauses[0], renewal_date=far)
        db.commit()
    finally:
        db.close()

    return {"client": client, "ta": ta, "tb": tb, "risky": str(risky),
            "clean": str(clean), "other": str(other)}


# ------------------------------------------------- PostgreSQL listing KPIs ---

def test_high_risk_filter_counts_distinct_contracts(kpi_org):
    ids, total = _list(kpi_org["client"], kpi_org["ta"], high_risk="true")
    assert ids == {kpi_org["risky"]}          # 2 findings → 1 contract
    assert total == 1

    # A contract whose only critical finding was REJECTED is not high risk.
    all_ids, all_total = _list(kpi_org["client"], kpi_org["ta"])
    assert all_total == 2 and kpi_org["clean"] in all_ids


def test_high_risk_filter_is_organization_scoped(kpi_org):
    ids, _ = _list(kpi_org["client"], kpi_org["tb"], high_risk="true")
    assert ids == {kpi_org["other"]}
    assert kpi_org["risky"] not in ids


def test_high_risk_flag_off_means_no_constraint(kpi_org):
    """`high_risk` is an opt-in flag: false/absent must never invert it."""
    c, t = kpi_org["client"], kpi_org["ta"]
    assert _list(c, t, high_risk="false") == _list(c, t)


def test_renewing_filter_uses_real_dates_and_deduplicates(kpi_org):
    c, t = kpi_org["client"], kpi_org["ta"]
    # Two renewal obligations in the window, but ONE renewing contract.
    ids, total = _list(c, t, renewing_within_days=30)
    assert ids == {kpi_org["risky"]} and total == 1

    # Widening the window reaches the 60-day renewal on `clean`.
    ids, total = _list(c, t, renewing_within_days=60)
    assert ids == {kpi_org["risky"], kpi_org["clean"]} and total == 2

    # A window with no known dates yields an EMPTY page, not an unfiltered one.
    ids, total = _list(c, t, renewing_within_days=5)
    assert ids == set() and total == 0


def test_kpi_filters_combine_with_each_other(kpi_org):
    c, t = kpi_org["client"], kpi_org["ta"]
    ids, total = _list(c, t, high_risk="true", renewing_within_days=30)
    assert ids == {kpi_org["risky"]} and total == 1

    # impossible combination → valid 200 with zero rows
    ids, total = _list(c, t, high_risk="true", renewing_within_days=5)
    assert ids == set() and total == 0


def test_kpi_filters_are_validated(kpi_org):
    c, t = kpi_org["client"], kpi_org["ta"]
    assert c.get("/api/v1/contracts", headers=auth(t),
                 params={"renewing_within_days": 0}).status_code == 422
    assert c.get("/api/v1/contracts", headers=auth(t),
                 params={"renewing_within_days": 366}).status_code == 422
    assert c.get("/api/v1/search/contracts", headers=auth(t),
                 params={"q": "pdf", "renewing_within_days": 999}
                 ).status_code == 422


def test_summary_counts_distinct_renewing_contracts(kpi_org):
    """Obligation-level and contract-level renewal counts differ on purpose."""
    r = kpi_org["client"].get("/api/v1/portfolio/summary",
                              headers=auth(kpi_org["ta"]))
    assert r.status_code == 200, r.text
    s = r.json()
    # two renewal obligations on ONE contract inside the window
    assert s["upcoming_renewals_30d"] == 2
    assert s["contracts_renewing_30d"] == 1
    # distinct contracts with a qualifying unresolved high/critical finding
    assert s["contracts_with_high_critical_unresolved"] == 1
    assert s["total_contracts"] == 2


# ---------------------------------------------------------- search paging ---

@pytest.mark.skipif(not _es_reachable(), reason="Elasticsearch not reachable")
def test_search_offset_sort_and_total_stay_consistent(client):
    ta = register(client, "p@x.com", "password123", "OrgP")
    for name in ("zulu_report.pdf", "alpha_report.pdf", "mike_report.pdf"):
        _upload(client, ta, name)

    page1 = _search(client, ta, q="report", limit=2, offset=0, sort="filename")
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["total"] == 3 and len(body1["results"]) == 2
    assert [r["filename"] for r in body1["results"]] == \
        ["alpha_report.pdf", "mike_report.pdf"]

    page2 = _search(client, ta, q="report", limit=2, offset=2,
                    sort="filename").json()
    assert page2["total"] == 3 and len(page2["results"]) == 1
    assert page2["results"][0]["filename"] == "zulu_report.pdf"
    # pages never overlap
    assert {r["contract_id"] for r in body1["results"]}.isdisjoint(
        {r["contract_id"] for r in page2["results"]})

    newest = _search(client, ta, q="report", limit=10, sort="newest").json()
    oldest = _search(client, ta, q="report", limit=10, sort="oldest").json()
    assert [r["contract_id"] for r in newest["results"]] == \
        [r["contract_id"] for r in reversed(oldest["results"])]


@pytest.mark.skipif(not _es_reachable(), reason="Elasticsearch not reachable")
def test_search_rejects_unknown_sort(client):
    ta = register(client, "s@x.com", "password123", "OrgS")
    _upload(client, ta, "thing.pdf")
    r = _search(client, ta, q="thing", sort="bogus")
    assert r.status_code == 400, r.text


@pytest.mark.skipif(not _es_reachable(), reason="Elasticsearch not reachable")
def test_kpi_filters_agree_between_listing_and_search(kpi_org):
    c, t = kpi_org["client"], kpi_org["ta"]

    listed, _ = _list(c, t, high_risk="true")
    r = _search(c, t, q="alpha", high_risk="true", limit=50)
    assert r.status_code == 200, r.text
    assert {row["contract_id"] for row in r.json()["results"]} == listed

    # a constraint that matches nothing empties the search too
    r = _search(c, t, q="alpha", high_risk="true", renewing_within_days=5,
                limit=50)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 0 and r.json()["results"] == []


@pytest.mark.skipif(not _es_reachable(), reason="Elasticsearch not reachable")
def test_kpi_search_filter_is_organization_scoped(kpi_org):
    c = kpi_org["client"]
    r = _search(c, kpi_org["tb"], q="alpha", high_risk="true", limit=50)
    assert r.status_code == 200, r.text
    found = {row["contract_id"] for row in r.json()["results"]}
    assert found == {kpi_org["other"]}
    assert kpi_org["risky"] not in found
