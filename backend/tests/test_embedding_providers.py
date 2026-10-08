"""Provider units: configuration, errors, determinism (hermetic)."""
import os

import pytest


@pytest.fixture(autouse=True)
def _hash_backend(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    from app.modules.retrieval.providers import reset_provider

    reset_provider()
    yield
    reset_provider()


def test_hash_provider_contract():
    from app.modules.retrieval.providers import get_provider

    p = get_provider()
    assert p.model_name == "hash-test" and p.dimension == 128
    v1 = p.embed_text("Supplier liability is capped.")
    v2 = p.embed_text("Supplier liability is capped.")
    assert v1 == v2  # deterministic
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-6  # L2-normalized
    batch = p.embed_batch(["alpha beta gamma", "delta epsilon zeta"])
    assert len(batch) == 2 and batch[0] != batch[1]


def test_empty_text_rejected():
    from app.modules.retrieval.providers import EmptyTextError, get_provider

    p = get_provider()
    with pytest.raises(EmptyTextError):
        p.embed_text("   ")
    with pytest.raises(EmptyTextError):
        p.embed_batch(["fine text here", "  "])


def test_provider_singleton_and_reset():
    from app.modules.retrieval import providers

    a = providers.get_provider()
    assert providers.get_provider() is a
    providers.reset_provider()
    assert providers.get_provider() is not a


def test_default_model_configured_not_hardcoded():
    from app.core.config import get_settings

    s = get_settings()
    assert "MiniLM" in s.EMBEDDING_MODEL and s.QDRANT_COLLECTION == "semantic_objects"
    assert 0 < s.SEMANTIC_MATCH_THRESHOLD < s.SEMANTIC_STRONG_THRESHOLD <= 1.0
    # env override path (no download attempted — constructor not called)
    os.environ["EMBEDDING_MODEL"] = "custom/model"
    try:
        from app.modules.retrieval.providers import default_model_name

        assert default_model_name() == "custom/model"
    finally:
        del os.environ["EMBEDDING_MODEL"]
