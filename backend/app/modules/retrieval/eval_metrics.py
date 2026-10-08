"""Retrieval metrics (Phase 13.10): Recall@K, Precision@K, MRR, nDCG@K plus
zero-hit accounting and source-type/category breakdowns. Pure functions over
(ranked object ids, expected sets) — backend-agnostic.
"""
from __future__ import annotations

import math


def _hits(ranked: list[str], expected: set[str], k: int) -> int:
    return len(set(ranked[:k]) & expected)


def recall_at_k(ranked: list[str], expected: set[str], k: int) -> float:
    if not expected:
        return 1.0
    return _hits(ranked, expected, k) / len(expected)


def precision_at_k(ranked: list[str], expected: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    return _hits(ranked, expected, k) / k


def reciprocal_rank(ranked: list[str], expected: set[str]) -> float:
    for i, oid in enumerate(ranked, 1):
        if oid in expected:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: list[str], expected: set[str], k: int,
              gains: dict[str, float] | None = None) -> float:
    """Binary or graded relevance (gains maps object id -> relevance grade)."""
    gains = gains or {}
    def rel(oid: str) -> float:
        return float(gains.get(oid, 1.0 if oid in expected else 0.0))

    dcg = sum(rel(oid) / math.log2(i + 2) for i, oid in enumerate(ranked[:k]))
    ideal = sorted((rel(oid) for oid in set(ranked[:k]) | expected), reverse=True)
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal[:k]))
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def evaluate_queries(results: list[dict], ks: tuple[int, ...] = (1, 3, 5, 10),
                     ) -> dict:
    """results: [{query_id, category, ranked: [object_ids],
    expected: {object_id: {...}}, zero_hit: bool}]."""
    per_query, zero_hit = [], 0
    for r in results:
        ranked, exp = r["ranked"], set(r["expected"])
        if not ranked:
            zero_hit += 1
        row = {"query_id": r["query_id"], "category": r.get("category"),
               "n_expected": len(exp), "zero_hit": not ranked}
        for k in ks:
            row[f"recall@{k}"] = round(recall_at_k(ranked, exp, k), 4)
            row[f"precision@{k}"] = round(precision_at_k(ranked, exp, k), 4)
        row["mrr"] = round(reciprocal_rank(ranked, exp), 4)
        row["ndcg@10"] = ndcg_at_k(ranked, exp, 10)
        per_query.append(row)
    n = len(results) or 1
    agg: dict = {"n_queries": len(results), "zero_hit_queries": zero_hit}
    for k in ks:
        agg[f"recall@{k}"] = round(sum(r[f"recall@{k}"] for r in per_query) / n, 4)
        agg[f"precision@{k}"] = round(sum(r[f"precision@{k}"] for r in per_query) / n, 4)
    agg["mrr"] = round(sum(r["mrr"] for r in per_query) / n, 4)
    agg["ndcg@10"] = round(sum(r["ndcg@10"] for r in per_query) / n, 4)
    by_category: dict[str, dict] = {}
    for r in per_query:
        cell = by_category.setdefault(r["category"] or "unknown",
                                      {"n": 0, "mrr_sum": 0.0, "r5_sum": 0.0})
        cell["n"] += 1
        cell["mrr_sum"] += r["mrr"]
        cell["r5_sum"] += r["recall@5"]
    agg["by_category"] = {
        c: {"n": v["n"], "mrr": round(v["mrr_sum"] / v["n"], 4),
            "recall@5": round(v["r5_sum"] / v["n"], 4)}
        for c, v in sorted(by_category.items())}
    return {"per_query": per_query, "aggregate": agg}


def source_type_breakdown(results: list[dict], id_to_type: dict[str, str],
                          k: int = 5) -> dict:
    """Share of top-k hits per source type + hit rate on expected items."""
    hits: dict[str, int] = {}
    expected_hits: dict[str, int] = {}
    for r in results:
        for oid in r["ranked"][:k]:
            t = id_to_type.get(oid, "unknown")
            hits[t] = hits.get(t, 0) + 1
            if oid in r["expected"]:
                expected_hits[t] = expected_hits.get(t, 0) + 1
    return {"topk_share": hits, "expected_hits": expected_hits, "k": k}
