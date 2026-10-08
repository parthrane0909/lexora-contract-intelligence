"""Experiment matrix runner (Phase 14.5/14.7). Configs × seeds executed
through the standard single-experiment runner, so frozen-test discipline,
MLflow logging, and registry writes are inherited — never reimplemented.

Matrix cells:
  A: seq128 + weighted (baseline_repro)      — production recipe
  B: seq256 + weighted (seq256_weighted)     — context experiment
  C: seq128 + unweighted (unweighted)        — weighting ablation
  D: seq256 + unweighted (seq256_unweighted) — interaction cell
Optional sensitivity cells append LR/epoch overrides. The runner executes
with eval_split="validation" ALWAYS (selection never touches the frozen
test set); final_selection re-evaluates the winner once on test.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

# cell -> preset name in training_configs.PRESETS
MATRIX_CELLS: dict[str, str] = {
    "A": "baseline_repro",
    "B": "seq256_weighted",
    "C": "unweighted",
    "D": "seq256_unweighted",
}

DEFAULT_SEEDS = (42, 43, 44, 45, 46)


def build_matrix(cells: list[str] | None = None,
                 seeds: list[int] | None = None,
                 lr_multipliers: list[float] | None = None,
                 epochs_list: list[int] | None = None) -> list[dict]:
    """Return the explicit list of {cell, preset, seed, overrides} runs."""
    from app.modules.ml_evaluation.training_configs import PRESETS

    cells = cells or ["A", "B", "C", "D"]
    seeds = seeds or list(DEFAULT_SEEDS)
    unknown = [c for c in cells if c not in MATRIX_CELLS and c not in PRESETS]
    if unknown:
        raise ValueError(f"Unknown matrix cells/presets: {unknown}.")
    runs = []
    for cell in cells:
        preset = MATRIX_CELLS.get(cell, cell)
        for seed in seeds:
            runs.append({"cell": cell, "preset": preset, "seed": int(seed),
                         "overrides": {}})
    for mult in lr_multipliers or []:
        if mult <= 0:
            raise ValueError(f"LR multiplier must be positive, got {mult}.")
        for seed in seeds:
            runs.append({"cell": f"lr x{mult}", "preset": "seq256_weighted",
                         "seed": int(seed),
                         "overrides": {"__lr_mult__": mult}})
    for ep in epochs_list or []:
        if ep < 1:
            raise ValueError(f"epochs must be >= 1, got {ep}.")
        for seed in seeds:
            runs.append({"cell": f"epochs={ep}", "preset": "seq256_weighted",
                         "seed": int(seed), "overrides": {"epochs": ep}})
    return runs


def run_matrix(db: Session, *, matrix_id: str, name: str,
               organization_id: uuid.UUID | None,
               dataset_version: str, model_version_prefix: str,
               cells: list[str] | None = None,
               seeds: list[int] | None = None,
               lr_multipliers: list[float] | None = None,
               epochs_list: list[int] | None = None,
               created_by: uuid.UUID | None = None,
               _run=None) -> dict:
    """Register + execute every matrix cell on the VALIDATION split.
    Returns {matrix_id, experiment_ids, n_runs}."""
    from app.modules.ml_evaluation import experiments as exp_mod
    from app.modules.ml_evaluation.training_configs import PRESETS

    runs = build_matrix(cells, seeds, lr_multipliers, epochs_list)
    run = _run or exp_mod.run_experiment
    ids = []
    for i, spec in enumerate(runs):
        overrides = dict(spec["overrides"])
        mult = overrides.pop("__lr_mult__", None)
        if mult is not None:
            base_lr = PRESETS[spec["preset"]]["learning_rate"]
            overrides["learning_rate"] = base_lr * mult
        exp = exp_mod.register_experiment(
            db, organization_id=organization_id,
            name=f"{name}[{spec['cell']}/seed={spec['seed']}]",
            preset=spec["preset"], dataset_version=dataset_version,
            model_version=f"{model_version_prefix}-{spec['cell']}-s{spec['seed']}",
            seed=spec["seed"], overrides=overrides or None,
            created_by=created_by)
        # group + force validation split (frozen-test discipline)
        exp.matrix_id = matrix_id
        db.commit()
        run(db, exp.id, eval_split="validation")
        ids.append(str(exp.id))
    return {"matrix_id": matrix_id, "experiment_ids": ids, "n_runs": len(ids)}


def run_multi_seed(db: Session, *, matrix_id: str, name: str,
                   organization_id: uuid.UUID | None, preset: str,
                   dataset_version: str, model_version_prefix: str,
                   seeds: list[int] | None = None,
                   created_by: uuid.UUID | None = None,
                   _run=None) -> dict:
    """One configuration across seeds (the core §8 procedure). Thin wrapper
    over run_matrix restricted to a single preset."""
    cell = next((c for c, p in MATRIX_CELLS.items() if p == preset), preset)
    return run_matrix(db, matrix_id=matrix_id, name=name,
                      organization_id=organization_id,
                      dataset_version=dataset_version,
                      model_version_prefix=model_version_prefix,
                      cells=[cell], seeds=seeds, created_by=created_by,
                      _run=_run)
