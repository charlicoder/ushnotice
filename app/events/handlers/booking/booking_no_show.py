"""
Handler for `booking.no_show` event.

A no-show releases the therapist / room slot, so the appointment cache record(s) in ushauth are
deleted. ushbooknpay applies the same rule when checking for double bookings
(see ``NON_BLOCKING_BOOKING_STATUSES``), which keeps the Therapist / Room Schedule pages and
booking creation in agreement.

Previously ``booking.no_show`` was not handled at all, so its cache row stayed ``confirmed``
and the slot looked booked on the schedule although ushbooknpay no longer blocked it.

Completed bookings are NOT handled here: they keep their cache row (updated to ``completed``
by ``BookingUpdatedHandler``) so they stay visible on the schedule and keep blocking the slot.
"""

from __future__ import annotations

from app.core.exceptions import ServiceClientError
from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient

logger = get_logger(__name__)


class BookingNoShowHandler:
    """Processes booking.no_show events: delete the appointment cache."""

    event_type: str = "booking.no_show"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id: str = str(data.get("booking_id") or "")
        correlation_id: str = envelope.correlation_id_str

        if not booking_id:
            logger.warning("booking_no_show_missing_booking_id", correlation_id=correlation_id)
            return

        ushauth_client = UshAuthClient()
        try:
            response = await ushauth_client.delete_appointment_cache_by_booking_id(
                booking_id=booking_id,
                correlation_id=correlation_id,
            )
            logger.info(
                "appointment_cache_deleted_on_booking_no_show",
                booking_id=booking_id,
                deleted_count=response.get("deleted_count", 0) if isinstance(response, dict) else 0,
                correlation_id=correlation_id,
            )
        except ServiceClientError as exc:
            if exc.status_code == 404:
                # Already gone — the handler is idempotent.
                logger.warning(
                    "appointment_cache_not_found_on_booking_no_show",
                    booking_id=booking_id,
                    correlation_id=correlation_id,
                )
                return
            logger.error(
                "appointment_cache_delete_failed_on_booking_no_show",
                booking_id=booking_id,
                error=str(exc),
                correlation_id=correlation_id,
            )
            raise
        except Exception as exc:
            logger.error(
                "appointment_cache_delete_failed_on_booking_no_show",
                booking_id=booking_id,
                error=str(exc),
                correlation_id=correlation_id,
            )
            raise
        finally:
            await ushauth_client.aclose()

