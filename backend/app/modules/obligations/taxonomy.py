"""Obligation/lifecycle taxonomy (Phase 9.5). Versioned; extraction support
tracked honestly — categories without reliable deterministic extraction are
marked supported=False rather than faked.
"""
OBLIGATION_TAXONOMY_VERSION = "1.0"
LIFECYCLE_ENGINE_VERSION = "1.0"
EXTRACTION_RULES_VERSION = "1.0"

# type -> {label, supported} (supported = deterministic extractor exists)
OBLIGATION_TYPES: dict[str, dict] = {
    "PAYMENT": {"supported": True},
    "DELIVERY": {"supported": False},
    "REPORTING": {"supported": True},
    "INSURANCE": {"supported": True},
    "CONFIDENTIALITY": {"supported": True},
    "DATA_PROTECTION": {"supported": False},
    "SECURITY": {"supported": False},
    "AUDIT": {"supported": True},
    "INDEMNIFICATION": {"supported": False},
    "MAINTENANCE": {"supported": False},
    "RENEWAL": {"supported": True},
    "TERMINATION": {"supported": True},
    "NOTICE": {"supported": True},
    "LICENSE": {"supported": False},
    "IP": {"supported": False},
    "COMPLIANCE": {"supported": False},
    "EFFECTIVE": {"supported": True},   # contract effective/start date
    "EXPIRY": {"supported": True},      # contract expiration date
    "OTHER": {"supported": False},
}

EVENT_TYPES = (
    "CONTRACT_START", "CONTRACT_EXPIRY", "RENEWAL", "RENEWAL_NOTICE",
    "TERMINATION_NOTICE", "TERMINATION", "PAYMENT_DEADLINE",
    "REPORTING_DEADLINE", "INSURANCE_RENEWAL", "COMPLIANCE_DEADLINE", "OTHER",
)

# Contract-level lifecycle (stored override wins; otherwise computed).
LIFECYCLE_STATUSES = ("DRAFT", "ACTIVE", "EXPIRING", "EXPIRED", "TERMINATED",
                      "ARCHIVED", "UNKNOWN")

# Obligation workflow states. UNCONFIRMED->CONFIRMED/etc. are workflow
# transitions; extracted type/dates/values are immutable (§5 vs §11).
OBLIGATION_STATUSES = ("UNCONFIRMED", "CONFIRMED", "ACTIVE", "COMPLETED",
                        "OVERDUE", "WAIVED", "NOT_APPLICABLE")

# Event classifications (computed from event_date vs today).
EVENT_STATES = ("UPCOMING", "DUE", "OVERDUE", "COMPLETED", "CANCELLED")

# Notification windows (days) supported by the upcoming-events endpoint.
NOTIFICATION_WINDOWS = (1, 7, 14, 30)

# Review decisions + correction taxonomy for lifecycle feedback (§12-13).
REVIEW_DECISIONS = ("confirm", "reject", "modify")
LIFECYCLE_CORRECTION_TYPES = (
    "WRONG_OBLIGATION_TYPE", "WRONG_DATE", "WRONG_NOTICE_PERIOD",
    "WRONG_PARTY", "WRONG_TRIGGER", "FALSE_POSITIVE", "MISSED_OBLIGATION",
    "EXTRACTION_ERROR", "OTHER",
)
COMMENT_MAX_CHARS = 2000
