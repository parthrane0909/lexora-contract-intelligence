"""Real retrieval evaluation runner (Phase 13). Executes a versioned eval
dataset against live search (real provider + real store by default), records
a full artifact (config, per-query rows, aggregates, breakdowns). The search
core is injectable so the aggregation logic is unit-testable without Qdrant.
"""
from __future__ import annotations

import datetime
import json
import os
import uuid

from sqlalchemy.orm import Session

from app.modules.retrieval import eval_metrics as em


def _default_search(db: Session, *, org_id: uuid.UUID, query: str, top_k: int,
                    provider=None, store=None) -> list[dict]:
    from app.modules.retrieval.search import semantic_search

    out = semantic_search(db, org_id=org_id, query=query, top_k=top_k,
                          provider=provider, store=store)
    return out.get("results", [])


def run_evaluation(db: Session, organization_id: uuid.UUID, dataset: dict, *,
                   top_k: int = 10, provider=None, store=None,
                   _search=None, out_dir: str | None = None) -> dict:
    """Run all queries, score, persist artifact. Returns the report dict."""
    search = _search or _default_search
    qrows, id_to_type = [], {}
    for ex in dataset["examples"]:
        try:
            hits = search(db, org_id=organization_id, query=ex["query"],
                          top_k=top_k, provider=provider, store=store)
        except Exception as e:
            hits = []
            qrows.append({"query_id": ex["query_id"], "category": ex.get("category"),
                          "ranked": [], "expected": set(),
                          "error": f"{type(e).__name__}: {e}"})
            continue
        ranked = []
        for h in hits:
            oid = h.get("object_id")
            if oid:
                ranked.append(oid)
                id_to_type[oid] = h.get("object_type", "unknown")
        expected = {s["object_id"] for s in ex["expected"]}
        qrows.append({"query_id": ex["query_id"], "category": ex.get("category"),
                      "ranked": ranked, "expected": expected})
    report = em.evaluate_queries(qrows)
    report["source_types"] = em.source_type_breakdown(qrows, id_to_type)
    report["config"] = {
        "dataset_version": dataset.get("dataset_version"),
        "organization_id": str(organization_id),
        "top_k": top_k,
        "provider": getattr(provider, "model_name", "configured-default"),
        "store": type(store).__name__ if store is not None else "configured-default",
        "evaluated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_queries": len(qrows),
    }
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"retrieval-eval-{dataset.get('dataset_version')}-report.json")
        if os.path.exists(path):
            raise ValueError(f"Evaluation artifact exists: {path} — it is immutable.")
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        report["artifact_path"] = path
    return report
