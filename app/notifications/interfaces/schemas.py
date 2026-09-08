"""
Pydantic schemas for API requests and responses.

Includes PII masking on outbound responses.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.common.masking import mask_email, mask_phone


class AttemptSchema(BaseModel):
    """Schema for a single notification attempt."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    attempt_number: int
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    provider_message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    is_retryable: bool
    latency_ms: int | None = None


class StatusHistorySchema(BaseModel):
    """Schema for an entry in the notification status change audit."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    from_status: str | None = None
    to_status: str
    reason: str | None = None
    created_at: datetime


class NotificationResponse(BaseModel):
    """List response schema for a Notification record with masked recipient."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    customer_id: str | None = None
    booking_id: str | None = None
    channel: str
    recipient: str
    recipient_name: str | None = None
    template_name: str | None = None
    subject: str | None = None
    status: str
    provider: str | None = None
    retry_count: int
    created_at: datetime
    updated_at: datetime
    correlation_id: str | None = None

    @classmethod
    def from_orm_masked(cls, obj: Any) -> "NotificationResponse":
        """Build from ORM model applying PII masking to recipient."""
        raw_recipient = obj.recipient or ""
        channel = (obj.channel or "").lower()
        if channel in ("sms", "whatsapp"):
            masked_recipient = mask_phone(raw_recipient)
        elif channel == "email":
            masked_recipient = mask_email(raw_recipient)
        else:
            masked_recipient = "***"

        return cls(
            id=str(obj.id),
            event_id=str(obj.event_id),
            customer_id=str(obj.customer_id) if obj.customer_id else None,
            booking_id=str(obj.booking_id) if obj.booking_id else None,
            channel=obj.channel,
            recipient=masked_recipient,
            recipient_name=obj.recipient_name,
            template_name=obj.template_name,
            subject=obj.subject,
            status=obj.status,
            provider=obj.provider,
            retry_count=obj.retry_count,
            created_at=obj.created_at,
            updated_at=obj.updated_at,
            correlation_id=str(obj.correlation_id) if obj.correlation_id else None,
        )


class NotificationDetailResponse(NotificationResponse):
    """Detailed response schema for a Notification record including attempts and history."""

    attempts: list[AttemptSchema] = Field(default_factory=list)
    status_history: list[StatusHistorySchema] = Field(default_factory=list)


class EventResponse(BaseModel):
    """Response schema for an Event record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    event_type: str
    version: int
    source: str
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] | None = None
    received_at: datetime
    processed_at: datetime | None = None
    status: str
    error: str | None = None
    retry_count: int


class DeliveryFailureResponse(BaseModel):
    """Response schema for failed delivery records."""

    notification_id: str
    channel: str
    recipient: str
    provider: str | None = None
    retry_count: int
    created_at: datetime
    last_error_code: str | None = None
    last_error_message: str | None = None


class StatisticsResponse(BaseModel):
    """Aggregate statistics response."""

    total_notifications: int
    by_status: dict[str, int]
    by_channel: dict[str, int]
    success_rate_percentage: float
