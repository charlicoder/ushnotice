"""
Handler for `booking.completed`: send the customer their feedback link.

Runs next to ``BookingUpdatedHandler`` (which only refreshes the appointment cache):

  1. Create (or fetch) the booking's feedback link in ushanr — one link per booking.
  2. Skip when the customer already submitted feedback or has no phone number.
  3. Send the link by WhatsApp; only if that fails, send it by SMS.

A failed link creation is re-raised so the event is retried. Sending failures are logged and
never retried, so the customer can't receive duplicate messages.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushanr_client import UshanrClient
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _uuid_or_none(value: Any) -> str | None:
    s = str(value or "").strip()
    return s if _UUID_RE.match(s) else None


def _str_or_none(value: Any) -> str | None:
    s = str(value or "").strip()
    return s or None


def _language(data: dict[str, Any]) -> str:
    customer = data.get("customer_data") if isinstance(data.get("customer_data"), dict) else {}
    raw = str(
        data.get("customer_language")
        or data.get("language_preference")
        or customer.get("language_preference")
        or customer.get("language")
        or "ar"
    )
    return "en" if raw.strip().lower().startswith("en") else "ar"


def feedback_message(url: str, customer_name: str | None, lang: str) -> str:
    """WhatsApp / SMS text (same wording as the ushdesk share text)."""
    first = (customer_name or "").strip().split(" ")[0]
    if lang == "en":
        return f"USHSPA: Hello{' ' + first if first else ''}, thank you for visiting us. We'd love to hear your feedback: {url}"
    return f"USHSPA: مرحباً{' ' + first if first else ''}، شكراً لزيارتكم. يسعدنا معرفة رأيكم عبر هذا الرابط: {url}"


def build_link_request(data: dict[str, Any], lang: str) -> dict[str, Any]:
    first_service = (data.get("services") or [{}])[0] if isinstance(data.get("services"), list) else {}
    return {
        "booking_id": str(data.get("booking_id") or ""),
        "booking_number": _str_or_none(data.get("booking_number")),
        "company_id": _uuid_or_none(data.get("company_id")),
        "customer_id": _uuid_or_none(data.get("customer_id")),
        "customer_name": _str_or_none(data.get("customer_name")),
        "customer_phone": _str_or_none(data.get("customer_phone")),
        "service_id": _uuid_or_none(data.get("service_id") or (first_service or {}).get("service_id")),
        "service_name": _str_or_none(data.get("service_name") or (first_service or {}).get("service_name")),
        "services": data.get("services") if isinstance(data.get("services"), list) else None,
        "therapist_id": _uuid_or_none(data.get("therapist_id")),
        "therapist_name": _str_or_none(data.get("therapist_name")),
        "secondary_therapist_id": _uuid_or_none(data.get("secondary_therapist_id")),
        "secondary_therapist_name": _str_or_none(data.get("secondary_therapist_name")),
        "branch_id": _uuid_or_none(data.get("branch_id")),
        "branch_name": _str_or_none(data.get("branch_name")),
        "appointment_start": _str_or_none(data.get("appointment_start")),
        "language": lang,
    }


class BookingCompletedFeedbackHandler:
    """Processes booking.completed events: create the feedback link and send it to the customer."""

    event_type: str = "booking.completed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id = str(data.get("booking_id") or "")
        correlation_id = envelope.correlation_id_str
        customer_id = str(data.get("customer_id") or "")

        if not _uuid_or_none(booking_id):
            logger.warning("booking_completed_feedback_missing_booking_id", correlation_id=correlation_id)
            return
        status = str(data.get("status") or "completed").strip().lower()
        if status not in ("", "completed"):
            logger.info("booking_completed_feedback_skipped_status", booking_id=booking_id, status=status)
            return

        wa_recipient = ChannelResolver.resolve_whatsapp_recipient(data)
        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if wa_recipient is None and sms_recipient is None:
            logger.info("booking_completed_feedback_skipped_no_phone", booking_id=booking_id)
            return

        lang = _language(data)
        client = UshanrClient()
        try:
            link = await client.create_feedback_link(build_link_request(data, lang))
        finally:
            await client.aclose()

        url = str(link.get("feedback_url") or "")
        if not url:
            raise RuntimeError(f"ushanr returned no feedback_url for booking {booking_id}")
        if str(link.get("status") or "").lower() == "submitted":
            logger.info("booking_completed_feedback_skipped_already_submitted", booking_id=booking_id)
            return

        service = NotificationService(ctx)
        customer_name = str(data.get("customer_name") or "")
        body = feedback_message(url, customer_name, lang)
        base_ctx = {"customer_name": customer_name, "message_body": body, "feedback_url": url, "language": lang}

        # 1. WhatsApp
        whatsapp_sent = False
        if wa_recipient is not None:
            try:
                await service.send(
                    NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=wa_recipient,
                        template_name="booking/feedback_whatsapp",
                        template_context=base_ctx,
                        customer_id=customer_id or None,
                        booking_id=booking_id,
                        correlation_id=correlation_id,
                    )
                )
                whatsapp_sent = True
                logger.info("booking_feedback_whatsapp_sent", booking_id=booking_id)
            except Exception as exc:
                logger.warning("booking_feedback_whatsapp_failed", booking_id=booking_id, error=str(exc))

        # 2. SMS — only when WhatsApp was not sent
        if not whatsapp_sent:
            if sms_recipient is None:
                logger.info("booking_feedback_sms_skipped_no_recipient", booking_id=booking_id)
                return
            try:
                await service.send(
                    NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=sms_recipient,
                        template_name="booking/feedback_sms",
                        template_context=base_ctx,
                        customer_id=customer_id or None,
                        booking_id=booking_id,
                        correlation_id=correlation_id,
                    )
                )
                logger.info("booking_feedback_sms_sent", booking_id=booking_id)
            except Exception as exc:
                logger.warning("booking_feedback_sms_failed", booking_id=booking_id, error=str(exc))
