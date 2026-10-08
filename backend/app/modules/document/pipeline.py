"""Document-processing pipeline (spec §5 architecture):

bytes → parse → persist structured doc → segment → persist clauses
→ statuses + model versions + audit.

Re-runs are idempotent: existing rows for the contract are replaced.
Any DocumentParseError (or unexpected error) becomes processing_status
"failed" + a user-facing processing_error — never a 500 with no record.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy.orm import Session

from app.models import Clause, Contract, DocumentPage, DocumentSection, ParsedDocument
from app.modules.audit.service import record
from app.modules.document.errors import DocumentParseError
from app.modules.document.parsers import parse_document
from app.modules.document.segmenters import get_segmenter
from app.modules.storage.service import StorageService


def _clear_previous(db: Session, contract_id: uuid.UUID) -> None:
    for model in (Clause, DocumentSection, DocumentPage, ParsedDocument):
        db.query(model).filter(model.contract_id == contract_id).delete()


def process_contract(db: Session, contract_id: uuid.UUID, *, segmenter: str = "deterministic") -> dict:
    """Run the full Phase 2 pipeline for one contract. Returns a summary dict."""
    contract = db.get(Contract, contract_id)
    if contract is None:
        raise ValueError(f"Contract {contract_id} not found")
    contract.processing_status = "processing"
    contract.processing_error = None
    db.commit()
    try:
        data = StorageService().load(contract.storage_key)
        parsed = parse_document(contract.filename, data)
        candidates = get_segmenter(segmenter).segment(parsed)
        if not candidates:
            raise DocumentParseError("Segmentation produced no clauses from the extracted text.")

        _clear_previous(db, contract.id)
        row = ParsedDocument(
            organization_id=contract.organization_id, contract_id=contract.id,
            parser_name=parsed.parser_name, parser_version=parsed.parser_version,
            page_count=parsed.page_count, full_text=parsed.full_text,
        )
        db.add(row)
        db.flush()
        for p in parsed.pages:
            db.add(DocumentPage(
                organization_id=contract.organization_id, contract_id=contract.id,
                parsed_document_id=row.id, page_number=p.page_number, text=p.text,
                char_start=p.char_start, char_end=p.char_end,
                paragraph_spans=json.dumps([list(s) for s in p.paragraph_spans]),
            ))
        for s in parsed.sections:
            db.add(DocumentSection(
                organization_id=contract.organization_id, contract_id=contract.id,
                heading=s.heading, level=s.level, section_number=s.section_number,
                order_index=s.order_index, page_start=s.page_start, page_end=s.page_end,
                char_start=s.char_start, char_end=s.char_end,
            ))
        seg = get_segmenter(segmenter)
        for c in candidates:
            db.add(Clause(
                organization_id=contract.organization_id, contract_id=contract.id,
                section_number=c.section_number, title=c.title, text=c.text,
                page_start=c.page_start, page_end=c.page_end,
                char_start=c.char_start, char_end=c.char_end,
                order_index=c.order_index, segmenter_name=seg.name, segmenter_version=seg.version,
            ))
        contract.processing_status = "processed"
        contract.processing_error = None
        contract.model_versions = json.dumps({"parser": f"{parsed.parser_name}-{parsed.parser_version}",
                                              "segmenter": f"{seg.name}-{seg.version}"})
        # Make the new clauses visible inside THIS transaction, then recompute
        # the analysis rollup before anything is committed: processing and
        # "analysis pending" must land in a single commit, so a reader can
        # never observe processing_status="processed" together with the
        # analysis_status="queued" left over from enqueue. Classification and
        # risk analysis have NOT run (and any previous ones were
        # cascade-deleted with the clauses they described).
        db.flush()
        from app.modules.risk_engine.state import refresh_analysis_status

        refresh_analysis_status(db, contract)
        db.commit()  # no-op if refresh_analysis_status already committed
        record(db, action="contract.processed", organization_id=contract.organization_id,
               user_id=contract.uploaded_by, object_type="contract", object_id=str(contract.id),
               detail=f"{len(parsed.pages)} pages, {len(parsed.sections)} sections, {len(candidates)} clauses")
        # Semantic indexing is only queued here (Redis, worker does the
        # embeddings + Qdrant work).
        # It must never raise: a vector-store outage cannot fail processing.
        try:
            from app.modules.retrieval.auto_index import enqueue_semantic_index

            enqueue_semantic_index(db, contract.id)
        except Exception as e:  # noqa: BLE001 — queueing is best-effort
            print(f"pipeline: semantic index enqueue failed for {contract.id}: {e}")
        return {"status": "processed", "pages": len(parsed.pages),
                "sections": len(parsed.sections), "clauses": len(candidates)}
    except DocumentParseError as e:
        return _fail(db, contract, str(e))
    except Exception as e:  # defensive: storage/DB errors must also leave a record
        return _fail(db, contract, f"Internal processing error: {type(e).__name__}")


def _fail(db: Session, contract: Contract, message: str) -> dict:
    db.rollback()
    contract = db.get(Contract, contract.id)
    _clear_previous(db, contract.id)
    contract.processing_status = "failed"
    contract.analysis_status = "failed"
    contract.processing_error = message[:2000]
    # Clauses were cleared, so whatever was indexed no longer describes this
    # contract: drop it back to `pending` (never `indexed`, never `failed` —
    # indexing itself did not fail, processing did).
    contract.semantic_index_status = "pending"
    contract.semantic_index_error = None
    db.commit()
    record(db, action="contract.process_failed", organization_id=contract.organization_id,
           user_id=contract.uploaded_by, object_type="contract", object_id=str(contract.id),
           detail=message[:500])
    # Part 2: this failure path writes statuses directly (it does not go
    # through refresh_analysis_status), so publish them explicitly —
    # best-effort, never raises.
    from app.modules.search.indexer import index_contract

    index_contract(contract)
    return {"status": "failed", "error": message}
