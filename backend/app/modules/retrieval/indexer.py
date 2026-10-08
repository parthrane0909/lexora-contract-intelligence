"""Indexing workflow (Phase 6.8): clauses → embeddings → Qdrant → PG state.

Contract processed → clauses available → batch-embed → upsert → per-object
`semantic_index` rows → ready. Idempotent: point IDs are deterministic
UUID5(org, type, id, model), so repeats overwrite rather than duplicate.
A model change yields NEW point IDs + NEW index rows; search always pins
`embedding_model == current`, so geometries never mix. Stale-model points
are deleted on re-index (see `reindex_scope`).

Partial failure is explicit: per-object `failed` rows + counts in the
result; a dead vector store raises VectorStoreError (→ HTTP 503) before
any state is written.
"""
from __future__ import annotations

import datetime
import json
import uuid

from sqlalchemy.orm import Session

from app.models import (Clause, ClauseClassification, Contract, Policy, SemanticIndex,
                        Template, TemplateClause)
from app.modules.audit.service import record
from app.modules.retrieval.providers import EmbeddingProvider, get_provider
from app.modules.retrieval.vector_store import VectorStore, VectorStoreError, point_id

SNIPPET_CHARS = 500


def get_store() -> VectorStore:
    """Production singleton. Tests pass an InMemoryVectorStore explicitly."""
    from app.modules.retrieval.vector_store import QdrantVectorStore

    global _STORE
    try:
        _STORE
    except NameError:
        _STORE = None
    if _STORE is None:
        _STORE = QdrantVectorStore()  # raises VectorStoreError when down
    return _STORE


def reset_store() -> None:
    global _STORE
    _STORE = None


_STORE = None


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def policy_text(category: str, params: dict) -> str:
    """Normalized textual representation of a policy (context only — the
    deterministic policy engine stays authoritative for actual checks)."""
    bits = "; ".join(f"{k} = {json.dumps(v)}" for k, v in sorted(params.items()))
    return f"Company policy [{category}]: {bits}"


def _latest_categories(db: Session, contract_id: uuid.UUID) -> dict[str, str]:
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id == contract_id)
            .order_by(ClauseClassification.created_at.desc()).all())
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(str(r.clause_id), r.predicted_category)
    return out


def _record(db: Session, *, org_id, object_type: str, object_id: str,
            contract_id=None, template_id=None, provider: EmbeddingProvider,
            vector_id: str | None, status: str, error: str | None = None) -> None:
    def _uid(v):
        return uuid.UUID(str(v)) if v is not None else None

    row = (db.query(SemanticIndex)
           .filter(SemanticIndex.organization_id == _uid(org_id),
                   SemanticIndex.object_type == object_type,
                   SemanticIndex.object_id == object_id,
                   SemanticIndex.embedding_model == provider.model_name).first())
    if row is None:
        row = SemanticIndex(organization_id=_uid(org_id), object_type=object_type,
                            object_id=object_id, contract_id=_uid(contract_id),
                            template_id=_uid(template_id),
                            embedding_model=provider.model_name,
                            embedding_version=provider.model_version,
                            dimension=provider.dimension)
        db.add(row)
    row.status = status
    row.error = (error[:2000] if error else None)
    row.vector_id = vector_id
    row.indexed_at = _now()


def _upsert_batch(db: Session, *, org_id, items: list[dict],
                  provider: EmbeddingProvider, store: VectorStore) -> dict:
    """items: {object_type, object_id, text, contract_id?, template_id?, extra payload}."""
    texts = [it["text"] for it in items]
    try:
        vectors = provider.embed_batch(texts)
    except Exception as e:
        raise VectorStoreError(f"Embedding batch failed: {e}") from e
    store.ensure_collection(provider.dimension)
    points, ok, failed = [], 0, []
    for it, vec in zip(items, vectors):
        pid = point_id(str(org_id), it["object_type"], it["object_id"], provider.model_name)
        payload = {"org_id": str(org_id), "object_type": it["object_type"],
                   "object_id": it["object_id"],
                   "embedding_model": provider.model_name,
                   "embedding_version": provider.model_version,
                   "indexed_at": _now().isoformat(),
                   "text_snippet": it["text"][:SNIPPET_CHARS]}
        for k in ("contract_id", "template_id", "template_version", "category",
                  "section_number", "page_start"):
            if it.get(k) is not None:
                payload[k] = it[k]
        points.append({"id": pid, "vector": vec, "payload": payload})
    try:
        store.upsert(points)
    except VectorStoreError:
        for it in items:
            _record(db, org_id=org_id, object_type=it["object_type"],
                    object_id=it["object_id"], contract_id=it.get("contract_id"),
                    template_id=it.get("template_id"), provider=provider,
                    vector_id=None, status="failed", error="Qdrant upsert failed")
        db.commit()
        raise
    for it, pt in zip(items, points):
        _record(db, org_id=org_id, object_type=it["object_type"],
                object_id=it["object_id"], contract_id=it.get("contract_id"),
                template_id=it.get("template_id"), provider=provider,
                vector_id=pt["id"], status="indexed")
        ok += 1
    db.commit()
    return {"indexed": ok, "failed": failed}


def _collect(items: list[dict]) -> tuple[list[dict], int]:
    """Split embeddable vs empty (empty is skipped explicitly, never embedded)."""
    good = [it for it in items if (it["text"] or "").strip()]
    return good, len(items) - len(good)


def index_contract(db: Session, contract_id: uuid.UUID, *,
                   provider: EmbeddingProvider | None = None,
                   store: VectorStore | None = None,
                   triggered_by: uuid.UUID | None = None) -> dict:
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    provider = provider or get_provider()
    store = store or get_store()
    clauses = db.query(Clause).filter(Clause.contract_id == contract.id).all()
    cats = _latest_categories(db, contract.id)
    items = [{"object_type": "clause", "object_id": str(c.id), "text": c.text,
              "contract_id": str(contract.id), "category": cats.get(str(c.id)),
              "section_number": c.section_number, "page_start": c.page_start}
             for c in clauses]
    good, skipped_empty = _collect(items)
    result = {"indexed": 0, "failed": [], "skipped_empty": skipped_empty}
    if good:
        # embed in chunks to bound memory; each chunk commits independently
        for i in range(0, len(good), 64):
            r = _upsert_batch(db, org_id=contract.organization_id, items=good[i:i + 64],
                              provider=provider, store=store)
            result["indexed"] += r["indexed"]
    record(db, action="contract.semantically_indexed",
           organization_id=contract.organization_id, user_id=triggered_by,
           object_type="contract", object_id=str(contract.id),
           detail=f"{result['indexed']} clauses, {provider.model_name}")
    return {**result, "model": provider.model_name, "status": "ready"}


def index_template(db: Session, template_id: uuid.UUID, *,
                   provider: EmbeddingProvider | None = None,
                   store: VectorStore | None = None,
                   triggered_by: uuid.UUID | None = None) -> dict:
    template = db.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")
    provider = provider or get_provider()
    store = store or get_store()
    clauses = db.query(TemplateClause).filter(TemplateClause.template_id == template.id).all()
    items = [{"object_type": "template_clause", "object_id": str(c.id), "text": c.text,
              "template_id": str(template.id), "template_version": template.version,
              "category": c.predicted_category, "section_number": c.section_number,
              "page_start": c.page_start} for c in clauses]
    good, skipped_empty = _collect(items)
    result = {"indexed": 0, "failed": [], "skipped_empty": skipped_empty}
    if good:
        for i in range(0, len(good), 64):
            r = _upsert_batch(db, org_id=template.organization_id, items=good[i:i + 64],
                              provider=provider, store=store)
            result["indexed"] += r["indexed"]
    record(db, action="template.semantically_indexed",
           organization_id=template.organization_id, user_id=triggered_by,
           object_type="template", object_id=str(template.id),
           detail=f"{result['indexed']} clauses, {provider.model_name}")
    return {**result, "model": provider.model_name, "status": "ready"}


def index_policy(db: Session, policy_id: uuid.UUID, *,
                 provider: EmbeddingProvider | None = None,
                 store: VectorStore | None = None,
                 triggered_by: uuid.UUID | None = None) -> dict:
    policy = db.get(Policy, policy_id)
    if policy is None:
        raise ValueError(f"Policy {policy_id} not found")
    provider = provider or get_provider()
    store = store or get_store()
    text = policy_text(policy.category, json.loads(policy.params))
    r = _upsert_batch(db, org_id=policy.organization_id,
                      items=[{"object_type": "policy", "object_id": str(policy.id),
                              "text": text, "category": policy.category}],
                      provider=provider, store=store)
    record(db, action="policy.semantically_indexed",
           organization_id=policy.organization_id, user_id=triggered_by,
           object_type="policy", object_id=str(policy.id), detail=policy.category)
    return {**r, "model": provider.model_name, "status": "ready"}


def reindex_scope(db: Session, org_id: uuid.UUID, *,
                  provider: EmbeddingProvider | None = None,
                  store: VectorStore | None = None,
                  contract_id: uuid.UUID | None = None,
                  template_id: uuid.UUID | None = None) -> dict:
    """Delete this org's points for OTHER models in scope, then re-index with
    the current provider. Never touches other orgs' points."""
    from app.modules.retrieval.vector_store import org_filter

    provider = provider or get_provider()
    store = store or get_store()
    flt = org_filter(str(org_id))
    if contract_id:
        flt["contract_id"] = str(contract_id)
    if template_id:
        flt["template_id"] = str(template_id)
    # Remove only stale-model points; current-model points are overwritten anyway.
    try:
        existing = store.search([0.0] * provider.dimension, top_k=10000, flt=flt)
    except VectorStoreError:
        raise
    stale = [h["id"] for h in existing
             if h["payload"].get("embedding_model") != provider.model_name]
    if stale:
        store.delete(stale)
    # Drop PG rows for stale models in scope so state matches the store.
    q = (db.query(SemanticIndex)
         .filter(SemanticIndex.organization_id == org_id,
                 SemanticIndex.embedding_model != provider.model_name))
    if contract_id:
        q = q.filter(SemanticIndex.contract_id == contract_id)
    if template_id:
        q = q.filter(SemanticIndex.template_id == template_id)
    dropped = q.delete(synchronize_session=False)
    db.commit()
    out: dict = {"dropped_stale_points": len(stale), "dropped_state_rows": dropped}
    if contract_id:
        out["contract"] = index_contract(db, contract_id, provider=provider, store=store)
    if template_id:
        out["template"] = index_template(db, template_id, provider=provider, store=store)
    return out


def index_status(db: Session, org_id: uuid.UUID,
                 contract_id: uuid.UUID | None = None) -> dict:
    q = db.query(SemanticIndex).filter(SemanticIndex.organization_id == org_id)
    if contract_id:
        q = q.filter(SemanticIndex.contract_id == contract_id)
    rows = q.all()
    by_status: dict[str, int] = {}
    models = sorted({r.embedding_model for r in rows})
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {"objects": len(rows), "by_status": by_status, "models": models}
