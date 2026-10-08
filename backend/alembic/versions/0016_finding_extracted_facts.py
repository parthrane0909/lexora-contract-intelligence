"""Part 1 (risk analysis): risk_findings.extracted_facts.

A finding must be explainable from what the rule actually saw. Until now the
deterministic facts (extractors output) were computed during analysis and
thrown away, leaving only contract_value/expected_value — which most rule
findings leave empty. This stores the facts verbatim as JSON text so the
evidence behind every finding is persisted, never re-derived later.

Additive only: one nullable column, no data rewrite (existing rows backfill
on the next re-analysis; readers treat NULL as {}).
"""
revision = "0016_finding_extracted_facts"
down_revision = "0015_auto_semantic_index"

from alembic import op
import sqlalchemy as sa


def upgrade():
    with op.batch_alter_table("risk_findings") as batch:
        batch.add_column(sa.Column("extracted_facts", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("risk_findings") as batch:
        batch.drop_column("extracted_facts")
