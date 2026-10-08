"""Clause segmentation (spec §6).

Interface contract: any segmenter — deterministic today, a Deep Learning
model in Phase 3+ — implements `segment(ParsedDocument) -> ClauseCandidate`
and registers via `register_segmenter`. The pipeline only talks to
`get_segmenter()`, so swapping implementations never changes callers
(spec §38.17: every major component replaceable independently).

Deterministic strategy (first implementation):
  1. Numbered headings ("8.2 ...") and sub-clause numbers at paragraph
     starts open new clauses.
  2. Unnumbered headings (ARTICLE, EXHIBIT, ALL-CAPS, DOCX Heading styles)
     open new clauses with section_number=None.
  3. Body paragraphs attach to the open clause.
  4. No structure at all → chunk body paragraphs (fallback, max 3 per clause).
"""
from __future__ import annotations

import re
from typing import Protocol

from app.modules.document.structures import ClauseCandidate, Paragraph, ParsedDocument

SEGMENTER_NAME = "deterministic"
SEGMENTER_VERSION = "1.0.0"

_SUBCLAUSE = re.compile(r"^(\d+(?:\.\d+)+)[\.\)\:\-]?\s+\S")
_FALLBACK_PARAS_PER_CLAUSE = 3


class ClauseSegmenter(Protocol):
    name: str
    version: str

    def segment(self, doc: ParsedDocument) -> list[ClauseCandidate]:
        ...


class DeterministicSegmenter:
    """Rule-based segmentation on numbering + headings + paragraph structure."""

    name = SEGMENTER_NAME
    version = SEGMENTER_VERSION

    def segment(self, doc: ParsedDocument) -> list[ClauseCandidate]:
        paras = [p for p in doc.paragraphs if p.text.strip()]
        if not paras:
            return []
        groups: list[list[Paragraph]] = []
        current: list[Paragraph] = []
        structured = False

        def flush():
            nonlocal current
            if current:
                groups.append(current)
            current = []

        for p in paras:
            opens = p.is_heading or bool(_SUBCLAUSE.match(p.text.strip()))
            if opens and current:
                flush()
                structured = True
            elif opens:
                structured = True
            current.append(p)
        flush()

        if not structured:
            groups = [paras[i:i + _FALLBACK_PARAS_PER_CLAUSE]
                      for i in range(0, len(paras), _FALLBACK_PARAS_PER_CLAUSE)]

        out: list[ClauseCandidate] = []
        for i, g in enumerate(groups):
            first = g[0]
            num = first.section_number
            if not num:
                m = _SUBCLAUSE.match(first.text.strip())
                if m:
                    num = m.group(1)
            title: str | None
            if first.is_heading:
                title = first.text.strip().split("\n")[0][:200]
            else:
                snippet = re.sub(r"\s+", " ", first.text.strip())[:80]
                title = f"Clause {i + 1} — {snippet}" if snippet else f"Clause {i + 1}"
            text = "\n\n".join(p.text.strip() for p in g)
            pages = [p.page_number for p in g if p.page_number is not None]
            out.append(ClauseCandidate(
                section_number=num, title=title, text=text,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                char_start=g[0].char_start, char_end=g[-1].char_end,
                order_index=i,
            ))
        return out


_REGISTRY: dict[str, type[ClauseSegmenter]] = {"deterministic": DeterministicSegmenter}


def register_segmenter(name: str, cls: type[ClauseSegmenter]) -> None:
    """Phase 3+: register e.g. a trained DL segmenter under its own name."""
    _REGISTRY[name] = cls


def get_segmenter(name: str = "deterministic") -> ClauseSegmenter:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(f"Unknown segmenter {name!r}. Registered: {sorted(_REGISTRY)}") from None
