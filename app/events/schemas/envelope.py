"""
Event envelope schema — the standard wrapper for all SQS messages.

Every message on the USHSPA SQS notification queue must conform to this
structure.  Messages that fail validation are persisted as INVALID events
and routed to the DLQ for inspection.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventEnvelope(BaseModel):
    """Standard USHSPA event envelope.

    All fields match the canonical event format documented in the architecture.

    Attributes:
        event_id: Globally unique event identifier (UUID4).
        event_type: Dot-separated event type string (e.g. ``"booking.confirmed"``).
        version: Schema version for forward compatibility (default 1).
        occurred_at: ISO 8601 timestamp when the event occurred in the source system.
        source: Originating service identifier (e.g. ``"ushauth"``, ``"ushbooknpay"``).
        correlation_id: Request/workflow correlation ID for distributed tracing.
        causation_id: ID of the event that caused this event (for event chains).
        data: Event-specific payload.  Structure varies by event type.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
    )

    event_id: UUID = Field(description="Unique event identifier.")
    event_type: str = Field(
        min_length=1,
        max_length=100,
        description="Dot-separated event type (e.g. 'booking.confirmed').",
    )
    version: int = Field(default=1, ge=1, description="Envelope schema version.")
    occurred_at: datetime = Field(description="When the event occurred (ISO 8601 with timezone).")
    source: str = Field(
        min_length=1,
        max_length=100,
        description="Originating service (e.g. 'ushauth').",
    )
    correlation_id: UUID | None = Field(
        default=None,
        description="Distributed tracing correlation ID.",
    )
    causation_id: UUID | None = Field(
        default=None,
        description="ID of the event that caused this event.",
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        description="Event-specific payload.",
    )

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        """Ensure event_type contains only safe characters."""
        import re
        if not re.match(r"^[a-zA-Z0-9._\-]+$", v):
            raise ValueError(
                f"event_type must contain only alphanumeric, dot, hyphen, "
                f"or underscore characters. Got: {v!r}"
            )
        return v.lower()

    @classmethod
    def model_validate(cls, obj: Any, *, strict: bool | None = None, from_attributes: bool | None = None, context: Any | None = None) -> "EventEnvelope":
        if isinstance(obj, dict):
            v = dict(obj)
            # 1. Default source if missing
            if not v.get("source"):
                event_name = str(v.get("event_name") or "")
                event_type = str(v.get("event_type") or "")
                lower_str = (event_name + " " + event_type).lower()
                if "booking" in lower_str or "payment" in lower_str:
                    v["source"] = "ushbooknpay"
                elif "user" in lower_str or "auth" in lower_str or "password" in lower_str:
                    v["source"] = "ushauth"
                else:
                    v["source"] = "ushspa"

            # 2. Map schema_version to version if version is not provided
            if "version" not in v and "schema_version" in v:
                try:
                    v["version"] = int(float(str(v["schema_version"])))
                except (ValueError, TypeError):
                    v["version"] = 1

            # 3. Ensure event_type is present
            if not v.get("event_type"):
                event_name = v.get("event_name")
                if event_name:
                    v["event_type"] = str(event_name).lower().replace(".", "_")

            # 4. If 'data' is missing or empty, or if root-level domain fields exist, merge into data
            envelope_keys = {
                "event_id", "event_type", "event_name", "version", "schema_version",
                "occurred_at", "source", "correlation_id", "causation_id", "data"
            }
            raw_data = v.get("data")
            if not isinstance(raw_data, dict):
                raw_data = {}
            else:
                raw_data = dict(raw_data)

            for k, val in v.items():
                if k not in envelope_keys and k not in raw_data:
                    raw_data[k] = val

            v["data"] = raw_data
            obj = v

        return super().model_validate(obj, strict=strict, from_attributes=from_attributes, context=context)

    @property
    def event_id_str(self) -> str:
        """Return event_id as a string."""
        return str(self.event_id)

    @property
    def correlation_id_str(self) -> str | None:
        """Return correlation_id as a string or None."""
        return str(self.correlation_id) if self.correlation_id else None

    @property
    def causation_id_str(self) -> str | None:
        """Return causation_id as a string or None."""
        return str(self.causation_id) if self.causation_id else None
