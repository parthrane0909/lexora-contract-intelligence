"""Template endpoints (Phase 5.3/5.4/5.11). Org-scoped.

Writes (upload, activate, reprocess, archive): admin, legal_reviewer,
contract_manager. Reads: any member. Viewers can never mutate.
"""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.models import Template, TemplateClause, User
from app.modules.audit.service import record
from app.modules.storage.service import StorageService
from app.modules.templates import schemas, service

router = APIRouter(prefix="/templates", tags=["templates"])
_WRITERS = ("admin", "legal_reviewer", "contract_manager")


def _parse_id(template_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(404, "Template not found")


def _get_owned(db: Session, template_id: str, org_id) -> Template:
    t = db.query(Template).filter(
        Template.id == _parse_id(template_id), Template.organization_id == org_id).first()
    if not t:
        raise HTTPException(404, "Template not found")
    return t


def _out(db: Session, t: Template) -> dict:
    email = None
    if t.created_by:
        u = db.get(User, t.created_by)
        email = u.email if u else None
    return {
        "id": str(t.id), "name": t.name, "contract_type": t.contract_type,
        "version": t.version, "is_active": t.is_active, "status": t.status,
        "filename": t.filename, "page_count": t.page_count,
        "clause_count": db.query(TemplateClause).filter(
            TemplateClause.template_id == t.id).count(),
        "processing_error": t.processing_error, "created_by": email,
        "created_at": t.created_at.isoformat(),
    }


@router.post("/upload", response_model=schemas.TemplateOut)
async def upload(file: UploadFile, name: str = Form(...),
                 contract_type: str | None = Form(default=None),
                 member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot upload templates")
    t = service.create_template(
        db, organization_id=member.organization_id, user_id=member.user_id,
        name=name, filename=file.filename or "template", data=await file.read(),
        contract_type=contract_type)
    return _out(db, t)


@router.get("/history", response_model=list[schemas.TemplateOut])
def history(name: str = Query(...), member=Depends(get_current_membership),
            db: Session = Depends(get_db)):
    rows = db.query(Template).filter(
        Template.organization_id == member.organization_id,
        Template.name == name).order_by(Template.version.desc()).all()
    return [_out(db, t) for t in rows]


@router.get("", response_model=list[schemas.TemplateOut])
def list_templates(contract_type: str | None = None, include_archived: bool = False,
                   active_only: bool = True, member=Depends(get_current_membership),
                   db: Session = Depends(get_db)):
    q = db.query(Template).filter(Template.organization_id == member.organization_id)
    if active_only:
        q = q.filter(Template.is_active.is_(True))
    if not include_archived:
        q = q.filter(Template.is_archived.is_(False))
    if contract_type:
        q = q.filter(Template.contract_type == contract_type)
    return [_out(db, t) for t in q.order_by(Template.name, Template.version.desc()).limit(200).all()]


@router.get("/{template_id}", response_model=schemas.TemplateOut)
def get_template(template_id: str, member=Depends(get_current_membership),
                 db: Session = Depends(get_db)):
    return _out(db, _get_owned(db, template_id, member.organization_id))


@router.get("/{template_id}/clauses", response_model=list[schemas.TemplateClauseOut])
def list_clauses(template_id: str, member=Depends(get_current_membership),
                 db: Session = Depends(get_db)):
    t = _get_owned(db, template_id, member.organization_id)
    rows = db.query(TemplateClause).filter(
        TemplateClause.template_id == t.id).order_by(TemplateClause.order_index).all()
    return [{
        "id": str(c.id), "section_number": c.section_number, "title": c.title,
        "text": c.text, "page_start": c.page_start, "page_end": c.page_end,
        "order_index": c.order_index, "predicted_category": c.predicted_category,
        "classification_confidence": c.classification_confidence} for c in rows]


@router.get("/{template_id}/download")
def download(template_id: str, member=Depends(get_current_membership),
             db: Session = Depends(get_db)):
    t = _get_owned(db, template_id, member.organization_id)
    data = StorageService().load(t.storage_key)
    mime = "application/pdf" if t.filename.lower().endswith(".pdf") else "application/octet-stream"
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{t.filename}"'})


@router.post("/{template_id}/activate", response_model=schemas.TemplateOut)
def activate(template_id: str, member=Depends(get_current_membership),
             db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot manage templates")
    t = _get_owned(db, template_id, member.organization_id)
    for sib in db.query(Template).filter(
            Template.organization_id == member.organization_id,
            Template.name == t.name, Template.id != t.id).all():
        sib.is_active = False
    t.is_active = True
    db.commit()
    record(db, action="template.activated", organization_id=member.organization_id,
           user_id=member.user_id, object_type="template", object_id=str(t.id),
           detail=f"{t.name} v{t.version}")
    return _out(db, t)


@router.post("/{template_id}/reprocess", response_model=schemas.TemplateOut)
def reprocess(template_id: str, member=Depends(get_current_membership),
              db: Session = Depends(get_db)):
    if member.role not in _WRITERS:
        raise HTTPException(403, "Viewers cannot manage templates")
    return _out(db, service.reprocess(
        db, _get_owned(db, template_id, member.organization_id)))


@router.delete("/{template_id}", dependencies=[Depends(require_roles("admin", "contract_manager"))])
def archive(template_id: str, member=Depends(get_current_membership),
            db: Session = Depends(get_db)):
    t = _get_owned(db, template_id, member.organization_id)
    t.is_archived = True
    db.commit()
    record(db, action="template.archived", organization_id=member.organization_id,
           user_id=member.user_id, object_type="template", object_id=str(t.id))
    return {"ok": True}
