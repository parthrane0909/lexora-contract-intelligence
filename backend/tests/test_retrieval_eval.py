"""Retrieval eval tests: dataset builder, metrics, runner, API isolation."""
import uuid

import pytest
from conftest import TestingSession, auth, register
from test_semantic_search_api import hermetic  # noqa: F401 (shared hermetic fixture)

from app.models import Clause, Contract, Organization, Policy, RiskFinding, User


def _seed_org(db, email="r@x.com", org_name="OrgR"):
    org = Organization(name=org_name)
    db.add(org)
    db.flush()
    user = User(email=email, password_hash="x")
    db.add(user)
    db.flush()
    return org, user


def _seed_finding(db, org, user, tag="c1", category="payment", decision=None):
    contract = Contract(organization_id=org.id, filename=f"{tag}.pdf",
                        storage_key="k", uploaded_by=user.id)
    db.add(contract)
    db.flush()
    clause = Clause(organization_id=org.id, contract_id=contract.id,
                    text=f"Clause text about {category} terms here.",
                    char_start=0, char_end=10, order_index=0,
                    segmenter_name="t", segmenter_version="t")
    db.add(clause)
    db.flush()
    finding = RiskFinding(
        organization_id=org.id, contract_id=contract.id, clause_id=clause.id,
        risk_category=category, finding_type="rule", priority="medium",
        title=f"{category} finding", reason="because reasons",
        rule_id="R-1", rule_version="1.0")
    db.add(finding)
    db.flush()
    if decision:
        from app.models import ReviewDecision

        db.add(ReviewDecision(organization_id=org.id, finding_id=finding.id,
                              reviewer_id=user.id, decision=decision))
        db.flush()
    return finding


def test_eval_dataset_builder_marks_strength(client):
    from app.modules.retrieval import eval_dataset as evd

    db = TestingSession()
    try:
        org, user = _seed_org(db)
        _seed_finding(db, org, user, tag="a", category="payment", decision="accept")
        _seed_finding(db, org, user, tag="b", category="renewal", decision=None)
        db.commit()
        ds = evd.build_eval_dataset(db, org.id, seed=1)
    finally:
        db.close()
    assert ds["n_queries"] == 2 and ds["n_human_confirmed"] == 1
    by_cat = {e["category"]: e for e in ds["examples"]}
    assert by_cat["payment"]["expected"][0]["strength"] == "human_confirmed"
    assert by_cat["renewal"]["expected"][0]["strength"] == "derived"
    assert ds["dataset_version"].startswith("retrieval-eval-v1-")
    # never called "gold"
    import json

    assert "gold" not in json.dumps(ds).lower()


def test_eval_metrics_mrr_ndcg_zero_hit():
    from app.modules.retrieval import eval_metrics as em

    assert em.reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert em.reciprocal_rank(["x"], {"a"}) == 0.0
    assert em.ndcg_at_k(["a", "x"], {"a"}, 2) == 1.0
    assert em.ndcg_at_k(["x", "y"], {"a"}, 2) == 0.0
    assert em.ndcg_at_k([], {"a"}, 2) == 0.0
    out = em.evaluate_queries([
        {"query_id": "q1", "category": "payment", "ranked": ["a", "b"],
         "expected": {"a"}},
        {"query_id": "q2", "category": "payment", "ranked": [],
         "expected": {"c"}},
    ])
    assert out["aggregate"]["zero_hit_queries"] == 1
    assert out["aggregate"]["recall@1"] == 0.5
    assert out["per_query"][1]["zero_hit"] is True
    assert out["aggregate"]["by_category"]["payment"]["n"] == 2


def test_eval_source_breakdown():
    from app.modules.retrieval import eval_metrics as em

    res = em.source_type_breakdown(
        [{"ranked": ["a", "b"], "expected": {"a"}},
         {"ranked": ["c"], "expected": {"c"}}],
        {"a": "clause", "b": "policy", "c": "template_clause"}, k=2)
    assert res["topk_share"] == {"clause": 1, "policy": 1, "template_clause": 1}
    assert res["expected_hits"] == {"clause": 1, "template_clause": 1}


def test_runner_with_stubbed_search_and_immutable_artifact(tmp_path):
    from app.modules.retrieval import evaluate_real as evr

    dataset = {"dataset_version": "retrieval-eval-v1-test",
               "examples": [
                   {"query_id": "q1", "query": "payment terms",
                    "category": "payment",
                    "expected": [{"object_type": "clause", "object_id": "A"}]},
                   {"query_id": "q2", "query": "renewal term",
                    "category": "renewal",
                    "expected": [{"object_type": "clause", "object_id": "B"}]},
               ]}

    def fake_search(db, *, org_id, query, top_k, provider=None, store=None):
        if "payment" in query:
            return [{"object_id": "A", "object_type": "clause", "score": 0.9}]
        return [{"object_id": "X", "object_type": "clause", "score": 0.5}]

    import uuid as _uuid

    from conftest import TestingSession as TS

    db = TS()
    try:
        rep = evr.run_evaluation(db, _uuid.uuid4(), dataset, top_k=5,
                                 _search=fake_search, out_dir=str(tmp_path))
    finally:
        db.close()
    assert rep["aggregate"]["recall@1"] == 0.5
    assert rep["aggregate"]["zero_hit_queries"] == 0
    assert rep["config"]["n_queries"] == 2
    assert rep["artifact_path"].endswith("-report.json")
    # artifacts immutable: second run refuses
    with pytest.raises(ValueError, match="immutable"):
        evr.run_evaluation(db, uuid.uuid4(), dataset, top_k=5,
                           _search=fake_search, out_dir=str(tmp_path))


def test_retrieval_eval_api_isolation(client, tmp_path, monkeypatch):
    import app.modules.retrieval.router as rmod

    ta = register(client, "a@x.com", "password123", "OrgA")
    tb = register(client, "b@x.com", "password123", "OrgB")
    db = TestingSession()
    try:
        org_a, user_a = _seed_org(db, "aa@x.com", "OA2")
        _seed_finding(db, org_a, user_a, tag="x", category="payment")
        db.commit()
    finally:
        db.close()
    # build dataset as org-A admin? datasets build from caller's own org (empty here)
    r = client.post("/api/v1/retrieval/eval-datasets/build", headers=auth(ta))
    assert r.status_code == 400  # admin org has no findings -> explicit, not empty dataset
    assert client.get("/api/v1/retrieval/eval-datasets",
                      headers=auth(tb)).json() == []
    assert client.get("/api/v1/retrieval/evaluations",
                      headers=auth(tb)).json() == []
    assert client.get("/api/v1/retrieval/evaluations/some-version",
                      headers=auth(tb)).status_code == 404
    # viewer cannot build
    db = TestingSession()
    try:
        from app.core.security import create_access_token

        from app.models import OrganizationMember

        user = User(email="v@x.com", password_hash="x")
        db.add(user)
        db.flush()
        from app.models import Organization as Org

        org = db.query(Org).filter(Org.name == "OrgA").one()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(org.id), "viewer")
    finally:
        db.close()
    assert client.post("/api/v1/retrieval/eval-datasets/build",
                       headers=auth(vtok)).status_code == 403


def test_precedent_usefulness_ranking_and_scope(client, hermetic):
    """Precedent results: similar first, category-compatible, org-scoped,
    traceable to stored clauses. Uses the hermetic hash provider."""
    from conftest import TestingSession, auth
    from test_semantic_search_api import ORG_A_PAGES, ORG_B_PAGES, _idx

    token, cid = _idx(client, None, ORG_A_PAGES, "pu@x.com")
    db = TestingSession()
    try:
        from app.models import Clause

        clauses = db.query(Clause).filter(
            Clause.contract_id == uuid.UUID(cid)).order_by(Clause.order_index).all()
        src_id = str(clauses[0].id)
    finally:
        db.close()
    out = client.get(f"/api/v1/retrieval/precedent?clause_id={src_id}&contract_id={cid}&top_k=5",
                     headers=auth(token)).json()
    assert out["source_clause_id"] == src_id
    assert out["results"], "expected similar historical clause"
    scores = [r["score"] for r in out["results"]]
    assert scores == sorted(scores, reverse=True)  # ranked
    assert all(r["object_id"] != src_id for r in out["results"])  # self excluded
    for r in out["results"]:
        assert r["contract_id"] == cid  # same-org traceability
        assert r["text"]  # navigable excerpt present
    # cross-org: org B must never see org A clauses
    token_b, _ = _idx(client, None, ORG_B_PAGES, "pub@x.com")
    out_b = client.get(f"/api/v1/retrieval/precedent?clause_id={src_id}&contract_id={cid}",
                       headers=auth(token_b)).json()
    assert out_b.get("results", []) == [] or all(
        r["contract_id"] != cid for r in out_b.get("results", []))
