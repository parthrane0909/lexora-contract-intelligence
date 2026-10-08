"""Document intelligence module (spec §5-6): parsing, structure, segmentation.

Public surface used by other modules: `process_contract` (pipeline),
`parse_document` (parsers), `get_segmenter`/`register_segmenter` (segmenters).
"""
from app.modules.document.errors import DocumentParseError  # noqa: F401
from app.modules.document.parsers import parse_document  # noqa: F401
from app.modules.document.pipeline import process_contract  # noqa: F401
from app.modules.document.segmenters import (  # noqa: F401
    get_segmenter,
    register_segmenter,
)

PIPELINE = ["validate", "store", "parse", "structure", "segment", "persist"]
