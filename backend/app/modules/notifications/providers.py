"""Delivery provider abstraction (Phase 10.3/10.4).

NotificationProvider.send(notification) -> provider message id (truthful:
only returned on actual success). Lifecycle logic never sends anything
directly; the scan service calls providers and records the outcome.

InAppNotificationProvider: always succeeds (in-app = the notification row
itself); clearly a dev/in-app channel, never presented as email.
SmtpEmailProvider: real SMTP via env config (no secrets in code).
WebhookProvider: signed JSON POST (HMAC-SHA256).
FakeFailProvider: TEST ONLY — fails the first N sends, then succeeds.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Protocol


class DeliveryError(Exception):
    """Provider-reported or transport failure. Recorded, never swallowed."""


class NotificationProvider(Protocol):
    name: str
    channel: str  # inapp | email | webhook

    def send(self, notification: dict) -> str:
        """Deliver; return provider message id. Raise DeliveryError on failure."""
        ...

    def validate_configuration(self) -> None:
        """Raise DeliveryError when misconfigured."""
        ...

    def health(self) -> bool:
        ...


class InAppNotificationProvider:
    """Dev/in-app channel: delivery = persisted row. Always succeeds."""

    name = "inapp"
    channel = "inapp"

    def validate_configuration(self) -> None:
        return None

    def health(self) -> bool:
        return True

    def send(self, notification: dict) -> str:
        return f"inapp-{notification['id']}"


class FakeFailProvider:
    """TEST ONLY. Fails the first `fail_times` sends, then succeeds."""

    name = "fake-fail"
    channel = "inapp"

    def __init__(self, fail_times: int = 1):
        self.fail_times = fail_times
        self.calls = 0

    def validate_configuration(self) -> None:
        return None

    def health(self) -> bool:
        return True

    def send(self, notification: dict) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise DeliveryError(f"simulated failure #{self.calls}")
        return f"fake-ok-{notification['id']}"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class SmtpEmailProvider:
    """Real SMTP email. Config strictly from env:
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_FROM."""

    name = "smtp-email"
    channel = "email"

    def __init__(self):
        self.host = _env("SMTP_HOST")
        self.port = int(_env("SMTP_PORT", "587") or 587)
        self.username = _env("SMTP_USERNAME")
        self.password = _env("SMTP_PASSWORD")
        self.from_addr = _env("EMAIL_FROM")

    def validate_configuration(self) -> None:
        missing = [k for k, v in (("SMTP_HOST", self.host), ("EMAIL_FROM", self.from_addr))
                   if not v]
        if missing:
            raise DeliveryError(f"Email not configured, missing: {missing}")

    def health(self) -> bool:
        try:
            self.validate_configuration()
            with smtplib.SMTP(self.host, self.port, timeout=10) as s:
                s.ehlo()
            return True
        except Exception:
            return False

    def send(self, notification: dict) -> str:
        self.validate_configuration()
        to = notification.get("recipient_email")
        if not to:
            raise DeliveryError("No recipient email address")
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to
        msg["Subject"] = f"[Contract] {notification.get('title', 'Notification')}"
        msg.set_content(notification.get("body", ""))
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                s.starttls()
                if self.username:
                    s.login(self.username, self.password)
                s.send_message(msg)
            return f"smtp-{notification['id']}"
        except Exception as e:
            raise DeliveryError(f"SMTP send failed: {e}") from e


def sign_webhook(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookProvider:
    """Signed JSON POST to a configured endpoint (env: WEBHOOK_URL,
    WEBHOOK_SECRET, WEBHOOK_TIMEOUT_S)."""

    name = "webhook"
    channel = "webhook"

    def __init__(self):
        self.url = _env("WEBHOOK_URL")
        self.secret = _env("WEBHOOK_SECRET")
        self.timeout = int(_env("WEBHOOK_TIMEOUT_S", "15") or 15)

    def validate_configuration(self) -> None:
        if not self.url:
            raise DeliveryError("Webhook not configured, missing: ['WEBHOOK_URL']")

    def health(self) -> bool:
        return bool(self.url)

    def send(self, notification: dict) -> str:
        self.validate_configuration()
        body = json.dumps({"id": notification["id"], "title": notification.get("title"),
                           "body": notification.get("body"),
                           "event_date": notification.get("event_date")}).encode()
        req = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json",
                                          "X-Signature": sign_webhook(self.secret or "", body)},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status >= 400:
                    raise DeliveryError(f"Webhook HTTP {resp.status}")
            return f"webhook-{notification['id']}"
        except DeliveryError:
            raise
        except Exception as e:
            raise DeliveryError(f"Webhook send failed: {e}") from e


def get_provider(name: str | None = None) -> NotificationProvider:
    """Default provider from env NOTIFICATION_PROVIDER (default: inapp)."""
    which = (name or os.environ.get("NOTIFICATION_PROVIDER", "inapp")).lower()
    if which == "inapp":
        return InAppNotificationProvider()
    if which == "smtp-email":
        return SmtpEmailProvider()
    if which == "webhook":
        return WebhookProvider()
    raise DeliveryError(f"Unknown notification provider {which!r}")
