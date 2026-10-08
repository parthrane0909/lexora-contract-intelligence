"""Part 1 — risk-analysis functional correctness.

Covers, with controlled inputs (classifications are inserted directly so rule
behavior is deterministic; the real model path has its own tests):

  * an explicit processing / classification / risk-analysis lifecycle that
    never reports "complete" for work that did not run
  * five mutually exclusive clause states, above all the difference between
    NO DETECTED RISK and NEVER ANALYZED
  * persisted evidence for every finding (facts, rule, location, clause text)
  * a structured deterministic explanation (what / why / evidence / attention)
  * contract-level aggregation (Phase 8 shape) and the risk-assessment API
  * organization isolation
  * visible failure states for classification and risk analysis
  * re-running analysis: history preserved, no duplicate current findings
  * re-processing invalidates earlier results instead of silently keeping them
"""
import uuid

import pytest
from conftest import TestingSession, auth, register
from doc_builders import make_pdf

from app.models import Clause, ClauseClassification

# One page → one clause (deterministic segmenter), heading on the first line.
PAGES = [
    ["3. Limitation of Liability", "The Supplier's liability is unlimited and uncapped under this Agreement."],
    ["4. Payment Terms", "Client shall pay all fees within 90 days of invoice."],
    ["5. Governing Law", "This Agreement shall be governed by the laws of Delaware."],
    ["6. Confidentiality", "The parties shall keep all information confidential."],
    ["7. Miscellaneous", "This Agreement constitutes the entire agreement between the parties."],
]

# clause index → (category, confidence). All ≥ 0.5 so CONFIDENCE-001 never
# fires and every state below is caused by a rule, not by model uncertainty.
CATS = {
    0: ("liability", 0.96),
    1: ("payment", 0.91),
    2: ("governing_law", 0.99),
    3: ("confidentiality", 0.93),
    4: ("other", 0.88),
}
N = len(PAGES)


def _process(client, email):
    """Register → upload → run the document pipeline (no analysis yet)."""
    from app.modules.document.pipeline import process_contract

    token = register(client, email, "password123", f"Org-{email}")
    r = client.post("/api/v1/contracts/upload", headers=auth(token),
                    files={"file": ("c.pdf", make_pdf(PAGES), "application/pdf")})
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        assert len(clauses) == N, [c.title for c in clauses]
        ids = [str(c.id) for c in clauses]
    finally:
        db.close()
    return token, cid, ids


def _classify(cid, cats=None):
    """Persist predictions the way the classifier does: one completed
    analysis run, one row per clause, then the status rollup is refreshed.
    Categories are chosen directly so every rule outcome is deterministic."""
    import json

    from app.models import AnalysisRun, Contract
    from app.modules.risk_engine.state import refresh_analysis_status

    cats = CATS if cats is None else cats
    db = TestingSession()
    try:
        contract_id = uuid.UUID(cid)
        contract = db.get(Contract, contract_id)
        clauses = db.query(Clause).filter(Clause.contract_id == contract_id)\
            .order_by(Clause.order_index).all()
        run = AnalysisRun(organization_id=contract.organization_id,
                          contract_id=contract.id,
                          kind="clause_classification", model_name="tiny-test",
                          model_version="tiny-v0", status="done",
                          stats=json.dumps({"clauses": len(clauses),
                                            "classified": len(cats),
                                            "skipped": len(clauses) - len(cats)}))
        db.add(run)
        db.commit()
        db.refresh(run)
        for idx, (cat, conf) in cats.items():
            cl = clauses[idx]
            db.add(ClauseClassification(
                organization_id=cl.organization_id, contract_id=cl.contract_id,
                clause_id=cl.id, analysis_run_id=run.id, model_name="tiny-test",
                model_version="tiny-v0", predicted_category=cat, confidence=conf,
                probabilities=None))
        db.commit()
        refresh_analysis_status(db, contract)
        return [str(c.id) for c in clauses]
    finally:
        db.close()


def _set_policy(client, token, category, params):
    r = client.post("/api/v1/policies", headers=auth(token),
                    json={"category": category, "params": params})
    assert r.status_code == 200, r.text
    return r.json()


def _detail(client, token, cid):
    r = client.get(f"/api/v1/contracts/{cid}", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def _assessment(client, token, cid):
    r = client.get(f"/api/v1/contracts/{cid}/risk-assessment", headers=auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def _by_state(assessment):
    out: dict[str, list[dict]] = {}
    for c in assessment["clauses"]:
        out.setdefault(c["risk_state"], []).append(c)
    return out


# ------------------------------------------------------------- lifecycle ---


def test_processing_alone_is_never_reported_as_analysis(client):
    """Phase 2 + 13: parsing finished ≠ classification ran ≠ risk analyzed."""
    token, cid, _ = _process(client, "fresh@x.com")
    h = auth(token)

    life = _detail(client, token, cid)["analysis_lifecycle"]
    assert life["processing"]["state"] == "complete"
    assert life["classification"]["state"] == "not_run"
    assert life["risk_analysis"]["state"] == "not_run"
    assert life["overall"] == "not_started"

    # The stored rollup must not claim success either.
    assert _detail(client, token, cid)["analysis_status"] == "pending"

    # Part 4: /summary is descriptive only — lifecycle, counts and "never
    # analyzed" state are owned by /risk-assessment (asserted below) and
    # must never leak back into the summary payload.
    summary = client.get(f"/api/v1/contracts/{cid}/summary", headers=h).json()
    assert summary["contract_id"] == cid and summary["overview"]
    for key in ("analysis", "risk_summary", "risks", "analysis_status"):
        assert key not in summary, key

    # "0 findings" before any run must be visible as "not run", not as safe.
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []
    ra = _assessment(client, token, cid)
    assert ra["analysis"]["overall"] == "not_started"
    assert ra["runs"] == {"classification": None, "risk_analysis": None}
    assert all(c["risk_state"] == "needs_review" for c in ra["clauses"])
    assert {c["state_reason"] for c in ra["clauses"]} == {"unclassified"}
    assert ra["summary"]["no_detected_risk"] == 0
    assert ra["summary"]["needs_review"] == N


def test_classification_is_explicit_and_persistent(client):
    """Phase 2/3: classification counts as done only when every clause has a
    persisted prediction; risk analysis still reports 'not run'."""
    token, cid, _ = _process(client, "cls@x.com")
    h = auth(token)
    _classify(cid)

    d = _detail(client, token, cid)
    life = d["analysis_lifecycle"]
    assert life["classification"]["state"] == "complete"
    assert life["classification"]["classified"] == N
    assert life["risk_analysis"]["state"] == "not_run"
    assert life["overall"] == "classified_only"
    assert d["analysis_status"] == "classified"

    # Classified ≠ analyzed: every clause still needs review, none is "clean".
    ra = _assessment(client, token, cid)
    assert all(c["risk_state"] == "needs_review" for c in ra["clauses"])
    assert {c["state_reason"] for c in ra["clauses"]} == {"risk_analysis_not_run"}
    assert ra["summary"]["classified_clauses"] == N
    assert ra["summary"]["unclassified_clauses"] == 0
    assert ra["summary"]["no_detected_risk"] == 0
    assert ra["summary"]["analyzed_clauses"] == 0
    for c in ra["clauses"]:
        assert c["classification"] and c["classification"]["category"]


# ------------------------------------------------------------ clause states


def test_clause_states_and_contract_summary(client):
    """Phase 3/6/7/8: five states, engine priorities unchanged, Phase 8 shape."""
    token, cid, ids = _process(client, "states@x.com")
    h = auth(token)
    _classify(cid)
    _set_policy(client, token, "payment", {"max_days": 30})  # 90 > 30 → medium

    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["status"] == "done" and run["findings"] == 3, run

    ra = _assessment(client, token, cid)
    assert ra["analysis"]["overall"] == "complete"
    assert _detail(client, token, cid)["analysis_status"] == "complete"

    by_state = _by_state(ra)
    assert set(by_state) == {"high_risk", "medium_risk", "low_risk",
                             "no_detected_risk"}
    assert by_state["high_risk"][0]["top_priority"] == "critical"
    assert by_state["high_risk"][0]["page_start"] is not None
    assert by_state["medium_risk"][0]["top_priority"] == "medium"
    assert by_state["low_risk"][0]["top_priority"] == "low"
    assert by_state["no_detected_risk"][0]["top_priority"] is None
    assert by_state["no_detected_risk"][0]["state_reason"] == "no_rule_matched"
    assert by_state["no_detected_risk"][0]["finding_ids"] == []

    s = ra["summary"]
    assert s["total_clauses"] == N
    assert s["classified_clauses"] == N
    assert s["unclassified_clauses"] == 0
    assert s["analyzed_clauses"] == N
    assert (s["high_risk"], s["medium_risk"], s["low_risk"],
            s["no_detected_risk"], s["needs_review"]) == (1, 1, 1, 2, 0)
    assert s["needs_attention"] == 2  # high + medium (+ any needs_review)
    assert s["findings"] == 3
    assert s["findings_by_priority"] == {"critical": 1, "medium": 1, "low": 1}
    assert s["risk_categories"] == ["confidentiality", "liability", "payment"]
    assert s["risk_analysis_state"] == "complete"
    for key in ("contract_id", "total_clauses", "classified_clauses",
                "unclassified_clauses", "high_risk", "medium_risk", "low_risk",
                "no_detected_risk", "needs_review", "needs_attention"):
        assert key in s, key

    # Phase 6: safe clauses are reported explicitly, never invented as findings.
    assert len(ra["findings"]) == 3
    assert {f["rule_id"] for f in ra["findings"]} == {"LIAB-002", "PAY-001", "CONF-001"}


def test_unclassified_clause_is_never_no_detected_risk(client):
    """Phase 3: partial classification → that clause needs review, the
    analyzed ones keep their real states."""
    token, cid, _ = _process(client, "partial@x.com")
    h = auth(token)
    _classify(cid, cats={k: v for k, v in CATS.items() if k != 4})  # 4 unclassified
    _set_policy(client, token, "payment", {"max_days": 30})

    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["unclassified"] == 1

    life = _detail(client, token, cid)["analysis_lifecycle"]
    assert life["classification"]["state"] == "partial"
    assert life["risk_analysis"]["state"] == "partial"
    assert life["overall"] == "partial"
    assert _detail(client, token, cid)["analysis_status"] == "partial"

    ra = _assessment(client, token, cid)
    by_state = _by_state(ra)
    assert len(by_state["needs_review"]) == 1
    assert by_state["needs_review"][0]["state_reason"] == "unclassified"
    assert by_state["needs_review"][0]["classification"] is None
    # The other clauses are still assessed honestly.
    assert len(by_state.get("high_risk", [])) == 1
    assert len(by_state.get("no_detected_risk", [])) == 1
    s = ra["summary"]
    assert s["unclassified_clauses"] == 1 and s["needs_review"] == 1
    assert s["needs_attention"] == 1 + 1 + 1  # high + medium + needs_review


# --------------------------------------------------------- finding evidence


def test_every_finding_explains_where_it_came_from(client):
    """Phase 4/5: location, rule, facts, original clause text, attention,
    structured what/why/evidence/attention — all from the rule engine."""
    token, cid, ids = _process(client, "explain@x.com")
    h = auth(token)
    _classify(cid)
    _set_policy(client, token, "payment", {"max_days": 30})
    client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h)

    findings = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert len(findings) == 3
    by_rule = {f["rule_id"]: f for f in findings}

    f = by_rule["LIAB-002"]
    assert f["clause_id"] == ids[0]
    assert f["clause_title"] and "Liability" in f["clause_title"]
    assert "unlimited" in f["clause_text"].lower()  # original clause, verbatim
    assert f["page_start"] is not None and f["page_end"] is not None
    assert f["section_number"] is not None  # located in the document
    assert f["extracted_facts"]["unlimited"] is True
    assert f["priority"] == "critical" and f["requires_attention"] is True
    assert f["attention_reason"] == "priority_critical"
    assert f["classification_model"] == "tiny-test"
    assert f["classification_confidence"] == 0.96

    ex = f["explanation"]
    assert ex["source"].startswith("deterministic") and "no LLM" in ex["source"]
    assert "LIAB-002" in ex["what"] and f["title"] in ex["what"]
    assert ex["why"] == f["reason"]
    assert ex["evidence"]["facts"]["unlimited"] is True
    assert ex["evidence"]["clause_text"] == f["evidence"]
    assert ex["detected_condition"]["risk_category"] == "liability"
    assert ex["detected_condition"]["priority"] == "critical"
    assert ex["attention"] and ex["attention"].startswith("High priority")
    assert ex["priority_reasons"]  # why this priority, transparently
    assert ex["explanation_version"]

    low = by_rule["CONF-001"]
    assert low["priority"] == "low" and low["requires_attention"] is False
    assert low["attention_reason"] == "priority_low"
    assert low["explanation"]["detected_condition"]["finding_type"] == "missing_term"

    # Never claims illegality; wording stays advisory.
    for f2 in findings:
        blob = (f2["reason"] + f2["explanation"]["what"]
                + f2["explanation"]["attention"]).lower()
        assert "illegal" not in blob and "law violation" not in blob

    # Clause-level endpoint returns the same rows for the same clause.
    same = client.get(f"/api/v1/contracts/{cid}/clauses/{ids[0]}/findings",
                      headers=h).json()
    assert [x["id"] for x in same] == [f2["id"] for f2 in findings
                                       if f2["clause_id"] == ids[0]]
    assert same and same[0]["clause_text"] == f["clause_text"]


def test_risk_assessment_api_is_deterministic_and_org_scoped(client):
    token, cid, _ = _process(client, "iso-a@x.com")
    h = auth(token)
    _classify(cid)
    client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h)

    first, second = _assessment(client, token, cid), _assessment(client, token, cid)
    assert first == second

    other = register(client, "iso-b@x.com", "password123", "Org-B")
    for path in (f"/api/v1/contracts/{cid}/risk-assessment",
                 f"/api/v1/contracts/{cid}/findings",
                 f"/api/v1/contracts/{cid}/summary"):
        assert client.get(path, headers=auth(other)).status_code == 404, path
        assert client.get(path).status_code in (401, 403), path
    # A finding id from org A is not reachable from org B either.
    fid = first["findings"][0]["id"] if first["findings"] else None
    if fid:
        assert client.get(f"/api/v1/contracts/{cid}/findings/{fid}",
                          headers=auth(other)).status_code == 404


# ------------------------------------------------------------ re-analysis ---


def test_reanalysis_preserves_history_without_duplicate_current_findings(client):
    token, cid, _ = _process(client, "rerun@x.com")
    h = auth(token)
    _classify(cid)
    _set_policy(client, token, "payment", {"max_days": 30})

    first = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    second = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert first["run_id"] != second["run_id"]

    current = client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json()
    assert len(current) == first["findings"] == 3  # no duplicates

    db = TestingSession()
    try:
        from app.models import RiskFinding

        total = db.query(RiskFinding).filter(
            RiskFinding.contract_id == uuid.UUID(cid)).count()
    finally:
        db.close()
    assert total == 6  # history is append-only…

    history = client.get(f"/api/v1/contracts/{cid}/findings?run_id="
                         f"{first['run_id']}", headers=h).json()
    assert len(history) == 3  # …and explicitly reachable

    ra = _assessment(client, token, cid)
    assert ra["summary"]["findings"] == 3  # summary counts the current run only
    assert ra["runs"]["risk_analysis"]["run_id"] == second["run_id"]
    assert ra["runs"]["classification"]["status"] == "done"


def test_history_lookup_never_reads_as_no_findings(client):
    """Postgres cascade-deletes a run's findings when the contract is
    re-processed (the SQLite test DB keeps them: FK cascade is off there).
    An explicit ?run_id= history lookup must say so instead of returning an
    empty list that reads like 'this run found nothing'."""
    token, cid, _ = _process(client, "hist410@x.com")
    h = auth(token)
    _classify(cid)
    _set_policy(client, token, "payment", {"max_days": 30})
    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert run["findings"] == 3

    db = TestingSession()
    try:
        from app.models import RiskFinding

        db.query(RiskFinding).filter(
            RiskFinding.contract_id == uuid.UUID(cid)).delete()
        db.commit()
    finally:
        db.close()

    r = client.get(f"/api/v1/contracts/{cid}/findings?run_id={run['run_id']}",
                   headers=h)
    assert r.status_code == 410, r.text
    assert "recorded 3 findings" in r.json()["detail"]

    # The current view stays a bare list — it makes no claim about history.
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []
    # Filters never turn into a history verdict.
    assert client.get(f"/api/v1/contracts/{cid}/findings?priority=critical",
                      headers=h).json() == []
    # Unknown run stays an empty list (unchanged, tested elsewhere).
    assert client.get(f"/api/v1/contracts/{cid}/findings?run_id="
                      f"{uuid.uuid4()}", headers=h).json() == []


def test_reprocessing_invalidates_previous_analysis(client):
    """Re-processing recreates clauses → earlier classifications/findings no
    longer describe this contract. The system must say 'stale', not 'complete'."""
    token, cid, _ = _process(client, "reproc@x.com")
    h = auth(token)
    _classify(cid)
    _set_policy(client, token, "payment", {"max_days": 30})
    assert client.post(f"/api/v1/contracts/{cid}/risk-analysis",
                       headers=h).json()["findings"] == 3

    from app.modules.document.pipeline import process_contract

    db = TestingSession()
    try:
        assert process_contract(db, uuid.UUID(cid))["status"] == "processed"
    finally:
        db.close()

    d = _detail(client, token, cid)
    life = d["analysis_lifecycle"]
    assert life["classification"]["state"] == "stale"
    assert life["risk_analysis"]["state"] == "stale"
    assert life["overall"] == "stale"
    assert d["analysis_status"] == "stale"  # needs re-running, not "complete"

    ra = _assessment(client, token, cid)
    assert {c["state_reason"] for c in ra["clauses"]} == {"classification_stale"}
    assert all(c["risk_state"] == "needs_review" for c in ra["clauses"])
    s = ra["summary"]
    assert s["high_risk"] == 0 and s["no_detected_risk"] == 0
    assert s["needs_review"] == N and s["needs_attention"] == N


# --------------------------------------------------------- failure states ---


def test_classification_failure_is_visible_not_hidden(client, monkeypatch):
    token, cid, _ = _process(client, "cls-fail@x.com")
    h = auth(token)

    import app.modules.ml_nlp.classifier as clf

    monkeypatch.setattr(clf, "get_classifier", lambda model_dir=None: {
        "model": None, "tokenizer": None, "labels": ["payment"],
        "model_version": "tiny-v0", "base_model": "tiny-test", "seq_len": 64})

    def boom(text, model_dir=None):
        raise clf.InferenceError("Model inference failed (test)")

    monkeypatch.setattr(clf, "classify_clause", boom)

    db = TestingSession()
    try:
        with pytest.raises(clf.InferenceError):
            clf.classify_contract(db, uuid.UUID(cid), triggered_by=None)
    finally:
        db.close()

    d = _detail(client, token, cid)
    life = d["analysis_lifecycle"]
    assert life["classification"]["state"] == "failed"
    assert "InferenceError" in life["classification"]["error"]
    assert life["risk_analysis"]["state"] == "not_run"
    assert life["overall"] == "failed"
    assert d["analysis_status"] == "failed"

    ra = _assessment(client, token, cid)
    assert ra["runs"]["classification"]["status"] == "failed"
    assert ra["runs"]["classification"]["error"]
    assert all(c["risk_state"] == "needs_review" for c in ra["clauses"])
    assert {c["state_reason"] for c in ra["clauses"]} == {"classification_failed"}
    assert ra["summary"]["no_detected_risk"] == 0  # failure is not "clean"
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []


def test_risk_analysis_failure_is_visible_not_hidden(client, monkeypatch):
    token, cid, _ = _process(client, "risk-fail@x.com")
    h = auth(token)
    _classify(cid)
    _set_policy(client, token, "payment", {"max_days": 30})
    ok = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h).json()
    assert ok["findings"] == 3
    good_run = ok["run_id"]

    import app.modules.risk_engine.analyzer as an

    def boom(category, text):
        raise RuntimeError("extractor exploded (test)")

    monkeypatch.setattr(an, "extract_facts", boom)
    db = TestingSession()
    try:
        with pytest.raises(RuntimeError):
            an.analyze_contract(db, uuid.UUID(cid), triggered_by=None)
    finally:
        db.close()

    d = _detail(client, token, cid)
    life = d["analysis_lifecycle"]
    assert life["risk_analysis"]["state"] == "failed"
    assert "RuntimeError" in life["risk_analysis"]["error"]
    assert d["analysis_status"] == "failed"

    # 0 findings here means "the run failed", and the state says exactly that.
    ra = _assessment(client, token, cid)
    assert ra["summary"]["findings"] == 0
    assert ra["summary"]["risk_analysis_state"] == "failed"
    assert ra["summary"]["no_detected_risk"] == 0
    assert {c["state_reason"] for c in ra["clauses"]} == {"risk_analysis_failed"}
    assert all(c["risk_state"] == "needs_review" for c in ra["clauses"])
    assert ra["runs"]["risk_analysis"]["status"] == "failed"
    assert client.get(f"/api/v1/contracts/{cid}/findings", headers=h).json() == []
    # The previous successful run is still reachable on purpose.
    assert client.get(f"/api/v1/contracts/{cid}/findings?run_id={good_run}",
                      headers=h).json() != []


# ------------------------------------------------------- real model path ---


@pytest.fixture()
def model_env(tmp_path, monkeypatch):
    from tiny_model import TINY_LABELS, build_tiny_model

    d = build_tiny_model(str(tmp_path / "clf"), TINY_LABELS)
    monkeypatch.setenv("MODEL_DIR", d)
    from app.modules.ml_nlp.classifier import reset_classifier

    reset_classifier()
    yield d
    reset_classifier()


def test_real_classification_then_risk_analysis_completes(client, model_env):
    """Full path: model → persisted predictions → risk run → clause states."""
    token, cid, _ = _process(client, "real@x.com")
    h = auth(token)
    r = client.post(f"/api/v1/contracts/{cid}/classify", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["classified"] == N

    d = _detail(client, token, cid)
    assert d["analysis_lifecycle"]["classification"]["state"] == "complete"
    assert d["analysis_status"] == "classified"

    run = client.post(f"/api/v1/contracts/{cid}/risk-analysis", headers=h)
    assert run.status_code == 200, run.text
    ra = _assessment(client, token, cid)
    assert ra["analysis"]["overall"] == "complete"
    assert _detail(client, token, cid)["analysis_status"] == "complete"
    assert ra["summary"]["classified_clauses"] == N
    assert ra["summary"]["needs_review"] == 0
    allowed = {"high_risk", "medium_risk", "low_risk", "no_detected_risk"}
    assert {c["risk_state"] for c in ra["clauses"]} <= allowed
    assert (ra["summary"]["high_risk"] + ra["summary"]["medium_risk"]
            + ra["summary"]["low_risk"]
            + ra["summary"]["no_detected_risk"]) == N
