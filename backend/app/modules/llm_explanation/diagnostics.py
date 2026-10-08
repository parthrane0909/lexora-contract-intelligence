"""Context diagnostics + evidence sufficiency (Phase 16.11/16.12).

Deterministic, metadata-only assessments of RAG inputs — NOT legal
judgments. Sufficiency answers only: "is there enough sourced evidence
to support an explanation?" Statuses: SUFFICIENT / PARTIAL / WEAK.
(NONE is unreachable: build_context refuses empty evidence; a vector-store
outage surfaces as an explicit error upstream, never as silent emptiness.)
"""
from __future__ import annotations

from collections import Counter


def diagnose_context(built: dict, finding_category: str | None = None) -> dict:
    """built: build_context() output. Reports composition, never quality
    judgments beyond counts."""
    sources = built.get("sources", [])
    texts = [(s.get("text") or "") for s in sources]
    nonempty = [t.strip().lower() for t in texts if t.strip()]
    return {
        "n_sources": len(sources),
        "char_count": built.get("char_count", 0),
        "truncated": built.get("truncated", False),
        "source_type_dist": dict(Counter(s.get("source_type") for s in sources)),
        "duplicate_texts": len(nonempty) - len(set(nonempty)),
        "empty_texts": sum(1 for t in texts if not t.strip()),
        "category_match_rate": (
            round(sum(1 for s in sources[1:]
                      if finding_category and s.get("category") == finding_category)
                  / max(1, len(sources) - 1), 4) if len(sources) > 1 else None),
        "has_mandatory_clause": bool(sources) and sources[0].get("source_type") == "contract_clause",
    }


def assess_sufficiency(sources: list[dict], finding_category: str | None = None) -> dict:
    """Deterministic rules on evidence metadata:
    - SUFFICIENT: mandatory clause + >=1 supporting source that is either a
      policy or category-matched to the finding.
    - PARTIAL: mandatory clause + supporting source(s), none category-matched
      and no policy.
    - WEAK: mandatory clause alone.
    """
    if not sources:
        return {"status": "WEAK", "reason": "no evidence items (context builder refuses this)"}
    supporting = sources[1:]
    if not supporting:
        return {"status": "WEAK", "reason": "mandatory clause only; no supporting evidence"}
    matched = [s for s in supporting
               if s.get("source_type") == "policy"
               or (finding_category and s.get("category") == finding_category)]
    if matched:
        return {"status": "SUFFICIENT",
                "reason": f"{len(matched)} category-matched/policy supporting source(s)",
                "matched_types": sorted({s.get("source_type") for s in matched})}
    return {"status": "PARTIAL",
            "reason": f"{len(supporting)} supporting source(s), none category-matched",
            "supporting_types": sorted({s.get("source_type") for s in supporting})}
