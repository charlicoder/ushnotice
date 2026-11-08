"""Shared helpers for ``course_subscription.*`` notification handlers.

Course events are published by ushbooknpay through its transactional outbox
(see the course contract, section F).  The envelope carries flat payload fields
such as ``subscription_id``, ``subscription_number``, ``customer_data``,
``course_name`` …  ushnotice only sends customer notifications for these events;
all money actions (payment, invoice, credit note) happen in ushbooknpay.

Delivery follows the existing booking/voucher pattern:
  • WhatsApp when the customer is WhatsApp-verified, SMS as fallback.
  • Messages are pre-composed (en/ar) and passed as ``message_body``; there are
    no dedicated Jinja templates, so the renderer's ``message_body`` fallback
    is used (same as the voucher/gift redeemed handlers).
  • Event-level idempotency is provided by the router's ``IdempotencyGuard``.
    On top of that, a business-level guard checks the notifications table so a
    re-published event (new event_id) for the same subscription/booking does
    not message the customer twice.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)

TEMPLATE_PREFIX = "course_subscription"


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def fmt_date(value: Any) -> str:
    """Render an ISO datetime/date as ``YYYY-MM-DD`` in the spa's local timezone."""
    raw = _s(value)
    if not raw:
        return ""
    try:
        from app.core.timezone import to_local_tz

        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = to_local_tz(dt) or dt
        return dt.date().isoformat()
    except Exception:
        return raw.split("T")[0]


def customer_info(data: dict[str, Any]) -> dict[str, Any]:
    """Resolve customer name / phone / language / WhatsApp flag from the payload."""
    cd: dict[str, Any] = data.get("customer_data") if isinstance(data.get("customer_data"), dict) else {}
    full_name = (
        _s(data.get("customer_name"))
        or _s(cd.get("name"))
        or " ".join(p for p in (_s(cd.get("first_name")), _s(cd.get("last_name"))) if p)
    )
    phone = (
        _s(data.get("customer_phone"))
        or _s(cd.get("phone_number"))
        or _s(cd.get("phone"))
        or _s(cd.get("mobile"))
    )
    whatsapp_verified = bool(
        data.get("whatsapp_verified")
        or data.get("is_whatsapp_verified")
        or cd.get("whatsapp_verified")
        or cd.get("is_whatsapp_verified")
    )
    return {
        "customer_id": _s(data.get("customer_id")) or _s(cd.get("id")),
        "customer_name": full_name,
        "first_name": _s(cd.get("first_name")) or (full_name.split()[0] if full_name else ""),
        "phone": phone,
        "language": ChannelResolver.extract_language(data),
        "whatsapp_verified": whatsapp_verified,
    }


def course_context(data: dict[str, Any]) -> dict[str, Any]:
    """Normalise course event fields for message composition."""
    return {
        "subscription_id": _s(data.get("subscription_id") or data.get("id")),
        "subscription_number": _s(data.get("subscription_number")),
        "course_name": _s(data.get("course_name")) or _s((data.get("course_data") or {}).get("name")),
        "course_code": _s(data.get("course_code")),
        "service_name": _s(data.get("service_name")),
        "total_sessions": _s(data.get("total_sessions")),
        "used_sessions": _s(data.get("used_sessions")),
        "available_sessions": _s(data.get("available_sessions")),
        "session_number": _s(data.get("session_number")),
        "lost_sessions": _s(data.get("lost_sessions")),
        "price": _s(data.get("price")),
        "currency": _s(data.get("currency")) or "KWD",
        "payment_number": _s(data.get("payment_number")),
        "invoice_number": _s(data.get("invoice_number")),
        "credit_note_number": _s(data.get("credit_note_number")),
        "refund_id": _s(data.get("refund_id")),
        "payment_url": _s(data.get("payment_url")),
        "payment_option": _s(data.get("payment_option")).lower(),
        "booking_id": _s(data.get("booking_id")),
        "booking_number": _s(data.get("booking_number")),
        "reason": _s(data.get("reason")),
        "activated_date": fmt_date(data.get("activated_at")),
        "expires_date": fmt_date(data.get("expires_at")),
        "appointment_date": fmt_date(data.get("appointment_start")),
    }


async def _already_notified(ctx: HandlerContext, entity_id: str, template_key: str) -> bool:
    """Business-level dedup: was a WhatsApp/SMS for this entity + template already sent?"""
    if not entity_id or not getattr(ctx, "notification_repo", None):
        return False
    try:
        return bool(
            await ctx.notification_repo.has_booking_notification(
                booking_id=entity_id,
                channels=["whatsapp", "sms"],
                template_prefix=f"{TEMPLATE_PREFIX}/{template_key}",
            )
        )
    except Exception as exc:
        logger.warning(
            "course_notification_dedup_check_failed",
            entity_id=entity_id,
            template_key=template_key,
            error=str(exc),
        )
        return False


def _is_delivered(notification: Any) -> bool:
    status_val = getattr(notification, "status", None)
    if status_val in (
        NotificationStatus.FAILED,
        NotificationStatus.FAILED.value,
    ):
        return False
    return notification is not None


async def send_course_notification(
    *,
    envelope: EventEnvelope,
    ctx: HandlerContext,
    data: dict[str, Any],
    template_key: str,
    entity_id: str,
    whatsapp_en: str,
    whatsapp_ar: str,
    sms_en: str,
    sms_ar: str,
    extra_context: dict[str, Any] | None = None,
) -> str | None:
    """Send a course notification (WhatsApp if verified, SMS fallback).

    Args:
        template_key: e.g. ``"activated"``; templates are
            ``course_subscription/{template_key}_whatsapp`` / ``_sms``.
        entity_id: Stored in ``notifications.booking_id`` and used for the
            business-level dedup (subscription id, or the redemption booking id
            for per-session events).

    Returns:
        The channel used (``"whatsapp"`` / ``"sms"``), ``"duplicate"`` when the
        notification was already sent, or ``None`` when nothing could be sent.
    """
    if await _already_notified(ctx, entity_id, template_key):
        logger.info(
            "course_notification_already_sent",
            template_key=template_key,
            entity_id=entity_id,
            event_id=envelope.event_id_str,
        )
        return "duplicate"

    cust = customer_info(data)
    if not cust["phone"]:
        logger.info(
            "course_notification_skipped_no_phone",
            template_key=template_key,
            entity_id=entity_id,
        )
        return None

    service = NotificationService(ctx)
    lang = cust["language"]
    base_payload = {
        "phone_number": cust["phone"],
        "customer_name": cust["customer_name"],
        "language_preference": lang,
    }
    base_context = {
        **course_context(data),
        **(extra_context or {}),
        "customer_name": cust["customer_name"],
        "language": lang,
    }
    customer_id = cust["customer_id"] or None
    correlation_id = envelope.correlation_id_str

    # WhatsApp (only when verified)
    if cust["whatsapp_verified"]:
        wa_recipient = ChannelResolver.resolve_whatsapp_recipient(base_payload)
        if wa_recipient:
            body = whatsapp_en if wa_recipient.language == "en" else whatsapp_ar
            req = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=wa_recipient,
                template_name=f"{TEMPLATE_PREFIX}/{template_key}_whatsapp",
                template_context={**base_context, "message_body": body},
                customer_id=customer_id,
                booking_id=entity_id or None,
                correlation_id=correlation_id,
            )
            try:
                notification = await service.send(req)
                if _is_delivered(notification):
                    logger.info(
                        "course_notification_whatsapp_sent",
                        template_key=template_key,
                        entity_id=entity_id,
                    )
                    return "whatsapp"
            except Exception as exc:
                logger.warning(
                    "course_notification_whatsapp_failed",
                    template_key=template_key,
                    entity_id=entity_id,
                    error=str(exc),
                )

    # SMS fallback
    sms_recipient = ChannelResolver.resolve_sms_recipient(base_payload)
    if not sms_recipient:
        return None
    sms_recipient = replace(sms_recipient, language=lang)
    body = sms_en if sms_recipient.language == "en" else sms_ar
    req = NotificationRequest(
        event_id=envelope.event_id_str,
        recipient=sms_recipient,
        template_name=f"{TEMPLATE_PREFIX}/{template_key}_sms",
        template_context={**base_context, "message_body": body},
        customer_id=customer_id,
        booking_id=entity_id or None,
        correlation_id=correlation_id,
    )
    try:
        await service.send(req)
        logger.info("course_notification_sms_sent", template_key=template_key, entity_id=entity_id)
        return "sms"
    except Exception as exc:
        logger.warning(
            "course_notification_sms_failed",
            template_key=template_key,
            entity_id=entity_id,
            error=str(exc),
        )
        return None

