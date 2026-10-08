"""Contract routes: upload/list/get/analyze/archive + download + document/clauses.

Part 5 (metadata API): the list endpoint gained validated server-side
filters (category, analysis status, important-only, processing status),
sorting and pagination; PATCH gained the important/starred flag and a
validated contract category. DELETE stays the existing ARCHIVE route —
see the endpoint docstring for the deletion consistency strategy.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import false, func
from sqlalchemy.orm import Session

from app.core.deps import get_current_membership, require_roles
from app.db.session import get_db
from app.models import Clause, Contract, DocumentPage, DocumentSection, ParsedDocument
from app.modules.audit.service import record
from app.modules.contracts import schemas, service
from app.modules.contracts.category import (CATEGORIES, UNSPECIFIED, parse_category)
from app.modules.document import schemas as doc_schemas
from app.modules.portfolio.service import kpi_contract_ids
from app.modules.risk_engine.state import ANALYSIS_STATUSES, analysis_lifecycle
from app.modules.search.indexer import index_contract
from app.modules.storage.service import StorageService

router = APIRouter(prefix="/contracts", tags=["contracts"])

# Allowed list sort orders (validated — no client-supplied SQL reaches the
# query builder). `category` coalesces NULL so PostgreSQL and SQLite order
# "Other / Unspecified" the same way.
SORTS = {
    "newest": (Contract.created_at.desc(),),
    "oldest": (Contract.created_at.asc(),),
    "filename": (Contract.filename.asc(), Contract.id.asc()),
    "category": (func.coalesce(Contract.contract_type, UNSPECIFIED).asc(),
                 Contract.filename.asc()),
}


def _parse_id(contract_id: str):
    import uuid as _uuid

    try:
        return _uuid.UUID(contract_id)
    except ValueError:
        raise HTTPException(404, "Contract not found")


def _get_owned(db: Session, contract_id: str, org_id) -> Contract:
    c = db.query(Contract).filter(
        Contract.id == _parse_id(contract_id), Contract.organization_id == org_id
    ).first()
    if not c:
        raise HTTPException(404, "Contract not found")
    return c


def _out(c: Contract) -> dict:
    return {
        "id": str(c.id), "filename": c.filename, "contract_type": c.contract_type,
        # Never-null view of the same column: NULL category is reported as
        # the explicit "Other / Unspecified" state, never as an empty cell.
        "contract_category": c.contract_type or UNSPECIFIED,
        "counterparty": c.counterparty,
        "processing_status": c.processing_status, "analysis_status": c.analysis_status,
        "semantic_index_status": c.semantic_index_status or "pending",
        "is_important": bool(c.is_important),
        "is_archived": c.is_archived, "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.post("/upload", response_model=schemas.ContractOut)
async def upload(
    file: UploadFile,
    contract_type: str | None = None,
    member=Depends(get_current_membership),
    db: Session = Depends(get_db),
):
    if member.role == "viewer":
        raise HTTPException(403, "Viewers cannot upload")
    data = await file.read()
    c = service.create_contract(
        db, organization_id=member.organization_id, user_id=member.user_id,
        filename=file.filename or "upload", data=data, contract_type=contract_type,
    )
    return _out(c)


@router.get("", response_model=list[schemas.ContractOut])
def list_contracts(
    response: Response,
    search: str | None = None,
    status: str | None = Query(default=None, description="processing_status filter"),
    analysis_status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    important: bool | None = Query(default=None),
    high_risk: bool = Query(default=False,
        description="true → only contracts with a qualifying unresolved "
                    "high/critical finding (the High-Risk KPI subset)"),
    renewing_within_days: int | None = Query(default=None, ge=1, le=365,
        description="only contracts with a stored renewal date within N days"),
    include_archived: bool = False,
    sort: str = Query(default="newest"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=200, ge=1, le=200),
    member=Depends(get_current_membership),
    db: Session = Depends(get_db),
):
    """Organization-scoped contract listing with validated server-side filters.

    Filters are ANDed together and always applied to PostgreSQL — never to
    the index, never by the frontend (search results and this listing share
    the same vocabulary and the same semantics).

      category        exact stored category; "Other / Unspecified" (or
                      "Unspecified") selects rows without one
      analysis_status the authoritative rollup column (risk_engine.state.
                      ANALYSIS_STATUSES) — never re-derived here
      important       true → only starred contracts
      high_risk       true → only contracts with ≥1 qualifying unresolved
                      high/critical finding (exact KPI semantics, distinct
                      contracts — never a count of findings)
      renewing_within_days  only contracts whose STORED renewal date falls in
                      the window (deduplicated by contract, no invented dates)
      status          processing_status (pre-existing filter)
      sort            newest | oldest | filename | category
      page / limit    pagination; the body stays a plain array (existing
                      clients), the total is reported in X-Total-Count
    """
    q = db.query(Contract).filter(Contract.organization_id == member.organization_id)
    if not include_archived:
        q = q.filter(Contract.is_archived.is_(False))
    if search:
        q = q.filter(Contract.filename.ilike(f"%{search}%"))
    if status:
        if status not in service.PROCESSING_STATUSES:
            raise HTTPException(400, f"Unknown processing status {status!r}. "
                                     f"Expected one of: {', '.join(service.PROCESSING_STATUSES)}")
        q = q.filter(Contract.processing_status == status)
    if analysis_status:
        if analysis_status not in ANALYSIS_STATUSES:
            raise HTTPException(400, f"Unknown analysis status {analysis_status!r}. "
                                     f"Expected one of: {', '.join(ANALYSIS_STATUSES)}")
        q = q.filter(Contract.analysis_status == analysis_status)
    if category is not None:
        try:
            wanted = parse_category(category)
        except ValueError as e:
            raise HTTPException(400, str(e))
        # NULL category IS the explicit "Other / Unspecified" state.
        q = (q.filter(Contract.contract_type.is_(None)) if wanted == UNSPECIFIED
             else q.filter(Contract.contract_type == wanted))
    if important is not None:
        q = q.filter(Contract.is_important.is_(important))
    kpi_ids = kpi_contract_ids(db, member.organization_id, high_risk=high_risk,
                               renewing_within_days=renewing_within_days)
    if kpi_ids is not None:
        # Empty set → no contract qualifies: filter on an impossible id rather
        # than dropping the constraint (an empty result must stay an empty
        # result, never an unfiltered list).
        q = q.filter(Contract.id.in_(kpi_ids)) if kpi_ids else q.filter(false())
    if sort not in SORTS:
        raise HTTPException(400, f"Unknown sort {sort!r}. "
                                 f"Expected one of: {', '.join(SORTS)}")
    total = q.count()
    rows = (q.order_by(*SORTS[sort])
            .offset((page - 1) * limit).limit(limit).all())
    response.headers["X-Total-Count"] = str(total)
    return [_out(c) for c in rows]


@router.get("/{contract_id}", response_model=schemas.ContractDetailOut)
def get_contract(contract_id: str, member=Depends(get_current_membership), db: Session = Depends(get_db)):
    c = _get_owned(db, contract_id, member.organization_id)
    d = _out(c)
    parsed = db.query(ParsedDocument).filter(ParsedDocument.contract_id == c.id).first()
    d.update({
        "storage_key": c.storage_key,
        "model_versions": c.model_versions,
        "processing_error": c.processing_error,
        "semantic_index_error": c.semantic_index_error,
        "page_count": parsed.page_count if parsed else None,
        "clause_count": db.query(Clause).filter(Clause.contract_id == c.id).count(),
        # Derived, never stored: processing / classification / risk analysis
        # are reported separately so "complete" always means what it says.
        "analysis_lifecycle": analysis_lifecycle(db, c),
    })
    return d


@router.get("/{contract_id}/summary")
def get_summary(contract_id: str, member=Depends(get_current_membership),
                db: Session = Depends(get_db)):
    """DESCRIPTIVE contract summary: what this agreement says, assembled
    from contract metadata, clause titles and clause text only. Contains no
    risk counts, findings, rules or recommendations — those belong to
    GET /{contract_id}/risk-assessment. Works before risk analysis exists."""
    from app.modules.contracts.summary import contract_summary

    c = _get_owned(db, contract_id, member.organization_id)
    return contract_summary(db, c)


@router.get("/{contract_id}/download")
def download(contract_id: str, member=Depends(get_current_membership), db: Session = Depends(get_db)):
    c = _get_owned(db, contract_id, member.organization_id)
    data = StorageService().load(c.storage_key)
    mime = "application/pdf" if c.filename.lower().endswith(".pdf") else "application/octet-stream"
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{c.filename}"'})


@router.post("/{contract_id}/analyze", response_model=schemas.ContractOut)
def analyze(contract_id: str, member=Depends(get_current_membership), db: Session = Depends(get_db)):
    if member.role == "viewer":
        raise HTTPException(403, "Viewers cannot start analysis")
    c = _get_owned(db, contract_id, member.organization_id)
    return _out(service.enqueue_analysis(
        db, organization_id=member.organization_id, user_id=member.user_id, contract=c))


@router.delete("/{contract_id}", dependencies=[Depends(require_roles("admin", "contract_manager"))])
def archive(contract_id: str, member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """ARCHIVE (soft delete) — the application's deletion semantics.

    Consistency strategy (Part 5 Step 9), deliberately NOT a SQL cascade:

      * PostgreSQL  the contract row and every related record (clauses,
                    classifications, findings, runs, obligations) are KEPT;
                    `is_archived` flips to true. Default listing hides it.
      * Elasticsearch the same contract is republished with
                    is_archived=true, so default search results drop it
                    (best-effort through index_contract(); an ES outage can
                    never corrupt or roll back PostgreSQL — the search API
                    re-verifies against PostgreSQL anyway).
      * Qdrant      untouched: the contract still exists, so its semantic
                    index stays valid. No collection is ever dropped.
      * Files       kept: the original upload stays downloadable and can be
                    restored/audited later.
      * Audit       contract.archived is recorded (existing audit service).

    Repeated deletion is idempotent (200 again), an unknown or
    other-organization contract is 404, and an archive is NEVER silently
    converted into a permanent purge — no permanent-delete route exists.
    """
    c = _get_owned(db, contract_id, member.organization_id)
    already_archived = bool(c.is_archived)
    c.is_archived = True
    db.commit()
    record(db, action="contract.archived", organization_id=member.organization_id,
           user_id=member.user_id, object_type="contract", object_id=str(c.id))
    # Part 2: archived contracts are indexed with is_archived=true and are
    # filtered out of default search results (best-effort).
    index_contract(c)
    return {"ok": True, "id": str(c.id), "archived": True,
            "permanent": False, "already_archived": already_archived}


@router.patch("/{contract_id}", response_model=schemas.ContractOut,
              dependencies=[Depends(require_roles("admin", "contract_manager"))])
def update_metadata(contract_id: str, body: schemas.ContractPatchIn,
                    member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """Member-supplied metadata only (counterparty, category, is_important).
    Never inferred — NULL category means "Other / Unspecified".

    `contract_category` is the validated Part 5 field (must be one of
    contracts.category.CATEGORIES, "Other / Unspecified" stores NULL);
    `contract_type` stays the free-text legacy field exactly as Phase 11
    shipped it. When both are sent, `contract_category` wins. Omitted fields
    (null in the body) are left untouched, so repeated identical PATCHes are
    idempotent and reprocessing never resets `is_important` or the category.
    """
    c = _get_owned(db, contract_id, member.organization_id)
    if body.counterparty is not None:
        cleaned = body.counterparty.strip()
        if len(cleaned) > 256:
            raise HTTPException(400, "counterparty must be <= 256 characters")
        c.counterparty = cleaned or None
    if body.contract_category is not None:
        try:
            wanted = parse_category(body.contract_category)
        except ValueError as e:
            raise HTTPException(400, str(e))
        c.contract_type = None if wanted == UNSPECIFIED else wanted
    elif body.contract_type is not None:
        cleaned_type = body.contract_type.strip()
        if len(cleaned_type) > 128:
            raise HTTPException(400, "contract_type must be <= 128 characters")
        c.contract_type = cleaned_type or None
    if body.is_important is not None:
        c.is_important = bool(body.is_important)
    db.commit()
    record(db, action="contract.metadata_updated", organization_id=member.organization_id,
           user_id=member.user_id, object_type="contract", object_id=str(c.id),
           detail=f"category={c.contract_type or UNSPECIFIED} "
                  f"important={c.is_important}")
    # Part 2: searchable metadata changed → refresh the search document
    # (best-effort; the API re-reads PostgreSQL anyway).
    index_contract(c)
    return _out(c)


# ------------------------------------------------- document & clauses ----


def _get_parsed(db: Session, contract: Contract) -> ParsedDocument:
    parsed = db.query(ParsedDocument).filter(ParsedDocument.contract_id == contract.id).first()
    if not parsed:
        raise HTTPException(404, "No parsed document yet (run analysis first)")
    return parsed


def _clause_out(cl: Clause) -> dict:
    return {
        "id": str(cl.id), "section_number": cl.section_number, "title": cl.title,
        "text": cl.text, "page_start": cl.page_start, "page_end": cl.page_end,
        "char_start": cl.char_start, "char_end": cl.char_end,
        "order_index": cl.order_index, "segmenter_name": cl.segmenter_name,
        "segmenter_version": cl.segmenter_version,
    }


@router.get("/{contract_id}/document", response_model=doc_schemas.DocumentOut)
def get_document(contract_id: str, member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """Structured document: pages, sections, full text (org-scoped)."""
    c = _get_owned(db, contract_id, member.organization_id)
    parsed = _get_parsed(db, c)
    pages = db.query(DocumentPage).filter(
        DocumentPage.parsed_document_id == parsed.id).order_by(DocumentPage.page_number).all()
    sections = db.query(DocumentSection).filter(
        DocumentSection.contract_id == c.id).order_by(DocumentSection.order_index).all()
    return {
        "contract_id": str(c.id), "parser_name": parsed.parser_name,
        "parser_version": parsed.parser_version, "page_count": parsed.page_count,
        "pages": [{"page_number": p.page_number, "char_start": p.char_start, "char_end": p.char_end,
                   "paragraph_spans": json.loads(p.paragraph_spans or "[]"), "text": p.text}
                  for p in pages],
        "sections": [{"heading": s.heading, "level": s.level, "section_number": s.section_number,
                      "order_index": s.order_index, "page_start": s.page_start, "page_end": s.page_end,
                      "char_start": s.char_start, "char_end": s.char_end} for s in sections],
        "full_text": parsed.full_text,
    }


@router.get("/{contract_id}/clauses", response_model=list[doc_schemas.ClauseOut])
def list_clauses(contract_id: str, member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """Clauses in document order with source-location metadata (org-scoped)."""
    c = _get_owned(db, contract_id, member.organization_id)
    rows = db.query(Clause).filter(Clause.contract_id == c.id).order_by(Clause.order_index).all()
    return [_clause_out(cl) for cl in rows]


@router.get("/{contract_id}/clauses/{clause_id}", response_model=doc_schemas.ClauseOut)
def get_clause(contract_id: str, clause_id: str,
               member=Depends(get_current_membership), db: Session = Depends(get_db)):
    """Single clause: stable ID for future risk findings to reference."""
    c = _get_owned(db, contract_id, member.organization_id)
    try:
        import uuid as _uuid

        cid = _uuid.UUID(clause_id)
    except ValueError:
        raise HTTPException(404, "Clause not found")
    cl = db.query(Clause).filter(Clause.id == cid, Clause.contract_id == c.id).first()
    if not cl:
        raise HTTPException(404, "Clause not found")
    return _clause_out(cl)
