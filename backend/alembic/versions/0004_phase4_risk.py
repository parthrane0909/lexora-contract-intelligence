"""Phase 4: policies (versioned) + risk_findings (append-only per run)."""
revision = "0004_phase4_risk"
down_revision = "0003_phase3_classification"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "policies",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("category", sa.String(64), index=True),
        sa.Column("params", sa.Text),
        sa.Column("version", sa.Integer, default=1),
        sa.Column("is_active", sa.Boolean, default=True, index=True),
        sa.Column("created_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "risk_findings",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("clauses.id", ondelete="CASCADE"), index=True),
        sa.Column("analysis_run_id", sa.Uuid(native_uuid=False), sa.ForeignKey("analysis_runs.id")),
        sa.Column("risk_category", sa.String(64), index=True),
        sa.Column("finding_type", sa.String(32), index=True),
        sa.Column("priority", sa.String(16), index=True),
        sa.Column("title", sa.String(256)),
        sa.Column("reason", sa.Text),
        sa.Column("evidence", sa.Text),
        sa.Column("section_number", sa.String(64)),
        sa.Column("page_start", sa.Integer),
        sa.Column("page_end", sa.Integer),
        sa.Column("rule_id", sa.String(32), index=True),
        sa.Column("rule_version", sa.String(16)),
        sa.Column("policy_id", sa.Uuid(native_uuid=False), sa.ForeignKey("policies.id")),
        sa.Column("policy_version", sa.Integer),
        sa.Column("contract_value", sa.Text),
        sa.Column("expected_value", sa.Text),
        sa.Column("priority_reasons", sa.Text),
        sa.Column("classification_model", sa.String(256)),
        sa.Column("classification_version", sa.String(128)),
        sa.Column("classification_confidence", sa.Float),
        sa.Column("status", sa.String(32), index=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("risk_findings")
    op.drop_table("policies")
