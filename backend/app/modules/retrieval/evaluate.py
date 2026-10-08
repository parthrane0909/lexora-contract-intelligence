"""Deterministic semantic-retrieval evaluation (Phase 6.21).

Two fixtures, real numbers:
1. Retrieval: fixed queries with relevant/non-relevant clause judgments →
   Recall@K / Precision@K. Demonstrates the plumbing retrieves AND measures
   how well — not "Qdrant works".
2. Template matching: labelled template↔contract pairs → correct / incorrect
   / unmatched counts with similarity scores.

Usage:
  python -m app.modules.retrieval.evaluate --provider real --store memory
  python -m app.modules.retrieval.evaluate --provider real --store qdrant
`real` = configured Sentence Transformers model; `hash` = hermetic fallback
(only proves plumbing, flagged as such in output).
"""
from __future__ import annotations

import argparse
import json

# ---------------------------------------------------------------- fixtures ----

CLAUSES = [
    ("L1", "Supplier's total liability shall not exceed $500,000."),
    ("L2", "Liability is unlimited under this agreement."),
    ("L3", "Neither party shall be liable for indirect or consequential damages."),
    ("P1", "Client shall pay all fees within 30 days of invoice."),
    ("P2", "Late payments accrue interest at 1 percent per month."),
    ("T1", "Either party may terminate this agreement with 60 days written notice."),
    ("G1", "This agreement shall be governed by the laws of Delaware."),
    ("C1", "Each party shall keep all confidential information strictly confidential."),
    ("I1", "Vendor shall maintain liability insurance of at least $2,000,000."),
    ("W1", "All warranties against defects last twelve months from delivery."),
]

QUERIES = [
    {"query": "unlimited liability", "relevant": {"L2", "L3"}},
    {"query": "cap on liability at five hundred thousand", "relevant": {"L1"}},
    {"query": "payment within 30 days of invoice", "relevant": {"P1"}},
    {"query": "termination notice period", "relevant": {"T1"}},
    {"query": "which law governs this agreement", "relevant": {"G1"}},
]

MATCH_TEMPLATE = [
    ("MT1", "Supplier liability is capped at $500,000."),
    ("MT2", "Pay all invoices within thirty days."),
    ("MT3", "Sixty days notice is required to terminate."),
]
MATCH_CONTRACT = [
    ("MC1", "Supplier's total liability shall not exceed $500,000."),
    ("MC2", "Client shall pay all fees within 30 days of invoice."),
    ("MC3", "The office pantry is stocked on Fridays."),
]
MATCH_EXPECTED = {"MT1": "MC1", "MT2": "MC2", "MT3": None}  # MT3 has no true mate


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    return len(set(ranked[:k]) & relevant) / k


# ------------------------------------------------------------------ runners ----

def run_retrieval_eval(provider, store, org_id: str, ks=(1, 3, 5)) -> dict:
    from app.modules.retrieval.vector_store import point_id

    store.ensure_collection(provider.dimension)
    store.upsert([{"id": point_id(org_id, "clause", cid, provider.model_name),
                   "vector": v,
                   "payload": {"org_id": org_id, "object_type": "clause",
                               "object_id": cid, "embedding_model": provider.model_name}}
                  for (cid, _), v in zip(CLAUSES, provider.embed_batch([t for _, t in CLAUSES]))])
    per_query, agg = [], {f"recall@{k}": [] for k in ks} | {f"precision@{k}": [] for k in ks}
    for q in QUERIES:
        qv = provider.embed_text(q["query"])
        hits = store.search(qv, max(ks), {"org_id": org_id, "embedding_model": provider.model_name})
        ranked = [h["payload"]["object_id"] for h in hits]
        row = {"query": q["query"], "ranked": ranked}
        for k in ks:
            row[f"recall@{k}"] = round(recall_at_k(ranked, q["relevant"], k), 4)
            row[f"precision@{k}"] = round(precision_at_k(ranked, q["relevant"], k), 4)
            agg[f"recall@{k}"].append(row[f"recall@{k}"])
            agg[f"precision@{k}"].append(row[f"precision@{k}"])
        per_query.append(row)
    summary = {m: round(sum(v) / len(v), 4) for m, v in agg.items()}
    return {"per_query": per_query, "summary": summary}


def run_match_eval(provider, threshold: float = 0.55) -> dict:
    from app.modules.retrieval.semantic_matcher import SemanticClauseMatcher

    class _C:
        def __init__(self, cid, text):
            self.id = cid
            self.text = text
            self.section_number = None
            self.title = None
            self.predicted_category = None

    matcher = SemanticClauseMatcher(provider=provider, match_threshold=threshold)
    t_clauses = [_C(cid, t) for cid, t in MATCH_TEMPLATE]
    c_clauses = [_C(cid, t) for cid, t in MATCH_CONTRACT]
    pairs = matcher.match(t_clauses, c_clauses)
    got = {t.id: (c.id if c else None) for t, c, _ in pairs if t is not None}
    # scores for the chosen mates
    scores = {}
    for t, c, method in pairs:
        if t is not None and c is not None:
            scores[t.id] = method
    correct = sum(1 for k, v in MATCH_EXPECTED.items() if got.get(k) == v)
    return {"expected": MATCH_EXPECTED, "got": got, "methods": scores,
            "correct": correct, "total": len(MATCH_EXPECTED),
            "accuracy": round(correct / len(MATCH_EXPECTED), 4)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate semantic retrieval")
    ap.add_argument("--provider", choices=["real", "hash"], default="real")
    ap.add_argument("--store", choices=["memory", "qdrant"], default="memory")
    ap.add_argument("--out", default=None)
    ap.add_argument("--threshold", type=float, default=0.55)
    args = ap.parse_args()

    from app.modules.retrieval.providers import HashEmbeddingProvider, get_provider
    from app.modules.retrieval.vector_store import InMemoryVectorStore, QdrantVectorStore

    if args.provider == "hash":
        import os

        os.environ["EMBEDDING_PROVIDER"] = "hash"
        from app.modules.retrieval.providers import reset_provider

        reset_provider()
    provider = get_provider()
    store = QdrantVectorStore(collection="eval_semantic_objects") if args.store == "qdrant" \
        else InMemoryVectorStore(collection="eval")
    try:
        store.delete_where({})
    except Exception:
        pass
    report = {"provider": provider.model_name, "dimension": provider.dimension,
              "hermetic": args.provider == "hash",
              "retrieval": run_retrieval_eval(provider, store, "eval-org"),
              "matching": run_match_eval(provider, args.threshold)}
    if args.store == "qdrant":
        try:
            from qdrant_client.http import models as qm

            store._client.delete_collection("eval_semantic_objects")
        except Exception:
            pass
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
