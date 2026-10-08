"""Phase 8: review_decisions + feedback_ledger (append-only human judgment)."""
revision = "0008_phase8_review"
down_revision = "0007_phase7_explanations"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("finding_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_findings.id", ondelete="CASCADE"), index=True),
        sa.Column("reviewer_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id"), index=True),
        sa.Column("decision", sa.String(24), index=True),
        sa.Column("comment", sa.Text),
        sa.Column("correction_type", sa.String(32)),
        sa.Column("corrected_category", sa.String(64)),
        sa.Column("corrected_priority", sa.String(16)),
        sa.Column("finding_snapshot", sa.Text),
        sa.Column("explanation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_explanations.id")),
        sa.Column("explanation_version", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "feedback_ledger",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("finding_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_findings.id", ondelete="CASCADE"), index=True),
        sa.Column("review_id", sa.Uuid(native_uuid=False), sa.ForeignKey("review_decisions.id", ondelete="CASCADE"), index=True),
        sa.Column("reviewer_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id"), index=True),
        sa.Column("reviewer_role", sa.String(32)),
        sa.Column("original_category", sa.String(64)),
        sa.Column("original_priority", sa.String(16)),
        sa.Column("original_rule", sa.String(32)),
        sa.Column("original_classifier_prediction", sa.String(64)),
        sa.Column("original_classifier_confidence", sa.Float),
        sa.Column("original_policy_version", sa.Integer),
        sa.Column("explanation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("risk_explanations.id")),
        sa.Column("explanation_version", sa.String(128)),
        sa.Column("decision", sa.String(24), index=True),
        sa.Column("correction_type", sa.String(32)),
        sa.Column("corrected_category", sa.String(64)),
        sa.Column("corrected_priority", sa.String(16)),
        sa.Column("reviewer_comment", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("feedback_ledger")
    op.drop_table("review_decisions")
