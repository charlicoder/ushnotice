"""
Async SQLAlchemy repositories for all notification-domain tables.

Repository pattern:
- Each repository wraps an ``AsyncSession``.
- All methods are ``async`` and use SQLAlchemy 2.x select/update/insert.
- No business logic lives here — repositories are pure data-access objects.

Repositories:
    EventRepository
    EventProcessingRepository
    NotificationRepository
    NotificationAttemptRepository
    ApiRequestRepository
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.notifications.domain.enums import (
    ApiRequestStatus,
    AttemptStatus,
    EventProcessingStatus,
    EventStatus,
    NotificationStatus,
)
from app.notifications.domain.models import (
    ApiRequest,
    Event,
    EventProcessing,
    Notification,
    NotificationAttempt,
    NotificationStatusHistory,
)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────────────────────────────────────────
# EventRepository
# ─────────────────────────────────────────────────────────────────────────────


class EventRepository:
    """Data-access object for the ``events`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        event_id: str,
        event_type: str,
        version: int,
        source: str,
        correlation_id: str | None,
        causation_id: str | None,
        payload: dict | None,
        sqs_message_id: str | None = None,
        sqs_receipt_handle: str | None = None,
    ) -> Event:
        """Persist a newly received event."""
        # For non-placeholder event IDs, check if already recorded
        if event_id and not event_id.startswith("malformed-json") and event_id != "validation-error":
            existing = await self.get_by_event_id(event_id)
            if existing:
                return existing

        pk = event_id if event_id and not event_id.startswith("malformed-json") and event_id != "validation-error" else _uuid()

        event = Event(
            id=pk,
            event_id=event_id,
            event_type=event_type,
            version=version,
            source=source,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload,
            sqs_message_id=sqs_message_id,
            sqs_receipt_handle=sqs_receipt_handle,
            status=EventStatus.RECEIVED.value,
        )
        self._db.add(event)
        await self._db.flush()
        return event

    async def get_by_event_id(self, event_id: str) -> Event | None:
        """Return the most recent ``Event`` record for a given event UUID."""
        result = await self._db.execute(
            select(Event)
            .where((Event.event_id == event_id) | (Event.id == event_id))
            .order_by(Event.received_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, pk: str) -> Event | None:
        """Return an ``Event`` by primary key."""
        return await self._db.get(Event, pk)

    async def mark_processing(self, event: Event) -> None:
        """Transition event status to PROCESSING."""
        await self._db.execute(
            update(Event)
            .where(Event.id == event.id)
            .values(status=EventStatus.PROCESSING.value)
        )
        await self._db.flush()

    async def mark_processed(self, event: Event) -> None:
        """Transition event status to PROCESSED."""
        await self._db.execute(
            update(Event)
            .where(Event.id == event.id)
            .values(
                status=EventStatus.PROCESSED.value,
                processed_at=_utcnow(),
            )
        )
        await self._db.flush()

    async def mark_failed(self, event: Event, *, error: str) -> None:
        """Transition event status to FAILED with error details."""
        await self._db.execute(
            update(Event)
            .where(Event.id == event.id)
            .values(
                status=EventStatus.FAILED.value,
                error=error,
                retry_count=Event.retry_count + 1,
            )
        )
        await self._db.flush()

    async def mark_invalid(self, event: Event, *, error: str) -> None:
        """Transition event status to INVALID (schema validation failure)."""
        await self._db.execute(
            update(Event)
            .where(Event.id == event.id)
            .values(
                status=EventStatus.INVALID.value,
                error=error,
            )
        )
        await self._db.flush()

    async def mark_dlq(self, event: Event) -> None:
        """Mark the event as routed to the dead-letter queue."""
        await self._db.execute(
            update(Event)
            .where(Event.id == event.id)
            .values(status=EventStatus.DLQ.value)
        )
        await self._db.flush()

    async def list_events(
        self,
        *,
        event_type: str | None = None,
        status: str | None = None,
        source: str | None = None,
        correlation_id: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Event], int]:
        """List events with optional filters, returning items and total count."""
        stmt = select(Event)
        count_stmt = select(func.count()).select_from(Event)

        filters: list[Any] = []
        if event_type:
            filters.append(Event.event_type == event_type)
        if status:
            filters.append(Event.status == status)
        if source:
            filters.append(Event.source == source)
        if correlation_id:
            filters.append(Event.correlation_id == correlation_id)
        if date_from:
            filters.append(Event.received_at >= date_from)
        if date_to:
            filters.append(Event.received_at <= date_to)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = (await self._db.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Event.received_at.desc()).offset(offset).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all()), total


# ─────────────────────────────────────────────────────────────────────────────
# EventProcessingRepository
# ─────────────────────────────────────────────────────────────────────────────


class EventProcessingRepository:
    """Data-access object for the ``event_processings`` (idempotency) table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, *, event_id: str, handler_name: str) -> EventProcessing | None:
        """Return an existing processing record for the given key."""
        result = await self._db.execute(
            select(EventProcessing).where(
                (EventProcessing.event_id == event_id) | (
                    EventProcessing.event_id.in_(
                        select(Event.id).where(Event.event_id == event_id)
                    )
                ),
                EventProcessing.handler_name == handler_name,
            )
        )
        return result.scalars().first()

    async def create(
        self,
        *,
        event_id: str,
        handler_name: str,
    ) -> EventProcessing:
        """Insert a new PENDING processing record."""
        evt = await self._db.get(Event, event_id)
        if not evt:
            result = await self._db.execute(
                select(Event.id)
                .where(Event.event_id == event_id)
                .order_by(Event.received_at.desc())
                .limit(1)
            )
            found_id = result.scalar_one_or_none()
            if found_id:
                event_id = found_id

        ep = EventProcessing(
            event_id=event_id,
            handler_name=handler_name,
            status=EventProcessingStatus.PENDING.value,
        )
        self._db.add(ep)
        await self._db.flush()
        return ep

    async def mark_processing(self, ep: EventProcessing) -> None:
        await self._db.execute(
            update(EventProcessing)
            .where(EventProcessing.id == ep.id)
            .values(
                status=EventProcessingStatus.PROCESSING.value,
                started_at=_utcnow(),
            )
        )
        await self._db.flush()

    async def mark_completed(self, ep: EventProcessing) -> None:
        await self._db.execute(
            update(EventProcessing)
            .where(EventProcessing.id == ep.id)
            .values(
                status=EventProcessingStatus.COMPLETED.value,
                completed_at=_utcnow(),
            )
        )
        await self._db.flush()

    async def mark_failed(self, ep: EventProcessing, *, error: str) -> None:
        await self._db.execute(
            update(EventProcessing)
            .where(EventProcessing.id == ep.id)
            .values(
                status=EventProcessingStatus.FAILED.value,
                error=error,
                completed_at=_utcnow(),
            )
        )
        await self._db.flush()

    async def mark_skipped(self, ep: EventProcessing) -> None:
        await self._db.execute(
            update(EventProcessing)
            .where(EventProcessing.id == ep.id)
            .values(
                status=EventProcessingStatus.SKIPPED.value,
                completed_at=_utcnow(),
            )
        )
        await self._db.flush()


# ─────────────────────────────────────────────────────────────────────────────
# NotificationRepository
# ─────────────────────────────────────────────────────────────────────────────


class NotificationRepository:
    """Data-access object for the ``notifications`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        event_id: str,
        channel: str,
        recipient: str,
        recipient_name: str | None = None,
        template_name: str | None = None,
        subject: str | None = None,
        customer_id: str | None = None,
        booking_id: str | None = None,
        correlation_id: str | None = None,
        provider: str | None = None,
    ) -> Notification:
        """Persist a new notification record."""
        # Resolve event_id to events.id if needed
        evt = await self._db.get(Event, event_id)
        if not evt:
            result = await self._db.execute(
                select(Event.id)
                .where(Event.event_id == event_id)
                .order_by(Event.received_at.desc())
                .limit(1)
            )
            found_id = result.scalar_one_or_none()
            if found_id:
                event_id = found_id

        n = Notification(
            event_id=event_id,
            channel=channel,
            recipient=recipient,
            recipient_name=recipient_name,
            template_name=template_name,
            subject=subject,
            customer_id=customer_id,
            booking_id=booking_id,
            correlation_id=correlation_id,
            provider=provider,
            status=NotificationStatus.CREATED.value,
        )
        self._db.add(n)
        await self._db.flush()
        # Record initial status history
        await self._add_status_history(n, from_status=None, to_status=NotificationStatus.CREATED.value)
        return n

    async def get_by_id(self, pk: str) -> Notification | None:
        return await self._db.get(Notification, pk)

    async def update_status(
        self,
        notification: Notification,
        *,
        status: NotificationStatus,
        reason: str | None = None,
    ) -> None:
        """Transition the notification to a new status and record history."""
        old_status = notification.status
        notification.status = status.value
        await self._db.flush()
        await self._add_status_history(notification, from_status=old_status, to_status=status.value, reason=reason)

    async def _add_status_history(
        self,
        notification: Notification,
        *,
        from_status: str | None,
        to_status: str,
        reason: str | None = None,
    ) -> None:
        history = NotificationStatusHistory(
            notification_id=notification.id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
        )
        self._db.add(history)
        await self._db.flush()

    async def increment_retry(self, notification: Notification) -> None:
        notification.retry_count += 1
        await self._db.flush()

    async def list_notifications(
        self,
        *,
        event_type: str | None = None,
        channel: str | None = None,
        status: str | None = None,
        provider: str | None = None,
        customer_id: str | None = None,
        booking_id: str | None = None,
        correlation_id: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Notification], int]:
        """List notifications with optional filters."""
        stmt = select(Notification)
        count_stmt = select(func.count()).select_from(Notification)

        filters: list[Any] = []
        if channel:
            filters.append(Notification.channel == channel)
        if status:
            filters.append(Notification.status == status)
        if provider:
            filters.append(Notification.provider == provider)
        if customer_id:
            filters.append(Notification.customer_id == customer_id)
        if booking_id:
            filters.append(Notification.booking_id == booking_id)
        if correlation_id:
            filters.append(Notification.correlation_id == correlation_id)
        if date_from:
            filters.append(Notification.created_at >= date_from)
        if date_to:
            filters.append(Notification.created_at <= date_to)

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = (await self._db.execute(count_stmt)).scalar_one()
        stmt = stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all()), total

    async def list_failed(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Notification], int]:
        """Return all FAILED notifications (for the delivery-failures endpoint)."""
        return await self.list_notifications(
            status=NotificationStatus.FAILED.value,
            offset=offset,
            limit=limit,
        )

    async def get_statistics(self) -> dict[str, Any]:
        """Aggregate statistics for the statistics endpoint."""
        from sqlalchemy import case, cast, Float

        status_counts = await self._db.execute(
            select(Notification.status, func.count().label("cnt"))
            .group_by(Notification.status)
        )
        channel_counts = await self._db.execute(
            select(Notification.channel, func.count().label("cnt"))
            .group_by(Notification.channel)
        )

        stats: dict[str, Any] = {
            "by_status": {row.status: row.cnt for row in status_counts},
            "by_channel": {row.channel: row.cnt for row in channel_counts},
        }
        return stats


# ─────────────────────────────────────────────────────────────────────────────
# NotificationAttemptRepository
# ─────────────────────────────────────────────────────────────────────────────


class NotificationAttemptRepository:
    """Data-access object for the ``notification_attempts`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        notification_id: str,
        provider: str,
        attempt_number: int,
    ) -> NotificationAttempt:
        attempt = NotificationAttempt(
            notification_id=notification_id,
            provider=provider,
            attempt_number=attempt_number,
            status=AttemptStatus.IN_PROGRESS.value,
        )
        self._db.add(attempt)
        await self._db.flush()
        return attempt

    async def mark_success(
        self,
        attempt: NotificationAttempt,
        *,
        provider_message_id: str | None,
        provider_response: dict | None,
        latency_ms: int | None = None,
    ) -> None:
        attempt.status = AttemptStatus.SUCCESS.value
        attempt.completed_at = _utcnow()
        attempt.provider_message_id = provider_message_id
        attempt.provider_response = provider_response
        attempt.latency_ms = latency_ms
        await self._db.flush()

    async def mark_failed(
        self,
        attempt: NotificationAttempt,
        *,
        error_code: str | None,
        error_message: str | None,
        provider_response: dict | None,
        is_retryable: bool,
        latency_ms: int | None = None,
    ) -> None:
        attempt.status = AttemptStatus.FAILED.value
        attempt.completed_at = _utcnow()
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.provider_response = provider_response
        attempt.is_retryable = is_retryable
        attempt.latency_ms = latency_ms
        await self._db.flush()

    async def list_for_notification(self, notification_id: str) -> list[NotificationAttempt]:
        result = await self._db.execute(
            select(NotificationAttempt)
            .where(NotificationAttempt.notification_id == notification_id)
            .order_by(NotificationAttempt.attempt_number.asc())
        )
        return list(result.scalars().all())

    async def count_for_notification(self, notification_id: str) -> int:
        result = await self._db.execute(
            select(func.count())
            .select_from(NotificationAttempt)
            .where(NotificationAttempt.notification_id == notification_id)
        )
        return result.scalar_one()


# ─────────────────────────────────────────────────────────────────────────────
# ApiRequestRepository
# ─────────────────────────────────────────────────────────────────────────────


class ApiRequestRepository:
    """Data-access object for the ``api_requests`` table."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        service: str,
        method: str,
        path: str,
        request_body: dict | None = None,
        notification_id: str | None = None,
        event_id: str | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ApiRequest:
        req = ApiRequest(
            service=service,
            method=method,
            path=path,
            request_body=request_body,
            notification_id=notification_id,
            event_id=event_id,
            request_id=request_id,
            correlation_id=correlation_id,
            status=ApiRequestStatus.PENDING.value,
        )
        self._db.add(req)
        await self._db.flush()
        return req

    async def mark_success(
        self,
        req: ApiRequest,
        *,
        response_status: int,
        response_body: dict | None,
        latency_ms: int | None = None,
    ) -> None:
        req.status = ApiRequestStatus.SUCCESS.value
        req.response_status = response_status
        req.response_body = response_body
        req.completed_at = _utcnow()
        req.latency_ms = latency_ms
        await self._db.flush()

    async def mark_failed(
        self,
        req: ApiRequest,
        *,
        error: str,
        response_status: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        req.status = ApiRequestStatus.FAILED.value
        req.error = error
        req.response_status = response_status
        req.completed_at = _utcnow()
        req.latency_ms = latency_ms
        await self._db.flush()
