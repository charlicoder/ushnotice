"""
Handler for ``booking.updated_payment_status_to_success`` event.

Fired by ushbooknpay when a booking is paid manually at the desk
(payment_status=success, source="ushspa app", reason="Paid on desk").

Action:
  POST /uauth/api/v1/update-appointment-cache-payment-status/
  with {"booking_id": "<booking_id>", "payment_status": "success"}

This keeps the UshSpaAppointmentCache.payment_status in sync with the
actual booking payment state so the schedule view reflects correct data.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient

logger = get_logger(__name__)


class BookingPaymentStatusSuccessHandler:
    """Processes ``booking.updated_payment_status_to_success`` events.

    Updates the appointment cache payment_status in ushauth to 'success'
    so that the service-arrangement schedule grid reflects the desk payment.
    """

    event_type: str = "booking.updated_payment_status_to_success"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id: str = data.get("booking_id") or ""
        payment_status: str = data.get("payment_status") or "success"
        correlation_id: str = envelope.event_id_str or ""

        if not booking_id:
            logger.warning(
                "booking_payment_status_success_missing_booking_id",
                event_id=correlation_id,
            )
            return

        logger.info(
            "booking_payment_status_success_received",
            booking_id=booking_id,
            payment_status=payment_status,
            event_id=correlation_id,
        )

        # ── Update appointment cache payment_status in ushauth ─────────────
        ushauth_client = UshAuthClient()
        try:
            response = await ushauth_client.update_appointment_cache_payment_status(
                booking_id=booking_id,
                payment_status=payment_status,
                correlation_id=correlation_id,
            )
            logger.info(
                "appointment_cache_payment_status_updated",
                booking_id=booking_id,
                payment_status=payment_status,
                records_updated=response.get("records_updated", 0),
                event_id=correlation_id,
            )
        except Exception as exc:
            logger.error(
                "appointment_cache_payment_status_update_failed",
                booking_id=booking_id,
                payment_status=payment_status,
                error=str(exc),
                event_id=correlation_id,
            )
            raise
        finally:
            await ushauth_client.aclose()
