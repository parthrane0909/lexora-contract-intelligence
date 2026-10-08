"""Phase 2: structured document content + clauses; contracts.processing_error."""
revision = "0002_phase2_document"
down_revision = "0001_phase1_foundation"

from alembic import op
import sqlalchemy as sa


def _org_contract(table):
    op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])
    op.create_index(f"ix_{table}_contract_id", table, ["contract_id"])


def upgrade():
    op.create_table(
        "parsed_documents",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE")),
        sa.Column("parser_name", sa.String(64)),
        sa.Column("parser_version", sa.String(32)),
        sa.Column("page_count", sa.Integer, default=0),
        sa.Column("full_text", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE")),
        sa.Column("parsed_document_id", sa.Uuid(native_uuid=False), sa.ForeignKey("parsed_documents.id", ondelete="CASCADE")),
        sa.Column("page_number", sa.Integer, index=True),
        sa.Column("text", sa.Text),
        sa.Column("char_start", sa.Integer),
        sa.Column("char_end", sa.Integer),
        sa.Column("paragraph_spans", sa.Text),
    )
    op.create_table(
        "document_sections",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE")),
        sa.Column("heading", sa.String(512)),
        sa.Column("level", sa.Integer, default=1),
        sa.Column("section_number", sa.String(64)),
        sa.Column("order_index", sa.Integer, index=True),
        sa.Column("page_start", sa.Integer),
        sa.Column("page_end", sa.Integer),
        sa.Column("char_start", sa.Integer),
        sa.Column("char_end", sa.Integer),
    )
    op.create_table(
        "clauses",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE")),
        sa.Column("section_number", sa.String(64)),
        sa.Column("title", sa.String(512)),
        sa.Column("text", sa.Text),
        sa.Column("page_start", sa.Integer),
        sa.Column("page_end", sa.Integer),
        sa.Column("char_start", sa.Integer),
        sa.Column("char_end", sa.Integer),
        sa.Column("order_index", sa.Integer, index=True),
        sa.Column("segmenter_name", sa.String(64)),
        sa.Column("segmenter_version", sa.String(32)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    for t in ("parsed_documents", "document_pages", "document_sections", "clauses"):
        _org_contract(t)
    op.create_index("ix_document_pages_parsed_document_id", "document_pages", ["parsed_document_id"])
    op.add_column("contracts", sa.Column("processing_error", sa.Text, nullable=True))


def downgrade():
    op.drop_column("contracts", "processing_error")
    op.drop_index("ix_document_pages_parsed_document_id", "document_pages")
    for t in ("parsed_documents", "document_pages", "document_sections", "clauses"):
        op.drop_index(f"ix_{t}_organization_id", t)
        op.drop_index(f"ix_{t}_contract_id", t)
    for t in ("clauses", "document_sections", "document_pages", "parsed_documents"):
        op.drop_table(t)
