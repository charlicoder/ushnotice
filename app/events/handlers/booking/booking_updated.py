"""
Handler for ``booking.updated`` and ``booking.rescheduled`` events.

Fired by ushbooknpay when a booking is updated or rescheduled.

Action:
  POST /api/v1/update-appointment-cache-by-booking-id/
  with {
      "booking_id": "<booking_id>",
      "appointment_date": "<YYYY-MM-DD>",
      "appointment_time": "<HH:MM:SS>",
      "duration": <int>,
      "therapist_id": "<therapist_id>",
      "status": "<status>",
      "payment_status": "<payment_status>"
  }
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient

logger = get_logger(__name__)


class BookingUpdatedHandler:
    """Processes ``booking.updated`` events to update appointment cache in ushauth."""

    event_type: str = "booking.updated"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id: str = str(data.get("booking_id") or "")
        correlation_id: str = envelope.event_id_str or ""

        if not booking_id:
            logger.warning("booking_updated_missing_booking_id", event_id=correlation_id)
            return

        appointment_date: str = str(data.get("appointment_date") or "")
        appointment_time: str = str(data.get("appointment_time") or "")
        duration: int = int(data.get("duration") or 0)
        therapist_id: str = str(data.get("therapist_id") or "")
        booking_status: str = str(data.get("status") or "")
        payment_status: str = str(data.get("payment_status") or "")
        branch_id: str | None = data.get("branch_id")
        service_arrangement_id: str | None = data.get("service_arrangement_id")

        logger.info(
            "booking_updated_event_received",
            booking_id=booking_id,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            event_id=correlation_id,
        )

        ushauth_client = UshAuthClient()
        try:
            response = await ushauth_client.update_appointment_cache_by_booking_id(
                booking_id=booking_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                duration=duration,
                therapist_id=therapist_id,
                status=booking_status,
                payment_status=payment_status,
                branch_id=branch_id,
                service_arrangement_id=service_arrangement_id,
                correlation_id=correlation_id,
            )
            logger.info(
                "appointment_cache_updated_on_booking_updated_event",
                booking_id=booking_id,
                records_updated=response.get("records_updated", 0),
                event_id=correlation_id,
            )
        except Exception as exc:
            logger.error(
                "appointment_cache_update_failed_on_booking_updated_event",
                booking_id=booking_id,
                error=str(exc),
                event_id=correlation_id,
            )
            raise
        finally:
            await ushauth_client.aclose()
