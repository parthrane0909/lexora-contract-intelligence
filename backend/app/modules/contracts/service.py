"""Contract service: validation → storage → DB (spec §5 architecture).

File validation: extension + magic bytes + size cap. Text/structure parsing
delegates to app.modules.document.pipeline (Phase 2).
"""
import os

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Contract, Document
from app.modules.audit.service import record
from app.modules.search.indexer import index_contract
from app.modules.storage.service import StorageService

# contracts.processing_status values written by upload (create_contract) and
# by the document pipeline. Shared by the contract listing filter and the
# Elasticsearch search filter so both accept exactly the same vocabulary.
PROCESSING_STATUSES = ("uploaded", "queued", "processing", "processed", "failed")


def validate_file(filename: str, data: bytes) -> str:
    s = get_settings()
    ext = os.path.splitext(filename.lower())[1]
    if ext not in s.ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type {ext}. Allowed: pdf, docx")
    if len(data) > s.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File exceeds {s.MAX_UPLOAD_MB} MB")
    if not data.startswith(s.ALLOWED_UPLOAD_MAGIC):
        raise HTTPException(400, "File content does not look like PDF/DOCX")
    return "application/pdf" if ext == ".pdf" else (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def create_contract(db: Session, *, organization_id, user_id, filename: str, data: bytes, contract_type=None):
    mime = validate_file(filename, data)
    key = StorageService().save(data, filename, str(organization_id))
    contract = Contract(
        organization_id=organization_id,
        filename=filename,
        contract_type=contract_type,
        processing_status="uploaded",
        analysis_status="pending",
        storage_key=key,
        uploaded_by=user_id,
    )
    db.add(contract)
    db.flush()
    db.add(
        Document(
            organization_id=organization_id,
            contract_id=contract.id,
            storage_key=key,
            mime_type=mime,
            size_bytes=len(data),
        )
    )
    db.commit()
    db.refresh(contract)
    record(
        db, action="contract.uploaded", organization_id=organization_id, user_id=user_id,
        object_type="contract", object_id=str(contract.id), detail=filename,
    )
    # Part 2: publish to the search index (best-effort — never raises).
    index_contract(contract)
    return contract


def enqueue_analysis(db: Session, *, organization_id, user_id, contract: Contract):
    """Mark queued and hand to the worker. If Redis is unreachable (local dev),
    run the pipeline synchronously so behavior stays identical."""
    contract.processing_status = "queued"
    contract.analysis_status = "queued"
    db.commit()
    pushed = False
    try:
        import redis  # fire-and-forget; worker picks up in Phase 2

        r = redis.from_url(get_settings().REDIS_URL, socket_connect_timeout=1)
        r.lpush("analysis_queue", str(contract.id))
        pushed = True
    except Exception:
        pass
    if not pushed:
        from app.modules.document.pipeline import process_contract

        process_contract(db, contract.id)
        db.refresh(contract)
    record(
        db, action="contract.analysis_queued", organization_id=organization_id, user_id=user_id,
        object_type="contract", object_id=str(contract.id),
    )
    # Part 2: statuses just changed (and the pipeline may already have run
    # synchronously) — republish the search document (best-effort).
    index_contract(contract)
    return contract
