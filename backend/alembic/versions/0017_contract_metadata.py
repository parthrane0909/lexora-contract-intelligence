"""Part 5: contracts.is_important (persistent important/starred flag).

A user-controlled organizational marker on the contract row, exactly like
`is_archived` — NOT a risk score, NOT derived from analysis. It is written
only through PATCH /api/v1/contracts/{id} and is never reset by reprocessing.

Category needed NO migration: the contract-level category reuses the
existing `contracts.contract_type` column (member-supplied, already exposed
by the list/detail APIs and already indexed by Elasticsearch).

Additive only: one NOT NULL boolean with server_default false, plus its
index and an index on the (already existing) category column so the new
list/search filters stay indexed. Existing rows stay valid and unstarred;
no data rewrite.
"""
revision = "0017_contract_metadata"
down_revision = "0016_finding_extracted_facts"

from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("contracts") as batch:
        batch.add_column(sa.Column("is_important", sa.Boolean(),
                                   nullable=False, server_default=sa.false()))
        batch.create_index("ix_contracts_is_important", ["is_important"])
        # Category is now a first-class list/search filter and sort key.
        batch.create_index("ix_contracts_contract_type", ["contract_type"])


def downgrade():
    with op.batch_alter_table("contracts") as batch:
        batch.drop_index("ix_contracts_contract_type")
        batch.drop_index("ix_contracts_is_important")
        batch.drop_column("is_important")
