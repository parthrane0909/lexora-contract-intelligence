"""Shared in-memory structures for the document pipeline (spec §5-6).

These are NOT database models. Parsers produce a ParsedDocument; the
segmenter consumes it; pipeline.py persists the result into
parsed_documents / document_pages / document_sections / clauses.

All character offsets refer to ParsedDocument.full_text so every clause
can be traced back to its exact source span.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Paragraph:
    text: str
    page_number: int | None  # None only if the source has no pagination
    char_start: int
    char_end: int
    is_heading: bool = False
    heading_level: int = 1
    section_number: str | None = None


@dataclass
class Page:
    page_number: int
    text: str
    char_start: int
    char_end: int
    paragraph_spans: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Section:
    heading: str
    level: int
    order_index: int
    section_number: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int = 0
    char_end: int = 0


@dataclass
class ParsedDocument:
    full_text: str
    pages: list[Page]
    sections: list[Section]
    paragraphs: list[Paragraph]
    parser_name: str
    parser_version: str
    page_count: int


@dataclass
class ClauseCandidate:
    section_number: str | None
    title: str | None
    text: str
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int
    order_index: int = 0
