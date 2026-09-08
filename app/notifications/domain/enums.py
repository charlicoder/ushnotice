"""
Enumerations for the notifications domain.

Centralising all enum definitions here prevents import cycles and makes it
easy to extend the state machine without touching handler or model code.
"""
from __future__ import annotations

from enum import Enum


class NotificationStatus(str, Enum):
    """State machine for a single :class:`~app.notifications.domain.models.Notification`.

    State transitions::

        CREATED → QUEUED → PROCESSING → SENT → DELIVERED
                                      ↓
                               RETRYING → FAILED
                               CANCELLED (from any state)
    """

    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    CANCELLED = "CANCELLED"


class NotificationChannel(str, Enum):
    """Supported delivery channels."""

    SMS = "sms"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class SmsProvider(str, Enum):
    """Available SMS providers."""

    KWTSMS = "kwtsms"
    TWILIO = "twilio"
    STUB = "stub"


class WhatsAppProvider(str, Enum):
    """Available WhatsApp providers."""

    META = "meta"
    STUB = "stub"


class EmailProvider(str, Enum):
    """Available email providers."""

    SMTP = "smtp"
    GMAIL = "gmail"
    STUB = "stub"


class EventProcessingStatus(str, Enum):
    """Processing status recorded in the ``event_processings`` table."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"       # duplicate / idempotency skip
    DLQ = "DLQ"               # routed to dead-letter queue


class EventStatus(str, Enum):
    """Status of a received event persisted in the ``events`` table."""

    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    INVALID = "INVALID"       # failed schema validation
    FAILED = "FAILED"
    DLQ = "DLQ"


class AttemptStatus(str, Enum):
    """Status of a single :class:`~app.notifications.domain.models.NotificationAttempt`."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class ApiRequestStatus(str, Enum):
    """Status of an inter-service API request record."""

    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
