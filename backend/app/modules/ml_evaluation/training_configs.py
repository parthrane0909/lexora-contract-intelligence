"""Named training configurations (Phase 13). Presets are data, validated
through CandidateConfig — no second training framework. Cost notes are
measured expectations from this repo's GPU, not promises.
"""
from __future__ import annotations

from app.modules.ml_evaluation.training import CandidateConfig

BASE_MODEL = "nlpaueb/legal-bert-base-uncased"

PRESETS: dict[str, dict] = {
    # Exact reproduction of the production recipe (same data version/seed).
    "baseline_repro": {
        "base_model": BASE_MODEL, "learning_rate": 2e-5, "batch_size": 8,
        "epochs": 3, "seq_len": 128, "seed": 42, "class_weighting": "balanced",
    },
    # Truncation experiments: identical except sequence length.
    "seq256": {
        "base_model": BASE_MODEL, "learning_rate": 2e-5, "batch_size": 8,
        "epochs": 3, "seq_len": 256, "seed": 42, "class_weighting": "balanced",
    },
    "seq512": {
        "base_model": BASE_MODEL, "learning_rate": 2e-5, "batch_size": 4,
        "epochs": 3, "seq_len": 512, "seed": 42, "class_weighting": "balanced",
    },
    # Class-weighting ablation: identical except weighting off.
    "unweighted": {
        "base_model": BASE_MODEL, "learning_rate": 2e-5, "batch_size": 8,
        "epochs": 3, "seq_len": 128, "seed": 42, "class_weighting": "none",
    },
    # Combined: longer context + weighting (the actual improvement attempt).
    "seq256_weighted": {
        "base_model": BASE_MODEL, "learning_rate": 2e-5, "batch_size": 8,
        "epochs": 3, "seq_len": 256, "seed": 42, "class_weighting": "balanced",
    },
    # Phase 14 matrix: seq256 without class weights (isolates weighting effect).
    "seq256_unweighted": {
        "base_model": BASE_MODEL, "learning_rate": 2e-5, "batch_size": 8,
        "epochs": 3, "seq_len": 256, "seed": 42, "class_weighting": "none",
    },
}


def resolve_config(preset: str, dataset_version: str, model_version: str,
                   overrides: dict | None = None) -> CandidateConfig:
    """Build a validated CandidateConfig from a preset + required identity."""
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset {preset!r}. Available: {sorted(PRESETS)}")
    params = {**PRESETS[preset], **(overrides or {})}
    if "dataset_version" in params or "model_version" in params:
        raise ValueError("dataset_version/model_version come from arguments, "
                         "not overrides.")
    return CandidateConfig(dataset_version=dataset_version,
                           model_version=model_version, **params).validate()


def diff_configs(a: CandidateConfig, b: CandidateConfig) -> dict:
    """Field-level diff for experiment reports (what actually changed)."""
    from dataclasses import asdict

    da, db = asdict(a), asdict(b)
    return {k: {"baseline": da[k], "candidate": db[k]}
            for k in da if da[k] != db[k]}
