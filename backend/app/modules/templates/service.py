"""Approved template store + processing (Phase 5.3/5.4).

Upload → validate (same rules as contracts) → object storage → the SAME
parse_document + segmenter as Phase 2 (no second parser) → template_clauses
+ full_text on the version row. Template clauses are optionally classified
with the existing read-only classifier; a missing model leaves them
unclassified (null) instead of failing — comparison never requires it.

Versioning mirrors policies: re-uploading the same (name, contract_type)
inserts version+1 and deactivates the old row. History is append-only.
"""
from __future__ import annotations

import json
import os
import uuid

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Template, TemplateClause
from app.modules.audit.service import record
from app.modules.document.errors import DocumentParseError
from app.modules.document.parsers import parse_document
from app.modules.document.segmenters import get_segmenter
from app.modules.storage.service import StorageService


def validate_file(filename: str, data: bytes) -> None:
    s = get_settings()
    ext = os.path.splitext(filename.lower())[1]
    if ext not in s.ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(400, f"Unsupported type {ext}. Allowed: pdf, docx")
    if len(data) > s.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"File exceeds {s.MAX_UPLOAD_MB} MB")
    if not data.startswith(s.ALLOWED_UPLOAD_MAGIC):
        raise HTTPException(400, "File content does not look like PDF/DOCX")


def create_template(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID,
                    name: str, filename: str, data: bytes,
                    contract_type: str | None = None) -> Template:
    """Store bytes + new version row, then process inline (fast, <1s typical)."""
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Template name is required")
    validate_file(filename, data)
    key = StorageService().save(data, filename, f"templates/{organization_id}")
    current = db.query(Template).filter(
        Template.organization_id == organization_id, Template.name == name,
        Template.is_active.is_(True)).first()
    version = (current.version + 1) if current else 1
    if current:
        current.is_active = False
    row = Template(organization_id=organization_id, name=name, contract_type=contract_type,
                   version=version, is_active=True, status="processing", storage_key=key,
                   filename=filename, created_by=user_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    _process(db, row, data)
    record(db, action="template.uploaded", organization_id=organization_id, user_id=user_id,
           object_type="template", object_id=str(row.id), detail=f"{name} v{version}")
    return row


def _process(db: Session, template: Template, data: bytes) -> Template:
    try:
        parsed = parse_document(template.filename, data)
        candidates = get_segmenter().segment(parsed)
        if not candidates:
            raise DocumentParseError("Segmentation produced no clauses from the extracted text.")
        seg = get_segmenter()
        template.full_text = parsed.full_text
        template.page_count = parsed.page_count
        template.parser_version = f"{parsed.parser_name}-{parsed.parser_version}"
        template.segmenter_version = f"{seg.name}-{seg.version}"
        for c in candidates:
            db.add(TemplateClause(
                organization_id=template.organization_id, template_id=template.id,
                section_number=c.section_number, title=c.title, text=c.text,
                page_start=c.page_start, page_end=c.page_end,
                char_start=c.char_start, char_end=c.char_end, order_index=c.order_index))
        db.flush()
        db.commit()  # persist parse results BEFORE best-effort classification,
        # so a classification failure can only roll back its own writes
        _classify_optional(db, template)
        template.status = "ready"
        template.processing_error = None
        db.commit()
    except DocumentParseError as e:
        db.rollback()
        template = db.get(Template, template.id)
        template.status = "failed"
        template.processing_error = str(e)[:2000]
        db.commit()
        record(db, action="template.process_failed", organization_id=template.organization_id,
               user_id=template.created_by, object_type="template", object_id=str(template.id),
               detail=str(e)[:500])
    db.refresh(template)
    return template


def _classify_optional(db: Session, template: Template) -> None:
    """Best-effort classification; missing model → nulls, never a failure."""
    try:
        from app.modules.ml_nlp.classifier import classify_clause
    except Exception:
        return
    rows = db.query(TemplateClause).filter(TemplateClause.template_id == template.id).all()
    try:
        for cl in rows:
            if not (cl.text or "").strip():
                continue
            pred = classify_clause(cl.text)
            cl.predicted_category = pred["category"]
            cl.classification_confidence = pred["confidence"]
            cl.classification_version = pred["model_version"]
        db.flush()
    except Exception:
        db.rollback()  # model vanished mid-run: leave clauses unclassified


def reprocess(db: Session, template: Template) -> Template:
    data = StorageService().load(template.storage_key)
    db.query(TemplateClause).filter(TemplateClause.template_id == template.id).delete()
    template.status = "processing"
    template.processing_error = None
    db.commit()
    return _process(db, template, data)
