"""Phase 13: experiment tracking rows (configs/metrics/costs are data)."""
revision = "0013_phase13_experiments"
down_revision = "0012_phase12_ml_registry"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "ml_experiments",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("name", sa.String(128), index=True),
        sa.Column("kind", sa.String(32), default="classification"),
        sa.Column("config", sa.Text),  # JSON: full resolved training config
        sa.Column("dataset_version", sa.String(64)),
        sa.Column("seed", sa.Integer, default=42),
        sa.Column("status", sa.String(32), default="registered", index=True),
        sa.Column("candidate_version", sa.String(128)),
        sa.Column("metrics", sa.Text),  # JSON: validation/test summary
        sa.Column("costs", sa.Text),  # JSON: train/infer time, memory
        sa.Column("mlflow_run_id", sa.String(64)),
        sa.Column("error", sa.Text),
        sa.Column("created_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("ml_experiments")
