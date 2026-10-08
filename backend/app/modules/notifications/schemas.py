"""Typed notification/recurrence schemas."""
from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: str
    contract_id: str | None = None
    obligation_id: str | None = None
    lifecycle_event_id: str | None = None
    notification_type: str
    title: str
    body: str | None = None
    event_date: str | None = None
    window_days: int | None = None
    status: str
    is_read: bool = False
    created_at: str


class DeliveryOut(BaseModel):
    id: str
    notification_id: str
    provider: str
    channel: str
    status: str
    attempt_count: int = 0
    provider_message_id: str | None = None
    error: str | None = None
    sent_at: str | None = None


class PreferenceOut(BaseModel):
    notification_type: str
    enabled: bool = True
    channel: str = "inapp"


class PreferenceIn(BaseModel):
    notification_type: str
    enabled: bool = True
    channel: str = "inapp"
    user_id: str | None = None  # admin/legal_reviewer may target others


class ScanOut(BaseModel):
    events_checked: int = 0
    instances_checked: int = 0
    notifications_created: int = 0
    notifications_skipped: int = 0
    deliveries_sent: int = 0
    deliveries_failed: int = 0


class RecurrenceIn(BaseModel):
    frequency: str
    interval: int = 1
    anchor_date: str | None = None
    anchor_type: str | None = None
    timezone: str = "UTC"
    horizon_months: int = 12
    event_type: str = "OTHER"


class RecurrenceOut(BaseModel):
    id: str
    frequency: str
    interval: int = 1
    anchor_type: str
    anchor_date: str
    next_due_date: str | None = None
    active: bool = True
    instances_created: int = 0


class InstanceOut(BaseModel):
    id: str
    obligation_id: str
    event_type: str
    due_date: str | None = None
    status: str
    completed_at: str | None = None
