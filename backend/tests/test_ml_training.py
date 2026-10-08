"""Training config + orchestration tests (stubbed heavy core — no GPUs)."""
import pytest

from app.modules.ml_evaluation.training import (
    CandidateConfig,
    config_fingerprint,
    train_candidate,
)
from app.modules.ml_evaluation.training_configs import PRESETS, diff_configs, resolve_config


def test_presets_resolve_and_validate():
    for preset in ("baseline_repro", "seq256", "seq512", "unweighted", "seq256_weighted"):
        cfg = resolve_config(preset, "ds-v1", "cand-v1")
        assert cfg.dataset_version == "ds-v1" and cfg.model_version == "cand-v1"
    with pytest.raises(ValueError, match="Unknown preset"):
        resolve_config("nope", "d", "m")
    with pytest.raises(ValueError, match="arguments"):
        resolve_config("seq256", "d", "m", overrides={"model_version": "x"})
    bad = resolve_config("seq256", "d", "m")
    bad.learning_rate = 5.0
    with pytest.raises(ValueError, match="learning_rate"):
        bad.validate()
    bad2 = resolve_config("seq256", "d", "m")
    bad2.seq_len = 3
    with pytest.raises(ValueError, match="seq_len"):
        bad2.validate()
    bad3 = resolve_config("seq256", "d", "m")
    bad3.class_weighting = "sometimes"
    with pytest.raises(ValueError, match="class_weighting"):
        bad3.validate()


def test_config_diff_shows_only_changes():
    a = resolve_config("baseline_repro", "d", "m1")
    b = resolve_config("seq256", "d", "m2")
    d = diff_configs(a, b)
    assert set(d) == {"seq_len", "model_version"}
    assert d["seq_len"] == {"baseline": 128, "candidate": 256}


def test_fingerprint_stable_and_sensitive():
    a = resolve_config("seq256", "d", "m")
    b = resolve_config("seq256", "d", "m")
    assert config_fingerprint(a) == config_fingerprint(b)
    c = resolve_config("seq256", "d", "m", overrides={"seed": 7})
    assert config_fingerprint(c) != config_fingerprint(a)


def test_train_candidate_orchestration_stubbed(tmp_path):
    calls = {}

    def fake_fit(cfg, data_dir, out_dir, mlflow_uri):
        calls["cfg"] = cfg
        calls["mlflow_uri"] = mlflow_uri
        import os

        os.makedirs(out_dir, exist_ok=True)
        return {"model_version": cfg.model_version, "artifact_dir": out_dir,
                "val_metrics": {"macro_f1": 0.5}, "mlflow_run_id": "run-1"}

    cfg = resolve_config("unweighted", "ds-x", "stub-v1")
    res = train_candidate(cfg, "/tmp/data", str(tmp_path / "out"),
                          "mlflow-uri-x", _fit=fake_fit)
    assert res["model_version"] == "stub-v1"
    assert res["config_fingerprint"] == config_fingerprint(cfg)
    assert calls["mlflow_uri"] == "mlflow-uri-x"
    assert calls["cfg"].class_weighting == "none"
    assert res["duration_s"] >= 0
