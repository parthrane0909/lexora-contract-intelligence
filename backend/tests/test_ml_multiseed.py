"""Phase 14: multi-seed aggregation, paired stats, matrix, selection, card."""
import pytest

from app.modules.ml_evaluation import multi_seed as ms
from app.modules.ml_evaluation import statistical as st


def _rep(macro, per=None, acc=0.8):
    return {"summary": {"accuracy": acc, "micro": {"f1": acc},
                        "macro": {"f1": macro}, "weighted": {"f1": acc}},
            "per_class": {k: {"f1": v} for k, v in (per or {}).items()}}


def test_aggregate_runs_describe():
    out = ms.aggregate_runs([
        {"seed": 42, "summary": {"accuracy": 0.80, "macro": {"f1": 0.70},
                                 "micro": {"f1": 0.80}, "weighted": {"f1": 0.78}},
         "per_class": {"payment": {"f1": 0.90}}},
        {"seed": 43, "summary": {"accuracy": 0.82, "macro": {"f1": 0.74},
                                 "micro": {"f1": 0.82}, "weighted": {"f1": 0.80}},
         "per_class": {"payment": {"f1": 0.70}}},
    ])
    assert out["n_runs"] == 2 and out["seeds"] == [42, 43]
    assert out["macro"] == {"mean": 0.72, "std": 0.02, "min": 0.70,
                            "max": 0.74, "n": 2}
    assert out["per_class"]["payment"]["n"] == 2
    with pytest.raises(ValueError, match="No runs"):
        ms.aggregate_runs([])


def test_paired_compare_ci_and_guards():
    base = {s: _rep(0.70 if s != 44 else 0.69) for s in (42, 43, 44, 45, 46)}
    cand = {s: _rep(0.72 if s != 44 else 0.715) for s in (42, 43, 44, 45, 46)}
    # diffs: .02, .02, .025, .02, .02 -> mean .021
    out = st.paired_compare(base, cand, scope="macro")
    assert out["n"] == 5 and out["seeds"] == [42, 43, 44, 45, 46]
    assert out["mean_difference"] == 0.021
    assert out["min_improvement"] == 0.02 and out["max_improvement"] == 0.025
    lo, hi = out["ci95"]
    assert lo < 0.021 < hi and out["ci_excludes_zero"] is True
    assert "paired t-interval" in out["method"]
    with pytest.raises(ValueError, match=">=2 shared seeds"):
        st.paired_compare({42: _rep(0.7)}, {43: _rep(0.7)})
    with pytest.raises(ValueError, match="Missing macro metric"):
        st.paired_compare({42: {"summary": {}}, 43: {"summary": {}}},
                          {42: _rep(0.7), 43: _rep(0.7)})


def test_matrix_build_and_validation():
    from app.modules.ml_evaluation import experiment_matrix as mx

    assert mx.MATRIX_CELLS == {"A": "baseline_repro", "B": "seq256_weighted",
                               "C": "unweighted", "D": "seq256_unweighted"}
    runs = mx.build_matrix(cells=["A", "D"], seeds=[42, 43])
    assert len(runs) == 4
    assert runs[0] == {"cell": "A", "preset": "baseline_repro", "seed": 42,
                       "overrides": {}}
    lr_runs = mx.build_matrix(cells=["B"], seeds=[42], lr_multipliers=[0.5, 1.5])
    assert len(lr_runs) == 3  # base B run + 2 sensitivity variants
    assert lr_runs[0]["overrides"] == {}
    assert lr_runs[1]["overrides"] == {"__lr_mult__": 0.5}
    ep_runs = mx.build_matrix(cells=["B"], seeds=[42], epochs_list=[1, 2])
    assert [r["overrides"] for r in ep_runs] == [{}, {"epochs": 1}, {"epochs": 2}]
    with pytest.raises(ValueError, match="Unknown matrix"):
        mx.build_matrix(cells=["Z"])
    with pytest.raises(ValueError, match="positive"):
        mx.build_matrix(lr_multipliers=[0])
    with pytest.raises(ValueError, match=">= 1"):
        mx.build_matrix(epochs_list=[0])


def test_run_matrix_stubbed_cores(client, monkeypatch):
    from app.modules.ml_evaluation import experiment_matrix as mx_mod

    calls = []

    def fake_run(db, eid, **kwargs):
        from app.models import MLExperiment

        calls.append(kwargs.get("eval_split"))
        exp = db.get(MLExperiment, eid)
        exp.status = "completed"
        db.commit()
        return {"experiment_id": str(eid), "status": "completed"}

    from conftest import TestingSession, auth, register

    token = register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        u = db.query(User).filter(User.email == "a@x.com").one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
    finally:
        db.close()
    out = mx_mod.run_matrix(
        db if False else TestingSession(), matrix_id="mx-test-1", name="m",
        organization_id=org_id, dataset_version="clause_v1",
        model_version_prefix="mxv1", cells=["A"], seeds=[42, 43],
        created_by=None, _run=fake_run)
    assert out["matrix_id"] == "mx-test-1" and out["n_runs"] == 2
    assert calls == ["validation", "validation"]  # frozen-test discipline
    db = TestingSession()
    try:
        from app.models import MLExperiment

        rows = db.query(MLExperiment).filter(
            MLExperiment.matrix_id == "mx-test-1").all()
        assert len(rows) == 2 and all(r.status == "completed" for r in rows)
    finally:
        db.close()


def test_run_multi_seed_delegates():
    import uuid as _uuid

    import app.modules.ml_evaluation.experiment_matrix as _mx

    seen = {}
    orig = _mx.run_matrix
    try:
        _mx.run_matrix = lambda db, **kw: (seen.update(kw), {"ok": True})[1]
        _mx.run_multi_seed(None, matrix_id="mx-ms", name="m",
                           organization_id=_uuid.uuid4(), preset="seq256_weighted",
                           dataset_version="d", model_version_prefix="p",
                           seeds=[42])
    finally:
        _mx.run_matrix = orig
    assert seen["cells"] == ["B"] and seen["seeds"] == [42]
    assert seen["matrix_id"] == "mx-ms"


def test_final_selection_priority_and_reasons():
    from app.modules.ml_evaluation import final_selection as fs

    cands = [
        {"version": "v-high-test", "val_macro_f1": 0.90, "leakage_ok": False,
         "protected": {}, "stability": {"std_macro_f1": 0.01}},
        {"version": "v-stable", "val_macro_f1": 0.80, "leakage_ok": True,
         "protected": {}, "stability": {"std_macro_f1": 0.005}},
        {"version": "v-leak", "val_macro_f1": 0.99, "leakage_ok": False,
         "protected": {}},
    ]
    out = fs.select_finalist(cands)
    assert out["selected_version"] == "v-stable"  # not highest-test trap; leakage kills v-high
    assert any("leakage" in r for r in
               next(x["reasons"] for x in out["rejected"] if x["version"] == "v-leak"))
    # protected regression rejects even with best macro
    cands2 = [
        {"version": "v-reg", "val_macro_f1": 0.85, "leakage_ok": True,
         "protected": {"renewal": {"ok": False}}},
        {"version": "v-safe", "val_macro_f1": 0.80, "leakage_ok": True,
         "protected": {"renewal": {"ok": True}}},
    ]
    out2 = fs.select_finalist(cands2)
    assert out2["selected_version"] == "v-safe"
    with pytest.raises(ValueError, match="No candidates"):
        fs.select_finalist([])


def test_model_card_write_and_frozen(tmp_path):
    from app.modules.ml_evaluation import final_selection as fs

    card = fs.build_model_card(model_version="m1")
    assert "does not constitute" in card["legal_disclaimer"]
    assert card["known_weaknesses"] and card["model"]["version"] == "m1"
    p = fs.write_model_card(str(tmp_path), card)
    assert p.endswith("model_card.json")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        fs.write_model_card(str(tmp_path), card)


def test_search_sensitivity_helpers():
    from app.modules.ml_evaluation import search as srch

    lr = srch.summarize_lr_sensitivity([
        {"lr": 2e-5, "lr_multiplier": 1, "report": {"summary": {"macro": {"f1": 0.7}}}},
        {"lr": 1e-5, "lr_multiplier": 0.5, "report": {"summary": {"macro": {"f1": 0.69}}}},
    ])
    assert lr["baseline_lr"] == 2e-5 and len(lr["rows"]) == 2
    ep = srch.summarize_epoch_sensitivity([
        {"epochs": 1, "report": {"summary": {"macro": {"f1": 0.6}}}},
        {"epochs": 2, "report": {"summary": {"macro": {"f1": 0.65}}}},
    ])
    assert ep["monotonic_gain"] is True
    ep2 = srch.summarize_epoch_sensitivity([
        {"epochs": 1, "report": {"summary": {"macro": {"f1": 0.65}}}},
    ])
    assert ep2["monotonic_gain"] is None


def test_eval_split_parameterized_and_invalid(client):
    import app.modules.ml_evaluation.experiments as exp_mod

    from conftest import TestingSession, auth, register

    token = register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        u = db.query(User).filter(User.email == "a@x.com").one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
        exp = exp_mod.register_experiment(
            db, organization_id=org_id, name="splittest", preset="seq256",
            dataset_version="clause_v1", model_version="splittest-v1", seed=42)
        eid = exp.id
    finally:
        db.close()

    def fake_train(cfg, data_dir, out_dir, mlflow_uri):
        import os

        os.makedirs(out_dir, exist_ok=True)
        return {"model_version": cfg.model_version, "artifact_dir": out_dir,
                "val_metrics": {}, "mlflow_run_id": "r", "duration_s": 0.1}

    def fake_eval(db, **kwargs):
        seen_eval.update(kwargs)
        return {"evaluation_id": "e1",
                "report": {"summary": {"accuracy": 0.8,
                                       "macro": {"f1": 0.7},
                                       "weighted": {"f1": 0.75},
                                       "micro": {"f1": 0.8}, "n": 10},
                           "per_class": {}, "confusion": {},
                           "calibration": {}, "errors": {}, "truncation": {}}}

    seen_eval = {}
    db = TestingSession()
    try:
        from app.models import MLDataset

        from app.modules.ml_evaluation import registry as reg

        reg.register_model(db, version="prod-s14", dataset_version="clause_v1",
                           base_model="b", metrics={}, artifact_location="/tmp/p",
                           status="production")
        db.add(MLDataset(organization_id=org_id, version="clause_v1",
                         created_from="test", source_count=1, usable_count=1,
                         excluded_count=0, manifest="{}",
                         artifact_path="/tmp/ds"))
        db.commit()
        import app.modules.ml_evaluation.service as svc_mod

        orig_train, orig_eval = exp_mod.train_candidate, svc_mod.evaluate_bundle
        exp_mod.train_candidate = fake_train
        svc_mod.evaluate_bundle = fake_eval
        try:
            out = exp_mod.run_experiment(db, eid, eval_split="validation")
        finally:
            exp_mod.train_candidate = orig_train
            svc_mod.evaluate_bundle = orig_eval
    finally:
        db.close()
    assert out["eval_split"] == "validation"
    assert seen_eval.get("split") == "validation"  # tuning never touches test
    with pytest.raises(ValueError, match="Unknown eval_split"):
        db = TestingSession()
        try:
            exp_mod.run_experiment(db, eid, eval_split="bogus")
        finally:
            db.close()


def test_matrix_id_model_and_migration_consistency(client):
    """Phase 14 §3: MLExperiment.matrix_id persists, groups runs, and the
    0014 migration chain (0013 -> 0014) resolves the schema mismatch."""
    from app.models import MLExperiment
    from conftest import TestingSession, register

    register(client, "a@x.com", "password123", "OrgA")
    db = TestingSession()
    try:
        from app.models import OrganizationMember, User

        u = db.query(User).filter(User.email == "a@x.com").one()
        org_id = db.query(OrganizationMember).filter(
            OrganizationMember.user_id == u.id).one().organization_id
        db.add(MLExperiment(
            organization_id=org_id, name="mx-row-1", kind="classification",
            config="{}", dataset_version="v", seed=42,
            status="registered", matrix_id="mx-persist"))
        db.add(MLExperiment(
            organization_id=org_id, name="mx-row-2", kind="classification",
            config="{}", dataset_version="v", seed=43,
            status="registered", matrix_id="mx-persist"))
        db.add(MLExperiment(
            organization_id=org_id, name="no-matrix", kind="classification",
            config="{}", dataset_version="v", seed=42,
            status="registered", matrix_id=None))
        db.commit()
        grouped = db.query(MLExperiment).filter(
            MLExperiment.matrix_id == "mx-persist").all()
        assert sorted(r.name for r in grouped) == ["mx-row-1", "mx-row-2"]
        assert db.query(MLExperiment).filter(
            MLExperiment.matrix_id.is_(None)).count() >= 1
    finally:
        db.close()
    # migration file resolves exactly the 0013 gap, nothing more
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "m14", "alembic/versions/0014_phase14_matrix_id.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0014_phase14_matrix_id"
    assert mod.down_revision == "0013_phase13_experiments"
    assert hasattr(mod, "upgrade") and hasattr(mod, "downgrade")
