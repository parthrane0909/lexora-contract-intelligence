"""Phase 1 tables: users, organizations, members, contracts, documents, audit_logs.
Phase 2 tables: parsed_documents, document_pages, document_sections, clauses.
Phase 3 tables: analysis_runs, clause_classifications.
Phase 4 tables: policies, risk_findings.

Future phases add: entities, policy_versions (if split),
templates, contract_comparisons, obligations, reviews, feedback,
model_versions, notifications. Qdrant holds vectors with org/contract/clause refs.
"""
import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy import Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid_pk():
    # Portable: native UUID on PG, CHAR(36) on sqlite.
    return mapped_column(Uuid(native_uuid=False), primary_key=True, default=uuid.uuid4)


def _now():
    return mapped_column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.timezone.utc))


def _now_on_update():
    """Insert timestamp + refresh on every UPDATE (Part 5: contracts expose
    `updated_at`, so a metadata edit must actually move it)."""
    stamp = lambda: datetime.datetime.now(datetime.timezone.utc)  # noqa: E731
    return mapped_column(DateTime(timezone=True), default=stamp, onupdate=stamp)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at = _now()
    updated_at = _now()
    members: Mapped[list["OrganizationMember"]] = relationship(back_populates="organization", cascade="all,delete")


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    created_at = _now()
    memberships: Mapped[list["OrganizationMember"]] = relationship(back_populates="user", cascade="all,delete")


VALID_ROLES = ("admin", "legal_reviewer", "contract_manager", "viewer")


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="admin")
    created_at = _now()
    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Contract(Base):
    __tablename__ = "contracts"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    # Part 5: ONE primary contract-level category for the whole document
    # (e.g. "SaaS Agreement", "Non-Disclosure Agreement"); NULL == the
    # explicit "Other / Unspecified" state. Member-overridable, never derived
    # from a single clause's classification (see contracts/category.py).
    contract_type: Mapped[str | None] = mapped_column(String(128), index=True)
    processing_status: Mapped[str] = mapped_column(String(32), default="uploaded", index=True)
    analysis_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    storage_key: Mapped[str] = mapped_column(String(1024))
    model_versions: Mapped[str | None] = mapped_column(Text)  # JSON string in Phase 1
    processing_error: Mapped[str | None] = mapped_column(Text)  # Phase 2: failure reason
    # Phase 17B: automatic semantic indexing is a SEPARATE lifecycle from
    # processing — a Qdrant outage must never flip processing_status.
    # pending → queued → processing → indexed | failed (failed = retryable)
    semantic_index_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    semantic_index_error: Mapped[str | None] = mapped_column(Text)
    lifecycle_status_override: Mapped[str | None] = mapped_column(String(16))  # Phase 9: manual status
    counterparty: Mapped[str | None] = mapped_column(String(256), index=True)  # Phase 11: member-supplied only
    # Part 5: user-controlled organizational marker (NOT a risk score).
    # Set only through PATCH /contracts/{id}; reprocessing never touches it.
    is_important: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at = _now()
    updated_at = _now_on_update()
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    documents: Mapped[list["Document"]] = relationship(back_populates="contract", cascade="all,delete")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at = _now()
    contract: Mapped[Contract] = relationship(back_populates="documents")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(128), index=True)
    object_type: Mapped[str | None] = mapped_column(String(128))
    object_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at = _now()


class ParsedDocument(Base):
    """One row per successful parse (re-runs replace it). Full text + provenance."""

    __tablename__ = "parsed_documents"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    parser_name: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    full_text: Mapped[str] = mapped_column(Text)
    created_at = _now()


class DocumentPage(Base):
    """Verbatim page text + offsets into ParsedDocument.full_text.

    paragraph_spans is a JSON list of [char_start, char_end] pairs so
    paragraphs survive without a separate table.
    """

    __tablename__ = "document_pages"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    parsed_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parsed_documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, index=True)
    text: Mapped[str] = mapped_column(Text)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    paragraph_spans: Mapped[str | None] = mapped_column(Text)


class DocumentSection(Base):
    """Detected headings in document order."""

    __tablename__ = "document_sections"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    heading: Mapped[str] = mapped_column(String(512))
    level: Mapped[int] = mapped_column(Integer, default=1)
    section_number: Mapped[str | None] = mapped_column(String(64))
    order_index: Mapped[int] = mapped_column(Integer, index=True)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)


class Clause(Base):
    """Segmented clause with source location. Stable UUID PK: future risk
    findings reference this row to point at the exact source clause/page."""

    __tablename__ = "clauses"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    section_number: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, index=True)
    segmenter_name: Mapped[str] = mapped_column(String(64))
    segmenter_version: Mapped[str] = mapped_column(String(32))
    created_at = _now()


class AnalysisRun(Base):
    """One model run over a contract (Phase 3: clause_classification).

    Later phases add kinds (risk, ner, retrieval...). Run ID links every
    prediction back to the model version + stats that produced it.
    """

    __tablename__ = "analysis_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    model_name: Mapped[str | None] = mapped_column(String(256))
    model_version: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    stats: Mapped[str | None] = mapped_column(Text)  # JSON
    error: Mapped[str | None] = mapped_column(Text)
    created_at = _now()


class ClauseClassification(Base):
    """One prediction for one clause. Append-only: re-running a model adds
    rows, never overwrites source clause data; versions stay comparable."""

    __tablename__ = "clause_classifications"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    clause_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clauses.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("analysis_runs.id"))
    model_name: Mapped[str | None] = mapped_column(String(256))
    model_version: Mapped[str] = mapped_column(String(128), index=True)
    predicted_category: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column()
    probabilities: Mapped[str | None] = mapped_column(Text)  # JSON {category: p}
    created_at = _now()


class Policy(Base):
    """One version of one org's position for one category (Phase 4.3).

    Updates insert a new row (version+1) and deactivate the old one — history
    is append-only so findings always resolve to the exact policy version.
    params is validated JSON (see policy_engine.service.validate_policy).
    """

    __tablename__ = "policies"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[str] = mapped_column(Text)  # JSON
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at = _now()


class RiskFinding(Base):
    """One deterministic finding for one clause (Phase 4.7).

    Append-only per risk-analysis run: re-analysis adds rows under a new
    analysis_runs row, preserving history. Classification / rule / policy
    deviation stay separate fields (spec §6) — never one merged score.
    """

    __tablename__ = "risk_findings"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    clause_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clauses.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("analysis_runs.id"))
    risk_category: Mapped[str] = mapped_column(String(64), index=True)
    finding_type: Mapped[str] = mapped_column(String(32), index=True)
    priority: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)  # clause excerpt (≤1000 chars)
    section_number: Mapped[str | None] = mapped_column(String(64))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(32), index=True)
    rule_version: Mapped[str] = mapped_column(String(16))
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("policies.id"))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    contract_value: Mapped[str | None] = mapped_column(Text)  # JSON
    expected_value: Mapped[str | None] = mapped_column(Text)  # JSON
    priority_reasons: Mapped[str | None] = mapped_column(Text)  # JSON list
    # JSON: the deterministic facts the rule saw for this clause (extractors
    # output) — the persisted evidence behind the finding, never re-derived.
    extracted_facts: Mapped[str | None] = mapped_column(Text)
    classification_model: Mapped[str | None] = mapped_column(String(256))
    classification_version: Mapped[str | None] = mapped_column(String(128))
    classification_confidence: Mapped[float | None] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    created_at = _now()


class Template(Base):
    """One version of an org-approved contract template (Phase 5.3).

    Versioning mirrors policies: same (organization, name) with a higher
    version deactivates the old row. Each version carries its own bytes and
    template_clauses, so comparisons snapshot an exact template version.
    """

    __tablename__ = "templates"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    contract_type: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="processing", index=True)
    storage_key: Mapped[str] = mapped_column(String(1024))
    filename: Mapped[str] = mapped_column(String(512))
    full_text: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    parser_version: Mapped[str | None] = mapped_column(String(64))
    segmenter_version: Mapped[str | None] = mapped_column(String(64))
    processing_error: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at = _now()


class TemplateClause(Base):
    """Template clause: same structural concepts as Clause (section/title/
    text/page/char offsets) but owned by a template version. predicted_
    fields are filled when the classifier is available, else null —
    comparison never requires them."""

    __tablename__ = "template_clauses"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    section_number: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, index=True)
    predicted_category: Mapped[str | None] = mapped_column(String(64))
    classification_confidence: Mapped[float | None] = mapped_column()
    classification_version: Mapped[str | None] = mapped_column(String(128))
    created_at = _now()


class Comparison(Base):
    """One contract-vs-template comparison run (Phase 5.5). Append-only:
    re-comparing adds rows, preserving history per template version."""

    __tablename__ = "comparisons"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    engine_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="done", index=True)
    stats: Mapped[str | None] = mapped_column(Text)  # JSON
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at = _now()


class ComparisonFinding(Base):
    """One pairwise result (Phase 5.6). difference_type ∈ added/missing/
    modified/unchanged ("removed" ≡ "missing": a template clause absent
    from the contract). materiality is deterministic (see comparison/engine)."""

    __tablename__ = "comparison_findings"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    comparison_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("comparisons.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    template_clause_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("template_clauses.id"))
    contract_clause_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clauses.id"))
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    difference_type: Mapped[str] = mapped_column(String(16), index=True)
    materiality: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    template_text: Mapped[str | None] = mapped_column(Text)
    contract_text: Mapped[str | None] = mapped_column(Text)
    value_diff: Mapped[str | None] = mapped_column(Text)  # JSON {field, template, contract}
    risk_finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("risk_findings.id"))
    reasons: Mapped[str | None] = mapped_column(Text)  # JSON list
    created_at = _now()


class RiskExplanation(Base):
    """One generated explanation for one finding (Phase 7.18). Append-only:
    regeneration inserts a new row; evidence_snapshot freezes the exact
    sources used so old explanations stay auditable when policies or
    templates change later."""

    __tablename__ = "risk_explanations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risk_findings.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("analysis_runs.id"))
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    clause_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clauses.id"))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(256))
    model_version: Mapped[str | None] = mapped_column(String(128), index=True)
    prompt_version: Mapped[str | None] = mapped_column(String(16))
    evidence_snapshot: Mapped[str | None] = mapped_column(Text)  # JSON list
    explanation: Mapped[str | None] = mapped_column(Text)  # JSON object
    status: Mapped[str] = mapped_column(String(16), default="completed", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at = _now()


class Obligation(Base):
    """One extracted lifecycle item (Phase 9.4). Typed, filterable columns;
    anything uncertain is NULL (never fabricated). Extracted type/dates/
    values are immutable; `status` is explicit workflow state (§11)."""

    __tablename__ = "obligations"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    clause_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clauses.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    party: Mapped[str | None] = mapped_column(String(16))
    trigger: Mapped[str | None] = mapped_column(String(64))
    frequency: Mapped[str | None] = mapped_column(String(32))
    period_days: Mapped[int | None] = mapped_column(Integer)
    period_approximate: Mapped[bool] = mapped_column(Boolean, default=False)
    notice_period_days: Mapped[int | None] = mapped_column(Integer)
    cure_period_days: Mapped[int | None] = mapped_column(Integer)
    deadline: Mapped[str | None] = mapped_column(String(16), index=True)  # ISO date
    effective_date: Mapped[str | None] = mapped_column(String(16))
    expiration_date: Mapped[str | None] = mapped_column(String(16))
    renewal_period_days: Mapped[int | None] = mapped_column(Integer)
    auto_renew: Mapped[bool | None] = mapped_column(Boolean)
    renewal_date: Mapped[str | None] = mapped_column(String(16))
    notice_deadline: Mapped[str | None] = mapped_column(String(16))
    amount: Mapped[float | None] = mapped_column()
    currency: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="UNCONFIRMED", index=True)
    confidence: Mapped[float | None] = mapped_column()
    extraction_method: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(256))
    model_version: Mapped[str | None] = mapped_column(String(128))
    rule_id: Mapped[str | None] = mapped_column(String(32))
    source_text: Mapped[str | None] = mapped_column(Text)
    risk_finding_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("risk_findings.id"))
    created_at = _now()
    updated_at = _now()


class LifecycleEvent(Base):
    """One derived event (Phase 9.15). Created per extraction run; existing
    (obligation, type, date) rows are kept, never duplicated. Human-confirmed
    state lives on obligations/reviews, not here. Status is recomputed
    against today on every read."""

    __tablename__ = "lifecycle_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    event_date: Mapped[str | None] = mapped_column(String(16), index=True)
    trigger: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="UPCOMING", index=True)
    source_clause_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clauses.id"))
    description: Mapped[str | None] = mapped_column(Text)
    created_at = _now()
    updated_at = _now()


class ObligationReview(Base):
    """One human judgment on one obligation (Phase 9.12). Mirrors the Phase 8
    pattern (append-only, snapshot, no mutation of the extraction) without
    touching Phase 8 tables — different snapshot/correction semantics."""

    __tablename__ = "obligation_reviews"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)  # confirm|reject|modify
    comment: Mapped[str | None] = mapped_column(Text)
    correction_type: Mapped[str | None] = mapped_column(String(32))
    corrected_type: Mapped[str | None] = mapped_column(String(32))
    corrected_date: Mapped[str | None] = mapped_column(String(16))
    corrected_notice_days: Mapped[int | None] = mapped_column(Integer)
    corrected_party: Mapped[str | None] = mapped_column(String(16))
    corrected_trigger: Mapped[str | None] = mapped_column(String(64))
    corrected_status: Mapped[str | None] = mapped_column(String(16))
    snapshot: Mapped[str | None] = mapped_column(Text)  # JSON of original extraction
    created_at = _now()


class LifecycleFeedback(Base):
    """One evaluation row per obligation review (Phase 9.13). Original +
    correction, never a training trigger."""

    __tablename__ = "lifecycle_feedback"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligation_reviews.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_role: Mapped[str | None] = mapped_column(String(32))
    original_type: Mapped[str | None] = mapped_column(String(32))
    original_deadline: Mapped[str | None] = mapped_column(String(16))
    original_notice_days: Mapped[int | None] = mapped_column(Integer)
    original_party: Mapped[str | None] = mapped_column(String(16))
    original_trigger: Mapped[str | None] = mapped_column(String(64))
    original_confidence: Mapped[float | None] = mapped_column()
    extraction_method: Mapped[str | None] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16), index=True)
    correction_type: Mapped[str | None] = mapped_column(String(32))
    corrected_type: Mapped[str | None] = mapped_column(String(32))
    corrected_date: Mapped[str | None] = mapped_column(String(16))
    corrected_notice_days: Mapped[int | None] = mapped_column(Integer)
    reviewer_comment: Mapped[str | None] = mapped_column(Text)
    created_at = _now()


class ReviewDecision(Base):
    """One human judgment on one finding (Phase 8.4). Append-only: concurrent
    reviewers each get their own row; history is never overwritten. The
    finding_snapshot freezes what the reviewer saw (finding + explanation
    versions), so the review stays meaningful after later changes."""

    __tablename__ = "review_decisions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risk_findings.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    decision: Mapped[str] = mapped_column(String(24), index=True)
    comment: Mapped[str | None] = mapped_column(Text)  # plain text, length-limited
    correction_type: Mapped[str | None] = mapped_column(String(32))
    corrected_category: Mapped[str | None] = mapped_column(String(64))
    corrected_priority: Mapped[str | None] = mapped_column(String(16))
    finding_snapshot: Mapped[str | None] = mapped_column(Text)  # JSON
    explanation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("risk_explanations.id"))
    explanation_version: Mapped[str | None] = mapped_column(String(128))  # model@prompt
    created_at = _now()


class FeedbackLedger(Base):
    """One evaluation-ready row per review (Phase 8.10). Original AI output +
    human decision + optional correction. Never triggers retraining."""

    __tablename__ = "feedback_ledger"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("risk_findings.id", ondelete="CASCADE"), index=True)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_decisions.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_role: Mapped[str | None] = mapped_column(String(32))
    original_category: Mapped[str | None] = mapped_column(String(64))
    original_priority: Mapped[str | None] = mapped_column(String(16))
    original_rule: Mapped[str | None] = mapped_column(String(32))
    original_classifier_prediction: Mapped[str | None] = mapped_column(String(64))
    original_classifier_confidence: Mapped[float | None] = mapped_column()
    original_policy_version: Mapped[int | None] = mapped_column(Integer)
    explanation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("risk_explanations.id"))
    explanation_version: Mapped[str | None] = mapped_column(String(128))
    decision: Mapped[str] = mapped_column(String(24), index=True)
    correction_type: Mapped[str | None] = mapped_column(String(32))
    corrected_category: Mapped[str | None] = mapped_column(String(64))
    corrected_priority: Mapped[str | None] = mapped_column(String(16))
    reviewer_comment: Mapped[str | None] = mapped_column(Text)
    created_at = _now()


class SemanticIndex(Base):
    """Indexing state per semantic object (Phase 6.7). The vector itself
    lives in Qdrant; this row records WHAT was embedded, with WHICH model,
    and WHETHER it succeeded. Unique per (org, type, id, model) so a model
    change re-indexes into new point IDs instead of mixing geometries."""

    __tablename__ = "semantic_index"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    object_type: Mapped[str] = mapped_column(String(32), index=True)  # clause|template_clause|policy
    object_id: Mapped[str] = mapped_column(String(64), index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    embedding_model: Mapped[str] = mapped_column(String(256))
    embedding_version: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="indexed", index=True)  # indexed|failed
    error: Mapped[str | None] = mapped_column(Text)
    vector_id: Mapped[str | None] = mapped_column(String(64))  # Qdrant point ID
    indexed_at = _now()


class Notification(Base):
    """One notification for one recipient (Phase 10.10). Append-only with a
    deterministic idempotency key (org+event/recipient+type+window) so
    duplicate scans never duplicate rows. Status = delivery state; is_read
    is per-recipient read state (never shared across users)."""

    __tablename__ = "notifications"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True)
    obligation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"))
    lifecycle_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lifecycle_events.id", ondelete="CASCADE"))
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    notification_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[str | None] = mapped_column(String(16))
    window_days: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    read_at = _now()
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True)
    created_at = _now()
    updated_at = _now()


class NotificationDelivery(Base):
    """Append-only delivery attempts (Phase 10.9). Failures preserved with
    reason; bounded retries via attempt_count (never infinite)."""

    __tablename__ = "notification_deliveries"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    notification_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("notifications.id", ondelete="CASCADE"), index=True)
    recipient_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    provider: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_message_id: Mapped[str | None] = mapped_column(String(256))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at = _now()
    created_at = _now()


class NotificationPreference(Base):
    """Per-member opt-out per notification type (Phase 10.7). Absent row =
    enabled (documented default). Viewers may read, never write others' rows."""

    __tablename__ = "notification_preferences"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    notification_type: Mapped[str] = mapped_column(String(32), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    channel: Mapped[str] = mapped_column(String(16), default="inapp")
    created_at = _now()
    updated_at = _now()


class RecurrenceDefinition(Base):
    """Recurrence rule, separate from generated instances (Phase 10.17).
    One active definition per obligation."""

    __tablename__ = "recurrence_definitions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    frequency: Mapped[str] = mapped_column(String(16), index=True)
    interval: Mapped[int] = mapped_column(Integer, default=1)
    anchor_type: Mapped[str] = mapped_column(String(32))
    anchor_date: Mapped[str] = mapped_column(String(16))
    next_due_date: Mapped[str | None] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at = _now()
    updated_at = _now()


class ObligationEventInstance(Base):
    """One generated occurrence (Phase 10.18). Unique per (recurrence,
    due_date); completion is human-recorded and auditable."""

    __tablename__ = "obligation_event_instances"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    obligation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("obligations.id", ondelete="CASCADE"), index=True)
    recurrence_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recurrence_definitions.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    due_date: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16), default="UPCOMING", index=True)
    completed_at = _now()
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    source_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("lifecycle_events.id"))
    created_at = _now()
    updated_at = _now()


class MLDataset(Base):
    """Versioned dataset metadata (Phase 12). Examples live in the versioned
    JSONL artifact (never duplicated here); this row is the reproducible
    manifest + counts + hashes."""

    __tablename__ = "ml_datasets"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(64), index=True)
    created_from: Mapped[str] = mapped_column(String(32), default="feedback")
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    usable_count: Mapped[int] = mapped_column(Integer, default=0)
    excluded_count: Mapped[int] = mapped_column(Integer, default=0)
    manifest: Mapped[str | None] = mapped_column(Text)  # JSON
    artifact_path: Mapped[str | None] = mapped_column(String(1024))
    created_at = _now()


class MLModelVersion(Base):
    """Model registry row (Phase 12). Global per (name, version); at most one
    `production` row per task — enforced in promotion code, never silently."""

    __tablename__ = "ml_model_versions"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(128), index=True)
    task: Mapped[str] = mapped_column(String(64), default="clause_classification")
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    base_model: Mapped[str | None] = mapped_column(String(256))
    metrics: Mapped[str | None] = mapped_column(Text)  # JSON
    artifact_location: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    created_at = _now()


class MLEvaluationRun(Base):
    """One evaluation of one model on one frozen split (Phase 12)."""

    __tablename__ = "ml_evaluation_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(128), index=True)
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    split: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[str | None] = mapped_column(Text)  # JSON
    report_path: Mapped[str | None] = mapped_column(String(1024))
    confusion_path: Mapped[str | None] = mapped_column(String(1024))
    created_at = _now()


class MLPromotionEvent(Base):
    """Auditable promotion/rejection decision (Phase 12)."""

    __tablename__ = "ml_promotion_events"
    id: Mapped[uuid.UUID] = _uuid_pk()
    model_name: Mapped[str] = mapped_column(String(128), index=True)
    model_version: Mapped[str] = mapped_column(String(128), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    gates: Mapped[str | None] = mapped_column(Text)  # JSON gate results
    reason: Mapped[str | None] = mapped_column(Text)
    created_at = _now()


class MLExperiment(Base):
    """One tracked classification experiment (Phase 13). Config + dataset +
    seed frozen at registration; status transitions registered → running →
    completed/failed. Never mutates production."""

    __tablename__ = "ml_experiments"
    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="classification")
    matrix_id: Mapped[str | None] = mapped_column(String(64), index=True)  # Phase 14: groups multi-seed/matrix runs
    config: Mapped[str | None] = mapped_column(Text)  # JSON resolved config
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column(Integer, default=42)
    status: Mapped[str] = mapped_column(String(32), default="registered", index=True)
    candidate_version: Mapped[str | None] = mapped_column(String(128))
    metrics: Mapped[str | None] = mapped_column(Text)  # JSON summary
    costs: Mapped[str | None] = mapped_column(Text)  # JSON timings/memory
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at = _now()
