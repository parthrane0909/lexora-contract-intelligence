"""Deterministic source/category-aware reranking (Phase 16.8).

RERANKER_VERSION changes are explicit. Pure function over scored hits:
final = cosine + W_CATEGORY * category_match + W_SOURCE * source_priority.

Source priority mirrors evidence.py: clause/policy/template > historical >
other. No learned weights, no LLM, no network. Latency is measured by the
caller (pure cpu loop); failure mode is "no boost applied", never a crash.
"""
from __future__ import annotations

RERANKER_VERSION = "v1"

W_CATEGORY = 0.15
W_SOURCE = 0.05

SOURCE_PRIORITY: dict[str, float] = {
    "clause": 1.0,
    "policy": 0.9,
    "template_clause": 0.8,
    "historical_clause": 0.6,
}


def rerank(hits: list[dict], *, query_category: str | None = None,
           top_k: int = 10) -> list[dict]:
    """hits: [{score, payload{object_type, category, ...}}]. Returns top_k
    with added final_score + rerank_explain, stable-sorted desc."""
    out = []
    for h in hits:
        p = h.get("payload", {})
        cat_match = (1.0 if query_category and p.get("category") == query_category
                     else 0.0)
        src = SOURCE_PRIORITY.get(p.get("object_type", ""), 0.0)
        final = float(h.get("score", 0.0)) + W_CATEGORY * cat_match + W_SOURCE * src
        out.append({**h, "final_score": round(final, 4),
                    "rerank_explain": {"category_match": cat_match,
                                       "source_priority": src,
                                       "reranker_version": RERANKER_VERSION}})
    out.sort(key=lambda h: h["final_score"], reverse=True)
    return out[:top_k]
