"""Notification taxonomy + window rules (Phase 10.5/10.6).

Windows are data, not hard-coded UI: each rule maps an event type to a
notification type at a day-window. Env override NOTIFICATION_RULES_JSON
(re JSON list) allows deployment tuning without code changes.
"""
from __future__ import annotations

import json
import os

NOTIFICATION_TAXONOMY_VERSION = "1.0"

NOTIFICATION_TYPES = (
    "UPCOMING_DEADLINE",
    "DUE_TODAY",
    "OVERDUE",
    "RENEWAL_APPROACHING",
    "NOTICE_DEADLINE_APPROACHING",
    "CONTRACT_EXPIRING",
    "OBLIGATION_REQUIRES_REVIEW",
)

# event_type -> [(window_days, notification_type)]. 0-day window = due today.
# Windows are "at or inside": a scan creates the notification for the largest
# window the event has entered; idempotency keys keep one row per window.
DEFAULT_WINDOW_RULES: dict[str, list[tuple[int, str]]] = {
    "CONTRACT_EXPIRY": [(30, "CONTRACT_EXPIRING"), (14, "CONTRACT_EXPIRING"),
                        (7, "CONTRACT_EXPIRING"), (1, "CONTRACT_EXPIRING"),
                        (0, "DUE_TODAY")],
    "RENEWAL": [(30, "RENEWAL_APPROACHING"), (14, "RENEWAL_APPROACHING")],
    "RENEWAL_NOTICE": [(30, "NOTICE_DEADLINE_APPROACHING"),
                       (14, "NOTICE_DEADLINE_APPROACHING"),
                       (7, "NOTICE_DEADLINE_APPROACHING")],
    "TERMINATION_NOTICE": [(30, "NOTICE_DEADLINE_APPROACHING"),
                           (14, "NOTICE_DEADLINE_APPROACHING"),
                           (7, "NOTICE_DEADLINE_APPROACHING")],
    "PAYMENT_DEADLINE": [(30, "UPCOMING_DEADLINE"), (14, "UPCOMING_DEADLINE"),
                         (7, "UPCOMING_DEADLINE"), (1, "UPCOMING_DEADLINE"),
                         (0, "DUE_TODAY")],
    "REPORTING_DEADLINE": [(30, "UPCOMING_DEADLINE"), (14, "UPCOMING_DEADLINE"),
                            (7, "UPCOMING_DEADLINE"), (1, "UPCOMING_DEADLINE"),
                            (0, "DUE_TODAY")],
    "INSURANCE_RENEWAL": [(30, "UPCOMING_DEADLINE"), (14, "UPCOMING_DEADLINE"),
                          (7, "UPCOMING_DEADLINE")],
    "COMPLIANCE_DEADLINE": [(30, "UPCOMING_DEADLINE"), (14, "UPCOMING_DEADLINE"),
                            (7, "UPCOMING_DEADLINE"), (1, "UPCOMING_DEADLINE"),
                            (0, "DUE_TODAY")],
    "CONTRACT_START": [],
    "TERMINATION": [],
    "OTHER": [],
}

RECIPIENT_ROLES = ("admin", "legal_reviewer", "contract_manager", "viewer")

# Max delivery attempts before a delivery stays FAILED (bounded retries).
MAX_DELIVERY_ATTEMPTS = 3


def load_window_rules() -> dict[str, list[tuple[int, str]]]:
    raw = os.environ.get("NOTIFICATION_RULES_JSON")
    if not raw:
        return DEFAULT_WINDOW_RULES
    try:
        parsed = json.loads(raw)
        out: dict[str, list[tuple[int, str]]] = {}
        for event_type, rules in parsed.items():
            items = []
            for window_days, ntype in rules:
                window_days = int(window_days)
                if ntype not in NOTIFICATION_TYPES:
                    raise ValueError(f"unknown notification type {ntype!r}")
                items.append((window_days, ntype))
            out[event_type] = sorted(items, reverse=True)
        return out
    except Exception as e:
        raise ValueError(f"Invalid NOTIFICATION_RULES_JSON: {e}") from e


def windows_for(event_type: str) -> list[tuple[int, str]]:
    return load_window_rules().get(event_type, [])
