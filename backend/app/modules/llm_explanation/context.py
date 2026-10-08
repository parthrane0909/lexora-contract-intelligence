"""Context builder (Phase 7.11). Numbered sources, hard budget, no concat soup.

Each source keeps its boundary ([n] header + metadata); the current clause
is always complete; supporting sources are excerpted to fit MAX_CONTEXT_CHARS.
Source indices are stable 1..n and are the ONLY citation targets the LLM may
use (see validator.py).
"""
from __future__ import annotations

MAX_CONTEXT_CHARS = 6000
CLAUSE_EXCERPT_CHARS = 3000
SUPPORT_EXCERPT_CHARS = 1200


def _excerpt(text: str, limit: int) -> tuple[str, bool]:
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    return text[:limit].rsplit(" ", 1)[0] + "…", True


def build_context(finding, evidence: list[dict]) -> dict:
    """Return {sources, prompt_block, truncated, char_count}."""
    if not evidence:
        raise ValueError("Cannot build context: evidence set is empty.")
    if evidence[0]["source_type"] != "contract_clause":
        raise ValueError("Cannot build context: current clause must be first.")
    blocks, total, truncated_any = [], 0, False
    for i, e in enumerate(evidence, 1):
        limit = CLAUSE_EXCERPT_CHARS if i == 1 else SUPPORT_EXCERPT_CHARS
        remaining = MAX_CONTEXT_CHARS - total - 200  # header reserve
        if remaining <= 200:
            truncated_any = True
            break
        body, trunc = _excerpt(e["text"], min(limit, remaining))
        truncated_any = truncated_any or trunc
        meta = f"category={e.get('category') or '–'}"
        if e.get("section") is not None:
            meta += f" §{e['section']}"
        if e.get("page") is not None:
            meta += f" p.{e['page']}"
        if e.get("similarity_score") is not None:
            meta += f" sim={e['similarity_score']:.2f}"
        blocks.append(f"[{i}] {e['title']} ({e['source_type']}; {meta})\n{body}")
        total += len(blocks[-1])
    return {"sources": evidence[:len(blocks)],
            "prompt_block": "\n\n---\n\n".join(blocks),
            "truncated": truncated_any, "char_count": total}
