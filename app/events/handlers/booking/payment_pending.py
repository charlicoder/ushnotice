"""
Handler for booking-level `booking.payment_pending` event.

Steps:
  1. Create an appointment-cache record in ushauth (status=pending,
     payment_status=pending) so the slot is reserved while the customer
     completes payment.
  2. Send SMS / email payment-pending reminder to the customer.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.value_objects import NotificationRequest

logger = get_logger(__name__)


class BookingPaymentPendingHandler:
    """Processes booking payment pending events."""

    event_type: str = "booking.payment_pending"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id: str = str(data.get("booking_id") or "")
        customer_id: str = str(data.get("customer_id") or "")
        customer_name: str = str(data.get("customer_name") or "")
        booking_type: str = str(data.get("booking_type") or "branch")
        correlation_id: str = envelope.correlation_id_str

        # ── Resolve appointment date / time ──────────────────────────────────
        appt_start: str = data.get("appointment_start") or ""
        appt_date: str = (
            data.get("appointment_date")
            or (appt_start.split("T")[0] if "T" in appt_start else "")
        )
        appt_time_raw: str = (
            data.get("appointment_starttime")
            or data.get("appointment_time")
            or (appt_start.split("T")[1].rstrip("Z").split("+")[0] if "T" in appt_start else "")
        )
        # Ensure at least HH:MM:SS — pad with :00 if only HH:MM given
        appt_time: str = (
            appt_time_raw if len(appt_time_raw) >= 8
            else f"{appt_time_raw}:00" if len(appt_time_raw) == 5
            else appt_time_raw
        )
        duration: int = int(
            data.get("total_duration")
            or data.get("duration_minutes")
            or 60
        )

        # ── 1. Create appointment-cache in ushauth ───────────────────────────
        # ushauth BookingType only accepts: branch | home | leave
        _USHAUTH_VALID_BOOKING_TYPES = {"branch", "home", "leave"}
        _cache_booking_type: str = (
            booking_type if booking_type in _USHAUTH_VALID_BOOKING_TYPES else "branch"
        )

        if booking_id:
            try:
                ushauth_client = UshAuthClient()
                _cache_payload: dict = {
                    "service_arrangement_id": str(data.get("service_arrangement_id") or "") or None,
                    "therapist_id": str(data.get("therapist_id") or ""),
                    "booking_id": booking_id,
                    "appointment_date": appt_date,
                    "appointment_time": appt_time,
                    "booking_type": _cache_booking_type,
                    "duration": duration,
                    "status": "confirmed",
                    "customer_id": customer_id or None,
                    "customer_name": customer_name or None,
                    "branch_id": str(data.get("branch_id") or "") or None,
                    "service_id": str(data.get("service_id") or "") or None,
                    "payment_status": "pending",
                }
                logger.info(
                    "booking_payment_pending_appointment_cache_request",
                    booking_id=booking_id,
                    payload=_cache_payload,
                )
                _cache_response = await ushauth_client.create_appointment_cache(
                    service_arrangement_id=_cache_payload["service_arrangement_id"],
                    therapist_id=_cache_payload["therapist_id"],
                    booking_id=booking_id,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                    booking_type=_cache_booking_type,
                    duration=duration,
                    status="confirmed",
                    customer_id=customer_id or None,
                    customer_name=customer_name or None,
                    branch_id=str(data.get("branch_id") or "") or None,
                    service_id=str(data.get("service_id") or "") or None,
                    payment_status="pending",
                    correlation_id=correlation_id,
                )
                await ushauth_client.aclose()
                logger.info(
                    "booking_payment_pending_appointment_cache_created",
                    booking_id=booking_id,
                    customer_id=customer_id,
                    appointment_date=appt_date,
                    appointment_time=appt_time,
                    response=_cache_response,
                )
            except Exception as exc:
                # Non-blocking — notification flow must not fail due to cache errors
                logger.warning(
                    "booking_payment_pending_appointment_cache_failed",
                    booking_id=booking_id,
                    error=str(exc),
                )

        # ── 2. Send SMS / email payment-pending reminder ─────────────────────
        service = NotificationService(ctx)

        context = {
            "booking_reference": data.get("booking_reference") or data.get("reference") or "",
            "branch_name": data.get("branch_name") or "USHSPA",
            "appointment_date": data.get("appointment_date") or data.get("date") or "",
            "appointment_time": data.get("appointment_time") or data.get("time") or "",
        }

        sms_recipient = ChannelResolver.resolve_sms_recipient(data)
        if sms_recipient:
            req_sms = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=sms_recipient,
                template_name="booking/payment_pending",
                template_context={**context, "customer_name": sms_recipient.name},
                customer_id=str(data.get("customer_id") or "") or None,
                booking_id=str(data.get("booking_id") or "") or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_sms)

        email_recipient = ChannelResolver.resolve_email_recipient(data)
        if email_recipient:
            ref = context["booking_reference"]
            subject = f"Payment Reminder – {ref}" if ref else "Complete Your USHSPA Booking Payment"
            req_email = NotificationRequest(
                event_id=envelope.event_id_str,
                recipient=email_recipient,
                template_name="booking/payment_pending",
                template_context={**context, "customer_name": email_recipient.name},
                subject=subject,
                customer_id=str(data.get("customer_id") or "") or None,
                booking_id=str(data.get("booking_id") or "") or None,
                correlation_id=envelope.correlation_id_str,
            )
            await service.send(req_email)
