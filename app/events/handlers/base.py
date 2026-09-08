"""
Event handler base protocol and context.

Defines the contract that every event handler must satisfy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.events.schemas.envelope import EventEnvelope
from app.notifications.infrastructure.repositories import (
    ApiRequestRepository,
    EventProcessingRepository,
    EventRepository,
    NotificationAttemptRepository,
    NotificationRepository,
)
from app.templates.renderer import TemplateRenderer


@dataclass
class HandlerContext:
    """Dependencies injected into every event handler.

    Handlers access infrastructure through this context object rather than
    by importing global singletons.  This makes handlers independently
    testable with mocked dependencies.

    Attributes:
        db: Active async database session.
        event_repo: Repository for Event records.
        processing_repo: Repository for idempotency/processing records.
        notification_repo: Repository for Notification records.
        attempt_repo: Repository for NotificationAttempt records.
        api_request_repo: Repository for ApiRequest audit records.
        renderer: Jinja2 template renderer.
    """

    db: AsyncSession
    event_repo: EventRepository
    processing_repo: EventProcessingRepository
    notification_repo: NotificationRepository
    attempt_repo: NotificationAttemptRepository
    api_request_repo: ApiRequestRepository
    renderer: TemplateRenderer = field(default_factory=TemplateRenderer)

    @classmethod
    def from_session(cls, db: AsyncSession) -> "HandlerContext":
        """Construct a full HandlerContext from a database session."""
        return cls(
            db=db,
            event_repo=EventRepository(db),
            processing_repo=EventProcessingRepository(db),
            notification_repo=NotificationRepository(db),
            attempt_repo=NotificationAttemptRepository(db),
            api_request_repo=ApiRequestRepository(db),
        )


@runtime_checkable
class EventHandler(Protocol):
    """Protocol that all event handlers must satisfy.

    Attributes:
        event_type: The event type string this handler processes
            (e.g. ``"user_registered"`` or ``"booking.confirmed"``).
    """

    event_type: str

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        """Process a validated event envelope.

        Args:
            envelope: The validated, deserialized event envelope.
            ctx: Handler context containing all infrastructure dependencies.

        Raises:
            RetryableProviderError: If processing should be retried.
            NonRetryableProviderError: If processing must not be retried.
            Any other exception will be treated as a retryable failure.
        """
        ...
