"""Semantic clause matcher (Phase 6.11) behind the Phase 5 protocol.

`SemanticClauseMatcher` implements `ClauseMatcher.match` — the comparison
engine is untouched; pairs flow through the same difference/materiality
logic, with the match method carrying the similarity score
(`semantic:strong:0.87`). Scores and thresholds are exposed, never hidden:
thresholds below are ENGINEERING defaults requiring evaluation, and
similarity is NOT legal equivalence (see retrieval/evaluate.py results).

Thresholds (env-overridable): below SEMANTIC_MATCH_THRESHOLD → no match;
≥ threshold → `possible`; ≥ SEMANTIC_STRONG_THRESHOLD → `strong`.
"""
from __future__ import annotations

import os

from app.modules.comparison.matching import register_matcher
from app.modules.retrieval.providers import EmbeddingProvider, get_provider


def _thresholds() -> tuple[float, float]:
    from app.core.config import get_settings

    s = get_settings()
    lo = float(os.environ.get("SEMANTIC_MATCH_THRESHOLD") or s.SEMANTIC_MATCH_THRESHOLD)
    hi = float(os.environ.get("SEMANTIC_STRONG_THRESHOLD") or s.SEMANTIC_STRONG_THRESHOLD)
    return lo, hi


def _cos(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class SemanticClauseMatcher:
    """Pair clauses by embedding cosine similarity (greedy best-first)."""

    name = "semantic"
    version = "1.0"

    def __init__(self, provider: EmbeddingProvider | None = None,
                 match_threshold: float | None = None,
                 strong_threshold: float | None = None):
        self._provider = provider
        lo, hi = _thresholds()
        self.match_threshold = match_threshold if match_threshold is not None else lo
        self.strong_threshold = strong_threshold if strong_threshold is not None else hi

    @property
    def _prov(self) -> EmbeddingProvider:
        return self._provider or get_provider()

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._prov.embed_batch([a, b])
        return _cos(va, vb)

    def match(self, template_clauses: list, contract_clauses: list) -> list[tuple]:
        texts = [t.text for t in template_clauses] + [c.text for c in contract_clauses]
        if not texts or any(not (t or "").strip() for t in texts):
            # Empty text anywhere: refuse to guess — fall back to unmatched pairs.
            return [(t, None, None) for t in template_clauses] + [
                (None, c, None) for c in contract_clauses]
        vecs = self._prov.embed_batch(texts)
        nt = len(template_clauses)
        tvecs, cvecs = vecs[:nt], vecs[nt:]
        # Greedy: repeatedly take the highest remaining similarity ≥ threshold.
        scored = []
        for i, t in enumerate(template_clauses):
            for j, c in enumerate(contract_clauses):
                scored.append((_cos(tvecs[i], cvecs[j]), i, j))
        scored.sort(reverse=True)
        used_t, used_c, mates = set(), set(), {}
        for score, i, j in scored:
            if score < self.match_threshold or i in used_t or j in used_c:
                continue
            used_t.add(i)
            used_c.add(j)
            band = "strong" if score >= self.strong_threshold else "possible"
            mates[i] = (contract_clauses[j], f"semantic:{band}:{score:.2f}")
        pairs = [(t, *mates.get(i, (None, None))) for i, t in enumerate(template_clauses)]
        used = {id(m[0]) for m in mates.values()}
        pairs += [(None, c, None) for c in contract_clauses if id(c) not in used]
        return pairs


register_matcher("semantic", SemanticClauseMatcher)
