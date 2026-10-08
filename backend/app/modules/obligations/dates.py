"""Deterministic date calculation (Phase 9.16). Timezone-aware, date-only
semantics (contract dates are calendar dates, not instants).

Only calculates when ALL required source inputs are known; otherwise returns
None with the caller exposing "insufficient source information". Never
invents anchor dates — today is used only for *comparison* (overdue /
upcoming), never as a fabricated trigger date.
"""
from __future__ import annotations

import calendar
import datetime


def today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def add_period(start: datetime.date, *, days: int = 0, months: int = 0,
               years: int = 0) -> datetime.date:
    """Calendar-aware addition (month-end clamped)."""
    month = start.month + months + years * 12
    year = start.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    out = datetime.date(year, month, day) + datetime.timedelta(days=days)
    return out


def renewal_date(expiration: str | None, renewal_period_days: int | None) -> str | None:
    exp = parse_date(expiration)
    if exp is None or not renewal_period_days:
        return None
    return add_period(exp, days=renewal_period_days).isoformat()


def notice_deadline(anchor: str | None, notice_days: int | None) -> str | None:
    """Deadline = anchor minus notice days (e.g. renewal/expiry notice)."""
    day = parse_date(anchor)
    if day is None or not notice_days:
        return None
    return (day - datetime.timedelta(days=notice_days)).isoformat()


def classify_event(event_date: str | None, ref: datetime.date | None = None) -> str:
    """UPCOMING / DUE (within 7 days) / OVERDUE vs reference date (today)."""
    ref = ref or today()
    day = parse_date(event_date)
    if day is None:
        return "UPCOMING"  # dateless events stay visible, never "overdue"
    if day < ref:
        return "OVERDUE"
    if (day - ref).days <= 7:
        return "DUE"
    return "UPCOMING"
