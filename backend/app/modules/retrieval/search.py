"""Semantic search + historical precedent (Phase 6.9/6.13). Retrieval ONLY —
no generation, no explanations, no advice.

Every query pins `org_id` from the authenticated membership and ANDs caller
filters on top. Results carry enough payload to navigate back to the source
clause (contract viewer `#clause-<id>` anchors).
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import (Clause, ClauseClassification, Contract, RiskFinding, Template,
                        TemplateClause)
from app.modules.retrieval.providers import EmbeddingProvider, EmptyTextError, get_provider
from app.modules.retrieval.vector_store import VectorStore, org_filter

ALLOWED_OBJECT_TYPES = ("clause", "template_clause", "policy")


def _latest_categories(db: Session, contract_ids: set[str]) -> dict[str, str]:
    if not contract_ids:
        return {}
    rows = (db.query(ClauseClassification)
            .filter(ClauseClassification.contract_id.in_(
                [uuid.UUID(c) for c in contract_ids]))
            .order_by(ClauseClassification.created_at.desc()).all())
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(str(r.clause_id), r.predicted_category)
    return out


def _enrich(db: Session, hits: list[dict]) -> list[dict]:
    """Attach source rows (text, section/page, filenames, categories)."""
    clause_ids = [h["payload"].get("object_id") for h in hits
                  if h["payload"].get("object_type") == "clause"]
    tclause_ids = [h["payload"].get("object_id") for h in hits
                   if h["payload"].get("object_type") == "template_clause"]
    clauses = {str(c.id): c for c in
               db.query(Clause).filter(Clause.id.in_(
                   [uuid.UUID(i) for i in clause_ids])).all()} if clause_ids else {}
    tclauses = {str(c.id): c for c in
                db.query(TemplateClause).filter(TemplateClause.id.in_(
                    [uuid.UUID(i) for i in tclause_ids])).all()} if tclause_ids else {}
    contract_ids = {c.contract_id for c in clauses.values() if c.contract_id}
    contracts = {str(c.id): c for c in
                 db.query(Contract).filter(Contract.id.in_(contract_ids)).all()
                 } if contract_ids else {}
    cats = _latest_categories(db, {str(c.contract_id) for c in clauses.values()
                                   if c.contract_id})
    out = []
    for h in hits:
        p = h["payload"]
        item = {"score": round(h["score"], 4), "object_type": p.get("object_type"),
                "object_id": p.get("object_id"), "contract_id": p.get("contract_id"),
                "template_id": p.get("template_id"),
                "category": p.get("category"), "section_number": p.get("section_number"),
                "page_start": p.get("page_start"),
                "embedding_model": p.get("embedding_model"),
                "text": p.get("text_snippet", "")}
        if p.get("object_type") == "clause" and p.get("object_id") in clauses:
            c = clauses[p["object_id"]]
            item.update({"text": c.text[:2000], "section_number": c.section_number,
                         "page_start": c.page_start, "page_end": c.page_end,
                         "category": cats.get(p["object_id"], p.get("category"))})
            con = contracts.get(str(c.contract_id))
            item["contract_filename"] = con.filename if con else None
        elif p.get("object_type") == "template_clause" and p.get("object_id") in tclauses:
            tc = tclauses[p["object_id"]]
            item.update({"text": tc.text[:2000], "section_number": tc.section_number,
                         "page_start": tc.page_start,
                         "category": tc.predicted_category or p.get("category")})
            t = db.get(Template, tc.template_id)
            item["template_name"] = t.name if t else None
            item["template_version"] = t.version if t else None
        out.append(item)
    return out


def semantic_search(db: Session, *, org_id: uuid.UUID, query: str, top_k: int = 10,
                    provider: EmbeddingProvider | None = None,
                    store: VectorStore | None = None,
                    object_type: str | None = None,
                    contract_id: uuid.UUID | None = None,
                    template_id: uuid.UUID | None = None,
                    category: str | None = None,
                    exclude_object_id: str | None = None) -> dict:
    """Embed query → filtered cosine search → PG enrichment. Org filter is
    non-negotiable and never comes from the client."""
    from app.modules.retrieval.indexer import get_store

    if not (query or "").strip():
        raise EmptyTextError("Search query is empty.")
    if object_type is not None and object_type not in ALLOWED_OBJECT_TYPES:
        raise ValueError(f"Invalid object_type {object_type!r}.")
    top_k = max(1, min(int(top_k), 50))
    provider = provider or get_provider()
    store = store or get_store()
    vector = provider.embed_text(query)
    store.ensure_collection(provider.dimension)
    flt = org_filter(str(org_id), {
        "object_type": object_type,
        "contract_id": str(contract_id) if contract_id else None,
        "template_id": str(template_id) if template_id else None,
        "category": category,
        "embedding_model": provider.model_name,
    })
    flt = {k: v for k, v in flt.items() if v is not None}
    hits = store.search(vector, top_k + (1 if exclude_object_id else 0), flt)
    if exclude_object_id:
        hits = [h for h in hits if h["payload"].get("object_id") != exclude_object_id][:top_k]
    return {"query": query, "top_k": top_k, "model": provider.model_name,
            "results": _enrich(db, hits)}


def precedent_for_clause(db: Session, *, org_id: uuid.UUID, clause_id: uuid.UUID,
                         top_k: int = 5, provider=None, store=None) -> dict:
    """Similar historical clauses for one clause (+ their latest risk
    findings as *previous analysis*, never as approved precedent)."""
    clause = db.get(Clause, clause_id)
    if clause is None or clause.organization_id != org_id:
        raise ValueError("Clause not found")
    res = semantic_search(db, org_id=org_id, query=clause.text, top_k=top_k,
                          provider=provider, store=store,
                          exclude_object_id=str(clause_id))
    clause_ids = [r["object_id"] for r in res["results"] if r["object_type"] == "clause"]
    findings: dict[str, list[dict]] = {}
    if clause_ids:
        rows = (db.query(RiskFinding)
                .filter(RiskFinding.clause_id.in_([uuid.UUID(i) for i in clause_ids]))
                .order_by(RiskFinding.created_at.desc()).all())
        seen = set()
        for r in rows:
            key = str(r.clause_id)
            if key in seen:
                continue
            seen.add(key)
            findings.setdefault(key, []).append({
                "rule_id": r.rule_id, "title": r.title, "priority": r.priority,
                "finding_type": r.finding_type,
                "note": "previous analysis on a similar historical clause — "
                        "not an approved legal precedent"})
    for r in res["results"]:
        if r["object_id"] in findings:
            r["previous_analysis"] = findings[r["object_id"]]
    res["source_clause_id"] = str(clause_id)
    return res
