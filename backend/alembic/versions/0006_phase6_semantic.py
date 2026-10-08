"""Phase 6: semantic_index (indexing state; vectors live in Qdrant)."""
revision = "0006_phase6_semantic"
down_revision = "0005_phase5_company"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "semantic_index",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("object_type", sa.String(32), index=True),
        sa.Column("object_id", sa.String(64), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("template_id", sa.Uuid(native_uuid=False), sa.ForeignKey("templates.id", ondelete="CASCADE"), index=True),
        sa.Column("embedding_model", sa.String(256)),
        sa.Column("embedding_version", sa.String(64)),
        sa.Column("dimension", sa.Integer),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("error", sa.Text),
        sa.Column("vector_id", sa.String(64)),
        sa.Column("indexed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "object_type", "object_id",
                            "embedding_model", name="uq_semantic_index_identity"),
    )


def downgrade():
    op.drop_table("semantic_index")
