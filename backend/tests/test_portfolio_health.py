"""Portfolio health score tests (Master Plan, Part 1, Step 9).

Each test pins one documented behaviour of PORTFOLIO_HEALTH_SCORING_VERSION
"1.0": empty portfolio, fully analyzed clean portfolio, low/high/critical
findings, review effect, overdue obligations, renewal + notice windows,
policy deviation, partial/stale/failed coverage, scale invariance, range,
determinism and hand-reproducibility from the API payload.

Source rows are inserted directly for exact control; every assertion goes
through the real HTTP endpoint.
"""
import datetime
import uuid

from conftest import TestingSession, auth, register

from app.models import (
    AnalysisRun,
    Clause,
    Contract,
    Obligation,
    ReviewDecision,
    RiskFinding,
)
from app.modules.portfolio.health import (
    PORTFOLIO_HEALTH_SCORING_VERSION,
    WEIGHTS,
)

PATH = "/api/v1/portfolio/health"


def _org(client, email, org):
    """Register an org and return (token, org_id, user_id)."""
    tok = register(client, email, "password123", org)
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        user = db.query(User).filter(User.email == email).one()
        org_id = (db.query(OrganizationMember)
                  .filter(OrganizationMember.user_id == user.id).one().organization_id)
        return tok, org_id, user.id
    finally:
        db.close()


def _contract(db, org_id, name, analysis="complete", processing="processed",
              run=True):
    """Insert one contract (+ clause, + completed risk run) and return ids."""
    c = Contract(organization_id=org_id, filename=name,
                 processing_status=processing, analysis_status=analysis,
                 storage_key=f"key/{uuid.uuid4()}")
    db.add(c)
    db.flush()
    cl = Clause(organization_id=org_id, contract_id=c.id,
                text="Payment is due within 30 days.", char_start=0, char_end=31,
                order_index=0, segmenter_name="deterministic",
                segmenter_version="1.0")
    db.add(cl)
    db.flush()
    r = None
    if run:
        r = AnalysisRun(organization_id=org_id, contract_id=c.id,
                        kind="risk_analysis", status="done")
        db.add(r)
        db.flush()
    return c.id, cl.id, (r.id if r else None)


def _finding(db, org_id, cid, clid, rid, priority="low", ftype="rule",
             category="payment"):
    f = RiskFinding(organization_id=org_id, contract_id=cid, clause_id=clid,
                    analysis_run_id=rid, risk_category=category,
                    finding_type=ftype, priority=priority, title="t",
                    reason="r", rule_id="R-1", rule_version="1.0")
    db.add(f)
    db.flush()
    return f.id


def _obligation(db, org_id, cid, clid, **kw):
    defaults = dict(organization_id=org_id, contract_id=cid, clause_id=clid,
                    type="PAYMENT", title="Pay", status="CONFIRMED",
                    confidence=0.9, extraction_method="deterministic_rule",
                    model_version="1.0/1.0", source_text="Pay within 30 days.")
    defaults.update(kw)
    o = Obligation(**defaults)
    db.add(o)
    db.flush()
    return o.id


def _get(client, tok):
    r = client.get(PATH, headers=auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


def _health(j):
    return j["portfolio_health"]


# --- TEST A: empty portfolio ---------------------------------------------
def test_a_empty_portfolio(client):
    tok, _, _ = _org(client, "empty@x.com", "EmptyOrg")
    j = _get(client, tok)
    assert j["scoring_version"] == PORTFOLIO_HEALTH_SCORING_VERSION == "1.0"
    assert _health(j)["score"] is None
    assert _health(j)["raw_score"] is None
    assert _health(j)["analysis_coverage"] is None
    assert _health(j)["label"] == "No contracts"
    assert _health(j)["orientation"] == "higher_is_healthier"
    assert j["drivers"]["total_contracts"] == 0
    assert j["drivers"]["analyzed_contracts"] == 0
    assert j["coverage"]["total_contracts"] == 0
    assert len(j["components"]) == len(WEIGHTS)
    assert "No active contracts" in _health(j)["explanation"]
    # authentication is required
    assert client.get(PATH).status_code in (401, 403)


# --- TEST B: all analyzed, no findings -----------------------------------
def test_b_clean_analyzed_portfolio_is_100(client):
    tok, org, _ = _org(client, "clean@x.com", "CleanOrg")
    db = TestingSession()
    try:
        for i in range(3):
            _contract(db, org, f"clean{i}.pdf")
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    assert h["score"] == 100 and h["raw_score"] == 100
    assert h["analysis_coverage"] == 100
    assert h["label"] == "Healthy"
    assert j["coverage"]["analyzed_contracts"] == 3
    assert j["coverage"]["unanalyzed_contracts"] == 0
    assert all(c["rate"] == 0.0 and c["points"] == 0.0 for c in j["components"])
    assert j["drivers"]["total_findings"] == 0


# --- TEST C: low findings only -------------------------------------------
def test_c_low_findings_only(client):
    """2 low unreviewed findings over 2 analyzed contracts:
    burden = 0.15 (unreviewed 2/2) → raw 85 → coverage 100% → 85."""
    tok, org, _ = _org(client, "low@x.com", "LowOrg")
    db = TestingSession()
    try:
        for i in range(2):
            cid, clid, rid = _contract(db, org, f"low{i}.pdf")
            _finding(db, org, cid, clid, rid, priority="low")
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    unrev = next(c for c in j["components"] if c["key"] == "unreviewed")
    assert unrev["rate"] == 1.0 and unrev["points"] == 15.0
    assert unrev["numerator"] == 2 and unrev["denominator"] == 2
    assert h["raw_score"] == 85
    assert h["score"] == 85
    assert h["label"] == "Low attention"
    assert j["drivers"]["low_findings"] == 2
    assert j["drivers"]["critical_findings"] == 0
    assert j["drivers"]["high_findings"] == 0


# --- TEST D: high findings -----------------------------------------------
def test_d_high_findings(client):
    """1 unresolved high + its review: burden 0.15+0.15 = 0.30 → 70."""
    tok, org, _ = _org(client, "high@x.com", "HighOrg")
    db = TestingSession()
    try:
        cid, clid, rid = _contract(db, org, "high.pdf")
        _finding(db, org, cid, clid, rid, priority="high")
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    high = next(c for c in j["components"] if c["key"] == "high")
    assert high["rate"] == 1.0 and high["points"] == 15.0
    assert h["raw_score"] == 70 and h["score"] == 70
    assert h["label"] == "Moderate attention"
    assert j["drivers"]["high_findings"] == 1
    assert j["drivers"]["unresolved_high_critical"] == 1


# --- TEST E: critical findings -------------------------------------------
def test_e_critical_findings(client):
    """1 unresolved critical weighs twice a high finding: 0.30+0.15 → 55."""
    tok, org, _ = _org(client, "crit@x.com", "CritOrg")
    db = TestingSession()
    try:
        cid, clid, rid = _contract(db, org, "crit.pdf")
        _finding(db, org, cid, clid, rid, priority="critical")
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    crit = next(c for c in j["components"] if c["key"] == "critical")
    assert crit["weight"] == 0.30 and crit["rate"] == 1.0
    assert crit["points"] == 30.0 and crit["numerator"] == 1
    assert h["raw_score"] == 55 and h["score"] == 55
    assert h["label"] == "High attention"
    assert j["drivers"]["critical_findings"] == 1
    # deterministic critical weight: critical costs exactly twice a high
    assert WEIGHTS["critical"] == 2 * WEIGHTS["high"]


# --- TEST F: unreviewed findings / review effect --------------------------
def test_f_reviewing_findings_only_helps(client):
    tok, org, user = _org(client, "rev@x.com", "RevOrg")
    db = TestingSession()
    try:
        cid, clid, rid = _contract(db, org, "rev.pdf")
        fid = _finding(db, org, cid, clid, rid, priority="high")
        db.commit()
    finally:
        db.close()
    before = _get(client, tok)
    assert _health(before)["score"] == 70
    assert before["drivers"]["unreviewed_findings"] == 1
    assert before["drivers"]["contracts_needing_review"] == 1

    db = TestingSession()
    try:
        db.add(ReviewDecision(organization_id=org, finding_id=fid,
                              reviewer_id=user, decision="accept"))
        db.commit()
    finally:
        db.close()
    after = _get(client, tok)
    assert _health(after)["score"] == 85  # unreviewed rate 0 → burden 0.15
    assert after["drivers"]["unreviewed_findings"] == 0
    assert after["drivers"]["contracts_needing_review"] == 0
    # the accepted high finding still counts as unresolved risk
    assert after["drivers"]["high_findings"] == 1

    # a reviewer-rejected finding contributes no risk weight at all
    db = TestingSession()
    try:
        db.add(ReviewDecision(
            organization_id=org, finding_id=fid, reviewer_id=user,
            decision="reject",
            created_at=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=1)))
        db.commit()
    finally:
        db.close()
    rejected = _get(client, tok)
    assert _health(rejected)["score"] == 100
    assert rejected["drivers"]["high_findings"] == 0


# --- TEST G: overdue obligations -----------------------------------------
def test_g_overdue_obligations(client):
    """1 overdue of 2 open obligations → overdue rate 0.5 → 100−7.5 → 93."""
    today = datetime.date.today()
    tok, org, _ = _org(client, "od@x.com", "OverdueOrg")
    db = TestingSession()
    try:
        cid, clid, _ = _contract(db, org, "od.pdf")
        _obligation(db, org, cid, clid,
                    deadline=(today - datetime.timedelta(days=5)).isoformat())
        _obligation(db, org, cid, clid,
                    deadline=(today + datetime.timedelta(days=60)).isoformat())
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    od = next(c for c in j["components"] if c["key"] == "overdue")
    assert od["numerator"] == 1 and od["denominator"] == 2
    assert od["rate"] == 0.5 and od["points"] == 7.5
    assert h["raw_score"] == 93 and h["score"] == 93
    assert j["drivers"]["overdue_obligations"] == 1
    assert j["drivers"]["open_obligations"] == 2


# --- TEST H: renewal + notice within 30 days -----------------------------
def test_h_renewal_and_notice_window(client):
    today = datetime.date.today()
    tok, org, _ = _org(client, "ren@x.com", "RenewOrg")
    db = TestingSession()
    try:
        c1, cl1, _ = _contract(db, org, "ren1.pdf")
        c2, cl2, _ = _contract(db, org, "ren2.pdf")
        _obligation(db, org, c1, cl1, type="RENEWAL", title="Renew",
                    renewal_date=(today + datetime.timedelta(days=20)).isoformat())
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    ren = next(c for c in j["components"] if c["key"] == "renewal")
    assert ren["numerator"] == 1 and ren["denominator"] == 2
    assert ren["rate"] == 0.5 and ren["points"] == 5.0
    assert h["score"] == 95
    assert j["drivers"]["renewals_next_30_days"] == 1

    db = TestingSession()
    try:
        _obligation(db, org, c2, cl2, type="NOTICE", title="Notice",
                    notice_deadline=(today + datetime.timedelta(days=10)).isoformat())
        db.commit()
    finally:
        db.close()
    j2 = _get(client, tok)
    ren2 = next(c for c in j2["components"] if c["key"] == "renewal")
    assert ren2["numerator"] == 2 and ren2["rate"] == 1.0
    assert _health(j2)["score"] == 90  # 100 − 10
    assert j2["drivers"]["notice_deadlines_next_30_days"] == 1
    # a renewal outside the window contributes nothing
    today = datetime.date.today()
    db = TestingSession()
    try:
        c3, cl3, _ = _contract(db, org, "ren3.pdf")
        _obligation(db, org, c3, cl3, type="RENEWAL", title="Renew",
                    renewal_date=(today + datetime.timedelta(days=90)).isoformat())
        db.commit()
    finally:
        db.close()
    j3 = _get(client, tok)
    assert j3["drivers"]["renewals_next_30_days"] == 1
    assert _health(j3)["score"] == int(round(100 * (1 - 0.10 * 2 / 3), 6) + 0.5)


# --- TEST I: policy deviation --------------------------------------------
def test_i_policy_deviation(client):
    """1 deviation-bearing contract of 2 (rate 0.5) + that finding unreviewed
    (rate 1.0): burden 0.075+0.15 = 0.225 → 77.5 → 78."""
    tok, org, _ = _org(client, "dev@x.com", "DevOrg")
    db = TestingSession()
    try:
        c1, cl1, r1 = _contract(db, org, "dev1.pdf")
        _contract(db, org, "dev2.pdf")
        _finding(db, org, c1, cl1, r1, priority="low",
                 ftype="policy_deviation", category="payment")
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    dev = next(c for c in j["components"] if c["key"] == "deviation")
    assert dev["numerator"] == 1 and dev["denominator"] == 2
    assert dev["rate"] == 0.5 and dev["points"] == 7.5
    assert h["raw_score"] == 78 and h["score"] == 78  # 100 − 22.5 = 77.5 → 78
    assert h["label"] == "Low attention"
    assert j["drivers"]["policy_deviations"] == 1
    assert j["drivers"]["contracts_with_policy_deviations"] == 1
    # no configured policy is reported honestly instead of implying compliance
    assert j["drivers"]["active_policies"] == 0


# --- TEST J: partial / stale / failed analysis ---------------------------
def test_j_partial_and_stale_analysis_never_reads_safe(client):
    tok, org, _ = _org(client, "part@x.com", "PartialOrg")
    db = TestingSession()
    try:
        _contract(db, org, "done.pdf", analysis="complete")
        _contract(db, org, "half.pdf", analysis="partial", run=False)
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    assert h["raw_score"] == 100       # the analyzed half is clean…
    assert h["analysis_coverage"] == 50
    assert h["score"] == 50             # …but the portfolio score is gated
    assert h["label"] != "Healthy"
    assert j["coverage"]["unanalyzed_contracts"] == 1
    assert j["coverage"]["by_status"] == {"complete": 1, "partial": 1}
    assert j["drivers"]["contracts_needing_analysis"] == 1

    tok2, org2, _ = _org(client, "stale@x.com", "StaleOrg")
    db = TestingSession()
    try:
        _contract(db, org2, "ok.pdf", analysis="complete")
        _contract(db, org2, "old.pdf", analysis="stale")
        _contract(db, org2, "bad.pdf", analysis="failed", processing="failed",
                  run=False)
        db.commit()
    finally:
        db.close()
    j2 = _get(client, tok2)
    assert _health(j2)["analysis_coverage"] == 33
    assert _health(j2)["score"] == 33
    assert j2["coverage"]["stale_contracts"] == 1
    assert j2["coverage"]["failed_contracts"] == 1
    assert j2["coverage"]["by_status"] == {
        "complete": 1, "stale": 1, "failed": 1}


# --- TEST K: adding clean contracts --------------------------------------
def test_k_clean_contracts_never_destroy_score(client):
    tok, org, _ = _org(client, "scale@x.com", "ScaleOrg")
    db = TestingSession()
    try:
        c1, cl1, r1 = _contract(db, org, "risk.pdf")
        _finding(db, org, c1, cl1, r1, priority="high")
        db.commit()
    finally:
        db.close()
    base = _health(_get(client, tok))["score"]
    assert base == 70

    # adding an ANALYSED contract with no findings can only help:
    # coverage stays 100%, high rate falls 1/1 → 1/2.
    db = TestingSession()
    try:
        _contract(db, org, "clean.pdf")
        db.commit()
    finally:
        db.close()
    better = _get(client, tok)
    assert _health(better)["score"] == 78
    assert _health(better)["score"] > base
    assert _health(better)["analysis_coverage"] == 100

    # adding a NOT-YET-ANALYSED contract lowers coverage only — it is not
    # "clean", it is unknown, and unknown must never read as healthy.
    db = TestingSession()
    try:
        _contract(db, org, "new.pdf", analysis="pending", processing="uploaded",
                  run=False)
        db.commit()
    finally:
        db.close()
    gated = _get(client, tok)
    assert _health(gated)["analysis_coverage"] == 67
    assert _health(gated)["score"] == 52
    assert _health(gated)["raw_score"] == 78   # analyzed half unchanged
    assert gated["drivers"]["contracts_needing_analysis"] == 1


# --- TEST L: range + weights ---------------------------------------------
def test_l_score_always_in_range(client):
    today = datetime.date.today()
    tok, org, _ = _org(client, "range@x.com", "RangeOrg")
    db = TestingSession()
    try:
        c1, cl1, r1 = _contract(db, org, "worst.pdf")
        for _ in range(5):
            _finding(db, org, c1, cl1, r1, priority="critical")
        _finding(db, org, c1, cl1, r1, priority="critical",
                 ftype="policy_deviation", category="liability")
        _obligation(db, org, c1, cl1,
                    deadline=(today - datetime.timedelta(days=9)).isoformat())
        _obligation(db, org, c1, cl1, type="RENEWAL", title="Renew",
                    renewal_date=(today + datetime.timedelta(days=1)).isoformat())
        _obligation(db, org, c1, cl1, type="NOTICE", title="Notice",
                    notice_deadline=(today + datetime.timedelta(days=2)).isoformat())
        _contract(db, org, "pending1.pdf", analysis="pending",
                  processing="uploaded", run=False)
        _contract(db, org, "queued.pdf", analysis="queued",
                  processing="queued", run=False)
        db.commit()
    finally:
        db.close()
    j = _get(client, tok)
    h = _health(j)
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-12
    assert all(0.0 <= c["rate"] <= 1.0 for c in j["components"])
    assert all(c["weight"] == WEIGHTS[c["key"]] for c in j["components"])
    assert 0 <= h["score"] <= 100
    assert 0 <= h["raw_score"] <= 100
    assert 0 <= h["analysis_coverage"] <= 100
    assert h["score"] <= h["raw_score"]          # coverage only ever subtracts
    # worst case, reproduced by hand from the same fixture:
    #   critical   6/1 = 1.0000 ×0.30 = 0.3000000000
    #   high       0/1 = 0.0000 ×0.15 = 0
    #   unreviewed 6/6 = 1.0000 ×0.15 = 0.1500000000
    #   overdue    1/3 = 0.3333 ×0.15 = 0.0500000000
    #   renewal    2/3 = 0.6667 ×0.10 = 0.0666666667
    #   deviation  1/1 = 1.0000 ×0.15 = 0.1500000000
    #   burden = 0.7166666667 → raw = 100 − 71.66666667 = 28.333333
    #   coverage = 1/3 = 33%   → score = round_half_up(9.444444) = 9
    assert h["raw_score"] == 28
    assert h["score"] == 9
    assert h["analysis_coverage"] == 33


# --- TEST M: determinism + hand reproduction -----------------------------
def test_m_deterministic_and_reproducible(client):
    tok, org, user = _org(client, "det@x.com", "DetOrg")
    today = datetime.date.today()
    db = TestingSession()
    try:
        c1, cl1, r1 = _contract(db, org, "det1.pdf")
        _contract(db, org, "det2.pdf")
        _finding(db, org, c1, cl1, r1, priority="high")
        _finding(db, org, c1, cl1, r1, priority="medium")
        _obligation(db, org, c1, cl1,
                    deadline=(today - datetime.timedelta(days=1)).isoformat())
        db.commit()
    finally:
        db.close()

    first = _get(client, tok)
    second = _get(client, tok)
    assert first == second                                   # deterministic
    assert first["computed_on"] == today.isoformat()

    # reproduce the integer from the payload alone, like a human would:
    #   raw      = 100 − Σ component points          (here 100 − 37.5 = 62.5)
    #   coverage = analyzed_contracts / total_contracts
    #   score    = round_half_up(raw × coverage)
    raw = round(100 - sum(c["points"] for c in first["components"]), 6)
    assert raw == 62.5                     # 100 − (7.5 + 15 + 15)
    assert abs(raw - _health(first)["raw_score"]) <= 0.5  # raw_score is whole
    cov = (first["coverage"]["analyzed_contracts"]
           / first["coverage"]["total_contracts"])
    expected = int(round(raw * cov, 6) + 0.5)
    assert expected == _health(first)["score"] == 63
    assert first["coverage"]["analysis_coverage"] == 100

    # and once more straight from the service with an injected date
    from app.modules.portfolio.health import portfolio_health

    db = TestingSession()
    try:
        a = portfolio_health(db, org, today=today.isoformat())
        b = portfolio_health(db, org, today=today.isoformat())
    finally:
        db.close()
    assert a == b
    assert a["portfolio_health"]["score"] == _health(first)["score"]
    assert a["drivers"] == first["drivers"]
    assert "not legal advice" in a["disclaimer"]
    assert a["portfolio_health"]["orientation"] == "higher_is_healthier"


# --- extra: org isolation -------------------------------------------------
def test_isolation_between_orgs(client):
    tok_a, org_a, _ = _org(client, "iso-a@x.com", "IsoA")
    tok_b, _, _ = _org(client, "iso-b@x.com", "IsoB")
    db = TestingSession()
    try:
        cid, clid, rid = _contract(db, org_a, "iso.pdf")
        _finding(db, org_a, cid, clid, rid, priority="critical")
        db.commit()
    finally:
        db.close()
    a = _get(client, tok_a)
    assert _health(a)["score"] == 55

    b = _get(client, tok_b)
    assert _health(b)["score"] is None      # B sees only its own (empty) org
    assert b["drivers"]["critical_findings"] == 0
