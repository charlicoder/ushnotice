"""
AWS SQS Event Producer for putting/publishing events to AWS_SQS_NOTIFICATION_QUEUE_URL.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.schemas.envelope import EventEnvelope

logger = get_logger(__name__)


class SQSProducer:
    """Async producer for publishing notification event envelopes to SQS."""

    def __init__(self) -> None:
        settings = get_settings()
        self._queue_url = settings.AWS_SQS_NOTIFICATION_QUEUE_URL

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

    async def publish_envelope(self, envelope: EventEnvelope) -> str:
        """Publish a validated EventEnvelope to AWS_SQS_NOTIFICATION_QUEUE_URL.

        Returns:
            The SQS MessageId.
        """
        payload_json = envelope.model_dump_json()
        return await self.publish_raw(
            body=payload_json,
            message_attributes={
                "event_type": {
                    "DataType": "String",
                    "StringValue": envelope.event_type,
                },
                "source": {
                    "DataType": "String",
                    "StringValue": envelope.source,
                },
            },
        )

    async def publish_raw(
        self,
        body: str,
        message_attributes: dict[str, Any] | None = None,
    ) -> str:
        """Publish a raw body string to AWS_SQS_NOTIFICATION_QUEUE_URL."""
        if not self._queue_url:
            raise RuntimeError("AWS_SQS_NOTIFICATION_QUEUE_URL is not configured.")

        params: dict[str, Any] = {
            "QueueUrl": self._queue_url,
            "MessageBody": body,
        }
        if message_attributes:
            params["MessageAttributes"] = message_attributes

        response = await asyncio.to_thread(self._sqs_client.send_message, **params)
        message_id = str(response.get("MessageId", ""))
        logger.info(
            "Published event to SQS queue",
            queue_url=self._queue_url,
            message_id=message_id,
        )
        return message_id
