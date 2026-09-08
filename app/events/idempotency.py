"""
Idempotency checker and guard for event processing.

Ensures that an event is processed at most once per handler, even if SQS
delivers the message multiple times.

Uses the `event_processings` database table with a unique constraint on
`(event_id, handler_name)`.
"""
from __future__ import annotations

from app.core.exceptions import IdempotencyConflict
from app.core.logging import get_logger
from app.notifications.domain.enums import EventProcessingStatus
from app.notifications.domain.models import EventProcessing
from app.notifications.infrastructure.repositories import EventProcessingRepository

logger = get_logger(__name__)


class IdempotencyGuard:
    """Guards handler execution against duplicate processing."""

    def __init__(self, repo: EventProcessingRepository) -> None:
        self._repo = repo

    async def acquire(self, *, event_id: str, handler_name: str) -> EventProcessing:
        """Acquire processing rights for an (event_id, handler_name) pair.

        Args:
            event_id: The UUID of the event.
            handler_name: The identifier of the handler attempting to process.

        Returns:
            The newly created or existing EventProcessing record.

        Raises:
            IdempotencyConflict: If the event has already been COMPLETED, PROCESSING,
                or SKIPPED by this handler.
        """
        existing = await self._repo.get(event_id=event_id, handler_name=handler_name)
        if existing:
            if existing.status in (
                EventProcessingStatus.COMPLETED.value,
                EventProcessingStatus.PROCESSING.value,
                EventProcessingStatus.SKIPPED.value,
            ):
                logger.info(
                    "Idempotency conflict detected - skipping handler execution",
                    event_id=event_id,
                    handler_name=handler_name,
                    status=existing.status,
                )
                raise IdempotencyConflict(event_id=event_id, handler_name=handler_name)

            # If it previously failed or is pending, we can re-attempt
            await self._repo.mark_processing(existing)
            return existing

        # Insert new record
        processing = await self._repo.create(event_id=event_id, handler_name=handler_name)
        await self._repo.mark_processing(processing)
        return processing

    async def complete(self, processing: EventProcessing) -> None:
        """Mark handler processing as COMPLETED."""
        await self._repo.mark_completed(processing)

    async def fail(self, processing: EventProcessing, error: str) -> None:
        """Mark handler processing as FAILED."""
        await self._repo.mark_failed(processing, error=error)

    async def skip(self, processing: EventProcessing) -> None:
        """Mark handler processing as SKIPPED."""
        await self._repo.mark_skipped(processing)
