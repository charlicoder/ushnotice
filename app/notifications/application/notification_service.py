"""
Notification Application Service.

Coordinates:
1. Notification creation and persistence
2. Template rendering
3. Dispatching to the appropriate provider (SMS, WhatsApp, Email)
4. Recording send attempts and status changes
5. Retry handling with exponential backoff
"""
from __future__ import annotations

import time
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.notifications.domain.enums import (
    AttemptStatus,
    NotificationChannel,
    NotificationStatus,
)
from app.notifications.domain.models import Notification
from app.notifications.domain.value_objects import DeliveryResult, NotificationRequest
from app.providers.factory import (
    get_email_provider,
    get_sms_provider,
    get_whatsapp_provider,
)

logger = get_logger(__name__)


_DEDUP_TTL_SECONDS = 24 * 60 * 60
# Templates whose notifications are scoped to a business object (voucher/gift id
# passed as ``booking_id``) rather than to a single event, so that the same gift
# republished under a new event_id still produces exactly one message per recipient.
_OBJECT_SCOPED_PREFIXES = ("voucher/", "gifts/")


class NotificationService:
    """Orchestrates notification dispatch, persistence, and delivery audit."""

    def __init__(self, ctx: HandlerContext) -> None:
        self._ctx = ctx

    @staticmethod
    def _dedup_key(req: NotificationRequest) -> str:
        template = req.template_name or ""
        scope = (
            req.booking_id
            if req.booking_id and template.startswith(_OBJECT_SCOPED_PREFIXES)
            else req.event_id
        )
        return (
            f"notif_sent:{template}:{req.recipient.channel.value}:"
            f"{req.recipient.address}:{scope}"
        )

    @staticmethod
    async def _claim(key: str) -> bool | None:
        """Atomically claim the right to send. True=claimed, False=duplicate, None=Redis unavailable."""
        try:
            from app.core.redis import get_redis

            return bool(await get_redis().set(key, "1", nx=True, ex=_DEDUP_TTL_SECONDS))
        except Exception as exc:  # noqa: BLE001
            logger.debug("notification_dedup_unavailable", error=str(exc))
            return None

    @staticmethod
    async def _release(key: str) -> None:
        try:
            from app.core.redis import get_redis

            await get_redis().delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.debug("notification_dedup_release_failed", error=str(exc))

    async def send(self, req: NotificationRequest) -> Notification:
        """Send a notification at most once per (template, channel, recipient, scope).

        The claim lives in Redis (not the DB) on purpose: when a handler fails
        after sending, the router rolls back the DB session — erasing the SENT
        record — and SQS redelivers the event. A DB-based check would then miss
        the earlier send and the recipient would receive the same SMS repeatedly.
        """
        key = self._dedup_key(req)
        claimed = await self._claim(key)
        if claimed is False:
            logger.info(
                "notification_duplicate_suppressed",
                template=req.template_name,
                channel=req.recipient.channel.value,
                recipient=req.recipient.masked_address,
                event_id=req.event_id,
            )
            # Transient (unpersisted) record flagged SENT so callers don't fall back to another channel.
            return Notification(
                event_id=req.event_id,
                channel=req.recipient.channel.value,
                recipient=req.recipient.address,
                template_name=req.template_name,
                status=NotificationStatus.SENT.value,
            )

        try:
            notification = await self._send_impl(req)
        except Exception:
            if claimed:
                await self._release(key)
            raise

        if claimed and notification.status not in (
            NotificationStatus.SENT.value,
            NotificationStatus.DELIVERED.value,
        ):
            # Delivery failed — allow a later retry to send.
            await self._release(key)
        return notification

    async def _send_impl(self, req: NotificationRequest) -> Notification:
        """Create and dispatch a notification.

        Args:
            req: NotificationRequest containing recipient, template, and context.

        Returns:
            The created and updated Notification ORM instance.
        """
        channel = req.recipient.channel
        settings = get_settings()

        # Determine active provider name
        provider_name: str
        if channel == NotificationChannel.SMS:
            provider_name = settings.SMS_PROVIDER
        elif channel == NotificationChannel.WHATSAPP:
            provider_name = settings.WHATSAPP_PROVIDER
        elif channel == NotificationChannel.EMAIL:
            provider_name = settings.EMAIL_PROVIDER if settings.EMAIL_ENABLED else "stub"
        else:
            provider_name = "unknown"

        # 1. Create Notification record in DB
        notification = await self._ctx.notification_repo.create(
            event_id=req.event_id,
            channel=channel.value,
            recipient=req.recipient.address,
            recipient_name=req.recipient.name or None,
            template_name=req.template_name,
            subject=req.subject or None,
            customer_id=req.customer_id,
            booking_id=req.booking_id,
            correlation_id=req.correlation_id,
            provider=provider_name,
        )

        await self._ctx.notification_repo.update_status(
            notification,
            status=NotificationStatus.PROCESSING,
            reason="Dispatching to provider",
        )

        # 2. Render Template
        is_html = (channel == NotificationChannel.EMAIL)
        try:
            rendered_body = self._ctx.renderer.render(
                req.template_name,
                lang=req.recipient.language,
                context=req.template_context,
                is_html=is_html,
            )
        except Exception as exc:
            logger.exception(
                "Template rendering failed",
                template=req.template_name,
                recipient=req.recipient.masked_address,
                error=str(exc),
            )
            await self._ctx.notification_repo.update_status(
                notification,
                status=NotificationStatus.FAILED,
                reason=f"Template rendering failed: {exc}",
            )
            return notification

        # 3. Dispatch to Provider with retry
        attempt_number = 1
        max_attempts = settings.NOTIFICATION_MAX_RETRIES + 1

        while attempt_number <= max_attempts:
            attempt = await self._ctx.attempt_repo.create(
                notification_id=notification.id,
                provider=provider_name,
                attempt_number=attempt_number,
            )

            start_time = time.monotonic()
            result: DeliveryResult

            try:
                if channel == NotificationChannel.SMS:
                    sms_prov = get_sms_provider()
                    result = await sms_prov.send_sms(
                        to=req.recipient.address,
                        body=rendered_body,
                    )
                elif channel == NotificationChannel.WHATSAPP:
                    wa_prov = get_whatsapp_provider()
                    result = await wa_prov.send_message(
                        to=req.recipient.address,
                        body=rendered_body,
                    )
                elif channel == NotificationChannel.EMAIL:
                    email_prov = get_email_provider()
                    subject = req.subject or self._ctx.renderer.render_subject(
                        req.template_name,
                        lang=req.recipient.language,
                        context=req.template_context,
                    ) or "Notification from USHSPA"
                    result = await email_prov.send_email(
                        to=req.recipient.address,
                        subject=subject,
                        html_body=rendered_body,
                        text_body=rendered_body,
                    )
                else:
                    result = DeliveryResult.fail(
                        error_code="UNSUPPORTED_CHANNEL",
                        error_message=f"Unsupported channel {channel}",
                        is_retryable=False,
                    )

            except Exception as exc:  # noqa: BLE001
                result = DeliveryResult.fail(
                    error_code="UNHANDLED_EXCEPTION",
                    error_message=str(exc),
                    is_retryable=True,
                )

            latency_ms = int((time.monotonic() - start_time) * 1000)

            # Record Attempt Outcome
            if result.success:
                raw_resp = result.raw_response if isinstance(result.raw_response, dict) else {"raw": result.raw_response}
                await self._ctx.attempt_repo.mark_success(
                    attempt,
                    provider_message_id=result.provider_message_id,
                    provider_response=raw_resp,
                    latency_ms=latency_ms,
                )
                await self._ctx.notification_repo.update_status(
                    notification,
                    status=NotificationStatus.SENT,
                    reason=f"Successfully sent via {provider_name}",
                )
                logger.info(
                    "Notification delivered successfully",
                    notification_id=notification.id,
                    channel=channel.value,
                    provider=provider_name,
                    attempt=attempt_number,
                )
                return notification
            else:
                raw_resp = result.raw_response if isinstance(result.raw_response, dict) else {"raw": result.raw_response}
                await self._ctx.attempt_repo.mark_failed(
                    attempt,
                    error_code=result.error_code,
                    error_message=result.error_message,
                    provider_response=raw_resp,
                    is_retryable=result.is_retryable,
                    latency_ms=latency_ms,
                )

                if result.is_retryable and attempt_number < max_attempts:
                    await self._ctx.notification_repo.increment_retry(notification)
                    await self._ctx.notification_repo.update_status(
                        notification,
                        status=NotificationStatus.RETRYING,
                        reason=f"Attempt {attempt_number} failed: {result.error_message}. Retrying...",
                    )
                    attempt_number += 1
                    import asyncio
                    delay = min(
                        settings.NOTIFICATION_RETRY_BASE_DELAY * (2 ** (attempt_number - 1)),
                        settings.NOTIFICATION_RETRY_MAX_DELAY,
                    )
                    await asyncio.sleep(delay)
                else:
                    await self._ctx.notification_repo.update_status(
                        notification,
                        status=NotificationStatus.FAILED,
                        reason=f"Delivery failed on attempt {attempt_number}: {result.error_message}",
                    )
                    logger.warning(
                        "Notification delivery failed permanently",
                        notification_id=notification.id,
                        channel=channel.value,
                        provider=provider_name,
                        attempts=attempt_number,
                        error=result.error_message,
                    )
                    break

        return notification
