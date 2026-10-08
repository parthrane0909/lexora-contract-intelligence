"""Promotion tests: gates, explicit promotion, rejection audit, no auto-swap."""
import uuid

import pytest
from conftest import TestingSession

from app.models import MLModelVersion, MLPromotionEvent
from app.modules.ml_evaluation import promotion as promo
from app.modules.ml_evaluation import registry as reg


def _report(macro, weighted, per):
    return {"summary": {"macro": {"f1": macro}, "weighted": {"f1": weighted}},
            "per_class": {k: {"f1": v} for k, v in per.items()}}


LABELS = ("liability", "termination", "payment")


FULL_PER = {"liability": {"f1": 0.68}, "indemnification": {"f1": 0.80},
              "termination": {"f1": 0.60}, "payment": {"f1": 0.86},
              "confidentiality": {"f1": 0.70}, "governing_law": {"f1": 0.88},
              "renewal": {"f1": 0.50}, "warranty": {"f1": 0.62}}


def test_promote_flow_and_audit_trail(client):
    db = TestingSession()
    try:
        base = reg.register_model(
            db, version="base-v1",
            metrics={"summary": {"macro": {"f1": 0.70}, "weighted": {"f1": 0.85}},
                     "per_class": {
                         "liability": {"f1": 0.66}, "indemnification": {"f1": 0.78},
                         "termination": {"f1": 0.58}, "payment": {"f1": 0.84},
                         "confidentiality": {"f1": 0.68},
                         "governing_law": {"f1": 0.86},
                         "renewal": {"f1": 0.48}, "warranty": {"f1": 0.60}}},
            artifact_location="/tmp/base", status="production")
        assert base.status == "production"
        reg.register_model(
            db, version="cand-v1",
            metrics={"summary": {"macro": {"f1": 0.72}, "weighted": {"f1": 0.86}},
                     "per_class": FULL_PER},
            artifact_location="/tmp/cand", status="evaluated")
        res = promo.promote(db, model_name="clause-classifier", version="cand-v1",
                            decided_by=uuid.uuid4(), organization_id=None,
                            reason="smoke")
        assert res["promoted"] is True and res["previous_production"] == "base-v1"
        assert reg.production_model(db).version == "cand-v1"
        assert reg.get_model(db, "clause-classifier", "base-v1").status == "archived"
        n = db.query(MLModelVersion).filter(
            MLModelVersion.status == "production").count()
        assert n == 1  # exactly one production row, always
        evs = db.query(MLPromotionEvent).all()
        assert any(e.to_status == "production" for e in evs)
        # non-evaluated candidates cannot be promoted
        reg.register_model(db, version="raw-v1", status="candidate")
        with pytest.raises(ValueError, match="only evaluated/approved"):
            promo.promote(db, model_name="clause-classifier", version="raw-v1",
                          decided_by=None, organization_id=None)
        # unknown model
        with pytest.raises(ValueError, match="not registered"):
            promo.promote(db, model_name="clause-classifier", version="nope",
                          decided_by=None, organization_id=None)
    finally:
        db.close()


def test_promote_rejected_candidate_records_rejection(client):
    db = TestingSession()
    try:
        reg.register_model(
            db, version="p-v1",
            metrics={"summary": {"macro": {"f1": 0.9}, "weighted": {"f1": 0.9}},
                     "per_class": {}}, artifact_location="/tmp/p",
            status="production")
        reg.register_model(
            db, version="c-v1",
            metrics={"summary": {"macro": {"f1": 0.5}, "weighted": {"f1": 0.5}},
                     "per_class": {}}, artifact_location="/tmp/c",
            status="evaluated")
        res = promo.promote(db, model_name="clause-classifier", version="c-v1",
                            decided_by=uuid.uuid4(), organization_id=None)
        assert res["promoted"] is False
        assert reg.production_model(db).version == "p-v1"  # unchanged
        ev = db.query(MLPromotionEvent).filter(
            MLPromotionEvent.model_version == "c-v1").one()
        assert ev.to_status == "evaluated" and "REJECTED" in (ev.reason or "")
    finally:
        db.close()


def test_passing_gates_never_auto_promote(client):
    """Evaluation output is advisory: nothing in check_gates touches status."""
    db = TestingSession()
    try:
        reg.register_model(
            db, version="auto-v1",
            metrics={"summary": {"macro": {"f1": 0.99}, "weighted": {"f1": 0.99}},
                     "per_class": {}}, artifact_location="/tmp/a",
            status="evaluated")
        assert reg.production_model(db) is None  # still nothing production
    finally:
        db.close()


def _mk_artifact(tmp_path, name="m"):
    import json as _json
    import os as _os

    d = str(tmp_path / name)
    _os.makedirs(d, exist_ok=True)
    with open(_os.path.join(d, "model_info.json"), "w") as f:
        _json.dump({}, f)
    return d


def test_rollback_restores_previous_production(client, tmp_path):
    import os

    from app.models import MLPromotionEvent

    db = TestingSession()
    try:
        old_dir = _mk_artifact(tmp_path, "old")
        new_dir = _mk_artifact(tmp_path, "new")
        reg.register_model(db, version="v-old", dataset_version="d",
                           base_model="b", metrics={}, artifact_location=old_dir,
                           status="archived")
        # fabricate prior production tenure via a promotion event
        db.add(MLPromotionEvent(
            model_name="clause-classifier", model_version="v-old",
            from_status="evaluated", to_status="production",
            decided_by=None, organization_id=None,
            gates="{}", reason="earlier promotion"))
        reg.register_model(db, version="v-new", dataset_version="d",
                           base_model="b", metrics={}, artifact_location=new_dir,
                           status="production")
        db.commit()
        import uuid as _uuid

        out = promo.rollback(db, model_name="clause-classifier", version="v-old",
                             decided_by=_uuid.uuid4(), organization_id=None,
                             reason="regression in prod")
        assert out == {"rolled_back": True, "restored_version": "v-old",
                       "previous_production": "v-new"}
        assert reg.production_model(db).version == "v-old"
        assert reg.get_model(db, "clause-classifier", "v-new").status == "archived"
        ev = db.query(MLPromotionEvent).filter(
            MLPromotionEvent.model_version == "v-old").order_by(
            MLPromotionEvent.created_at.desc()).first()
        assert ev.to_status == "production" and "rollback" in (ev.reason or "")
        assert os.path.exists(os.path.join(new_dir, "model_info.json"))  # artifacts kept
        # rolling back to current production is refused
        with pytest.raises(ValueError, match="already production"):
            promo.rollback(db, model_name="clause-classifier", version="v-old",
                           decided_by=None, organization_id=None)
        # never-production target is refused
        reg.register_model(db, version="v-never", dataset_version="d",
                           base_model="b", metrics={}, artifact_location=new_dir,
                           status="evaluated")
        with pytest.raises(ValueError, match="never held production"):
            promo.rollback(db, model_name="clause-classifier", version="v-never",
                           decided_by=None, organization_id=None)
        # missing artifact is refused
        reg.register_model(db, version="v-gone", dataset_version="d",
                           base_model="b", metrics={}, artifact_location="/tmp/definitely-missing-xyz",
                           status="archived")
        db.add(MLPromotionEvent(
            model_name="clause-classifier", model_version="v-gone",
            from_status="evaluated", to_status="production",
            decided_by=None, organization_id=None, gates="{}", reason="x"))
        db.commit()
        with pytest.raises(ValueError, match="Artifact missing"):
            promo.rollback(db, model_name="clause-classifier", version="v-gone",
                           decided_by=None, organization_id=None)
        with pytest.raises(ValueError, match="not registered"):
            promo.rollback(db, model_name="clause-classifier", version="nope",
                           decided_by=None, organization_id=None)
    finally:
        db.close()
