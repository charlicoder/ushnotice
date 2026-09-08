"""
Handler for `voucher.redeemed` event.

On a voucher.redeemed event this handler sends notifications to both
the SENDER and RECIPIENT about the redemption booking:
  1. WhatsApp/SMS — details of the booked appointment.
  2. Email         — full booking schedule with branch, service, and date/time.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


# ── Context builder ────────────────────────────────────────────────────────────

def _build_redeemed_context(data: dict) -> dict:
    """Extract all fields needed for voucher.redeemed notification templates."""
    sender_details: dict = data.get("sender_details") or {}
    recipient_details: dict = data.get("recipient_details") or {}
    service_data: dict = data.get("service_data") or {}
    branch_data: dict = data.get("branch_data") or {}
    booking_data: dict = data.get("redeemed_booking") or {}

    sender_name = sender_details.get("name") or data.get("sender_name") or "A generous friend"
    recipient_name = recipient_details.get("name") or data.get("recipient_name") or "Valued Customer"
    service_name = service_data.get("name") or data.get("service_name") or "Spa Experience"
    branch_name = branch_data.get("name") or data.get("branch_name") or "USHSPA"
    branch_location = (
        branch_data.get("location")
        or branch_data.get("address")
        or branch_data.get("city")
        or ""
    )
    branch_phone = branch_data.get("phone") or branch_data.get("phone_number") or ""

    # Appointment details — prefer booking_data, fall back to event root fields
    appointment_date = (
        booking_data.get("appointment_date")
        or data.get("appointment_date")
        or ""
    )
    appointment_time = (
        booking_data.get("appointment_starttime")
        or booking_data.get("appointment_time")
        or data.get("appointment_time")
        or ""
    )
    appointment_end_time = (
        booking_data.get("appointment_endtime")
        or booking_data.get("end_time")
        or data.get("appointment_end_time")
        or ""
    )
    booking_reference = (
        booking_data.get("booking_reference")
        or data.get("booking_reference")
        or str(booking_data.get("id") or data.get("booking_id") or "")
    )

    return {
        "voucher_id": str(data.get("id") or ""),
        "booking_id": str(booking_data.get("id") or data.get("booking_id") or ""),
        "booking_reference": booking_reference,
        "sender_name": sender_name,
        "sender_phone": sender_details.get("phone_number") or "",
        "recipient_name": recipient_name,
        "recipient_phone": recipient_details.get("phone_number") or data.get("recipient_phone") or "",
        "recipient_email": recipient_details.get("email") or data.get("recipient_email") or "",
        "service_name": service_name,
        "branch_name": branch_name,
        "branch_location": branch_location,
        "branch_phone": branch_phone,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "appointment_end_time": appointment_end_time,
        "total_duration": str(data.get("total_duration") or ""),
        "redeemed_at": str(data.get("redeemed_at") or ""),
    }


# ── Message composers ──────────────────────────────────────────────────────────

def _redeemed_whatsapp_message(ctx: dict, recipient_label: str, audience: str) -> str:
    """Compose a WhatsApp redemption message for sender or recipient."""
    time_range = (
        f"{ctx['appointment_time']}–{ctx['appointment_end_time']}"
        if ctx["appointment_end_time"]
        else ctx["appointment_time"]
    )
    if audience == "recipient":
        intro = f"Great news, {ctx['recipient_name']}! 🎉 Your USHSPA gift has been redeemed."
        detail_line = f"A booking has been scheduled using the gift from {ctx['sender_name']}."
    else:
        intro = f"Hi {ctx['sender_name']}! 🎉 Your gift to {ctx['recipient_name']} has been redeemed."
        detail_line = f"{ctx['recipient_name']} has scheduled a booking using your gift!"

    lines = [
        "✅ *Gift Redeemed* — USHSPA",
        "",
        intro,
        "",
        detail_line,
        "",
        f"🧴 *Service:* {ctx['service_name']}",
        f"📍 *Branch:* {ctx['branch_name']}",
    ]
    if ctx["branch_location"]:
        lines.append(f"   {ctx['branch_location']}")
    if ctx["appointment_date"]:
        lines.append(f"📅 *Date:* {ctx['appointment_date']}")
    if time_range:
        lines.append(f"🕐 *Time:* {time_range}")
    if ctx["booking_reference"]:
        lines.append(f"🔖 *Ref:* {ctx['booking_reference']}")
    lines += [
        "",
        "Enjoy your spa experience!",
        "— The USHSPA Team",
    ]
    return "\n".join(lines)


def _redeemed_sms_message(ctx: dict, audience: str) -> str:
    service = ctx["service_name"]
    date = ctx["appointment_date"]
    time = ctx["appointment_time"]
    ref_part = f" Ref:{ctx['booking_reference']}" if ctx["booking_reference"] else ""
    if audience == "recipient":
        name_first = (ctx["recipient_name"] or "").split()[0] or "Customer"
        return f"USHSPA: Hi {name_first}, your gift booking for {service} on {date} at {time} is confirmed.{ref_part} Enjoy!"
    else:
        name_first = (ctx["sender_name"] or "").split()[0] or "Customer"
        recipient_first = (ctx["recipient_name"] or "").split()[0] or "Recipient"
        return (
            f"USHSPA: Hi {name_first}, your gift to {recipient_first} was redeemed — "
            f"{service} on {date} at {time}.{ref_part}"
        )


def _redeemed_email_subject(ctx: dict, audience: str) -> str:
    service = ctx["service_name"] or "USHSPA Gift"
    if audience == "recipient":
        return f"✅ Your USHSPA Gift Booking is Confirmed — {service}"
    else:
        return f"🎁 {ctx['recipient_name']} Redeemed Your USHSPA Gift — {service}"


# ── Handler ────────────────────────────────────────────────────────────────────

class VoucherRedeemedHandler:
    """
    Processes voucher.redeemed events.

    Sends booking schedule notifications to:
      - RECIPIENT: appointment confirmed with date, time, branch, and service.
      - SENDER:    gift has been redeemed by recipient.
    """

    event_type: str = "voucher.redeemed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        service = NotificationService(ctx)
        correlation_id = envelope.correlation_id_str

        voucher_id = str(data.get("id") or "")
        vctx = _build_redeemed_context(data)

        logger.info(
            "voucher_redeemed_notification_start",
            voucher_id=voucher_id,
            recipient_phone=vctx["recipient_phone"],
            sender_phone=vctx["sender_phone"],
        )

        # ── Notify RECIPIENT ─────────────────────────────────────────────────
        await self._notify_person(
            envelope=envelope,
            service=service,
            vctx=vctx,
            phone=vctx["recipient_phone"],
            email=vctx["recipient_email"],
            name=vctx["recipient_name"],
            audience="recipient",
            voucher_id=voucher_id,
            correlation_id=correlation_id,
        )

        # ── Notify SENDER ────────────────────────────────────────────────────
        sender_details: dict = data.get("sender_details") or {}
        sender_phone = sender_details.get("phone_number") or data.get("sender_phone") or ""
        sender_email = sender_details.get("email") or data.get("sender_email") or ""

        await self._notify_person(
            envelope=envelope,
            service=service,
            vctx=vctx,
            phone=sender_phone,
            email=sender_email,
            name=vctx["sender_name"],
            audience="sender",
            voucher_id=voucher_id,
            correlation_id=correlation_id,
        )

    async def _notify_person(
        self,
        *,
        envelope: EventEnvelope,
        service: NotificationService,
        vctx: dict,
        phone: str,
        email: str,
        name: str,
        audience: str,  # "sender" | "recipient"
        voucher_id: str,
        correlation_id: str | None,
    ) -> None:
        """Send WhatsApp/SMS + Email to a specific person (sender or recipient)."""
        tpl_prefix = f"voucher/redeemed_{audience}"

        # WhatsApp
        if phone:
            try:
                wa_body = _redeemed_whatsapp_message(vctx, name, audience)
                wa_payload = {"phone_number": phone, "customer_name": name}
                wa_recipient = ChannelResolver.resolve_whatsapp_recipient(wa_payload)
                if wa_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=wa_recipient,
                        template_name=f"{tpl_prefix}_whatsapp",
                        template_context={**vctx, "message_body": wa_body, "audience": audience},
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info(f"voucher_redeemed_{audience}_whatsapp_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning(f"voucher_redeemed_{audience}_whatsapp_failed", voucher_id=voucher_id, error=str(exc))

            # SMS
            try:
                sms_body = _redeemed_sms_message(vctx, audience)
                sms_payload = {"phone_number": phone, "customer_name": name}
                sms_recipient = ChannelResolver.resolve_sms_recipient(sms_payload)
                if sms_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=sms_recipient,
                        template_name=f"{tpl_prefix}_sms",
                        template_context={**vctx, "message_body": sms_body, "audience": audience},
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info(f"voucher_redeemed_{audience}_sms_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning(f"voucher_redeemed_{audience}_sms_failed", voucher_id=voucher_id, error=str(exc))

        # Email
        if email:
            try:
                email_payload = {"email": email, "customer_name": name}
                email_recipient = ChannelResolver.resolve_email_recipient(email_payload)
                if email_recipient:
                    req = NotificationRequest(
                        event_id=envelope.event_id_str,
                        recipient=email_recipient,
                        template_name=f"{tpl_prefix}_email",
                        template_context={**vctx, "customer_name": name, "audience": audience},
                        subject=_redeemed_email_subject(vctx, audience),
                        customer_id=None,
                        booking_id=voucher_id or None,
                        correlation_id=correlation_id,
                    )
                    await service.send(req)
                    logger.info(f"voucher_redeemed_{audience}_email_sent", voucher_id=voucher_id)
            except Exception as exc:
                logger.warning(f"voucher_redeemed_{audience}_email_failed", voucher_id=voucher_id, error=str(exc))
        else:
            logger.info(f"voucher_redeemed_{audience}_email_skipped_no_email", voucher_id=voucher_id)
