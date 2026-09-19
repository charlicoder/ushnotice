"""
Handler for `gift.purchase.completed` event.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)

_GIFT_CARD_PAGE_URL = "https://ushspa.co/gift/{public_token}"
_APP_INSTALL_URL = "https://apps.apple.com/kw/app/ushspa/id6771279814"
_WEBSITE_URL = "https://ushspa.co/"


def _fmt_expire(expire_date: str) -> str:
    """Format an ISO expire_date string as 'DD MMM YYYY', gracefully."""
    try:
        dt = datetime.fromisoformat(expire_date.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return expire_date


def _build_gift_context(data: dict) -> dict:
    """Extract and normalise all gift fields for template rendering."""
    sender_data: dict = data.get("sender_data") or {}
    recipient_data: dict = data.get("recipient_data") or {}

    expire_raw = data.get("expire_date") or ""
    expire_fmt = _fmt_expire(expire_raw) if expire_raw else ""

    public_token = data.get("public_token") or ""
    gift_card_url = _GIFT_CARD_PAGE_URL.format(public_token=public_token) if public_token else _APP_INSTALL_URL

    sender_name = sender_data.get("name") or data.get("sender_id") or "A generous friend"
    recipient_name = recipient_data.get("name") or "Valued Customer"

    return {
        "id": str(data.get("id") or ""),
        "gift_type": str(data.get("gift_type") or ""),
        "public_token": public_token,
        "secret_code": str(data.get("secret_code") or ""),
        "digital_gift_data": data.get("digital_gift_data") or {},
        "total_amount": str(data.get("total_amount") or ""),
        "currency": "KWD",
        "expire_date": expire_fmt,
        "expire_date_raw": expire_raw,
        "gift_message": data.get("gift_message") or "",
        "gift_card_url": gift_card_url,
        "sender_id": str(data.get("sender_id") or ""),
        "sender_name": sender_name,
        "sender_phone": sender_data.get("phone_number") or "",
        "sender_email": sender_data.get("email") or "",
        "recipient_name": recipient_name,
        "recipient_phone": data.get("recipient_phone") or recipient_data.get("phone_number") or "",
        "recipient_email": recipient_data.get("email") or "",
        "recipient_password": str(recipient_data.get("password") or data.get("recipient_password") or ""),
        "recipient_language": str(data.get("recipient_language") or "en").lower(),
        "payment_provider": str(data.get("payment_provider") or ""),
        "payment_through": str(data.get("payment_through") or ""),
    }


def _sender_whatsapp_message(ctx: dict) -> str:
    lines = [
        "🎁 Gift Sent Successfully — USHSPA ",
        "",
        f"Dear {ctx['sender_name']},",
        "",
        f"Your gift has been sent to {ctx['recipient_name']} and is now active!"
    ]
    if ctx["total_amount"]:
        lines.append(f"Value: {ctx['total_amount']} {ctx['currency']}")
    if ctx["expire_date"]:
        lines.append(f"Expires: {ctx['expire_date']}")
    lines.append(f"View your gift link: {ctx['gift_card_url']}")
    return "\n".join(lines)


def _recipient_whatsapp_message_en(ctx: dict) -> str:
    pass_part = f"\nYour Login Password: {ctx['recipient_password']}" if ctx.get("recipient_password") else ""
    return (
        f"🎁 You received a gift from {ctx['sender_name']}!\n\n"
        f"Your Secret Code: {ctx['secret_code']}\n"
        f"Expires: {ctx['expire_date']}{pass_part}\n"
        f"View your gift: {ctx['gift_card_url']}"
    )


def _recipient_whatsapp_message_ar(ctx: dict) -> str:
    pass_part = f"\nكلمة المرور لتسجيل الدخول: {ctx['recipient_password']}" if ctx.get("recipient_password") else ""
    return (
        f"🎁 لقد استلمت هدية من {ctx['sender_name']}!\n\n"
        f"رمز الهدية السري: {ctx['secret_code']}\n"
        f"تنتهي في: {ctx['expire_date']}{pass_part}\n"
        f"شاهد هديتك: {ctx['gift_card_url']}"
    )


class GiftPurchaseCompletedHandler:
    """
    Processes gift.purchase.completed events.
    """
    event_type: str = "gift.purchase.completed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)
        correlation_id = envelope.correlation_id_str

        gift_id = str(data.get("id") or "")
        gctx = _build_gift_context(data)

        payment_through = gctx["payment_through"]
        sender_phone = gctx["sender_phone"]
        sender_name = gctx["sender_name"]

        # ── Notify SENDER ─────────────────────────────────────────────────
        if sender_phone:
            await self._notify_sender(
                envelope=envelope,
                service=service,
                gctx=gctx,
                sender_phone=sender_phone,
                sender_name=sender_name,
                gift_id=gift_id,
                correlation_id=correlation_id,
            )
        else:
            logger.info("gift_purchase_completed_sender_no_phone", gift_id=gift_id)

        # ── Notify RECIPIENT ──────────────────────────────────────────────
        if payment_through != "desk":
            recipient_phone = gctx["recipient_phone"]
            recipient_name = gctx["recipient_name"]

            if recipient_phone:
                await self._notify_recipient(
                    envelope=envelope,
                    service=service,
                    gctx=gctx,
                    recipient_phone=recipient_phone,
                    recipient_name=recipient_name,
                    gift_id=gift_id,
                    correlation_id=correlation_id,
                )
            else:
                logger.info("gift_purchase_completed_recipient_no_contact", gift_id=gift_id)
        else:
            logger.info("gift_purchase_completed_recipient_skipped_desk", gift_id=gift_id)

    async def _notify_sender(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        gctx: dict,
        sender_phone: str,
        sender_name: str,
        gift_id: str,
        correlation_id: str | None,
    ) -> None:
        """Send WhatsApp to the gift sender."""
        try:
            wa_body = _sender_whatsapp_message(gctx)
            # Remove secret_code from context for safety before sending
            safe_ctx = {k: v for k, v in gctx.items() if k != "secret_code"}
            sender_payload = {
                "phone_number": sender_phone,
                "customer_name": sender_name,
            }
            recipient = ChannelResolver.resolve_whatsapp_recipient(sender_payload)
            if recipient:
                req = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=recipient,
                    template_name="gifts/purchase_sender_whatsapp",
                    template_context={**safe_ctx, "message_body": wa_body},
                    customer_id=None,
                    booking_id=gift_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req)
                logger.info("gift_purchase_sender_whatsapp_sent", gift_id=gift_id)
        except Exception as exc:
            logger.warning("gift_purchase_sender_whatsapp_failed", gift_id=gift_id, error=str(exc))

    async def _notify_recipient(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        gctx: dict,
        recipient_phone: str,
        recipient_name: str,
        gift_id: str,
        correlation_id: str | None,
    ) -> None:
        """Send WhatsApp to the gift recipient with secret_code and link."""
        try:
            lang = gctx["recipient_language"]
            if lang == "ar":
                wa_body = _recipient_whatsapp_message_ar(gctx)
            else:
                wa_body = _recipient_whatsapp_message_en(gctx)

            recipient_payload = {
                "phone_number": recipient_phone,
                "customer_name": recipient_name,
            }
            recipient = ChannelResolver.resolve_whatsapp_recipient(recipient_payload)
            if recipient:
                req = NotificationRequest(
                    event_id=envelope.event_id_str,
                    recipient=recipient,
                    template_name="gifts/purchase_recipient_whatsapp",
                    template_context={**gctx, "message_body": wa_body},
                    customer_id=None,
                    booking_id=gift_id or None,
                    correlation_id=correlation_id,
                )
                await service.send(req)
                logger.info("gift_purchase_recipient_whatsapp_sent", gift_id=gift_id)
        except Exception as exc:
            logger.warning("gift_purchase_recipient_whatsapp_failed", gift_id=gift_id, error=str(exc))
