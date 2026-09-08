"""
Handler for `loyalty.redeedmed` event.

Processes events published when a customer redeems a loyalty reward.

On receipt this handler:
  1. Updates the linked booking in ushbooknpay:
       status         = "confirmed"
       payment_status = "rewarded"
       reward_id      = reward.id  (from event)
       loyalty_data   = full reward dict from event

  2. Creates an appointment-cache record in ushauth using the
     booking details returned from step 1, so the calendar and
     therapist schedule stay consistent.

Both API calls are best-effort — failures are logged but never
prevent the event from being acknowledged.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import EventHandler, HandlerContext
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient

logger = get_logger(__name__)


class LoyaltyRedeemedHandler:
    """Processes loyalty.redeedmed events.

    Responsibilities (in order):
      1. Update booking status/payment_status/reward_id/loyalty_data in ushbooknpay.
      2. Create appointment-cache record in ushauth from booking response.
    """

    event_type: str = "loyalty.redeedmed"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        correlation_id = envelope.correlation_id_str

        # ── Extract reward data ───────────────────────────────────────────────
        # The envelope merges root-level fields into data (see EventEnvelope.model_validate).
        # ``reward`` is the full reward dict; individual fields are also at the root level.
        reward: dict[str, Any] = data.get("reward") or {}

        # Prefer the nested reward dict for clean snapshot, fall back to root fields
        reward_id: str = str(
            reward.get("id")
            or data.get("reward_id")
            or data.get("id")
            or ""
        )
        customer_id: str = str(
            reward.get("customer_id")
            or data.get("customer_id")
            or ""
        )
        # The booking that the reward is being redeemed against
        booking_id: str = str(
            reward.get("redeemed_in_booking_id")
            or data.get("redeemed_in_booking_id")
            or ""
        )
        service_arrangement_id: str = str(
            reward.get("service_arrangement_id")
            or data.get("service_arrangement_id")
            or ""
        )
        service_id: str = str(
            reward.get("service_id")
            or data.get("service_id")
            or ""
        )
        therapist_id: str = str(
            reward.get("therapist_id")
            or data.get("therapist_id")
            or ""
        )
        appointment_date: str = str(
            reward.get("appointment_date")
            or data.get("appointment_date")
            or ""
        )
        appointment_time: str = str(
            reward.get("appointment_time")
            or data.get("appointment_time")
            or ""
        )
        try:
            duration: int = int(
                reward.get("duration")
                or data.get("duration")
                or 60
            )
        except (ValueError, TypeError):
            duration = 60

        # loyalty_data snapshot = the full reward dict
        loyalty_data_payload: dict[str, Any] = reward if reward else {
            k: v for k, v in data.items()
            if k not in {"reward", "event_id", "event_type", "event_name",
                         "occurred_at", "source", "correlation_id", "causation_id"}
        }

        if not booking_id:
            logger.warning(
                "loyalty_redeemed_no_booking_id",
                reward_id=reward_id,
                customer_id=customer_id,
                event_id=envelope.event_id_str,
            )
            return

        # ── 1. Update booking in ushbooknpay ─────────────────────────────────
        booking_response: dict[str, Any] = {}
        try:
            booknpay = UshBookNPayClient()
            booking_response = await booknpay.update_booking_loyalty_status(
                booking_id=booking_id,
                status="confirmed",
                payment_status="rewarded",
                reward_id=reward_id,
                loyalty_data=loyalty_data_payload,
                correlation_id=correlation_id,
            )
            await booknpay.aclose()
            logger.info(
                "loyalty_redeemed_booking_updated",
                booking_id=booking_id,
                reward_id=reward_id,
                customer_id=customer_id,
            )
        except Exception as exc:
            logger.warning(
                "loyalty_redeemed_booking_update_failed",
                booking_id=booking_id,
                reward_id=reward_id,
                error=str(exc),
            )

        # ── 2. Create appointment-cache in ushauth ────────────────────────────
        # Prefer fields from the booking response (most up-to-date),
        # then fall back to the event reward data.
        try:
            booking_data: dict[str, Any] = booking_response.get("data") or {}

            cache_therapist_id: str = str(
                booking_data.get("therapist_id") or therapist_id or ""
            ).strip()
            cache_service_arrangement_id: str = str(
                booking_data.get("service_arrangement_id")
                or service_arrangement_id
                or ""
            ).strip()
            cache_booking_id: str = str(booking_data.get("id") or booking_id)
            cache_customer_id: str | None = (
                str(booking_data.get("customer_id") or customer_id or "").strip() or None
            )
            cache_service_id: str | None = (
                str(booking_data.get("service_id") or service_id or "").strip() or None
            )

            # appointment_date / appointment_time from booking response.
            # ushbooknpay BookingDetailResponse returns appointment_start (ISO datetime),
            # not separate appointment_date / appointment_starttime fields — split it.
            appt_start_iso: str = str(booking_data.get("appointment_start") or "").strip()
            _date_from_start: str = appt_start_iso.split("T")[0] if "T" in appt_start_iso else ""
            _time_from_start: str = (
                appt_start_iso.split("T")[1].rstrip("Z").split("+")[0].split("-")[0]
                if "T" in appt_start_iso else ""
            )

            cache_appt_date: str = (
                str(booking_data.get("appointment_date") or "").strip()
                or _date_from_start
                or appointment_date
            )
            cache_appt_time: str = (
                str(
                    booking_data.get("appointment_starttime")
                    or booking_data.get("appointment_time")
                    or ""
                ).strip()
                or _time_from_start
                or appointment_time
            )
            # Ensure HH:MM:SS format required by ushauth
            if cache_appt_time and len(cache_appt_time) == 5:
                cache_appt_time = f"{cache_appt_time}:00"

            cache_duration: int = int(
                booking_data.get("duration_minutes")
                or booking_data.get("duration")
                or duration
                or 60
            )
            # ushauth BookingType only accepts: branch | home | leave
            # Map ushbooknpay-specific "loyalty" → "branch"
            _USHAUTH_VALID_BOOKING_TYPES = {"branch", "home", "leave"}
            _raw_btype: str = str(booking_data.get("booking_type") or "branch").lower()
            cache_booking_type: str = _raw_btype if _raw_btype in _USHAUTH_VALID_BOOKING_TYPES else "branch"
            cache_customer_name: str | None = (
                str(booking_data.get("customer_name") or "").strip() or None
            )
            cache_branch_id: str | None = (
                str(booking_data.get("branch_id") or "").strip() or None
            )

            if not cache_therapist_id or not cache_service_arrangement_id:
                logger.warning(
                    "loyalty_redeemed_appointment_cache_skipped_missing_fields",
                    booking_id=cache_booking_id,
                    therapist_id=cache_therapist_id,
                    service_arrangement_id=cache_service_arrangement_id,
                )
            else:
                _cache_payload: dict = {
                    "service_arrangement_id": cache_service_arrangement_id,
                    "therapist_id": cache_therapist_id,
                    "booking_id": cache_booking_id,
                    "appointment_date": cache_appt_date,
                    "appointment_time": cache_appt_time,
                    "booking_type": cache_booking_type,
                    "duration": cache_duration,
                    "status": "confirmed",
                    "customer_id": cache_customer_id,
                    "customer_name": cache_customer_name,
                    "branch_id": cache_branch_id,
                    "service_id": cache_service_id,
                    "payment_status": "success",
                }
                logger.info(
                    "loyalty_redeemed_appointment_cache_request",
                    booking_id=cache_booking_id,
                    reward_id=reward_id,
                    payload=_cache_payload,
                )
                ushauth = UshAuthClient()
                _cache_response = await ushauth.create_appointment_cache(
                    service_arrangement_id=cache_service_arrangement_id,
                    therapist_id=cache_therapist_id,
                    booking_id=cache_booking_id,
                    appointment_date=cache_appt_date,
                    appointment_time=cache_appt_time,
                    booking_type=cache_booking_type,
                    duration=cache_duration,
                    status="confirmed",
                    customer_id=cache_customer_id,
                    customer_name=cache_customer_name,
                    branch_id=cache_branch_id,
                    service_id=cache_service_id,
                    payment_status="success",  # ushauth PaymentStatus only accepts: unpaid|success|pending|refunded|failed
                    correlation_id=correlation_id,
                )
                await ushauth.aclose()
                logger.info(
                    "loyalty_redeemed_appointment_cache_response",
                    booking_id=cache_booking_id,
                    reward_id=reward_id,
                    response=_cache_response,
                )
                logger.info(
                    "loyalty_redeemed_appointment_cache_created",
                    booking_id=cache_booking_id,
                    therapist_id=cache_therapist_id,
                    service_arrangement_id=cache_service_arrangement_id,
                )
        except Exception as exc:
            logger.warning(
                "loyalty_redeemed_appointment_cache_failed",
                booking_id=booking_id,
                reward_id=reward_id,
                error=str(exc),
            )
