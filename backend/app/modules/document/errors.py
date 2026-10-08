"""Pipeline-level document errors. Parsers raise these; pipeline.py converts
them into failed statuses + messages instead of crashing (spec: fail gracefully)."""


class DocumentParseError(Exception):
    """The file could not be turned into structured text (corrupt, encrypted,
    image-only scan, or unsupported type). Message is user-facing."""
