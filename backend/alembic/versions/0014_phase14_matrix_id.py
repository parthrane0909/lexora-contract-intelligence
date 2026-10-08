"""Phase 14: bring ml_experiments schema in sync with the model.

The MLExperiment model already declares `matrix_id` (groups multi-seed /
matrix runs); migration 0013 never created the column. Smallest safe change:
one nullable indexed column, no data rewrite.
"""
revision = "0014_phase14_matrix_id"
down_revision = "0013_phase13_experiments"

from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("ml_experiments") as batch:
        batch.add_column(sa.Column("matrix_id", sa.String(64), nullable=True))
        batch.create_index("ix_ml_experiments_matrix_id", ["matrix_id"])


def downgrade():
    with op.batch_alter_table("ml_experiments") as batch:
        batch.drop_index("ix_ml_experiments_matrix_id")
        batch.drop_column("matrix_id")
