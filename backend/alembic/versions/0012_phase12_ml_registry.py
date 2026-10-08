"""Phase 12: ML dataset/model registry metadata (examples live in versioned
artifacts, not duplicated here)."""
revision = "0012_phase12_ml_registry"
down_revision = "0011_phase11_portfolio"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "ml_datasets",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("version", sa.String(64), index=True),
        sa.Column("created_from", sa.String(32), default="feedback"),
        sa.Column("source_count", sa.Integer, default=0),
        sa.Column("usable_count", sa.Integer, default=0),
        sa.Column("excluded_count", sa.Integer, default=0),
        sa.Column("manifest", sa.Text),  # JSON: counts, splits hash, versions
        sa.Column("artifact_path", sa.String(1024)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "version", name="uq_ml_dataset_org_version"),
    )
    op.create_table(
        "ml_model_versions",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("name", sa.String(128), index=True),
        sa.Column("version", sa.String(128), index=True),
        sa.Column("task", sa.String(64), default="clause_classification"),
        sa.Column("dataset_version", sa.String(64)),
        sa.Column("base_model", sa.String(256)),
        sa.Column("metrics", sa.Text),  # JSON
        sa.Column("artifact_location", sa.String(1024)),
        sa.Column("status", sa.String(32), default="candidate", index=True),
        sa.Column("mlflow_run_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("name", "version", name="uq_ml_model_name_version"),
    )
    op.create_table(
        "ml_evaluation_runs",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("model_version", sa.String(128), index=True),
        sa.Column("dataset_version", sa.String(64)),
        sa.Column("split", sa.String(16)),
        sa.Column("metrics", sa.Text),  # JSON
        sa.Column("report_path", sa.String(1024)),
        sa.Column("confusion_path", sa.String(1024)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "ml_promotion_events",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("model_name", sa.String(128), index=True),
        sa.Column("model_version", sa.String(128), index=True),
        sa.Column("from_status", sa.String(32)),
        sa.Column("to_status", sa.String(32)),
        sa.Column("decided_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("organization_id", sa.Uuid(native_uuid=False),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("gates", sa.Text),  # JSON gate results
        sa.Column("reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("ml_promotion_events")
    op.drop_table("ml_evaluation_runs")
    op.drop_table("ml_model_versions")
    op.drop_table("ml_datasets")
