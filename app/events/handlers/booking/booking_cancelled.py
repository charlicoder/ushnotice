"""
Handler for `booking.cancelled` event.

On a cancelled booking this handler:
  1. Reverse loyalty points if the booking was eligible for loyalty rewards.
     - Reads `is_eligible_for_loyalty`, `loyalty_points`, `arrangement_loyalty_points`
       from the event payload.
     - Calls POST /api/v1/loyalty/internal/cancel/ on ushbooknpay.
     - This is non-blocking — cancellation notifications still fire even if the
       loyalty reversal fails.
  2. Delete appointment cache in ushauth.
  3. For paid bookings (refund_required=true):
     - Create a credit note in ushanr WITHOUT recording the refund payment
       (record_refund_payment=false). The outbound refund payment is posted
       later when booking.refund_completed fires.
     - Link the credit note back to the Refund record in ushbooknpay.
     - Do NOT change booking.payment_status — it stays "success" until the
       refund is completed by staff from ushdesk.
  4. For unpaid bookings with payment link: cancel the payment link.
  5. Sync booking status in ushbooknpay (always sends status=cancelled).
"""

from __future__ import annotations

from app.core.exceptions import ServiceClientError
from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.invoicing.triggers import trigger_credit_note

logger = get_logger(__name__)


class BookingCancelledHandler:
    """
    Processes booking.cancelled events.

    Responsibilities (in order):
      1. Reverse loyalty points credited to the customer for the cancelled booking.
      2. Delete appointment cache in ushauth.
      3. For paid bookings: create credit note (no cash movement) and link to refund.
      4. For unpaid bookings with payment link: cancel payment link.
      5. Sync booking status to cancelled in ushbooknpay.
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

        # ── 2. Determine booking payment context ─────────────────────────────
        if not booking_id:
            return

        old_status = str(data.get("old_status") or data.get("previous_status") or "").lower().strip()
        old_pay_status = str(data.get("old_payment_status") or data.get("previous_payment_status") or "").lower().strip()
        raw_payment_status = str(data.get("payment_status") or "").lower().strip()
        payment_url = (
            data.get("payment_url")
            or (data.get("payment_data") or {}).get("payment_url")
            or (data.get("payments_data") or {}).get("payment_url")
            or data.get("invoice_url")
        )
        has_payment_link = bool(
            payment_url
            and str(payment_url).strip()
            and str(payment_url).strip().lower() not in ("none", "null")
        )
        refund_required = bool(data.get("refund_required"))
        refund_status = str(data.get("refund_status") or "").lower().strip()

        is_paid_booking = (
            refund_required
            or bool(data.get("refund_issued"))
            or raw_payment_status in ("success", "paid")
            or old_pay_status in ("success", "paid")
        )
        is_pending_payment = (
            old_pay_status in ("pending", "unpaid")
            or raw_payment_status in ("pending", "unpaid", "cancelled", "payment_pending")
        ) and not is_paid_booking

        if not has_payment_link and is_pending_payment and booking_id:
            try:
                b_client = UshBookNPayClient()
                b_resp = await b_client.get_booking(booking_id, correlation_id=correlation_id)
                await b_client.aclose()
                b_data = b_resp.get("data") if isinstance(b_resp, dict) and "data" in b_resp else b_resp
                if isinstance(b_data, dict):
                    fetched_url = (
                        b_data.get("payment_url")
                        or (b_data.get("payment_data") or {}).get("payment_url")
                        or b_data.get("invoice_url")
                    )
                    if fetched_url and str(fetched_url).strip() and str(fetched_url).strip().lower() not in ("none", "null"):
                        payment_url = fetched_url
                        has_payment_link = True
            except Exception as exc:
                logger.debug("booking_cancelled_check_payment_url_failed", booking_id=booking_id, error=str(exc))

        # ── 3. Delete appointment cache (always) ─────────────────────────────
        ushauth_client = UshAuthClient()
        try:
            try:
                del_resp = await ushauth_client.delete_appointment_cache_by_booking_id(
                    booking_id=booking_id,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "appointment_cache_deleted_on_booking_cancellation",
                    booking_id=booking_id,
                    deleted_count=del_resp.get("deleted_count", 0) if isinstance(del_resp, dict) else 0,
                    correlation_id=correlation_id,
                )
            except Exception:
                # Fallback to status update if delete endpoint not available
                await ushauth_client.update_appointment_cache_status_by_booking_id(
                    booking_id=booking_id,
                    new_status="cancelled",
                    payment_status="cancelled" if not is_paid_booking else raw_payment_status or "success",
                    correlation_id=correlation_id,
                )
                logger.info(
                    "appointment_cache_status_updated_on_cancellation_fallback",
                    booking_id=booking_id,
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
                    "appointment_cache_delete_failed_on_cancellation",
                    booking_id=booking_id,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
        except Exception as exc:
            logger.error(
                "appointment_cache_delete_failed_on_cancellation",
                booking_id=booking_id,
                error=str(exc),
                correlation_id=correlation_id,
            )
        finally:
            await ushauth_client.aclose()

        # ── 4. Cancel payment link (unpaid bookings with payment link) ────────
        if is_pending_payment and has_payment_link:
            booknpay_client = UshBookNPayClient()
            try:
                await booknpay_client.cancel_payment_link(
                    booking_id=booking_id,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "booking_cancelled_payment_link_cancelled",
                    booking_id=booking_id,
                    payment_url=payment_url,
                    correlation_id=correlation_id,
                )
            except Exception as exc:
                logger.warning(
                    "booking_cancelled_payment_link_cancel_failed",
                    booking_id=booking_id,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
            finally:
                await booknpay_client.aclose()

        # ── 5. Sync booking status in ushbooknpay ─────────────────────────────
        # For PAID bookings: do NOT override payment_status.
        # If refund was requested: payment_status is "refund_requested".
        # If no refund requested: payment_status stays "success".
        # For UNPAID bookings: set payment_status to "cancelled".
        if raw_payment_status == "refund_requested":
            sync_payment_status = "refund_requested"
        elif is_paid_booking:
            sync_payment_status = raw_payment_status or "success"
        elif is_pending_payment:
            sync_payment_status = "cancelled"
        elif raw_payment_status:
            sync_payment_status = raw_payment_status
        else:
            sync_payment_status = "cancelled"

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
                payment_status=sync_payment_status,
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
                payment_status=sync_payment_status,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.error(
                "booking_payment_status_update_failed_on_cancellation",
                booking_id=booking_id,
                payment_status=sync_payment_status,
                error=str(exc),
                correlation_id=correlation_id,
            )
        finally:
            await booknpay_client.aclose()

        # ── 6. Create refund request if refund was requested ─────────────────
        is_refund_requested = (
            raw_payment_status == "refund_requested"
            or refund_required
            or bool(data.get("refund_requested"))
        )
        if is_refund_requested and booking_id:
            canc_fee = data.get("cancellation_fee") or 0
            refund_amt = data.get("refund_amount") or data.get("refundable_amount")
            invoice_num = data.get("invoice_number")
            invoice_id_val = data.get("invoice_id")
            payment_id_val = data.get("payment_id")
            reason_str = str(data.get("cancellation_reason") or data.get("reason") or "Booking cancellation refund requested")
            processed_by = data.get("change_by_user") or data.get("cancelled_by")

            booknpay_client = UshBookNPayClient()
            try:
                await booknpay_client.create_refund_request(
                    booking_id=booking_id,
                    payment_id=str(payment_id_val) if payment_id_val else None,
                    invoice_id=str(invoice_id_val) if invoice_id_val else None,
                    invoice_number=str(invoice_num) if invoice_num else None,
                    refund_amount=refund_amt,
                    cancellation_fee=canc_fee,
                    reason=reason_str,
                    change_by_user=str(processed_by) if processed_by else None,
                    change_by_user_data=change_by_user_data if isinstance(change_by_user_data, dict) else None,
                    correlation_id=correlation_id,
                )
                logger.info(
                    "refund_request_created_on_booking_cancellation",
                    booking_id=booking_id,
                    correlation_id=correlation_id,
                )
            except Exception as exc:
                logger.error(
                    "refund_request_creation_failed_on_cancellation",
                    booking_id=booking_id,
                    error=str(exc),
                    correlation_id=correlation_id,
                )
            finally:
                await booknpay_client.aclose()

