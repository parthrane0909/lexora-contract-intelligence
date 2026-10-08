"""Experiment lifecycle API tests (stubbed heavy cores)."""
import json
import uuid

import pytest
from conftest import TestingSession, auth, register


def _stub_run(db, experiment_id, **kwargs):
    from app.models import MLExperiment

    exp = db.get(MLExperiment, experiment_id)
    exp.status = "completed"
    exp.candidate_version = "stub-cand-v1"
    exp.metrics = json.dumps({"summary": {"accuracy": 0.8,
                                          "macro": {"f1": 0.7},
                                          "weighted": {"f1": 0.75},
                                          "micro": {"f1": 0.8}}})
    exp.costs = json.dumps({"train_duration_s": 1.0})
    db.commit()
    return {"experiment_id": str(exp.id), "status": "completed",
            "comparison": {"overall": {}, "protected_classes": {}}}


def test_experiment_register_run_get_rbac(client, monkeypatch):
    import app.modules.ml_evaluation.experiments as exp_mod

    monkeypatch.setattr(exp_mod, "run_experiment", _stub_run)

    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    assert client.get("/api/v1/ml-evaluation/classification/experiments",
                      headers=h).json() == []
    r = client.post("/api/v1/ml-evaluation/classification/experiments", headers=h,
                    json={"name": "e1", "preset": "seq256", "dataset_version": "clause_v1",
                          "model_version": "stub-cand-v1"})
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    assert r.json()["status"] == "registered"
    bad = client.post("/api/v1/ml-evaluation/classification/experiments", headers=h,
                      json={"name": "e2", "preset": "nope", "dataset_version": "d",
                            "model_version": "m"})
    assert bad.status_code == 400
    got = client.get(f"/api/v1/ml-evaluation/classification/experiments/{eid}",
                     headers=h).json()
    assert got["name"] == "e1"
    assert client.get("/api/v1/ml-evaluation/classification/experiments/nope",
                      headers=h).status_code == 404
    run = client.post(
        f"/api/v1/ml-evaluation/classification/experiments/{eid}/run", headers=h)
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"
    got2 = client.get(f"/api/v1/ml-evaluation/classification/experiments/{eid}",
                      headers=h).json()
    assert got2["status"] == "completed" and got2["candidate_version"] == "stub-cand-v1"
    # cross-org invisibility
    tb = register(client, "b@x.com", "password123", "OrgB")
    assert client.get(f"/api/v1/ml-evaluation/classification/experiments/{eid}",
                      headers=auth(tb)).status_code == 404
    assert client.get("/api/v1/ml-evaluation/classification/experiments",
                      headers=auth(tb)).json() == []
    # viewer cannot create/run
    db = TestingSession()
    try:
        from app.core.security import create_access_token

        from app.models import Organization as Org
        from app.models import OrganizationMember, User

        user = User(email="v@x.com", password_hash="x")
        db.add(user)
        db.flush()
        org = db.query(Org).filter(Org.name == "OrgA").one()
        db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="viewer"))
        db.commit()
        vtok = create_access_token(str(user.id), str(org.id), "viewer")
    finally:
        db.close()
    assert client.post("/api/v1/ml-evaluation/classification/experiments",
                       headers=auth(vtok),
                       json={"name": "e", "preset": "seq256",
                             "dataset_version": "d", "model_version": "m"}).status_code == 403
    assert client.post(
        f"/api/v1/ml-evaluation/classification/experiments/{eid}/run",
        headers=auth(vtok)).status_code == 403
