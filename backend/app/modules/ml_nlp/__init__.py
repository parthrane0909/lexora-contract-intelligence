"""Deep Learning clause classification (spec §7): dataset, training,
evaluation, inference. No LLM involvement — Transformer softmax only.
"""
from app.modules.ml_nlp.classifier import (  # noqa: F401
    EmptyClauseError,
    InferenceError,
    ModelUnavailableError,
    classify_clause,
    classify_contract,
    get_classifier,
    reset_classifier,
)
from app.modules.ml_nlp.taxonomy import CATEGORIES, MODEL_LABELS  # noqa: F401
