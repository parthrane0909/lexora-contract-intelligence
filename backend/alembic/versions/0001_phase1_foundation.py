"""Phase 1 foundation tables."""
revision = "0001_phase1_foundation"
down_revision = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, index=True, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "organization_members",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("user_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("role", sa.String(32), default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "contracts",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("filename", sa.String(512)),
        sa.Column("contract_type", sa.String(128)),
        sa.Column("processing_status", sa.String(32), default="uploaded", index=True),
        sa.Column("analysis_status", sa.String(32), default="pending", index=True),
        sa.Column("storage_key", sa.String(1024)),
        sa.Column("model_versions", sa.Text),
        sa.Column("is_archived", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("uploaded_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("storage_key", sa.String(1024)),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("size_bytes", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id"), index=True),
        sa.Column("user_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(128), index=True),
        sa.Column("object_type", sa.String(128)),
        sa.Column("object_id", sa.String(64)),
        sa.Column("detail", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    for t in ("audit_logs", "documents", "contracts", "organization_members", "users", "organizations"):
        op.drop_table(t)
