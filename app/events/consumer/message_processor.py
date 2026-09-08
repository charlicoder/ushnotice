"""
SQS Message Processor.

Responsible for parsing raw SQS message strings, validating against EventEnvelope,
persisting the raw event, and delegating to the EventRouter.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EventValidationError, UnknownEventTypeError
from app.core.logging import bind_request_context, get_logger
from app.events.handlers.base import HandlerContext
from app.events.router import EventRouter
from app.events.schemas.envelope import EventEnvelope
from app.notifications.domain.models import Event

logger = get_logger(__name__)


class MessageProcessor:
    """Processes single SQS messages end-to-end within a database session."""

    def __init__(self, router: EventRouter) -> None:
        self._router = router

    async def process_raw_message(
        self,
        raw_body: str,
        *,
        sqs_message_id: str | None = None,
        sqs_receipt_handle: str | None = None,
        db: AsyncSession,
    ) -> Event | None:
        """Process a raw SQS message string.

        Args:
            raw_body: Raw JSON string from SQS MessageBody.
            sqs_message_id: SQS-assigned MessageId.
            sqs_receipt_handle: ReceiptHandle for message deletion/visibility extension.
            db: Active async DB session.

        Returns:
            The created Event record.
        """
        ctx = HandlerContext.from_session(db)

        # 1. Parse JSON
        try:
            parsed_json = json.loads(raw_body)
            # Support SNS-wrapped SQS messages if needed
            if isinstance(parsed_json, dict) and "Message" in parsed_json and "Type" in parsed_json:
                try:
                    parsed_json = json.loads(parsed_json["Message"])
                except Exception:
                    pass
        except Exception as exc:
            logger.error("Failed to parse SQS body as JSON", error=str(exc), body=raw_body[:200])
            # Persist minimal invalid event
            event = await ctx.event_repo.create(
                event_id="malformed-json",
                event_type="unknown.invalid",
                version=1,
                source="unknown",
                correlation_id=None,
                causation_id=None,
                payload={"raw_body": raw_body[:500]},
                sqs_message_id=sqs_message_id,
                sqs_receipt_handle=sqs_receipt_handle,
            )
            await ctx.event_repo.mark_invalid(event, error=f"Malformed JSON: {exc}")
            await db.commit()
            raise EventValidationError(f"Invalid JSON payload: {exc}") from exc

        # 2. Validate against EventEnvelope
        try:
            envelope = EventEnvelope.model_validate(parsed_json)
        except ValidationError as exc:
            event_id = str(parsed_json.get("event_id") or "validation-error")
            event_type = str(parsed_json.get("event_type") or "unknown")
            source = str(parsed_json.get("source") or "unknown")

            logger.error("Event envelope schema validation failed", errors=exc.errors(), payload=parsed_json)

            event = await ctx.event_repo.create(
                event_id=event_id,
                event_type=event_type,
                version=int(parsed_json.get("version") or 1),
                source=source,
                correlation_id=parsed_json.get("correlation_id"),
                causation_id=parsed_json.get("causation_id"),
                payload=parsed_json.get("data") if isinstance(parsed_json.get("data"), dict) else None,
                sqs_message_id=sqs_message_id,
                sqs_receipt_handle=sqs_receipt_handle,
            )
            await ctx.event_repo.mark_invalid(event, error=str(exc))
            await db.commit()
            raise EventValidationError(f"Schema validation failed: {exc}") from exc

        bind_request_context(
            event_id=envelope.event_id_str,
            correlation_id=envelope.correlation_id_str,
        )

        # 3. Persist Event in DB
        event = await ctx.event_repo.create(
            event_id=envelope.event_id_str,
            event_type=envelope.event_type,
            version=envelope.version,
            source=envelope.source,
            correlation_id=envelope.correlation_id_str,
            causation_id=envelope.causation_id_str,
            payload=envelope.data,
            sqs_message_id=sqs_message_id,
            sqs_receipt_handle=sqs_receipt_handle,
        )
        await db.commit()

        # 4. Route to handlers
        try:
            await self._router.route(envelope, event, ctx)
            await db.commit()
            return event
        except UnknownEventTypeError as exc:
            logger.warning("No handler registered for event type", event_type=exc.event_type)
            await ctx.event_repo.mark_processed(event)  # Don't retry unroutable events forever
            await db.commit()
            return event
        except Exception as exc:
            await db.rollback()
            logger.exception("Error processing event", event_id=envelope.event_id_str, error=str(exc))
            raise
