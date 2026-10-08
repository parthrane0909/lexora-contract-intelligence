"""Phase 5: templates (+versions), template_clauses, comparisons, comparison_findings."""
revision = "0005_phase5_company"
down_revision = "0004_phase4_risk"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "templates",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(256), index=True),
        sa.Column("contract_type", sa.String(128)),
        sa.Column("version", sa.Integer, default=1),
        sa.Column("is_active", sa.Boolean, default=True, index=True),
        sa.Column("status", sa.String(32), index=True),
        sa.Column("storage_key", sa.String(1024)),
        sa.Column("filename", sa.String(512)),
        sa.Column("full_text", sa.Text),
        sa.Column("page_count", sa.Integer, default=0),
        sa.Column("parser_version", sa.String(64)),
        sa.Column("segmenter_version", sa.String(64)),
        sa.Column("processing_error", sa.Text),
        sa.Column("is_archived", sa.Boolean, default=False),
        sa.Column("created_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "template_clauses",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("template_id", sa.Uuid(native_uuid=False), sa.ForeignKey("templates.id", ondelete="CASCADE"), index=True),
        sa.Column("section_number", sa.String(64)),
        sa.Column("title", sa.String(512)),
        sa.Column("text", sa.Text),
        sa.Column("page_start", sa.Integer),
        sa.Column("page_end", sa.Integer),
        sa.Column("char_start", sa.Integer),
        sa.Column("char_end", sa.Integer),
        sa.Column("order_index", sa.Integer, index=True),
        sa.Column("predicted_category", sa.String(64)),
        sa.Column("classification_confidence", sa.Float),
        sa.Column("classification_version", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "comparisons",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("template_id", sa.Uuid(native_uuid=False), sa.ForeignKey("templates.id", ondelete="CASCADE"), index=True),
        sa.Column("engine_version", sa.String(16)),
        sa.Column("status", sa.String(32), index=True),
        sa.Column("stats", sa.Text),
        sa.Column("created_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "comparison_findings",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("comparison_id", sa.Uuid(native_uuid=False), sa.ForeignKey("comparisons.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("template_id", sa.Uuid(native_uuid=False), sa.ForeignKey("templates.id", ondelete="CASCADE"), index=True),
        sa.Column("template_clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("template_clauses.id")),
        sa.Column("contract_clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("clauses.id")),
        sa.Column("category", sa.String(64), index=True),
        sa.Column("difference_type", sa.String(16), index=True),
        sa.Column("materiality", sa.String(32), index=True),
        sa.Column("title", sa.String(256)),
        sa.Column("template_text", sa.Text),
        sa.Column("contract_text", sa.Text),
        sa.Column("value_diff", sa.Text),
        sa.Column("risk_finding_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_findings.id")),
        sa.Column("reasons", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("comparison_findings")
    op.drop_table("comparisons")
    op.drop_table("template_clauses")
    op.drop_table("templates")
