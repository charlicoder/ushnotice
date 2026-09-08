"""
Handler for `loyalty.rewarded` event.

Processes events published when a customer earns a loyalty reward (5th booking).
Sends:
  1. WhatsApp message  — if whatsapp_verified is True or WhatsApp recipient resolved.
  2. SMS               — ONLY if WhatsApp was NOT sent (fallback).
  3. Email             — independently if customer email is present.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


def _email_subject(context: dict) -> str:
    service_name = context.get("service_name")
    if service_name:
        return f"🎉 You Earned a Free {service_name} Reward!"
    return "🎉 You Earned a Free Loyalty Reward – USHSPA"


class LoyaltyRewardedHandler:
    """Processes loyalty.rewarded events."""

    event_type: str = "loyalty.rewarded"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)

        customer_id = str(data.get("customer_id") or "")
        reward_id = str(data.get("reward_id") or "")
        booking_id = str(data.get("booking_id") or "")
        correlation_id = envelope.correlation_id_str

        whatsapp_verified: bool = bool(data.get("whatsapp_verified") or data.get("is_whatsapp_verified"))

        # Context for templates
        context = {
            "reward_id": reward_id,
            "tracker_id": str(data.get("tracker_id") or ""),
            "customer_id": customer_id,
            "customer_name": data.get("customer_name") or "",
            "service_id": str(data.get("service_id") or ""),
            "service_name": data.get("service_name") or "Spa Treatment",
            "bookings_required": data.get("bookings_required") or 5,
            "total_rewards_earned": data.get("total_rewards_earned") or 1,
            "reward_status": data.get("reward_status") or "available",
            "expires_at": data.get("expires_at") or "",
            "booking_id": booking_id,
        }

        whatsapp_sent = False

        # ── 1. WhatsApp (if verified / available) ────────────────────────────
        if whatsapp_verified:
            wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
            if wa_recipient:
                req_wa = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=wa_recipient,
                    template_name="loyalty/rewarded_whatsapp",
                    template_context={
                        **context,
                        "customer_name": wa_recipient.name or context["customer_name"],
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req_wa)
                whatsapp_sent = True
                logger.info(
                    "loyalty_rewarded_whatsapp_sent",
                    reward_id=reward_id,
                    customer_id=customer_id,
                )

        # ── 2. SMS — fallback if WhatsApp not sent ────────────────────────────
        if not whatsapp_sent:
            sms_recipient = ChannelResolver.resolve_sms_recipient(data)
            if sms_recipient:
                req_sms = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=sms_recipient,
                    template_name="loyalty/rewarded_sms",
                    template_context={
                        **context,
                        "customer_name": sms_recipient.name or context["customer_name"],
                    },
                    customer_id=customer_id or None,
                    booking_id=booking_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req_sms)
                logger.info(
                    "loyalty_rewarded_sms_sent",
                    reward_id=reward_id,
                    customer_id=customer_id,
                )

        # ── 3. Email — sent independently if email present ───────────────────
        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="loyalty/rewarded_email",
                template_context={
                    **context,
                    "customer_name": email_recipient.name or context["customer_name"],
                },
                subject=_email_subject(context),
                customer_id=customer_id or None,
                booking_id=booking_id or None,
                correlation_id=correlation_id,
            )
            await service.send(req_email)
            logger.info(
                "loyalty_rewarded_email_sent",
                reward_id=reward_id,
                customer_id=customer_id,
            )
