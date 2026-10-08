"""Tiny-model fixtures + inference unit tests (no downloads beyond a 230KB vocab).

A randomly-initialized 2-layer BERT with a 3-label head exercises the whole
inference path: load → tokenize → softmax → persist-shaped output, plus every
explicit failure mode. Real-model quality is covered by evaluate.py metrics,
not mocked here.
"""
import json

import pytest

from tiny_model import TINY_LABELS, build_tiny_model


@pytest.fixture(scope="module")
def tiny_model_dir(tmp_path_factory):
    return build_tiny_model(str(tmp_path_factory.mktemp("tiny-clf")), TINY_LABELS)


def test_classify_clause_structure(tiny_model_dir):
    from app.modules.ml_nlp.classifier import classify_clause, reset_classifier

    reset_classifier()
    out = classify_clause("Pay within 30 days of invoice.", model_dir=tiny_model_dir)
    assert out["category"] in TINY_LABELS
    assert 0.0 <= out["confidence"] <= 1.0
    assert abs(sum(out["probabilities"].values()) - 1.0) < 1e-4
    assert set(out["probabilities"]) == set(TINY_LABELS)
    assert out["model_version"] == "tiny-v0"
    reset_classifier()


def test_empty_clause_explicit():
    from app.modules.ml_nlp.classifier import EmptyClauseError, classify_clause

    with pytest.raises(EmptyClauseError):
        classify_clause("   ", model_dir="/nonexistent")


def test_missing_model_explicit(tmp_path):
    from app.modules.ml_nlp.classifier import ModelUnavailableError, load_model_bundle

    with pytest.raises(ModelUnavailableError):
        load_model_bundle(str(tmp_path))


def test_corrupt_bundle_explicit(tmp_path):
    from app.modules.ml_nlp.classifier import ModelUnavailableError, load_model_bundle

    json.dump({"labels": ["a"]}, open(tmp_path / "label_map.json", "w"))
    # model_info.json absent → explicit, not a cryptic stack trace
    with pytest.raises(ModelUnavailableError):
        load_model_bundle(str(tmp_path))
