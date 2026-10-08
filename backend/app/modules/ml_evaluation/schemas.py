"""Typed ml-evaluation schemas. Metrics payloads are plain dicts (full
reports); the API never invents numbers, only relays measured ones.
"""
from pydantic import BaseModel


class DatasetBuildIn(BaseModel):
    version: str
    seed: int = 42


class DatasetOut(BaseModel):
    id: str
    version: str
    created_from: str = "feedback"
    source_count: int = 0
    usable_count: int = 0
    excluded_count: int = 0
    artifact_path: str | None = None
    manifest: dict = {}
    stats: dict = {}
    created_at: str | None = None


class TrainIn(BaseModel):
    base_model: str = "nlpaueb/legal-bert-base-uncased"
    dataset_version: str
    learning_rate: float = 2e-5
    batch_size: int = 8
    epochs: float = 1
    seq_len: int = 128
    seed: int = 42
    class_weighting: str = "balanced"
    weight_decay: float = 0.01
    fp16: bool = True
    model_version: str


class EvaluateIn(BaseModel):
    model_version: str | None = None
    model_dir: str | None = None
    dataset_version: str
    split: str = "test"
    batch_size: int = 32


class ShadowIn(BaseModel):
    model_version: str | None = None
    model_dir: str | None = None
    texts: list[str]


class PromoteIn(BaseModel):
    reason: str = ""


class ExperimentIn(BaseModel):
    name: str
    preset: str
    dataset_version: str
    model_version: str
    seed: int = 42
    overrides: dict | None = None


class ExperimentOut(BaseModel):
    id: str
    name: str
    kind: str = "classification"
    config: dict = {}
    dataset_version: str | None = None
    seed: int = 42
    status: str
    candidate_version: str | None = None
    metrics: dict = {}
    costs: dict = {}
    mlflow_run_id: str | None = None
    error: str | None = None
    created_at: str | None = None


class MatrixIn(BaseModel):
    name: str
    dataset_version: str
    model_version_prefix: str
    cells: list[str] | None = None
    seeds: list[int] | None = None
    lr_multipliers: list[float] | None = None
    epochs_list: list[int] | None = None


class MatrixOut(BaseModel):
    matrix_id: str
    experiment_ids: list[str] = []
    n_runs: int = 0


class RollbackIn(BaseModel):
    reason: str = ""


class ModelOut(BaseModel):
    id: str
    name: str
    version: str
    task: str
    dataset_version: str | None = None
    base_model: str | None = None
    metrics: dict = {}
    artifact_location: str | None = None
    status: str
    mlflow_run_id: str | None = None
    created_at: str | None = None
