"""DeterministicSegmenter unit tests on synthetic parsed documents."""
import pytest

from app.modules.document.segmenters import (
    DeterministicSegmenter,
    get_segmenter,
    register_segmenter,
)
from app.modules.document.structures import Page, Paragraph, ParsedDocument


def _doc(paras: list[tuple[str, int | None, bool, str | None]]) -> ParsedDocument:
    """(text, page, is_heading, section_number) → ParsedDocument with offsets."""
    full_parts, structured, pos = [], [], 0
    for i, (text, page, head, num) in enumerate(paras):
        if i > 0:
            full_parts.append("\n\n")
            pos += 2
        start = pos
        full_parts.append(text)
        pos += len(text)
        structured.append(Paragraph(text, page, start, pos, is_heading=head,
                                    section_number=num))
    full = "".join(full_parts)
    return ParsedDocument(full, [Page(1, full, 0, len(full))], [], structured,
                          "test", "0", 1)


def test_numbered_headings_open_clauses():
    doc = _doc([
        ("1. Payment Terms", 1, True, "1"),
        ("Pay within 30 days.", 1, False, None),
        ("2. Confidentiality", 2, True, "2"),
        ("Keep secrets.", 2, False, None),
        ("8.2 Limitation of Liability", 3, True, "8.2"),
        ("Cap at 12 months fees.", 3, False, None),
    ])
    out = DeterministicSegmenter().segment(doc)
    assert [c.section_number for c in out] == ["1", "2", "8.2"]
    assert "Pay within 30 days" in out[0].text
    assert [c.order_index for c in out] == [0, 1, 2]
    assert [c.page_start for c in out] == [1, 2, 3]


def test_subclause_number_opens_clause():
    doc = _doc([
        ("2. Termination", 1, True, "2"),
        ("Either party may terminate.", 1, False, None),
        ("8.2.1 Subcontractor liability stays.", 1, False, None),
    ])
    out = DeterministicSegmenter().segment(doc)
    assert [c.section_number for c in out] == ["2", "8.2.1"]


def test_unnumbered_fallback_chunks():
    doc = _doc([(f"Body paragraph {i} with substance.", 1, False, None) for i in range(7)])
    out = DeterministicSegmenter().segment(doc)
    assert len(out) == 3  # 3 + 3 + 1
    assert all(c.section_number is None for c in out)
    assert out[0].char_start == 0 and out[-1].char_end == len(doc.full_text)


def test_empty_document_no_clauses():
    doc = ParsedDocument("", [], [], [], "test", "0", 0)
    assert DeterministicSegmenter().segment(doc) == []


def test_registry_protocol():
    assert get_segmenter().name == "deterministic"
    with pytest.raises(ValueError):
        get_segmenter("nope")

    class Stub:
        name, version = "stub", "0"
        def segment(self, doc):
            return []

    register_segmenter("stub-test", Stub)
    assert get_segmenter("stub-test").segment(None) == []
