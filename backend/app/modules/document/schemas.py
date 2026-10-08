"""Typed document/clause schemas (spec §5-6)."""
from pydantic import BaseModel


class PageOut(BaseModel):
    page_number: int
    char_start: int
    char_end: int
    paragraph_spans: list[list[int]] = []
    text: str


class SectionOut(BaseModel):
    heading: str
    level: int = 1
    section_number: str | None = None
    order_index: int = 0
    page_start: int | None = None
    page_end: int | None = None
    char_start: int = 0
    char_end: int = 0


class DocumentOut(BaseModel):
    contract_id: str
    parser_name: str
    parser_version: str
    page_count: int
    pages: list[PageOut]
    sections: list[SectionOut]
    full_text: str


class ClauseOut(BaseModel):
    id: str
    section_number: str | None = None
    title: str | None = None
    text: str
    page_start: int | None = None
    page_end: int | None = None
    char_start: int = 0
    char_end: int = 0
    order_index: int = 0
    segmenter_name: str = ""
    segmenter_version: str = ""
