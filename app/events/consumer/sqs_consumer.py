"""
AWS SQS Consumer for async event ingestion.

Runs as a managed background loop within the FastAPI application lifecycle.
Features:
- Long polling (WaitTimeSeconds = 20s)
- Batch fetching (up to 10 messages)
- Concurrency limiting via asyncio.Semaphore
- Automatic message deletion upon successful processing
- Visibility timeout management
- Graceful shutdown handling
"""
from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.core.database import get_db_context
from app.core.logging import get_logger
from app.events.consumer.message_processor import MessageProcessor
from app.events.router import EventRouter

logger = get_logger(__name__)


class SQSConsumer:
    """Async consumer polling AWS SQS for notification events."""

    def __init__(self, router: EventRouter) -> None:
        self._router = router
        self._processor = MessageProcessor(router)
        self._is_running = False
        self._task: asyncio.Task[None] | None = None

        settings = get_settings()
        self._queue_url = settings.AWS_SQS_NOTIFICATION_QUEUE_URL
        self._dlq_url = settings.AWS_SQS_NOTIFICATION_DLQ_URL
        self._max_messages = settings.SQS_MAX_MESSAGES
        self._wait_time_seconds = settings.SQS_WAIT_TIME_SECONDS
        self._visibility_timeout = settings.SQS_VISIBILITY_TIMEOUT
        self._max_concurrency = settings.SQS_MAX_CONCURRENCY

        # Initialize boto3 SQS client
        boto_config = Config(
            region_name=settings.AWS_REGION,
            retries={"max_attempts": 5, "mode": "standard"},
        )
        client_kwargs: dict[str, Any] = {
            "service_name": "sqs",
            "config": boto_config,
            "region_name": settings.AWS_REGION,
        }
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY.get_secret_value():
            client_kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            client_kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY.get_secret_value()
            session_token = settings.AWS_SESSION_TOKEN.strip()
            if session_token:
                client_kwargs["aws_session_token"] = session_token

        self._sqs_client = boto3.client(**client_kwargs)

    async def start(self) -> None:
        """Start the background polling task."""
        if not self._queue_url:
            logger.warning("AWS_SQS_NOTIFICATION_QUEUE_URL is not set. SQS consumer disabled.")
            return

        self._is_running = True
        self._task = asyncio.create_task(self._poll_loop(), name="sqs_consumer_loop")
        logger.info("SQS Consumer started", queue_url=self._queue_url)

    async def stop(self) -> None:
        """Signal consumer to stop and await active tasks."""
        logger.info("Stopping SQS Consumer...")
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SQS Consumer stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        semaphore = asyncio.Semaphore(self._max_concurrency)

        while self._is_running:
            try:
                # Run sync boto3 call in thread pool to not block asyncio event loop
                messages = await asyncio.to_thread(self._receive_messages)
                if not messages:
                    continue

                logger.info("Received SQS messages", count=len(messages))

                tasks = [
                    self._handle_single_message_with_semaphore(msg, semaphore)
                    for msg in messages
                ]
                await asyncio.gather(*tasks, return_exceptions=True)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Error in SQS poll loop", error=str(exc))
                await asyncio.sleep(5)  # Backoff on error

    def _receive_messages(self) -> list[dict[str, Any]]:
        """Synchronously receive messages from SQS."""
        try:
            response = self._sqs_client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=self._max_messages,
                WaitTimeSeconds=self._wait_time_seconds,
                VisibilityTimeout=self._visibility_timeout,
                AttributeNames=["All"],
                MessageAttributeNames=["All"],
            )
            return response.get("Messages", [])
        except Exception as exc:
            logger.error("SQS receive_message error", error=str(exc))
            return []

    async def _handle_single_message_with_semaphore(
        self,
        message: dict[str, Any],
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            await self._process_and_delete(message)

    async def _process_and_delete(self, message: dict[str, Any]) -> None:
        receipt_handle = message.get("ReceiptHandle", "")
        message_id = message.get("MessageId", "")
        body = message.get("Body", "")

        success = False
        async with get_db_context() as db:
            try:
                await self._processor.process_raw_message(
                    raw_body=body,
                    sqs_message_id=message_id,
                    sqs_receipt_handle=receipt_handle,
                    db=db,
                )
                success = True
            except Exception as exc:
                logger.error("Message processing failed", message_id=message_id, error=str(exc))

        # Delete message from SQS upon successful processing
        if success and receipt_handle:
            try:
                await asyncio.to_thread(
                    self._sqs_client.delete_message,
                    QueueUrl=self._queue_url,
                    ReceiptHandle=receipt_handle,
                )
                logger.info("Deleted processed message from SQS", message_id=message_id)
            except Exception as exc:
                logger.error("Failed to delete message from SQS", message_id=message_id, error=str(exc))
