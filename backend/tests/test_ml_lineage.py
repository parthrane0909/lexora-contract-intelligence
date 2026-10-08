"""Lineage chain + production pinning behavior."""
import json
import uuid

from conftest import TestingSession, auth, register

from app.models import MLEvaluationRun, MLModelVersion


def _eval_row(db, org_id, version):
    e = MLEvaluationRun(organization_id=org_id, model_version=version,
                        dataset_version="v1", split="test",
                        metrics=json.dumps({"summary": {}}),
                        report_path="/tmp/r.json", confusion_path="/tmp/c.csv")
    db.add(e)
    db.commit()
    return e


def test_lineage_links_all_stages(client, tmp_path, monkeypatch):
    import app.modules.ml_evaluation.service as svc

    monkeypatch.setattr(svc, "artifact_base", lambda: str(tmp_path))
    token = register(client, "a@x.com", "password123", "OrgA")
    h = auth(token)
    assert client.post("/api/v1/ml-evaluation/datasets/build", headers=h,
                       json={"version": "v1"}).status_code == 200
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        u = db.query(User).filter(User.email == "a@x.com").one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
        from app.modules.ml_evaluation import registry as reg

        reg.register_model(db, version="m-v1", dataset_version="v1",
                           base_model="b", metrics={}, artifact_location="/tmp/m",
                           status="evaluated")
        _eval_row(db, org_id, "m-v1")
        db.add(__import__("app.models", fromlist=["MLPromotionEvent"]).MLPromotionEvent(
            model_name="clause-classifier", model_version="m-v1",
            from_status="evaluated", to_status="evaluated",
            decided_by=u.id, organization_id=org_id,
            gates="{}", reason="REJECTED"))
        db.commit()
    finally:
        db.close()
    lin = client.get("/api/v1/ml-evaluation/lineage?model_version=m-v1",
                     headers=h).json()
    assert any(d["version"] == "v1" for d in lin["datasets"])
    assert any(m["version"] == "m-v1" for m in lin["models"])
    assert any(e["model_version"] == "m-v1" for e in lin["evaluations"])
    assert any(p["model"] == "clause-classifier/m-v1" for p in lin["promotions"])
    # filtered lineage for unknown model is empty, not an error
    lin2 = client.get("/api/v1/ml-evaluation/lineage?model_version=nope",
                      headers=h).json()
    assert lin2["models"] == [] and lin2["evaluations"] == []


def test_production_pinning_resolution(client, tmp_path, monkeypatch):
    from app.modules.ml_evaluation import registry as reg
    from app.modules.ml_nlp.classifier import default_model_dir

    db = TestingSession()
    try:
        row = reg.register_model(
            db, version="pin-v1", dataset_version="v1", base_model="b",
            metrics={}, artifact_location=str(tmp_path), status="production")
        (tmp_path / "model_info.json").write_text("{}")
        monkeypatch.setenv("ACTIVE_MODEL_VERSION", "pin-v1")
        monkeypatch.delenv("MODEL_DIR", raising=False)
        assert reg.resolve_production_model_dir() == str(tmp_path)
        # unknown pin or missing artifact -> legacy fallback, never crash
        monkeypatch.setenv("ACTIVE_MODEL_VERSION", "nope")
        assert reg.resolve_production_model_dir() is None
        monkeypatch.delenv("ACTIVE_MODEL_VERSION")
        assert reg.resolve_production_model_dir() is None
        assert default_model_dir()  # legacy path intact
    finally:
        db.close()
