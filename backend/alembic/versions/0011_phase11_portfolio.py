"""Phase 11: contract counterparty (member-supplied, optional, never inferred)."""
revision = "0011_phase11_portfolio"
down_revision = "0010_phase10_notifications"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("contracts", sa.Column("counterparty", sa.String(256), nullable=True))
    op.create_index("ix_contracts_counterparty", "contracts", ["counterparty"])


def downgrade():
    op.drop_index("ix_contracts_counterparty", table_name="contracts")
    op.drop_column("contracts", "counterparty")
