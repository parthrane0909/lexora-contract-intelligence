"""Embedding abstraction (Phase 6.4).

`EmbeddingProvider` protocol: model_name / model_version / dimension /
embed_text / embed_batch. Model choice comes from settings, never hard-coded
at call sites. Default is a small local Sentence Transformers model; the
Legal-BERT classifier is deliberately NOT reused (different task, different
geometry — classification logits are not retrieval embeddings).

`HashEmbeddingProvider` is a deterministic, dependency-free fallback used
ONLY in unit tests (never the default) so the suite stays hermetic.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import Protocol


class EmbeddingUnavailableError(Exception):
    """The embedding model could not be loaded or used."""


class EmptyTextError(Exception):
    """Nothing to embed (empty/whitespace-only input)."""


class EmbeddingProvider(Protocol):
    model_name: str
    model_version: str
    dimension: int

    def embed_text(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


def default_model_name() -> str:
    from app.core.config import get_settings

    return os.environ.get("EMBEDDING_MODEL") or get_settings().EMBEDDING_MODEL


def default_model_version() -> str:
    from app.core.config import get_settings

    return os.environ.get("EMBEDDING_VERSION") or get_settings().EMBEDDING_VERSION


class SentenceTransformerProvider:
    """Local Sentence Transformers model (all-MiniLM-L6-v2 default, 384-dim)."""

    def __init__(self, model_name: str | None = None, model_version: str | None = None):
        self.model_name = model_name or default_model_name()
        self.model_version = model_version or default_model_version()
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        except Exception as e:
            raise EmbeddingUnavailableError(
                f"Could not load embedding model {self.model_name!r}: {e}") from e
        self.dimension = int(self._model.get_sentence_embedding_dimension())

    def embed_text(self, text: str) -> list[float]:
        if not (text or "").strip():
            raise EmptyTextError("Cannot embed empty text.")
        try:
            return [float(x) for x in self._model.encode(text, normalize_embeddings=True)]
        except Exception as e:
            raise EmbeddingUnavailableError(f"Embedding failed: {e}") from e

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if any(not (t or "").strip() for t in texts):
            raise EmptyTextError("Batch contains empty text; filter before embedding.")
        try:
            vecs = self._model.encode(texts, normalize_embeddings=True, batch_size=32)
            return [[float(x) for x in v] for v in vecs]
        except Exception as e:
            raise EmbeddingUnavailableError(f"Batch embedding failed: {e}") from e


class HashEmbeddingProvider:
    """Deterministic hashed char-trigram embeddings (TESTS ONLY).

    Cosine similarity here reflects surface overlap, not semantics — good
    enough to exercise indexing/search/isolation plumbing hermetically.
    Never used in production paths.
    """

    model_name = "hash-test"
    model_version = "test-v0"
    dimension = 128

    @staticmethod
    def _vec(text: str) -> list[float]:
        vec = [0.0] * HashEmbeddingProvider.dimension
        toks = re.findall(r"[a-z0-9]{3,}", text.lower())
        grams = set()
        for t in toks:
            grams.add(t)
            grams.update(t[i:i + 3] for i in range(len(t) - 2))
        for g in grams:
            vec[int(hashlib.md5(g.encode()).hexdigest(), 16) % len(vec)] += 1.0
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / n for x in vec]

    def embed_text(self, text: str) -> list[float]:
        if not (text or "").strip():
            raise EmptyTextError("Cannot embed empty text.")
        return self._vec(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if any(not (t or "").strip() for t in texts):
            raise EmptyTextError("Batch contains empty text; filter before embedding.")
        return [self._vec(t) for t in texts]


_PROVIDER: EmbeddingProvider | None = None
_PROVIDER_KEY: tuple | None = None


def get_provider(model_name: str | None = None) -> EmbeddingProvider:
    """Process-wide singleton per model name (same pattern as the classifier).

    Tests select the hermetic backend with EMBEDDING_PROVIDER=hash.
    """
    global _PROVIDER, _PROVIDER_KEY
    backend = os.environ.get("EMBEDDING_PROVIDER", "sentence-transformers")
    key = (backend, model_name or default_model_name())
    if _PROVIDER is None or _PROVIDER_KEY != key:
        if backend == "hash":
            _PROVIDER = HashEmbeddingProvider()
        else:
            _PROVIDER = SentenceTransformerProvider(model_name)
        _PROVIDER_KEY = key
    return _PROVIDER


def reset_provider() -> None:
    """Test hook."""
    global _PROVIDER, _PROVIDER_KEY
    _PROVIDER, _PROVIDER_KEY = None, None
