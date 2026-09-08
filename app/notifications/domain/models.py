"""
SQLAlchemy ORM models for the notifications domain.

Tables:
  - events                    — raw incoming event records
  - event_processings         — idempotency + per-handler processing state
  - notifications             — one row per channel per event
  - notification_status_history — immutable state-change log
  - notification_attempts     — one row per provider send attempt
  - api_requests              — inter-service HTTP call records

All primary keys are UUIDs generated at the database/application level.
Indexes are defined on all commonly-queried columns per section 48 of the spec.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.notifications.domain.enums import (
    ApiRequestStatus,
    AttemptStatus,
    EventProcessingStatus,
    EventStatus,
    NotificationChannel,
    NotificationStatus,
)


def _uuid() -> str:
    return str(uuid.uuid4())


class Event(Base):
    """Persisted record of every message received from SQS.

    The raw payload is stored for auditability.  Sensitive fields within the
    payload (e.g. OTP values) should be stripped or masked before storage
    according to business rules in the handler.
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=EventStatus.RECEIVED.value, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sqs_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sqs_receipt_handle: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    processings: Mapped[list["EventProcessing"]] = relationship(
        "EventProcessing", back_populates="event", cascade="all, delete-orphan"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification", back_populates="event", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_events_event_type_status", "event_type", "status"),
        Index("ix_events_received_at", "received_at"),
    )

    def __repr__(self) -> str:
        return f"<Event id={self.id!r} type={self.event_type!r} status={self.status!r}>"


class EventProcessing(Base):
    """Idempotency record — one row per (event_id, handler_name) pair.

    The unique constraint on ``(event_id, handler_name)`` is the primary guard
    against duplicate processing when SQS delivers a message more than once.
    """

    __tablename__ = "event_processings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    handler_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=EventProcessingStatus.PENDING.value,
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="processings")

    __table_args__ = (
        UniqueConstraint("event_id", "handler_name", name="uq_event_processing"),
        Index("ix_event_processings_event_id", "event_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<EventProcessing event_id={self.event_id!r} handler={self.handler_name!r} "
            f"status={self.status!r}>"
        )


class Notification(Base):
    """One notification record per delivery channel per event.

    A single event may produce multiple ``Notification`` rows (e.g. one SMS
    and one Email for ``booking.confirmed``).
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    booking_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)  # masked on read
    recipient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=NotificationStatus.CREATED.value,
    )
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="notifications")
    attempts: Mapped[list["NotificationAttempt"]] = relationship(
        "NotificationAttempt", back_populates="notification", cascade="all, delete-orphan"
    )
    status_history: Mapped[list["NotificationStatusHistory"]] = relationship(
        "NotificationStatusHistory",
        back_populates="notification",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_notifications_event_id", "event_id"),
        Index("ix_notifications_customer_id", "customer_id"),
        Index("ix_notifications_booking_id", "booking_id"),
        Index("ix_notifications_channel_status", "channel", "status"),
        Index("ix_notifications_provider", "provider"),
        Index("ix_notifications_created_at", "created_at"),
        Index("ix_notifications_correlation_id", "correlation_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id!r} channel={self.channel!r} status={self.status!r}>"
        )


class NotificationStatusHistory(Base):
    """Immutable audit log of every status transition for a notification.

    Records are never updated or deleted — only inserted.
    """

    __tablename__ = "notification_status_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    notification_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    notification: Mapped["Notification"] = relationship(
        "Notification", back_populates="status_history"
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationStatusHistory notification_id={self.notification_id!r} "
            f"{self.from_status!r} → {self.to_status!r}>"
        )


class NotificationAttempt(Base):
    """One row per provider send attempt for a notification.

    Previous attempt records are never overwritten — a new row is always
    inserted.  This enables the dashboard to show the full retry history.
    """

    __tablename__ = "notification_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    notification_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AttemptStatus.PENDING.value
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    notification: Mapped["Notification"] = relationship(
        "Notification", back_populates="attempts"
    )

    __table_args__ = (
        Index("ix_notification_attempts_notification_id", "notification_id"),
        Index("ix_notification_attempts_provider", "provider"),
        Index("ix_notification_attempts_status", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationAttempt notification_id={self.notification_id!r} "
            f"#{self.attempt_number} status={self.status!r}>"
        )


class ApiRequest(Base):
    """Record of every inter-service HTTP call made by ushnotice.

    Captures the full request and response for audit purposes.
    """

    __tablename__ = "api_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    notification_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    service: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "ushbooknpay"
    method: Mapped[str] = mapped_column(String(10), nullable=False)    # GET, POST, PATCH
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    request_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ApiRequestStatus.PENDING.value
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_api_requests_event_id", "event_id"),
        Index("ix_api_requests_notification_id", "notification_id"),
        Index("ix_api_requests_service", "service"),
        Index("ix_api_requests_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ApiRequest service={self.service!r} {self.method} {self.path!r} "
            f"status={self.status!r}>"
        )
