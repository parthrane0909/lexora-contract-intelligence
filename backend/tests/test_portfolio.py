"""Portfolio tests (§31): deterministic fixture, reconciliation, scoring math,
patterns, isolation. Source rows inserted directly for exact control; one
end-to-end path runs the real obligation extractor.
"""
import datetime
import uuid

from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import (
    AnalysisRun,
    Clause,
    Comparison,
    ComparisonFinding,
    Contract,
    FeedbackLedger,
    LifecycleEvent,
    Notification,
    NotificationDelivery,
    Obligation,
    ObligationEventInstance,
    ObligationReview,
    ReviewDecision,
    RiskFinding,
)


def _upload(client, token, name):
    from app.modules.document.pipeline import process_contract

    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": (name, make_pdf(
                        [["1. Payment Terms", "Pay within 30 days."],
                         ["8.2 Liability", "Liability is capped."]]), "application/pdf")})
    assert r.status_code == 200, r.text
    cid = uuid.UUID(r.json()["id"])
    db = TestingSession()
    try:
        assert process_contract(db, cid)["status"] == "processed"
        clauses = db.query(Clause).filter(Clause.contract_id == cid).all()
        assert len(clauses) == 2
        return cid, [c.id for c in clauses]
    finally:
        db.close()


def _fixture(client):
    """Org A: contract A (2 high: 1 unreviewed + 1 accepted, 1 overdue ob,
    1 renewal<30d, 1 deviation) + contract B (1 critical rejected).
    Org B: 1 contract, 1 finding (isolation). Returns dict of ids."""
    ta = register(client, "a@x.com", "password123", "OrgA")
    tb = register(client, "b@x.com", "password123", "OrgB")
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        ua = db.query(User).filter(User.email == "a@x.com").one()
        ub = db.query(User).filter(User.email == "b@x.com").one()
        orga = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == ua.id).one().organization_id
        orgb = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == ub.id).one().organization_id
    finally:
        db.close()
    ca, clauses_a = _upload(client, ta, "a1.pdf")
    cb, clauses_b = _upload(client, ta, "b1.pdf")
    cc, clauses_c = _upload(client, tb, "c1.pdf")

    today = datetime.date.today()
    past = (today - datetime.timedelta(days=5)).isoformat()
    soon20 = (today + datetime.timedelta(days=20)).isoformat()
    soon60 = (today + datetime.timedelta(days=60)).isoformat()
    db = TestingSession()
    try:
        run_a = AnalysisRun(organization_id=orga, contract_id=ca,
                            kind="risk_analysis", status="done")
        run_b = AnalysisRun(organization_id=orga, contract_id=cb,
                            kind="risk_analysis", status="done")
        run_c = AnalysisRun(organization_id=orgb, contract_id=cc,
                            kind="risk_analysis", status="done")
        db.add_all([run_a, run_b, run_c])
        db.flush()

        def finding(org, cid, clauses, run, priority, category, ftype, rule="R-1"):
            f = RiskFinding(
                organization_id=org, contract_id=cid, clause_id=clauses[0],
                analysis_run_id=run.id, risk_category=category, finding_type=ftype,
                priority=priority, title=f"{rule} {category}", reason="r",
                rule_id=rule, rule_version="1.0")
            db.add(f)
            db.flush()
            return f

        fa1 = finding(orga, ca, clauses_a, run_a, "high", "payment", "policy_deviation")
        fa2 = finding(orga, ca, clauses_a, run_a, "high", "liability", "rule", rule="R-2")
        fb1 = finding(orga, cb, clauses_b, run_b, "critical", "liability", "rule")
        fc1 = finding(orgb, cc, clauses_c, run_c, "high", "payment", "rule")

        def ob(org, cid, clauses, **kw):
            defaults = dict(organization_id=org, contract_id=cid, clause_id=clauses[0],
                            type="PAYMENT", title="Pay", status="CONFIRMED",
                            confidence=0.9, extraction_method="deterministic_rule",
                            model_version="1.0/1.0", source_text="Pay within 30 days.")
            defaults.update(kw)
            o = Obligation(**defaults)
            db.add(o)
            db.flush()
            return o

        ob_over = ob(orga, ca, clauses_a, deadline=past)
        ob_ren = ob(orga, ca, clauses_a, type="RENEWAL", title="Renew",
                    renewal_date=soon20)
        ob_ok = ob(orga, cb, clauses_b, deadline=soon60)
        # reviews: fa2 accepted, fb1 rejected
        ra = ReviewDecision(organization_id=orga, finding_id=fa2.id,
                            reviewer_id=ua.id, decision="accept")
        rb = ReviewDecision(organization_id=orga, finding_id=fb1.id,
                            reviewer_id=ua.id, decision="reject")
        db.add_all([ra, rb])
        db.flush()
        db.add(FeedbackLedger(
            organization_id=orga, finding_id=fa2.id, review_id=ra.id,
            reviewer_id=ua.id, original_category="liability",
            original_priority="high", original_rule="R-2", decision="accept"))
        db.add(FeedbackLedger(
            organization_id=orga, finding_id=fb1.id, review_id=rb.id,
            reviewer_id=ua.id, original_category="liability",
            original_priority="critical", original_rule="R-1",
            decision="reject", correction_type="FALSE_POSITIVE"))
        # lifecycle event + notification + instance + comparison on contract A
        ev = LifecycleEvent(
            organization_id=orga, contract_id=ca, obligation_id=ob_over.id,
            event_type="PAYMENT_DEADLINE", event_date=past, status="OVERDUE",
            source_clause_id=clauses_a[0])
        db.add(ev)
        db.flush()
        n = Notification(
            organization_id=orga, contract_id=ca, obligation_id=ob_over.id,
            lifecycle_event_id=ev.id, recipient_user_id=ua.id,
            notification_type="OVERDUE", title="Overdue", status="SENT",
            idempotency_key=f"seed-{uuid.uuid4()}")
        db.add(n)
        db.flush()
        db.add(NotificationDelivery(
            organization_id=orga, notification_id=n.id, recipient_user_id=ua.id,
            provider="inapp", channel="inapp", status="SENT", attempt_count=1))
        from app.models import Comparison as Cmp
        from app.models import Template

        tpl = Template(organization_id=orga, name="Std", version=1, is_active=True,
                       status="ready", storage_key="k", filename="t.pdf",
                       created_by=ua.id)
        db.add(tpl)
        db.flush()
        cmp = Cmp(organization_id=orga, contract_id=ca, template_id=tpl.id,
                  engine_version="1.0", status="done")
        db.add(cmp)
        db.flush()
        db.add(ComparisonFinding(
            organization_id=orga, comparison_id=cmp.id, contract_id=ca,
            template_id=tpl.id, category="liability", difference_type="modified",
            materiality="material", title="Changed cap"))
        db.commit()
        return {
            "ta": ta, "tb": tb, "orga": orga, "orgb": orgb, "ua": ua.id,
            "ca": ca, "cb": cb, "cc": cc,
            "fa1": fa1.id, "fa2": fa2.id, "fb1": fb1.id, "fc1": fc1.id,
            "run_a": run_a.id,
        }
    finally:
        db.close()


def _h(token):
    return auth(token)


def test_summary_reconciles(client):
    fx = _fixture(client)
    s = client.get("/api/v1/portfolio/summary", headers=_h(fx["ta"])).json()
    assert s["total_contracts"] == 2
    assert s["open_obligations"] == 3
    assert s["overdue_obligations"] == 1
    assert s["upcoming_renewals_30d"] == 1
    assert s["contracts_requiring_review"] == 1  # only A has unreviewed findings
    assert s["findings_by_review_state"] == {"unreviewed": 1, "accepted": 1, "rejected": 1}
    assert s["unresolved_high_critical"] == 2  # fa1, fa2 (rejected fb1 excluded)
    assert s["scoring_version"] == "1.0"
    # reconciliation: attention queue sums match source queries
    q = client.get("/api/v1/portfolio/attention", headers=_h(fx["ta"])).json()
    assert sum(r["critical_risk_count"] for r in q) == 0  # rejected excluded everywhere
    assert sum(r["high_risk_count"] for r in q) == 2
    assert sum(r["unreviewed_count"] for r in q) == 1
    assert sum(r["overdue_obligation_count"] for r in q) == 1
    assert sum(r["policy_deviation_count"] for r in q) == 1


def test_attention_score_exact_math(client):
    """critical 10 + high 6 + overdue 5 + renewal<30 4 = 25 (both reviewed)."""
    fx = _fixture(client)
    h = _h(fx["ta"])
    db = TestingSession()
    try:
        # review the remaining unreviewed finding so only base weights count
        rev = ReviewDecision(organization_id=fx["orga"], finding_id=fx["fa1"],
                             reviewer_id=fx["ua"], decision="accept")
        db.add(rev)
        db.commit()
    finally:
        db.close()
    q = {r["contract_id"]: r for r in
         client.get("/api/v1/portfolio/attention", headers=h).json()}
    a = q[str(fx["ca"])]
    assert a["attention_score"] == 24, a["score_breakdown"]
    assert a["score_breakdown"] == {"high": 12, "overdue_obligation": 5,
                                    "renewal_30d": 4, "policy_deviation": 3}
    assert a["scoring_version"] == "1.0"
    b = q[str(fx["cb"])]
    assert b["attention_score"] == 0  # sole finding rejected → no weight
    assert [r["contract_id"] for r in
            client.get("/api/v1/portfolio/attention", headers=h).json()][0] == str(fx["ca"])


def test_risk_aggregation_and_review_awareness(client):
    fx = _fixture(client)
    r = client.get("/api/v1/portfolio/risks", headers=_h(fx["ta"])).json()
    assert r["by_priority"] == {"high": 2, "critical": 1}
    assert r["by_category"] == {"payment": 1, "liability": 2}
    assert r["total"] == 3
    assert r["by_review_state"] == {"unreviewed": 1, "accepted": 1, "rejected": 1}
    assert r["by_contract"][str(fx["ca"])] == 2


def test_obligation_renewal_deviation_aggregations(client):
    fx = _fixture(client)
    h = _h(fx["ta"])
    o = client.get("/api/v1/portfolio/obligations", headers=h).json()
    assert o["total"] == 3 and o["by_status"]["CONFIRMED"] == 3
    assert o["by_type"] == {"PAYMENT": 2, "RENEWAL": 1}
    assert len(o["overdue_ids"]) == 1
    assert client.get("/api/v1/portfolio/obligations?status=CONFIRMED",
                      headers=h).json()["total"] == 3
    assert client.get("/api/v1/portfolio/obligations?type=NOPE", headers=h).json()["total"] == 0
    r30 = client.get("/api/v1/portfolio/renewals?window_days=30", headers=h).json()
    assert len(r30["renewals"]) == 1 and r30["renewals"][0]["auto_renew"] in (True, False, None)
    assert client.get("/api/v1/portfolio/renewals?window_days=60", headers=h).json()["window_days"] == 60
    assert client.get("/api/v1/portfolio/renewals?window_days=7", headers=h).status_code == 400
    d = client.get("/api/v1/portfolio/policy-deviations", headers=h).json()
    assert d["total"] == 1 and d["by_contract"][str(fx["ca"])] == 1
    assert d["by_category"] == {"payment": 1}
    c = client.get("/api/v1/portfolio/comparison", headers=h)
    assert c.status_code == 404  # singular path does not exist
    comp = client.get("/api/v1/portfolio/comparisons", headers=h).json()
    assert comp["materiality_totals"] == {"material": 1}
    assert comp["by_contract"][0]["material"] == 1


def test_counterparties_honest(client):
    fx = _fixture(client)
    h = _h(fx["ta"])
    cp = client.get("/api/v1/portfolio/counterparties", headers=h).json()
    assert cp["counterparty_data_available"] is False
    assert cp["counterparties"][0]["counterparty"] == "Unspecified"
    assert cp["counterparties"][0]["contract_count"] == 2
    assert client.patch(f"/api/v1/contracts/{fx['ca']}",
                        headers=h, json={"counterparty": "Acme Corp"}).status_code == 200
    cp2 = client.get("/api/v1/portfolio/counterparties", headers=h).json()
    assert cp2["counterparty_data_available"] is True
    acme = next(c for c in cp2["counterparties"] if c["counterparty"] == "Acme Corp")
    assert acme["contract_count"] == 1 and acme["risk_count"] == 2
    assert acme["high_risk_count"] == 2 and acme["overdue_obligation_count"] == 1
    assert acme["upcoming_renewal_count"] == 1
    att = client.get("/api/v1/portfolio/attention?counterparty=Acme Corp", headers=h).json()
    assert [r["contract_id"] for r in att] == [str(fx["ca"])]
    # viewer cannot PATCH metadata
    from app.core.security import create_access_token

    from app.models import OrganizationMember, User

    db = TestingSession()
    try:
        u = User(email="v@x.com", password_hash="x")
        db.add(u)
        db.flush()
        orga = fx["orga"]
        db.add(OrganizationMember(organization_id=orga, user_id=u.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(u.id), str(orga), "viewer")
    finally:
        db.close()
    assert client.patch(f"/api/v1/contracts/{fx['ca']}", headers=auth(vtok),
                        json={"counterparty": "X"}).status_code == 403
    assert client.patch(f"/api/v1/contracts/{fx['ca']}", headers=h,
                        json={"counterparty": "x" * 300}).status_code == 400


def test_feedback_and_notification_analytics(client):
    fx = _fixture(client)
    h = _h(fx["ta"])
    # feedback row for fa2
    db = TestingSession()
    try:
        from app.models import FeedbackLedger as FL

        rev = db.query(ReviewDecision).filter(
            ReviewDecision.finding_id == fx["fa2"]).one()
        db.add(FL(organization_id=fx["orga"], finding_id=fx["fa2"],
                  review_id=rev.id, reviewer_id=fx["ua"],
                  original_category="liability", original_priority="high",
                  decision="modify", correction_type="WRONG_PRIORITY",
                  corrected_priority="medium"))
        db.commit()
    finally:
        db.close()
    fb = client.get("/api/v1/portfolio/review-feedback", headers=h).json()
    assert fb["total"] == 3  # 2 seeded + 1 added here
    assert fb["wrong_priority"] == 1
    assert fb["by_correction_type"] == {"FALSE_POSITIVE": 1, "WRONG_PRIORITY": 1}
    assert fb["reviewer_rejected"] == 1  # explicit FALSE_POSITIVE row
    assert fb["by_decision"] == {"accept": 1, "reject": 1, "modify": 1}
    n = client.get("/api/v1/portfolio/notifications", headers=h).json()
    assert n["overdue_events"] == 1  # seeded PAYMENT_DEADLINE 5 days past
    assert n["notifications_by_status"] == {"SENT": 1}
    assert n["failed_deliveries"] == 0


def test_trends_buckets_and_versions(client):
    fx = _fixture(client)
    h = _h(fx["ta"])
    t = client.get("/api/v1/portfolio/trends?granularity=day", headers=h).json()
    assert t["granularity"] == "day"
    assert sum(p["count"] for p in t["risk_findings"]) == 3
    assert sum(p["count"] for p in t["reviews"]) == 2
    assert sum(p["count"] for p in t["policy_deviations"]) == 1
    assert client.get("/api/v1/portfolio/trends?granularity=hour", headers=h).status_code == 400
    w = client.get("/api/v1/portfolio/trends?granularity=week", headers=h).json()
    assert sum(p["count"] for p in w["risk_findings"]) == 3


def test_filters_and_drilldown(client):
    fx = _fixture(client)
    h = _h(fx["ta"])
    # priority filter is review-aware (consistent with displayed counts):
    # B's only critical was rejected, so no contract matches 'critical'
    assert client.get(
        "/api/v1/portfolio/attention?priority=critical", headers=h).json() == []
    assert [r["contract_id"] for r in client.get(
        "/api/v1/portfolio/attention?priority=high", headers=h).json()] == [str(fx["ca"])]
    assert client.get("/api/v1/portfolio/attention?priority=low", headers=h).status_code == 400
    assert client.get(
        "/api/v1/portfolio/attention?category=payment", headers=h).json()[0]["contract_id"] == str(fx["ca"])
    assert client.get(
        "/api/v1/portfolio/attention?review_state=accepted", headers=h).json()[0]["contract_id"] == str(fx["ca"])
    assert client.get("/api/v1/portfolio/attention?min_score=100", headers=h).json() == []
    assert client.get("/api/v1/portfolio/attention?has_overdue=true",
                      headers=h).json()[0]["contract_id"] == str(fx["ca"])
    d = client.get(f"/api/v1/portfolio/contracts/{fx['ca']}", headers=h).json()
    assert d["attention"]["contract_id"] == str(fx["ca"])
    assert d["obligations"]["total"] == 2
    assert d["renewals"]["window_days"] == 180
    assert client.get(f"/api/v1/portfolio/contracts/{uuid.uuid4()}", headers=h).status_code == 404


def test_isolation_and_empty(client):
    fx = _fixture(client)
    hb = _h(fx["tb"])
    assert client.get("/api/v1/portfolio/summary", headers=hb).json()["total_contracts"] == 1
    assert client.get("/api/v1/portfolio/attention", headers=hb).json()[0]["attention_score"] >= 6
    assert client.get(f"/api/v1/portfolio/contracts/{fx['ca']}", headers=hb).status_code == 404
    cpb = client.get("/api/v1/portfolio/counterparties", headers=hb).json()
    assert len(cpb["counterparties"]) == 1  # org B has one contract (Unspecified)
    assert cpb["counterparties"][0]["contract_count"] == 1
    assert client.patch(f"/api/v1/contracts/{fx['ca']}", headers=hb,
                        json={"counterparty": "X"}).status_code in (403, 404)
    # empty org
    tn = register(client, "n@x.com", "password123", "OrgN")
    hn = _h(tn)
    s = client.get("/api/v1/portfolio/summary", headers=hn).json()
    assert s["total_contracts"] == 0 and s["unresolved_high_critical"] == 0
    assert client.get("/api/v1/portfolio/attention", headers=hn).json() == []
    assert client.get("/api/v1/portfolio/trends", headers=hn).json()["risk_findings"] == []


def test_no_duplicate_counts_on_rerun(client):
    """Second risk run on A: portfolio counts each finding once (latest run
    wins — old run's 2 findings excluded, new run's 1 included)."""
    fx = _fixture(client)
    h = _h(fx["ta"])
    db = TestingSession()
    try:
        run2 = AnalysisRun(organization_id=fx["orga"], contract_id=fx["ca"],
                           kind="risk_analysis", status="done")
        db.add(run2)
        db.flush()
        clauses = db.query(Clause).filter(Clause.contract_id == fx["ca"]).all()
        db.add(RiskFinding(
            organization_id=fx["orga"], contract_id=fx["ca"], clause_id=clauses[0].id,
            analysis_run_id=run2.id, risk_category="payment", finding_type="rule",
            priority="low", title="dup", reason="r", rule_id="R-9", rule_version="1.0"))
        db.commit()
    finally:
        db.close()
    r = client.get("/api/v1/portfolio/risks", headers=h).json()
    assert r["total"] == 2  # 1 new low on A + 1 critical on B; old A run excluded
    assert r["by_priority"] == {"low": 1, "critical": 1}
