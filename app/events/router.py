"""
Event Router — routes validated EventEnvelope objects to registered handlers.

The router coordinates:
1. Handler resolution via HandlerRegistry
2. Idempotency checking via IdempotencyGuard
3. Invocation of each registered handler with HandlerContext
4. Updating Event status and EventProcessing status
"""
from __future__ import annotations

from app.core.exceptions import IdempotencyConflict, UnknownEventTypeError
from app.core.logging import bind_request_context, get_logger
from app.events.handlers.base import HandlerContext
from app.events.idempotency import IdempotencyGuard
from app.events.registry.handler_registry import HandlerRegistry
from app.events.schemas.envelope import EventEnvelope
from app.notifications.domain.models import Event

logger = get_logger(__name__)


class EventRouter:
    """Dispatches incoming events to their designated handlers."""

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    async def route(
        self,
        envelope: EventEnvelope,
        event_record: Event,
        ctx: HandlerContext,
    ) -> None:
        """Route an event envelope to all registered handlers for its event_type.

        Args:
            envelope: Validated event envelope.
            event_record: Persisted Event ORM instance.
            ctx: HandlerContext holding database session & repositories.

        Raises:
            UnknownEventTypeError: If no handlers are registered for the event type.
            Exception: Any unhandled exception from the handlers.
        """
        event_type = envelope.event_type
        event_id = envelope.event_id_str
        correlation_id = envelope.correlation_id_str

        bind_request_context(
            event_id=event_id,
            correlation_id=correlation_id,
        )

        handlers = self._registry.resolve(event_type)
        guard = IdempotencyGuard(ctx.processing_repo)

        logger.info(
            "Routing event to handlers",
            event_type=event_type,
            event_id=event_id,
            handler_count=len(handlers),
        )

        await ctx.event_repo.mark_processing(event_record)

        # Cache the event PK as a plain Python value NOW, before any rollback.
        # After rollback() SQLAlchemy expires all ORM attributes; accessing
        # event_record.id afterward triggers a lazy DB refresh which fails with
        # MissingGreenlet inside the asyncpg async loop.
        event_record_id = event_record.id

        handler_errors: list[str] = []

        for handler in handlers:
            handler_name = type(handler).__name__
            try:
                processing = await guard.acquire(
                    event_id=event_record_id,
                    handler_name=handler_name,
                )
            except IdempotencyConflict:
                logger.info(
                    "Skipping duplicate event for handler",
                    event_id=event_id,
                    handler=handler_name,
                )
                continue

            try:
                logger.info(
                    "Executing handler",
                    handler=handler_name,
                    event_type=event_type,
                    event_id=event_id,
                )
                await handler.handle(envelope, ctx)
                await guard.complete(processing)
                logger.info(
                    "Handler completed successfully",
                    handler=handler_name,
                    event_type=event_type,
                    event_id=event_id,
                )
            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "Handler execution failed",
                    handler=handler_name,
                    event_type=event_type,
                    event_id=event_id,
                    error=error_msg,
                )
                try:
                    await ctx.db.rollback()
                except Exception:
                    pass
                try:
                    await guard.fail(processing, error=error_msg)
                except Exception as guard_exc:
                    logger.warning("Failed to mark processing record as failed", error=str(guard_exc))
                handler_errors.append(f"[{handler_name}] {error_msg}")

        if handler_errors:
            full_error = " | ".join(handler_errors)
            try:
                await ctx.db.rollback()
            except Exception:
                pass
            # Use the cached plain ID — event_record attributes are expired after rollback
            await ctx.event_repo.mark_failed_by_id(event_record_id, error=full_error)
            await ctx.db.commit()
            raise RuntimeError(f"Handler execution failed for event {event_id}: {full_error}")

        # Use the cached plain ID for consistency
        await ctx.event_repo.mark_processed_by_id(event_record_id)
        logger.info("Event routed and processed successfully", event_id=event_id, event_type=event_type)
