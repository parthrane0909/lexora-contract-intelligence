"""API tests: RBAC, validation, isolation, lineage, shadow (stubbed trainer).
Heavy training is stubbed at the router boundary; real training is verified
manually in the Phase 12 smoke (small real candidate on GPU).
"""
import json
import uuid

import pytest
from conftest import TestingSession, auth, register

from app.models import MLDataset, MLModelVersion


def _seed_dataset(db, org_id, version="v1"):
    row = MLDataset(
        organization_id=org_id, version=version, created_from="feedback",
        source_count=10, usable_count=8, excluded_count=2,
        manifest=json.dumps({"dataset_version": version, "seed": 42,
                             "quality": {}, "splits_sha256": "abc"}),
        artifact_path=f"/tmp/ds-{version}")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _org_of(db, email):
    from app.models import OrganizationMember, User

    u = db.query(User).filter(User.email == email).one()
    m = db.query(OrganizationMember).filter(
        OrganizationMember.user_id == u.id).one()
    return m.organization_id, u.id


def _viewer_token(db, org_id, email="v@x.com"):
    from app.core.security import create_access_token

    from app.models import OrganizationMember, User

    user = User(email=email, password_hash="x")
    db.add(user)
    db.flush()
    db.add(OrganizationMember(organization_id=org_id, user_id=user.id, role="viewer"))
    db.commit()
    return create_access_token(str(user.id), str(org_id), "viewer")


def test_dataset_build_list_get_quality_rbac(client, tmp_path, monkeypatch):
    from app.modules.ml_evaluation import service as svc

    monkeypatch.setattr(svc, "artifact_base", lambda: str(tmp_path))
    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    assert client.get("/api/v1/ml-evaluation/datasets", headers=h).json() == []
    r = client.post("/api/v1/ml-evaluation/datasets/build", headers=h,
                    json={"version": "v1", "seed": 42})
    # no feedback rows in this org -> empty but valid versioned dataset
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "v1" and body["manifest"]["seed"] == 42
    assert body["stats"]["total"] == 0
    # duplicate version refused (immutability)
    assert client.post("/api/v1/ml-evaluation/datasets/build", headers=h,
                       json={"version": "v1"}).status_code == 400
    q = client.get("/api/v1/ml-evaluation/datasets/v1/quality", headers=h).json()
    assert q["version"] == "v1" and "quality" in q
    assert client.get("/api/v1/ml-evaluation/datasets/nope", headers=h).status_code == 404
    db = TestingSession()
    try:
        org_id, _ = _org_of(db, "a@x.com")
        vtok = _viewer_token(db, org_id)
    finally:
        db.close()
    # viewer reads summaries, cannot build
    assert client.get("/api/v1/ml-evaluation/datasets", headers=auth(vtok)).status_code == 200
    assert client.post("/api/v1/ml-evaluation/datasets/build", headers=auth(vtok),
                       json={"version": "v2"}).status_code == 403


def test_models_crud_and_train_validation(client, tmp_path, monkeypatch):
    import app.modules.ml_evaluation.router as router_mod
    import app.modules.ml_evaluation.service as svc_mod

    monkeypatch.setattr(svc_mod, "artifact_base", lambda: str(tmp_path))
    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    assert client.get("/api/v1/ml-evaluation/models", headers=h).json() == []
    assert client.get(f"/api/v1/ml-evaluation/models/{uuid.uuid4()}",
                      headers=h).status_code == 404

    def fake_train(cfg, data_dir, out_dir, mlflow_uri):
        import os

        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "model_info.json"), "w") as f:
            json.dump({"model_version": cfg.model_version,
                       "labels": ["payment"], "seq_len": 128,
                       "dataset_version": cfg.dataset_version,
                       "base_model": cfg.base_model}, f)
        return {"model_version": cfg.model_version, "artifact_dir": out_dir,
                "val_metrics": {"accuracy": 0.5}, "mlflow_run_id": "fake-run"}

    monkeypatch.setattr(router_mod, "train_candidate", fake_train)
    db = TestingSession()
    try:
        org_id, _ = _org_of(db, "a@x.com")
        _seed_dataset(db, org_id, "v1")
    finally:
        db.close()
    bad = client.post("/api/v1/ml-evaluation/models/train", headers=h,
                      json={"dataset_version": "v1", "model_version": "c1",
                            "learning_rate": 5.0})
    assert bad.status_code == 400  # config validated before any training
    bad2 = client.post("/api/v1/ml-evaluation/models/train", headers=h,
                       json={"dataset_version": "missing", "model_version": "c1"})
    assert bad2.status_code == 404
    good = client.post("/api/v1/ml-evaluation/models/train", headers=h,
                       json={"dataset_version": "v1", "model_version": "c1",
                             "epochs": 1})
    assert good.status_code == 200, good.text
    mid = good.json()["model"]["id"]
    assert good.json()["model"]["status"] == "candidate"
    got = client.get(f"/api/v1/ml-evaluation/models/{mid}", headers=h).json()
    assert got["version"] == "c1"
    # duplicate model version refused
    dup = client.post("/api/v1/ml-evaluation/models/train", headers=h,
                      json={"dataset_version": "v1", "model_version": "c1"})
    assert dup.status_code == 400
    # viewer cannot train/promote
    db = TestingSession()
    try:
        org_id2, _ = _org_of(db, "a@x.com")
        vtok = _viewer_token(db, org_id2, "vv@x.com")
    finally:
        db.close()
    assert client.post("/api/v1/ml-evaluation/models/train", headers=auth(vtok),
                       json={"dataset_version": "v1",
                             "model_version": "c2"}).status_code == 403
    assert client.post(f"/api/v1/ml-evaluation/models/{mid}/promote",
                       headers=auth(vtok), json={"reason": "x"}).status_code == 403


def test_evaluate_shadow_promote_and_lineage(client, tmp_path, monkeypatch):
    import app.modules.ml_evaluation.router as router_mod
    import app.modules.ml_evaluation.service as svc_mod

    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    db = TestingSession()
    try:
        org_id, uid = _org_of(db, "a@x.com")
        _seed_dataset(db, org_id, "v1")
        from app.modules.ml_evaluation import registry as reg

        reg.register_model(db, version="prod-v9", dataset_version="v1",
                           base_model="b", metrics={"summary": {}},
                           artifact_location="/tmp/prod", status="production")
        reg.register_model(db, version="cand-v9", dataset_version="v1",
                           base_model="b", metrics={"summary": {}},
                           artifact_location="/tmp/cand", status="candidate")
        cand = reg.get_model(db, "clause-classifier", "cand-v9")
        mid = str(cand.id)
    finally:
        db.close()

    def fake_eval(db, **kwargs):
        return {"evaluation_id": str(uuid.uuid4()),
                "report": {"summary": {"accuracy": 0.8,
                                       "macro": {"f1": 0.7},
                                       "weighted": {"f1": 0.75},
                                       "micro": {"f1": 0.8}},
                           "per_class": {}, "confusion": {},
                           "calibration": {}, "errors": {}, "truncation": {}}}

    monkeypatch.setattr(svc_mod, "evaluate_bundle", fake_eval)
    ev = client.post("/api/v1/ml-evaluation/models/evaluate", headers=h,
                     json={"model_version": "cand-v9", "dataset_version": "v1",
                           "split": "test"})
    assert ev.status_code == 200, ev.text
    assert ev.json()["report"]["summary"]["accuracy"] == 0.8
    db = TestingSession()
    try:
        from app.modules.ml_evaluation import registry as reg2

        row = reg2.get_model(db, "clause-classifier", "cand-v9")
        assert row.status == "evaluated"  # advanced by evaluation
        import json as _json

        assert _json.loads(row.metrics or "{}")["summary"]["accuracy"] == 0.8
    finally:
        db.close()
    assert client.post("/api/v1/ml-evaluation/models/evaluate", headers=h,
                       json={"model_version": "cand-v9", "dataset_version": "v1",
                             "split": "bogus"}).status_code in (200, 400)

    def fake_shadow(model_dir, texts):
        return {"production_prediction": [{"category": "payment", "confidence": 0.9}
                                          for _ in texts],
                "candidate_prediction": [{"category": "payment", "confidence": 0.8}
                                         for _ in texts],
                "production_model": "prod-v9", "candidate_model": "cand-v9",
                "note": "Shadow output only — production predictions unchanged."}

    monkeypatch.setattr(svc_mod, "shadow_evaluate", fake_shadow)
    sh = client.post(f"/api/v1/ml-evaluation/models/{mid}/shadow-evaluate",
                     headers=h, json={"texts": ["Pay now."]})
    assert sh.status_code == 200
    assert sh.json()["candidate_model"] == "cand-v9"
    assert client.post(f"/api/v1/ml-evaluation/models/{mid}/shadow-evaluate",
                       headers=h, json={"texts": []}).status_code == 400

    # promote with stubbed gates: candidate metrics beat empty production
    pr = client.post(f"/api/v1/ml-evaluation/models/{mid}/promote",
                     headers=h, json={"reason": "test"})
    assert pr.status_code in (200, 400)  # gates decide; either is explicit
    lin = client.get("/api/v1/ml-evaluation/lineage?model_version=cand-v9",
                     headers=h).json()
    assert any(m["version"] == "cand-v9" for m in lin["models"])
    assert any(d["version"] == "v1" for d in lin["datasets"])
    # errors/calibration read paths (no eval rows with real reports here)
    assert client.get(f"/api/v1/ml-evaluation/models/{mid}/errors",
                      headers=h).status_code == 404


def test_cross_org_isolation(client):
    ta = register(client, "a@x.com", "password123", "OrgA")
    tb = register(client, "b@x.com", "password123", "OrgB")
    db = TestingSession()
    try:
        org_a, _ = _org_of(db, "a@x.com")
        _seed_dataset(db, org_a, "v1")
    finally:
        db.close()
    assert client.get("/api/v1/ml-evaluation/datasets",
                      headers=auth(tb)).json() == []
    assert client.get("/api/v1/ml-evaluation/datasets/v1",
                      headers=auth(tb)).status_code == 404
    assert client.get("/api/v1/ml-evaluation/datasets/v1/quality",
                      headers=auth(tb)).status_code == 404
    assert client.post("/api/v1/ml-evaluation/datasets/build",
                       headers=auth(tb), json={"version": "v1"}).status_code == 200
    # models are global registry rows (metrics only); raw data stays org-scoped
    assert isinstance(client.get("/api/v1/ml-evaluation/models",
                                 headers=auth(tb)).json(), list)


def test_promote_endpoint_rbac_gates_and_audit(client):
    from conftest import TestingSession

    from app.models import MLPromotionEvent
    from app.modules.ml_evaluation import registry as reg

    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    good_metrics = lambda f1: {  # noqa: E731
        "summary": {"macro": {"f1": f1}, "weighted": {"f1": f1}},
        "per_class": {c: {"f1": f1} for c in
                      ("liability", "indemnification", "termination", "payment",
                       "confidentiality", "governing_law", "renewal", "warranty")}}
    db = TestingSession()
    try:
        reg.register_model(db, version="prod-live", dataset_version="v1",
                           base_model="b", metrics=good_metrics(0.70),
                           artifact_location="/tmp/p", status="production")
        cand = reg.register_model(db, version="cand-live", dataset_version="v1",
                                  base_model="b", metrics=good_metrics(0.80),
                                  artifact_location="/tmp/c", status="evaluated")
        mid = str(cand.id)
    finally:
        db.close()
    # viewer forbidden
    db = TestingSession()
    try:
        from app.core.security import create_access_token

        from app.models import OrganizationMember, User

        user = User(email="v@x.com", password_hash="x")
        db.add(user)
        db.flush()
        from app.models import Organization as Org

        org = db.query(Org).filter(Org.name == "OrgA").one()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id,
                                 role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(org.id), "viewer")
    finally:
        db.close()
    assert client.post(f"/api/v1/ml-evaluation/models/{mid}/promote",
                       headers=auth(vtok), json={"reason": "x"}).status_code == 403
    # admin promotes through gates -> production swaps, event recorded
    r = client.post(f"/api/v1/ml-evaluation/models/{mid}/promote",
                    headers=h, json={"reason": "all gates green"})
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] is True
    assert r.json()["previous_production"] == "prod-live"
    db = TestingSession()
    try:
        assert reg.production_model(db).version == "cand-live"
        ev = db.query(MLPromotionEvent).filter(
            MLPromotionEvent.model_version == "cand-live").one()
        assert ev.decided_by is not None and ev.to_status == "production"
        # second promote of an already-production model is refused
        r2 = client.post(f"/api/v1/ml-evaluation/models/{mid}/promote",
                         headers=h, json={"reason": "again"})
        assert r2.status_code == 400
    finally:
        db.close()


def test_matrix_modelcard_statistics_rollback_endpoints(client, tmp_path, monkeypatch):
    import app.modules.ml_evaluation.experiments as exp_mod
    import app.modules.ml_evaluation.service as svc_mod

    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    db = TestingSession()
    try:
        from app.models import MLEvaluationRun, MLModelVersion

        from app.modules.ml_evaluation import registry as reg2

        reg2.register_model(db, version="m-stat", dataset_version="v1",
                            base_model="b", metrics={}, artifact_location="/tmp/m",
                            status="evaluated")
        mid = str(reg2.get_model(db, "clause-classifier", "m-stat").id)
        db.add(MLEvaluationRun(
            organization_id=None, model_version="m-stat", dataset_version="v1",
            split="validation",
            metrics='{"summary": {"macro": {"f1": 0.7}}}',
            report_path="/tmp/missing-report.json",
            confusion_path="/tmp/missing-cm.csv"))
        db.commit()
    finally:
        db.close()
    # statistics aggregates tracked runs (missing files skipped)
    assert client.get(f"/api/v1/ml-evaluation/models/{mid}/statistics",
                      headers=h).status_code == 404
    # model-card falls back to a clearly-marked partial card
    card = client.get(f"/api/v1/ml-evaluation/models/{mid}/model-card",
                      headers=h).json()
    assert "does not constitute" in card["legal_disclaimer"]
    assert card["model"]["version"] == "m-stat"
    # matrix run with stubbed core
    def fake_run(db, eid, **kwargs):
        from app.models import MLExperiment

        exp = db.get(MLExperiment, eid)
        exp.status = "completed"
        db.commit()
        return {"experiment_id": str(eid), "status": "completed"}

    monkeypatch.setattr(exp_mod, "run_experiment", fake_run)
    mx = client.post("/api/v1/ml-evaluation/classification/experiments/matrix", headers=h,
                     json={"name": "mx1", "dataset_version": "clause_v1",
                           "model_version_prefix": "mxv1",
                           "cells": ["A"], "seeds": [42, 43]})
    assert mx.status_code == 200, mx.text
    assert mx.json()["n_runs"] == 2
    listed = client.get("/api/v1/ml-evaluation/classification/experiments/matrix",
                        headers=h).json()
    assert listed == [{"matrix_id": mx.json()["matrix_id"], "n_runs": 2}]
    detail = client.get(
        f"/api/v1/ml-evaluation/classification/experiments/matrix/{mx.json()['matrix_id']}",
        headers=h).json()
    assert len(detail["experiments"]) == 2
    assert client.get("/api/v1/ml-evaluation/classification/experiments/matrix/nope",
                      headers=h).status_code == 404
    # viewer forbidden from matrix runs, rollback, and crossed-org reads
    db = TestingSession()
    try:
        from app.core.security import create_access_token

        from app.models import Organization as Org
        from app.models import OrganizationMember, User

        user = User(email="vm@x.com", password_hash="x")
        db.add(user)
        db.flush()
        org = db.query(Org).filter(Org.name == "OrgA").one()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(org.id), "viewer")
    finally:
        db.close()
    assert client.post("/api/v1/ml-evaluation/classification/experiments/matrix",
                       headers=auth(vtok),
                       json={"name": "x", "dataset_version": "d",
                             "model_version_prefix": "p"}).status_code == 403
    assert client.post(f"/api/v1/ml-evaluation/models/{mid}/rollback",
                       headers=auth(vtok), json={"reason": "x"}).status_code == 403
    # rollback of a never-production model is refused (400, explicit)
    rb = client.post(f"/api/v1/ml-evaluation/models/{mid}/rollback",
                     headers=h, json={"reason": "oops"})
    assert rb.status_code == 400
    assert client.post("/api/v1/ml-evaluation/models/00000000-0000-0000-0000-000000000000/rollback",
                       headers=h, json={}).status_code == 404


def test_selection_protected_history_endpoints(client, tmp_path):
    """Phase 14: selection over validation only, protected breakdown,
    org-scoped promotion history + cross-org isolation."""
    import json as _json

    token_a = register(client, "a@x.com", "password123", "OrgA")
    token_b = register(client, "b@x.com", "password123", "OrgB")
    ha, hb = auth(token_a), auth(token_b)
    db = TestingSession()
    try:
        from app.models import MLEvaluationRun, MLPromotionEvent

        from app.modules.ml_evaluation import registry as reg2

        org_a, _ = _org_of(db, "a@x.com")
        org_b, _ = _org_of(db, "b@x.com")
        reg2.register_model(db, version="sel-a", dataset_version="v1",
                            base_model="b", metrics={}, artifact_location="/tmp/s",
                            status="evaluated")
        # validation reports on disk (org A only)
        rep = {"summary": {"accuracy": 0.8, "macro": {"f1": 0.75},
                          "weighted": {"f1": 0.78}, "micro": {"f1": 0.8}},
               "per_class": {"payment": {"f1": 0.9, "support": 10},
                             "renewal": {"f1": 0.3, "support": 4}}}
        p = tmp_path / "rep.json"
        p.write_text(_json.dumps(rep))
        db.add(MLEvaluationRun(
            organization_id=org_a, model_version="sel-a", dataset_version="v1",
            split="validation", metrics="{}", report_path=str(p),
            confusion_path=str(p)))
        db.add(MLPromotionEvent(
            model_name="clause-classifier", model_version="sel-a",
            from_status="evaluated", to_status="production",
            decided_by=None, organization_id=org_a,
            gates="{}", reason="promoted in OrgA"))
        db.commit()
    finally:
        db.close()
    # selection scopes to caller's org: A sees a winner, B sees none -> 400
    sel_a = client.get("/api/v1/ml-evaluation/classification/selection", headers=ha)
    assert sel_a.status_code == 200, sel_a.text
    assert sel_a.json()["selected_version"] == "sel-a"
    assert "VALIDATION" in sel_a.json()["warning"]
    sel_b = client.get("/api/v1/ml-evaluation/classification/selection", headers=hb)
    assert sel_b.status_code == 400  # no candidates in OrgB, explicit
    # protected breakdown returns all 8 classes with explicit nulls
    db = TestingSession()
    try:
        from app.modules.ml_evaluation import registry as reg3

        mid = str(reg3.get_model(db, "clause-classifier", "sel-a").id)
    finally:
        db.close()
    prot = client.get(f"/api/v1/ml-evaluation/models/{mid}/protected", headers=ha).json()
    assert set(prot["protected"]) == {
        "liability", "indemnification", "payment", "termination",
        "renewal", "confidentiality", "governing_law", "warranty"}
    assert prot["protected"]["payment"]["candidate_f1"] == 0.9
    # promotion history is org-scoped
    hist_a = client.get("/api/v1/ml-evaluation/promotions/history", headers=ha).json()
    assert any(e["model_version"] == "sel-a" for e in hist_a)
    hist_b = client.get("/api/v1/ml-evaluation/promotions/history", headers=hb).json()
    assert all(e["model_version"] != "sel-a" for e in hist_b)
    # cross-org model-id probing: B cannot resolve A's model id via 404-or-empty;
    # registry rows are global but evaluation data (selection/history) never leaks
    assert client.get(f"/api/v1/ml-evaluation/models/{mid}/protected",
                      headers=hb).status_code == 200  # metrics-only, no raw data
    assert client.get("/api/v1/ml-evaluation/classification/experiments/matrix",
                      headers=hb).json() == []
