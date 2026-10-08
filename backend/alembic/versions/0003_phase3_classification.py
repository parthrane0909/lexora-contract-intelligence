"""Phase 3: analysis_runs + clause_classifications (append-only predictions)."""
revision = "0003_phase3_classification"
down_revision = "0002_phase2_document"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("kind", sa.String(64), index=True),
        sa.Column("model_name", sa.String(256)),
        sa.Column("model_version", sa.String(128), index=True),
        sa.Column("status", sa.String(32), index=True),
        sa.Column("stats", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "clause_classifications",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("clauses.id", ondelete="CASCADE"), index=True),
        sa.Column("analysis_run_id", sa.Uuid(native_uuid=False), sa.ForeignKey("analysis_runs.id")),
        sa.Column("model_name", sa.String(256)),
        sa.Column("model_version", sa.String(128), index=True),
        sa.Column("predicted_category", sa.String(64), index=True),
        sa.Column("confidence", sa.Float),
        sa.Column("probabilities", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("clause_classifications")
    op.drop_table("analysis_runs")
