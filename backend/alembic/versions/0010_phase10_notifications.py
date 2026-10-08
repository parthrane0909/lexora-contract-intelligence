"""Phase 10: notifications, notification_deliveries, notification_preferences,
recurrence_definitions, obligation_event_instances."""
revision = "0010_phase10_notifications"
down_revision = "0009_phase9_lifecycle"

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("contract_id", sa.Uuid(native_uuid=False), sa.ForeignKey("contracts.id", ondelete="CASCADE"), index=True),
        sa.Column("obligation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligations.id", ondelete="CASCADE")),
        sa.Column("lifecycle_event_id", sa.Uuid(native_uuid=False), sa.ForeignKey("lifecycle_events.id", ondelete="CASCADE")),
        sa.Column("recipient_user_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id"), index=True),
        sa.Column("notification_type", sa.String(32), index=True),
        sa.Column("title", sa.String(256)),
        sa.Column("body", sa.Text),
        sa.Column("event_date", sa.String(16)),
        sa.Column("window_days", sa.Integer),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("is_read", sa.Boolean, default=False, index=True),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("idempotency_key", sa.String(256), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("notification_id", sa.Uuid(native_uuid=False), sa.ForeignKey("notifications.id", ondelete="CASCADE"), index=True),
        sa.Column("recipient_user_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("provider", sa.String(64)),
        sa.Column("channel", sa.String(16)),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("attempt_count", sa.Integer, default=0),
        sa.Column("provider_message_id", sa.String(256)),
        sa.Column("error", sa.Text),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("user_id", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), index=True),
        sa.Column("notification_type", sa.String(32), index=True),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("channel", sa.String(16), default="inapp"),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "user_id", "notification_type",
                            name="uq_notification_preference"),
    )
    op.create_table(
        "recurrence_definitions",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("obligation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligations.id", ondelete="CASCADE"), index=True),
        sa.Column("frequency", sa.String(16), index=True),
        sa.Column("interval", sa.Integer, default=1),
        sa.Column("anchor_type", sa.String(32)),
        sa.Column("anchor_date", sa.String(16)),
        sa.Column("next_due_date", sa.String(16)),
        sa.Column("timezone", sa.String(64), default="UTC"),
        sa.Column("active", sa.Boolean, default=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "obligation_event_instances",
        sa.Column("id", sa.Uuid(native_uuid=False), primary_key=True),
        sa.Column("organization_id", sa.Uuid(native_uuid=False), sa.ForeignKey("organizations.id", ondelete="CASCADE"), index=True),
        sa.Column("obligation_id", sa.Uuid(native_uuid=False), sa.ForeignKey("obligations.id", ondelete="CASCADE"), index=True),
        sa.Column("recurrence_id", sa.Uuid(native_uuid=False), sa.ForeignKey("recurrence_definitions.id", ondelete="CASCADE"), index=True),
        sa.Column("event_type", sa.String(32), index=True),
        sa.Column("due_date", sa.String(16), index=True),
        sa.Column("status", sa.String(16), index=True),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("completed_by", sa.Uuid(native_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("source_event_id", sa.Uuid(native_uuid=False), sa.ForeignKey("lifecycle_events.id")),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("recurrence_id", "due_date", name="uq_recurrence_instance"),
    )


def downgrade():
    op.drop_table("obligation_event_instances")
    op.drop_table("recurrence_definitions")
    op.drop_table("notification_preferences")
    op.drop_table("notification_deliveries")
    op.drop_table("notifications")
