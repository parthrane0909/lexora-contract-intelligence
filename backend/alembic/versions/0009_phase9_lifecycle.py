"""Phase 9: obligations, lifecycle_events, obligation_reviews,
lifecycle_feedback + contracts.lifecycle_status_override (nullable, additive)."""
revision = "0009_phase9_lifecycle"
down_revision = "0008_phase8_review"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "obligations",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("clauses.id", ondelete="CASCADE"), index=True),
        sa.Column("type", sa.String(32), index=True),
        sa.Column("title", sa.String(256)),
        sa.Column("description", sa.Text),
        sa.Column("party", sa.String(16)),
        sa.Column("trigger", sa.String(64)),
        sa.Column("frequency", sa.String(32)),
        sa.Column("period_days", sa.Integer),
        sa.Column("period_approximate", sa.Boolean, default=False),
        sa.Column("notice_period_days", sa.Integer),
        sa.Column("cure_period_days", sa.Integer),
        sa.Column("deadline", sa.String(16), index=True),
        sa.Column("effective_date", sa.String(16)),
        sa.Column("expiration_date", sa.String(16)),
        sa.Column("renewal_period_days", sa.Integer),
        sa.Column("auto_renew", sa.Boolean),
        sa.Column("renewal_date", sa.String(16)),
        sa.Column("notice_deadline", sa.String(16)),
        sa.Column("amount", sa.Float),
        sa.Column("currency", sa.String(8)),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("confidence", sa.Float),
        sa.Column("extraction_method", sa.String(32)),
        sa.Column("model_name", sa.String(256)),
        sa.Column("model_version", sa.String(128)),
        sa.Column("rule_id", sa.String(32)),
        sa.Column("source_text", sa.Text),
        sa.Column("risk_finding_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_findings.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "lifecycle_events",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("obligation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligations.id", ondelete="CASCADE"), index=True),
        sa.Column("event_type", sa.String(32), index=True),
        sa.Column("event_date", sa.String(16), index=True),
        sa.Column("trigger", sa.String(64)),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("source_clause_id", sa.Uuid(native_uuid=False), sa.ForeignKey("clauses.id")),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "obligation_reviews",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("obligation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligations.id", ondelete="CASCADE"), index=True),
        sa.Column("reviewer_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id"), index=True),
        sa.Column("decision", sa.String(16), index=True),
        sa.Column("comment", sa.Text),
        sa.Column("correction_type", sa.String(32)),
        sa.Column("corrected_type", sa.String(32)),
        sa.Column("corrected_date", sa.String(16)),
        sa.Column("corrected_notice_days", sa.Integer),
        sa.Column("corrected_party", sa.String(16)),
        sa.Column("corrected_trigger", sa.String(64)),
        sa.Column("corrected_status", sa.String(16)),
        sa.Column("snapshot", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "lifecycle_feedback",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("obligation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligations.id", ondelete="CASCADE"), index=True),
        sa.Column("review_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligation_reviews.id", ondelete="CASCADE"), index=True),
        sa.Column("reviewer_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id"), index=True),
        sa.Column("reviewer_role", sa.String(32)),
        sa.Column("original_type", sa.String(32)),
        sa.Column("original_deadline", sa.String(16)),
        sa.Column("original_notice_days", sa.Integer),
        sa.Column("original_party", sa.String(16)),
        sa.Column("original_trigger", sa.String(64)),
        sa.Column("original_confidence", sa.Float),
        sa.Column("extraction_method", sa.String(32)),
        sa.Column("decision", sa.String(16), index=True),
        sa.Column("correction_type", sa.String(32)),
        sa.Column("corrected_type", sa.String(32)),
        sa.Column("corrected_date", sa.String(16)),
        sa.Column("corrected_notice_days", sa.Integer),
        sa.Column("reviewer_comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.add_column("contracts", sa.Column("lifecycle_status_override", sa.String(16), nullable=True))


def downgrade():
    op.drop_column("contracts", "lifecycle_status_override")
    op.drop_table("lifecycle_feedback")
    op.drop_table("obligation_reviews")
    op.drop_table("lifecycle_events")
    op.drop_table("obligations")
