"""Phase 14 resume-safety tests (stage 1c prep). No GPU, no training, no test split."""
import json
import os

import pytest
from conftest import TestingSession, register

from app.modules.ml_evaluation import resume as resume_mod


def _org(db, email="a@x.com"):
    from app.models import OrganizationMember, User

    u = db.query(User).filter(User.email == email).one()
    return db.query(OrganizationMember).filter(
        OrganizationMember.user_id == u.id).one().organization_id


def _spec(prefix="t", cell="A", seed=42, matrix="mx-t"):
    return {"cell": cell, "preset": "baseline_repro", "seed": seed,
            "model_version": resume_mod.model_version_for(prefix, cell, seed),
            "name": f"t[{cell}/seed={seed}]", "matrix_id": matrix}


def test_completed_runs_are_skipped_and_missing_selected(client, tmp_path):
    from app.modules.ml_evaluation import experiments as exp_mod
    from app.modules.ml_evaluation import registry as reg

    register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        org_id = _org(db)
        art = tmp_path / "m"
        art.mkdir()
        (art / "model.safetensors").write_text("fake-weights")
        exp = exp_mod.register_experiment(
            db, organization_id=org_id, name="t[A/seed=42]", preset="seq256",
            dataset_version="clause_v1", model_version="t-A-s42", seed=42)
        exp.matrix_id = "mx-t"
        exp.status = "completed"
        db.commit()
        reg.register_model(db, version="t-A-s42", dataset_version="clause_v1",
                           base_model="b", metrics={}, artifact_location=str(art),
                           status="candidate")
        plan = resume_mod.plan_resume(db, [_spec(), _spec(seed=43)])
    finally:
        db.close()
    assert [s["model_version"] for s in plan["completed"]] == ["t-A-s42"]
    assert [s["model_version"] for s in plan["missing"]] == ["t-A-s43"]
    assert plan["needs_finalize"] == []


def test_b45_finalized_without_retraining_and_cost_honest(client, tmp_path, monkeypatch):
    import app.modules.ml_evaluation.training as train_mod
    from app.models import MLEvaluationRun
    from app.modules.ml_evaluation import comparisons as cmp_mod
    from app.modules.ml_evaluation import experiments as exp_mod
    from app.modules.ml_evaluation import registry as reg

    def _no_train(*a, **k):
        raise AssertionError("training must not run during finalization")

    monkeypatch.setattr(train_mod, "train_candidate", _no_train)
    monkeypatch.setattr(exp_mod, "train_candidate", _no_train)

    seen = {}

    def fake_compare(db, **kwargs):
        seen.update(kwargs)
        return {"baseline_version": "prod", "candidate_version": kwargs["candidate_version"]}

    monkeypatch.setattr(cmp_mod, "compare_against_baseline", fake_compare)

    register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        from app.models import MLDataset

        org_id = _org(db)
        db.add(MLDataset(organization_id=org_id, version="clause_v1",
                         created_from="test", source_count=1, usable_count=1,
                         excluded_count=0, manifest="{}",
                         artifact_path="/tmp/ds"))
        db.commit()
        art = tmp_path / "b45"
        art.mkdir()
        (art / "model.safetensors").write_text("fake-weights")
        (art / "model_info.json").write_text("{}")
        rep = tmp_path / "rep.json"
        rep.write_text(json.dumps({"summary": {"accuracy": 0.87, "n": 10}}))
        exp = exp_mod.register_experiment(
            db, organization_id=org_id, name="t[B/seed=45]", preset="seq256",
            dataset_version="clause_v1", model_version="t-B-s45", seed=45)
        exp.matrix_id = "mx-t"
        exp.status = "running"  # stale, training+eval done
        db.commit()
        reg.register_model(db, version="t-B-s45", dataset_version="clause_v1",
                           base_model="b",
                           metrics={"accuracy": 0.87, "n": 10},
                           artifact_location=str(art), status="candidate")
        db.add(MLEvaluationRun(
            organization_id=org_id, model_version="t-B-s45",
            dataset_version="clause_v1", split="validation",
            metrics="{}", report_path=str(rep), confusion_path=str(rep)))
        db.commit()
        eid = exp.id
        plan = resume_mod.plan_resume(db, [_spec(cell="B", seed=45,
                                                prefix="t")])
        assert [s["model_version"] for s in plan["needs_finalize"]] == ["t-B-s45"]
        out = resume_mod.finalize_without_retraining(db, eid, eval_split="validation")
    finally:
        db.close()
    assert out["status"] == "completed" and out["eval_split"] == "validation"
    assert seen["split"] == "validation" and seen["candidate_version"] == "t-B-s45"
    db = TestingSession()
    try:
        from app.models import MLExperiment

        exp = db.get(MLExperiment, eid)
        assert exp.status == "completed" and exp.candidate_version == "t-B-s45"
        costs = json.loads(exp.costs)
        assert costs["train_duration_s"] is None  # never fabricated
        assert costs["recovered_run"] is True
        assert costs["eval_split"] == "validation"
        # already-completed work is refused (no overwrite)
        with pytest.raises(ValueError, match="already completed"):
            resume_mod.finalize_without_retraining(db, eid, eval_split="validation")
    finally:
        db.close()


def test_test_split_rejected_and_unknown_split_rejected(client):
    from app.modules.ml_evaluation import experiments as exp_mod

    register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        org_id = _org(db)
        exp = exp_mod.register_experiment(
            db, organization_id=org_id, name="t2", preset="seq256",
            dataset_version="clause_v1", model_version="t2-v", seed=42)
        eid = exp.id
    finally:
        db.close()
    db = TestingSession()
    try:
        with pytest.raises(ValueError, match="Refusing test split"):
            resume_mod.finalize_without_retraining(db, eid, eval_split="test")
        with pytest.raises(ValueError, match="Unknown eval_split"):
            resume_mod.finalize_without_retraining(db, eid, eval_split="bogus")
    finally:
        db.close()


def test_disk_guard_logic(tmp_path):
    gate = resume_mod.disk_ok(str(tmp_path), required_bytes=1, floor_bytes=1)
    assert gate["ok"] is True and gate["free_bytes"] > 0
    gate2 = resume_mod.disk_ok(str(tmp_path), required_bytes=10**18, floor_bytes=1)
    assert gate2["ok"] is False  # impossible requirement stops cleanly


def test_mlflow_artifact_flag(monkeypatch):
    from app.modules.ml_evaluation import training as train_mod

    monkeypatch.delenv("PHASE14_MLFLOW_LOG_ARTIFACTS", raising=False)
    assert train_mod.mlflow_log_artifacts_enabled() is True  # default preserves history
    monkeypatch.setenv("PHASE14_MLFLOW_LOG_ARTIFACTS", "1")
    assert train_mod.mlflow_log_artifacts_enabled() is True
    monkeypatch.setenv("PHASE14_MLFLOW_LOG_ARTIFACTS", "0")
    assert train_mod.mlflow_log_artifacts_enabled() is False


def test_finalize_requires_artifact_and_eval(client):
    from app.modules.ml_evaluation import experiments as exp_mod

    register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        org_id = _org(db)
        exp = exp_mod.register_experiment(
            db, organization_id=org_id, name="t3", preset="seq256",
            dataset_version="clause_v1", model_version="t3-v", seed=42)
        eid = exp.id
    finally:
        db.close()
    db = TestingSession()
    try:
        with pytest.raises(ValueError, match="not registered"):
            resume_mod.finalize_without_retraining(db, eid, eval_split="validation")
    finally:
        db.close()
