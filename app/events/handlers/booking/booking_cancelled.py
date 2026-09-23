"""
Handler for `booking.cancelled` event.

On a cancelled booking this handler:
  1. Reverse loyalty points if the booking was eligible for loyalty rewards.
     - Reads `is_eligible_for_loyalty`, `loyalty_points`, `arrangement_loyalty_points`
       from the event payload.
     - Calls POST /api/v1/loyalty/internal/cancel/ on ushbooknpay.
     - This is non-blocking — cancellation notifications still fire even if the
       loyalty reversal fails.

Additional handlers (notifications, refund tracking, etc.) can be added as
numbered steps below step 1 as the feature set grows.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushbooknpay_client import UshBookNPayClient

logger = get_logger(__name__)


class BookingCancelledHandler:
    """
    Processes booking.cancelled events.

    Responsibilities (in order):
      1. Reverse loyalty points credited to the customer for the cancelled booking.
    """

    event_type: str = "booking.cancelled"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        booking_id: str = str(data.get("booking_id") or "")
        booking_number: str = str(data.get("booking_number") or "")
        customer_id: str = str(data.get("customer_id") or "")
        correlation_id: str = envelope.correlation_id_str

        # ── 1. Reverse loyalty points (if eligible) ───────────────────────────
        booking_type: str = str(data.get("booking_type") or "")
        payment_type: str = str(data.get("payment_type") or "")
        _is_loyalty_redemption = (
            booking_type == "loyalty"
            or payment_type == "rewarded"
            or str(data.get("payment_status", "")).lower() == "rewarded"
            or bool(data.get("reward_id"))
            or bool(data.get("loyalty_data", {}).get("points_cost"))
            or bool(data.get("loyalty_data", {}).get("reward_id"))
        )
        is_eligible_for_loyalty: bool = bool(data.get("is_eligible_for_loyalty"))
        if _is_loyalty_redemption:
            logger.info(
                "booking_cancelled_loyalty_reversal_skipped_loyalty_redemption",
                booking_id=booking_id,
                customer_id=customer_id,
            )
        elif is_eligible_for_loyalty and booking_id and customer_id:
            loyalty_points: int = int(data.get("loyalty_points") or 0)
            arr_loyalty_points = data.get("arrangement_loyalty_points")  # None or int

            # Compute effective points that were originally credited
            effective_points = (
                int(arr_loyalty_points)
                if (arr_loyalty_points is not None and int(arr_loyalty_points) > 0)
                else loyalty_points
            )

            if effective_points > 0:
                try:
                    client = UshBookNPayClient()
                    await client.cancel_loyalty_points(
                        customer_id=customer_id,
                        points=effective_points,
                        booking_id=booking_id,
                        booking_number=booking_number,
                        correlation_id=correlation_id,
                    )
                    await client.aclose()
                    logger.info(
                        "booking_cancelled_loyalty_reversed",
                        booking_id=booking_id,
                        customer_id=customer_id,
                        points_reversed=effective_points,
                    )
                except Exception as exc:
                    # Non-blocking — other cancellation logic must continue
                    logger.warning(
                        "booking_cancelled_loyalty_reversal_failed",
                        booking_id=booking_id,
                        customer_id=customer_id,
                        error=str(exc),
                    )
            else:
                logger.info(
                    "booking_cancelled_loyalty_skipped_zero_points",
                    booking_id=booking_id,
                    loyalty_points=loyalty_points,
                    arrangement_loyalty_points=arr_loyalty_points,
                )
        else:
            logger.debug(
                "booking_cancelled_loyalty_skipped",
                booking_id=booking_id,
                is_eligible_for_loyalty=is_eligible_for_loyalty,
                has_customer=bool(customer_id),
            )
