"""Phase 7: risk_explanations (append-only, evidence snapshots)."""
revision = "0007_phase7_explanations"
down_revision = "0006_phase6_semantic"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "risk_explanations",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("finding_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_findings.id", ondelete="CASCADE"), index=True),
        sa.Column("analysis_run_id", sa.Uuid(native_uuid=False), sa.ForeignKey("analysis_runs.id")),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("clauses.id")),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(256)),
        sa.Column("model_version", sa.String(128), index=True),
        sa.Column("prompt_version", sa.String(16)),
        sa.Column("evidence_snapshot", sa.Text),
        sa.Column("explanation", sa.Text),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("error", sa.Text),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("risk_explanations")
