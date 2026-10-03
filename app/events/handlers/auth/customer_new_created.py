"""
Handler for `customer.new_created` / `CUSTOMER_NEW_CREATED` event.

Fires when a new customer account is auto-created via the get-or-create API
(no OTP flow). Sends the customer an SMS with their temporary password and
instructions to download the USH Spa app.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class CustomerNewCreatedHandler:
    """Processes customer.new_created events by sending a welcome WhatsApp with SMS fallback."""

    event_type: str = "customer.new_created"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        # Build display name from first_name / last_name or name field
        customer_name = (
            data.get("name")
            or " ".join(
                filter(None, [data.get("first_name", ""), data.get("last_name", "")])
            )
            or ""
        ).strip()

        password = str(data.get("password") or "")
        customer_id = str(data.get("user_id") or data.get("customer_id") or "")
        phone = str(data.get("phone_number") or data.get("phone") or "")
        norm_phone = "".join(c for c in phone if c.isdigit())

        # Check if recipient is part of a gift voucher purchase
        if norm_phone:
            try:
                import asyncio
                from app.core.redis import get_redis
                redis_client = get_redis()
                # Check if voucher handler already notified or marked pending.
                # If concurrent, poll briefly up to 1.5 seconds.
                for attempt in range(5):
                    if await redis_client.get(f"voucher_recipient_notified:{norm_phone}") or \
                       await redis_client.get(f"voucher_recipient_pending:{norm_phone}"):
                        logger.info(
                            "customer_new_created_skipped_voucher_recipient",
                            event_id=envelope.event_id_str,
                            phone=norm_phone,
                        )
                        return
                    if attempt < 4:
                        await asyncio.sleep(0.3)
            except Exception as exc:
                logger.debug("voucher_recipient_redis_check_failed", error=str(exc))

        whatsapp_sent = False

        # 1. WhatsApp Welcome Notification
        try:
            wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
            if wa_recipient:
                req_wa = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=wa_recipient,
                    template_name="user/customer_new_created",
                    template_context={
                        "customer_name": customer_name,
                        "password": password,
                    },
                    customer_id=customer_id,
                    correlation_id=envelope.correlation_id_str,
                )
                notification = await service.send(req_wa)
                status_val = getattr(notification, "status", None)
                if status_val in (
                    NotificationStatus.SENT,
                    NotificationStatus.DELIVERED,
                    NotificationStatus.SENT.value,
                    NotificationStatus.DELIVERED.value,
                ) or (notification is not None and not isinstance(status_val, (str, NotificationStatus))):
                    whatsapp_sent = True
                logger.info(
                    "Welcome WhatsApp sent for new customer",
                    event_id=envelope.event_id_str,
                    phone=wa_recipient.address,
                )
        except Exception as exc:
            logger.warning(
                "Welcome WhatsApp failed for new customer",
                event_id=envelope.event_id_str,
                error=str(exc),
            )

        # 2. SMS Welcome Notification (fallback only if WhatsApp was NOT sent)
        if not whatsapp_sent:
            try:
                sms_recipient = ChannelResolver.resolve_sms_recipient(data)
                if sms_recipient:
                    req_sms = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=sms_recipient,
                        template_name="user/customer_new_created",
                        template_context={
                            "customer_name": customer_name,
                            "password": password,
                        },
                        customer_id=customer_id,
                        correlation_id=envelope.correlation_id_str,
                    )
                    await service.send(req_sms)
                    logger.info(
                        "Welcome SMS sent for new customer",
                        event_id=envelope.event_id_str,
                        phone=sms_recipient.address,
                    )
                else:
                    logger.warning(
                        "No phone number in customer.new_created event — skipping SMS",
                        event_id=envelope.event_id_str,
                    )
            except Exception as exc:
                logger.warning(
                    "Welcome SMS failed for new customer",
                    event_id=envelope.event_id_str,
                    error=str(exc),
                )


class CustomerCreatedHandler(CustomerNewCreatedHandler):
    """Processes customer.created events by sending a welcome SMS with password."""

    event_type: str = "customer.created"
