"""Orchestration tests with stubbed heavy cores (no GPUs)."""
import json
import uuid

import pytest
from conftest import TestingSession

from app.modules.ml_evaluation import service as svc


def _rep(macro, weighted, per):
    return {"summary": {"accuracy": 0.8, "micro": {"f1": 0.8},
                        "macro": {"f1": macro}, "weighted": {"f1": weighted},
                        "n": 10},
            "per_class": {k: {"f1": v} for k, v in per.items()},
            "confusion": {}, "calibration": {}, "errors": {}, "truncation": {}}


def test_compare_models_uses_stored_reports(client, monkeypatch):
    from app.modules.ml_evaluation import registry as reg

    db = TestingSession()
    try:
        reg.register_model(db, version="prod-x", dataset_version="d",
                           base_model="b",
                           metrics=_rep(0.70, 0.85, {"liability": 0.66})["summary"]
                           and {"summary": {"macro": {"f1": 0.70},
                                            "weighted": {"f1": 0.85}},
                                "per_class": {c: {"f1": 0.7} for c in
                                              ("liability", "indemnification",
                                               "termination", "payment",
                                               "confidentiality", "governing_law",
                                               "renewal", "warranty")}},
                           artifact_location="/tmp/prod", status="production")
        reg.register_model(db, version="cand-x", dataset_version="d",
                           base_model="b", metrics={},
                           artifact_location="/tmp/cand", status="candidate")
    finally:
        db.close()

    def fake_eval(db, **kwargs):
        v = kwargs.get("model_version")
        rep = _rep(0.72, 0.86, {"liability": 0.68, "indemnification": 0.80,
                                "termination": 0.60, "payment": 0.86,
                                "confidentiality": 0.70, "governing_law": 0.88,
                                "renewal": 0.50, "warranty": 0.62}) \
            if v == "cand-x" else _rep(0.70, 0.85, {"liability": 0.66,
                                                     "indemnification": 0.78,
                                                     "termination": 0.58,
                                                     "payment": 0.84,
                                                     "confidentiality": 0.68,
                                                     "governing_law": 0.86,
                                                     "renewal": 0.48,
                                                     "warranty": 0.60})
        return {"evaluation_id": str(uuid.uuid4()), "report": rep}

    monkeypatch.setattr(svc, "evaluate_bundle", fake_eval)
    db = TestingSession()
    try:
        out = svc.compare_models(db, candidate_version="cand-x",
                                 dataset_path="/tmp", split="test")
    finally:
        db.close()
    assert out["candidate"]["version"] == "cand-x"
    assert out["gates"]["approved"] is True
    with pytest.raises(ValueError, match="unknown"):
        db = TestingSession()
        try:
            svc.compare_models(db, candidate_version="nope",
                               dataset_path="/tmp", split="test")
        finally:
            db.close()


def test_shadow_evaluate_stubbed_predict(client, monkeypatch):
    import app.modules.ml_evaluation.service as svc_mod

    def fake_predict(model_dir, texts, seq_len=128, batch_size=32):
        n = len(texts)
        return ([0] * n, [0.9] * n,
                {"labels": ["payment", "termination"], "model_version": "m-" + model_dir[-3:],
                 "base_model": "b", "seq_len": 128})

    monkeypatch.setattr(svc_mod, "predict_bundle", fake_predict)
    import app.modules.ml_nlp.classifier as clf

    monkeypatch.setattr(clf, "default_model_dir", lambda: "/tmp/prod")
    out = svc_mod.shadow_evaluate("/tmp/cand", ["Pay now.", "Terminate now."])
    assert [p["category"] for p in out["production_prediction"]] == ["payment"] * 2
    assert out["candidate_model"] == "m-and"
    assert "unchanged" in out["note"]


def test_run_experiment_stubbed_cores(client, tmp_path, monkeypatch):
    from conftest import auth, register

    import app.modules.ml_evaluation.experiments as exp_mod

    token = register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        u = db.query(User).filter(User.email == "a@x.com").one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
        exp = exp_mod.register_experiment(
            db, organization_id=org_id, name="stub-exp", preset="seq256",
            dataset_version="clause_v1", model_version="stub-v1", seed=42)
        eid = exp.id
    finally:
        db.close()

    def fake_train(cfg, data_dir, out_dir, mlflow_uri):
        import os

        os.makedirs(out_dir, exist_ok=True)
        return {"model_version": cfg.model_version, "artifact_dir": out_dir,
                "val_metrics": {"macro_f1": 0.5}, "mlflow_run_id": "r",
                "duration_s": 1.0}

    import app.modules.ml_evaluation.service as svc_mod

    def fake_eval(db, **kwargs):
        return {"evaluation_id": str(uuid.uuid4()),
                "report": {"summary": {"accuracy": 0.8,
                                       "macro": {"f1": 0.7},
                                       "weighted": {"f1": 0.75},
                                       "micro": {"f1": 0.8}, "n": 10},
                           "per_class": {}, "confusion": {},
                           "calibration": {}, "errors": {}, "truncation": {}}}

    db = TestingSession()
    try:
        from app.models import MLDataset
        from app.modules.ml_evaluation import registry as reg

        reg.register_model(db, version="prod-stub", dataset_version="clause_v1",
                           base_model="b", metrics={}, artifact_location="/tmp/p",
                           status="production")
        db.add(MLDataset(organization_id=org_id, version="clause_v1",
                         created_from="test", source_count=1, usable_count=1,
                         excluded_count=0, manifest="{}",
                         artifact_path=str(tmp_path)))
        db.commit()
        out = exp_mod.run_experiment(db, eid, _train=fake_train, _evaluate=fake_eval)
    finally:
        db.close()
    assert out["status"] == "completed"
    assert out["comparison"]["candidate_version"] == "stub-v1"
    db = TestingSession()
    try:
        exp = exp_mod.get_experiment(db, eid)
        assert exp.status == "completed" and exp.candidate_version == "stub-v1"
        # rerunning a completed experiment is refused
        with pytest.raises(ValueError, match="only registered/failed"):
            exp_mod.run_experiment(db, eid)
    finally:
        db.close()
