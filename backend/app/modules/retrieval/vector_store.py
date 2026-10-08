"""Vector-store abstraction (Phase 6.6). Routers talk to `VectorStore`;
nothing outside this file touches Qdrant APIs.

Payload schema (every point, all backends):
  org_id, object_type (clause|template_clause|policy), object_id,
  contract_id?, template_id?, template_version?, category?,
  section_number?, page_start?, text_snippet?, embedding_model,
  embedding_version, indexed_at

Org isolation = mandatory `org_id` match filter, always ANDed server-side.
Point IDs are deterministic UUID5(org, type, id, model) → idempotent upserts.

`QdrantVectorStore` is the production backend; `InMemoryVectorStore` is a
brute-force cosine backend for unit tests (no daemon required).
"""
from __future__ import annotations

import math
import os
import uuid
from typing import Protocol


class VectorStoreError(Exception):
    """Qdrant unreachable, dimension mismatch, or backend failure. Explicit,
    never silent — callers map to 503; core flows never depend on this."""


class ScoredPoint(dict):
    """{"id", "score", "payload"} — plain dict subclass for typing clarity."""


class VectorStore(Protocol):
    collection: str
    dimension: int | None

    def health_check(self) -> bool:
        ...

    def ensure_collection(self, dimension: int) -> None:
        ...

    def upsert(self, points: list[dict]) -> int:
        """points: {"id", "vector", "payload"}. Returns count written."""

    def delete(self, point_ids: list[str]) -> int:
        ...

    def delete_where(self, flt: dict) -> int:
        """Delete by exact-match payload filter (re-indexing)."""

    def search(self, vector: list[float], top_k: int, flt: dict | None = None) -> list[dict]:
        ...


def point_id(org_id: str, object_type: str, object_id: str, embedding_model: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL,
                          f"{org_id}:{object_type}:{object_id}:{embedding_model}"))


def org_filter(org_id: str, extra: dict | None = None) -> dict:
    """Org match is ALWAYS present; extra exact-match filters are ANDed."""
    flt = {"org_id": org_id}
    if extra:
        flt.update(extra)
    return flt


def default_collection() -> str:
    from app.core.config import get_settings

    return os.environ.get("QDRANT_COLLECTION") or get_settings().QDRANT_COLLECTION


def default_qdrant_url() -> str:
    from app.core.config import get_settings

    return os.environ.get("QDRANT_URL") or get_settings().QDRANT_URL


def default_api_key() -> str | None:
    from app.core.config import get_settings

    return os.environ.get("QDRANT_API_KEY") or get_settings().QDRANT_API_KEY or None


class QdrantVectorStore:
    """Production backend over qdrant-client (cosine distance)."""

    def __init__(self, collection: str | None = None, url: str | None = None,
                 api_key: str | None = None, timeout: int = 10):
        self.collection = collection or default_collection()
        self.dimension: int | None = None
        try:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=url or default_qdrant_url(),
                                       api_key=api_key or default_api_key(),
                                       timeout=timeout)
            self._client.get_collections()  # fail fast if unreachable
        except Exception as e:
            raise VectorStoreError(f"Qdrant unreachable: {e}") from e

    def health_check(self) -> bool:
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    def ensure_collection(self, dimension: int) -> None:
        from qdrant_client.http import models as qm

        try:
            if self._client.collection_exists(self.collection):
                info = self._client.get_collection(self.collection)
                actual = info.config.params.vectors.size
                if actual != dimension:
                    raise VectorStoreError(
                        f"Collection {self.collection!r} has dim {actual}, "
                        f"model needs {dimension}: refusing to mix embedding models.")
                self.dimension = actual
                return
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=qm.VectorParams(size=dimension, distance=qm.Distance.COSINE))
            for field in ("org_id", "object_type", "contract_id", "template_id", "category"):
                try:
                    self._client.create_payload_index(
                        collection_name=self.collection,
                        field_name=field, field_schema=qm.PayloadSchemaType.KEYWORD)
                except Exception:
                    pass  # index exists / older server — non-fatal
            self.dimension = dimension
        except VectorStoreError:
            raise
        except Exception as e:
            raise VectorStoreError(f"Qdrant ensure_collection failed: {e}") from e

    @staticmethod
    def _qfilter(flt: dict | None):
        if not flt:
            return None
        from qdrant_client.http import models as qm

        return qm.Filter(must=[qm.FieldCondition(key=k, match=qm.MatchValue(value=v))
                              for k, v in flt.items() if v is not None])

    def upsert(self, points: list[dict]) -> int:
        from qdrant_client.http import models as qm

        try:
            self._client.upsert(collection_name=self.collection, points=[
                qm.PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
                for p in points])
            return len(points)
        except Exception as e:
            raise VectorStoreError(f"Qdrant upsert failed: {e}") from e

    def delete(self, point_ids: list[str]) -> int:
        from qdrant_client.http import models as qm

        try:
            self._client.delete(collection_name=self.collection,
                                points_selector=qm.PointIdsList(points=point_ids))
            return len(point_ids)
        except Exception as e:
            raise VectorStoreError(f"Qdrant delete failed: {e}") from e

    def delete_where(self, flt: dict) -> int:
        from qdrant_client.http import models as qm

        try:
            self._client.delete(collection_name=self.collection,
                                points_selector=qm.FilterSelector(filter=self._qfilter(flt)))
            return -1  # server-side count not returned; callers re-query state
        except Exception as e:
            raise VectorStoreError(f"Qdrant delete_where failed: {e}") from e

    def search(self, vector: list[float], top_k: int, flt: dict | None = None) -> list[dict]:
        try:
            hits = self._client.query_points(
                collection_name=self.collection, query=vector,
                query_filter=self._qfilter(flt), limit=top_k, with_payload=True).points
            return [{"id": str(h.id), "score": float(h.score), "payload": dict(h.payload or {})}
                    for h in hits]
        except Exception as e:
            raise VectorStoreError(f"Qdrant search failed: {e}") from e


class InMemoryVectorStore:
    """Brute-force cosine backend (unit tests; same filter semantics)."""

    def __init__(self, collection: str = "test", dimension: int | None = None):
        self.collection = collection
        self.dimension = dimension
        self._points: dict[str, dict] = {}

    def health_check(self) -> bool:
        return True

    def ensure_collection(self, dimension: int) -> None:
        if self.dimension is not None and self.dimension != dimension:
            raise VectorStoreError(
                f"In-memory collection dim {self.dimension} vs model dim {dimension}.")
        self.dimension = dimension

    def upsert(self, points: list[dict]) -> int:
        for p in points:
            if self.dimension is not None and len(p["vector"]) != self.dimension:
                raise VectorStoreError("Vector dimension mismatch on upsert.")
            self._points[p["id"]] = p
        return len(points)

    def delete(self, point_ids: list[str]) -> int:
        n = sum(1 for pid in point_ids if self._points.pop(pid, None) is not None)
        return n

    def delete_where(self, flt: dict) -> int:
        goners = [pid for pid, p in self._points.items()
                  if all(p["payload"].get(k) == v for k, v in flt.items())]
        for pid in goners:
            del self._points[pid]
        return len(goners)

    def search(self, vector: list[float], top_k: int, flt: dict | None = None) -> list[dict]:
        def cos(a: list[float], b: list[float]) -> float:
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return dot / (na * nb)

        scored = []
        for pid, p in self._points.items():
            if flt and any(p["payload"].get(k) != v for k, v in flt.items() if v is not None):
                continue
            scored.append({"id": pid, "score": cos(vector, p["vector"]), "payload": p["payload"]})
        scored.sort(key=lambda h: h["score"], reverse=True)
        return scored[:top_k]
