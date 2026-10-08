"""Phase 17B: contracts.semantic_index_status / _error.

Automatic semantic indexing needs its own observable lifecycle, separate
from processing (a Qdrant failure must never flip processing_status).
Existing rows get server_default 'pending' so the worker's recovery sweep
picks them up and indexes contracts processed before this phase.
Additive only: two columns + one index, no data rewrite.
"""
revision = "0015_auto_semantic_index"
down_revision = "0014_phase14_matrix_id"

from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("contracts") as batch:
        batch.add_column(sa.Column("semantic_index_status", sa.String(16),
                                   nullable=False, server_default="pending"))
        batch.add_column(sa.Column("semantic_index_error", sa.Text(), nullable=True))
        batch.create_index("ix_contracts_semantic_index_status", ["semantic_index_status"])


def downgrade():
    with op.batch_alter_table("contracts") as batch:
        batch.drop_index("ix_contracts_semantic_index_status")
        batch.drop_column("semantic_index_error")
        batch.drop_column("semantic_index_status")
