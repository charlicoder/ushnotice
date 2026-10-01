"""
Handler for `booking.cancelled` event.

On a cancelled booking this handler:
  1. Reverse loyalty points if the booking was eligible for loyalty rewards.
     - Reads `is_eligible_for_loyalty`, `loyalty_points`, `arrangement_loyalty_points`
       from the event payload.
     - Calls POST /api/v1/loyalty/internal/cancel/ on ushbooknpay.
     - This is non-blocking — cancellation notifications still fire even if the
       loyalty reversal fails.
  2. Update appointment cache in ushauth via:
     POST /uauth/api/v1/update-appointment-cache-status-by-booking-id/
     with {"booking_id": "<booking_id>", "new_status": "cancelled", "payment_status": "refunded"}
  3. Update booking payment status in ushbooknpay via:
     PATCH /booknpay/api/v1/bookings/<booking_id>/status/
     with {"status": "cancelled", "payment_status": "refunded", ...}
"""

from __future__ import annotations

from app.core.exceptions import ServiceClientError
from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient

logger = get_logger(__name__)


class BookingCancelledHandler:
    """
    Processes booking.cancelled events.

    Responsibilities (in order):
      1. Reverse loyalty points credited to the customer for the cancelled booking.
      2. Update appointment cache in ushauth to status="cancelled" and payment_status="refunded".
      3. Update booking payment status in ushbooknpay to payment_status="refunded".
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

        # ── 2. Update appointment cache in ushauth ───────────────────────────
        if booking_id:
            raw_payment_status = str(data.get("payment_status") or "").lower()
            if data.get("refund_issued") or raw_payment_status in ("success", "paid", "refunded"):
                new_payment_status = "refunded"
            elif raw_payment_status in ("pending", "unpaid", "not_initiated", "cancelled", "payment_pending"):
                new_payment_status = "cancelled"
            elif raw_payment_status:
                new_payment_status = raw_payment_status
            else:
                new_payment_status = "cancelled"

            ushauth_client = UshAuthClient()
            try:
                resp = await ushauth_client.update_appointment_cache_status_by_booking_id(
                    booking_id=booking_id,
                    new_status="cancelled",
                    payment_status=new_payment_status,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "appointment_cache_status_updated_on_cancellation",
                    booking_id=booking_id,
                    new_status="cancelled",
                    payment_status=new_payment_status,
                    records_updated=resp.get("records_updated", 0) if isinstance(resp, dict) else 0,
                    correlation_id=correlation_id,
                )
            except ServiceClientError as exc:
                if exc.status_code == 404:
                    logger.warning(
                        "appointment_cache_not_found_on_cancellation",
                        booking_id=booking_id,
                        correlation_id=correlation_id,
                    )
                else:
                    logger.error(
                        "appointment_cache_update_failed_on_cancellation",
                        booking_id=booking_id,
                        error=str(exc),
                        correlation_id=correlation_id,
                    )
                    raise
            except Exception as exc:
                logger.error(
                    "appointment_cache_update_failed_on_cancellation",
                    booking_id=booking_id,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
                raise
            finally:
                await ushauth_client.aclose()

        # ── 3. Update booking payment status in ushbooknpay ───────────────────
        if booking_id:
            change_by_user = (
                data.get("change_by_user")
                or data.get("changed_by")
                or data.get("cancelled_by")
                or "ushnotice"
            )
            change_by_user_data = (
                data.get("change_by_user_data")
                or ({"source": "ushnotice", "reason": str(data.get("cancellation_reason") or data.get("reason") or "Booking cancelled")})
            )
            booknpay_client = UshBookNPayClient()
            try:
                await booknpay_client.update_booking_status(
                    booking_id=booking_id,
                    status="cancelled",
                    payment_status=new_payment_status,
                    reason=str(data.get("cancellation_reason") or data.get("reason") or "Booking cancelled"),
                    source="ushnotice",
                    change_by_user=str(change_by_user),
                    change_by_user_data=change_by_user_data,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "booking_payment_status_updated_on_cancellation",
                    booking_id=booking_id,
                    status="cancelled",
                    payment_status=new_payment_status,
                    correlation_id=correlation_id,
                )
            except Exception as exc:
                logger.error(
                    "booking_payment_status_update_failed_on_cancellation",
                    booking_id=booking_id,
                    payment_status=new_payment_status,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
            finally:
                await booknpay_client.aclose()
