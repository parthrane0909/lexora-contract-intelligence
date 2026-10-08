"""Grounding validation (Phase 7.17). A safeguard, not proof of correctness.

Checks, all deterministic:
1. output is a dict with exactly the required schema fields;
2. every source_references[].source_index resolves to a context source;
3. every cited quote appears (case-insensitive substring) in that source;
4. every cited source belongs to the finding's organization;
5. the mandatory current clause (source [1]) is cited at least once;
6. no banned legal-conclusion phrases anywhere in the output.

Raises GroundingError with an explicit reason — persisted as a failed
explanation, never silently accepted.
"""
from __future__ import annotations

from app.modules.llm_explanation.prompts import FIELDS

BANNED_PHRASES = (
    "contract is illegal",
    "violates the law",
    "violation of the law",
    "legally safe",
    "guaranteed compliant",
    "no legal risk",
)


class GroundingError(Exception):
    """The LLM output failed grounding validation."""


def validate_explanation(output: dict, sources: list[dict], organization_id) -> dict:
    if not isinstance(output, dict):
        raise GroundingError("LLM output is not a JSON object.")
    missing = [f for f in FIELDS if f not in output]
    if missing:
        raise GroundingError(f"LLM output missing fields: {missing}.")
    refs = output.get("source_references")
    if not isinstance(refs, list) or not refs:
        raise GroundingError("LLM output has no source_references.")
    by_index = {i + 1: s for i, s in enumerate(sources)}
    cited: set[int] = set()
    for r in refs:
        if not isinstance(r, dict) or not isinstance(r.get("source_index"), int):
            raise GroundingError(f"Malformed source reference: {r!r}.")
        idx = r["source_index"]
        if idx not in by_index:
            raise GroundingError(f"Citation [{idx}] does not resolve to supplied evidence.")
        src = by_index[idx]
        if str(src.get("organization_id")) != str(organization_id):
            raise GroundingError(f"Citation [{idx}] belongs to another organization.")
        quote = (r.get("quote") or "").strip()
        if len(quote) < 4 or quote.lower() not in (src.get("text") or "").lower():
            raise GroundingError(f"Citation [{idx}] quote not found in the cited source.")
        cited.add(idx)
    if 1 not in cited:
        raise GroundingError("Current contract clause [1] is never cited.")
    blob = " ".join(str(output.get(f, "")) for f in FIELDS if f != "source_references").lower()
    for phrase in BANNED_PHRASES:
        if phrase in blob:
            raise GroundingError(f"Unsupported legal conclusion detected: {phrase!r}.")
    return output
