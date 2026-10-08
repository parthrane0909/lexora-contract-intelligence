"""Clause matching (Phase 5.5, Level 1 deterministic + Level 2-ready).

`ClauseMatcher` protocol pairs template clauses with contract clauses.
`DeterministicMatcher` (v1) tries, in order:
  1. equal section numbers ("8.2" ↔ "8.2") — strongest structural signal
  2. equal normalized titles
  3. same predicted category (both sides classified) + token overlap ≥ 0.5

Each pair records its match method for auditability. A future vector
matcher (Phase 6 embeddings) implements the same protocol and plugs in via
`get_matcher("semantic")` — the engine never changes.
"""
from __future__ import annotations

import re
from typing import Protocol


def normalize(text: str | None) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str | None) -> set[str]:
    return {t for t in normalize(text).split() if len(t) > 2}


def overlap(a: str | None, b: str | None) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class ClauseMatcher(Protocol):
    name: str
    version: str

    def match(self, template_clauses: list, contract_clauses: list) -> list[tuple]:
        """Return [(template_clause|None, contract_clause|None, method)]."""


class DeterministicMatcher:
    """Section → title → category+overlap. Deterministic, explainable."""

    name = "deterministic"
    version = "1.0"
    OVERLAP_THRESHOLD = 0.5

    def match(self, template_clauses: list, contract_clauses: list) -> list[tuple]:
        pairs: list[tuple] = []
        free_contract = list(contract_clauses)

        def take(pred) -> object | None:
            for c in free_contract:
                if pred(c):
                    free_contract.remove(c)
                    return c
            return None

        for t in template_clauses:
            mate, method = None, None
            if t.section_number:
                mate = take(lambda c: c.section_number == t.section_number)
                method = "section_number" if mate else None
            if mate is None and normalize(t.title):
                mate = take(lambda c, _t=t: normalize(c.title) == normalize(_t.title))
                method = "title" if mate else None
            if mate is None and getattr(t, "predicted_category", None):
                scored = [(overlap(t.text, c.text), c) for c in free_contract
                          if getattr(c, "predicted_category", None) == t.predicted_category]
                scored = [(s, c) for s, c in scored if s >= self.OVERLAP_THRESHOLD]
                if scored:
                    _, mate = max(scored, key=lambda p: p[0])
                    free_contract.remove(mate)
                    method = "category_overlap"
            pairs.append((t, mate, method))
        for c in free_contract:
            pairs.append((None, c, None))
        return pairs


_MATCHERS: dict[str, type[ClauseMatcher]] = {"deterministic": DeterministicMatcher}


def register_matcher(name: str, cls: type[ClauseMatcher]) -> None:
    """Phase 6+: register e.g. an embedding-based matcher under its own name."""
    _MATCHERS[name] = cls


def get_matcher(name: str = "deterministic") -> ClauseMatcher:
    try:
        return _MATCHERS[name]()
    except KeyError:
        raise ValueError(f"Unknown matcher {name!r}. Registered: {sorted(_MATCHERS)}") from None
