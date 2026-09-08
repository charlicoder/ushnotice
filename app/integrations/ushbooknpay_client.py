"""
UshBookNPay service client — booking status update API.

All calls go through the API Gateway at ``/booknpay``.
ushbooknpay remains the source of truth for all booking and payment state.

Payment records are created by this service on booking.confirmed events,
using the payments_meta data from the SQS event payload.
"""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.integrations.http_client import GatewayHttpClient


class UshBookNPayClient:
    """Client for the ushbooknpay microservice via the API Gateway."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = GatewayHttpClient(
            base_url=settings.API_GATEWAY_BASE_URL,
            service_path=settings.USHBOOKNPAY_BASE_PATH,
            service_name="ushbooknpay",
            timeout=settings.GATEWAY_TIMEOUT,
        )

    async def update_booking_status(
        self,
        booking_id: str,
        *,
        status: str,
        reason: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Request a booking status update from ushbooknpay.

        Args:
            booking_id: UUID of the booking to update.
            status: Target status string (e.g. ``"RESCHEDULE_REQUESTED"``).
            reason: Optional human-readable reason for the status change.
            correlation_id: Propagated correlation ID.

        Returns:
            Response dict from ushbooknpay.
        """
        payload: dict[str, Any] = {"status": status}
        if reason:
            payload["reason"] = reason

        return await self._client.patch(
            f"/api/v1/bookings/{booking_id}/status/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def update_booking_loyalty_status(
        self,
        booking_id: str,
        *,
        status: str,
        payment_status: str,
        reward_id: str,
        loyalty_data: dict[str, Any],
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Update a booking's status, payment_status, reward_id and loyalty_data.

        Called when a ``loyalty.redeedmed`` event is received, to mark the
        free-booking as confirmed and record the reward reference.

        Args:
            booking_id: UUID of the booking being redeemed.
            status: Target status (``"confirmed"``).
            payment_status: Payment status (``"rewarded"``).
            reward_id: UUID of the loyalty reward being redeemed.
            loyalty_data: Full reward dict from the SQS event to snapshot on the booking.
            correlation_id: Distributed tracing correlation ID.
        """
        payload: dict[str, Any] = {
            "status": status,
            "payment_status": payment_status,
            "reward_id": reward_id,
            "loyalty_data": loyalty_data,
        }
        return await self._client.patch(
            f"/api/v1/bookings/{booking_id}/status/",
            json=payload,
            correlation_id=correlation_id,
        )


    async def get_booking(
        self,
        booking_id: str,
        *,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve booking details."""
        return await self._client.get(
            f"/api/v1/bookings/{booking_id}/",
            correlation_id=correlation_id,
        )

    async def create_payment(
        self,
        *,
        booking_id: str,
        customer_id: str,
        amount: str,
        currency: str = "KWD",
        provider: str = "myfatoorah",
        payment_method: str = "card",
        status: str = "success",
        is_paid: bool = True,
        payments_meta: dict | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a payment record in ushbooknpay from the booking.confirmed event data.

        This is called by BookingConfirmedHandler to persist the payment
        details that were collected during the mobile payment flow.

        Args:
            booking_id: UUID of the confirmed booking.
            customer_id: UUID of the customer.
            amount: Payment amount as a string (e.g. "45.000").
            currency: Currency code (default "KWD").
            provider: Payment gateway provider name (default "myfatoorah").
            payment_method: Payment method used (default "card").
            status: Payment transaction status (default "success").
            is_paid: Whether the payment was captured (default True).
            payments_meta: Full payments_meta dict from the booking event.
            correlation_id: Propagated correlation ID.
        """
        meta = payments_meta or {}
        payload: dict[str, Any] = {
            "booking_id": booking_id,
            "customer_id": customer_id,
            "amount": amount,
            "currency": currency,
            "provider": provider,
            "payment_method": payment_method,
            "status": status,
            "is_paid": is_paid,
            # Standard unified gateway fields from payments_meta
            "payment_id": meta.get("payment_id") or meta.get("transaction_id"),
            "transaction_id": meta.get("transaction_id"),
            "invoice_id": meta.get("invoice_id"),
            "invoice_value": meta.get("invoice_value") or amount,
            "invoice_reference": meta.get("invoice_reference"),
            "customer_reference": meta.get("customer_reference"),
            "reference_id": meta.get("reference_id"),
            "track_id": meta.get("track_id"),
            "authorization_id": meta.get("authorization_id"),
            "payment_gateway": meta.get("payment_gateway"),
            "customer_name": meta.get("customer_name"),
            "customer_mobile": meta.get("customer_mobile"),
            "customer_email": meta.get("customer_email"),
            "created_date": meta.get("created_date"),
            "transaction_date": meta.get("transaction_date"),
            "vat_amount": meta.get("vat_amount"),
            "payment_url": meta.get("payment_url"),
            "idempotency_key": f"booking-confirmed-{booking_id}",
        }
        return await self._client.post(
            "/api/v1/payments/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def record_loyalty_tracker(
        self,
        *,
        customer_id: str,
        service_id: str,
        booking_id: str,
        service_arrangement_id: str | None = None,
        booking_type: str = "branch",
        customer_name: str = "",
        customer_email: str = "",
        customer_phone: str = "",
        service_name: str = "",
        is_eligible_for_loyalty: bool = False,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Record a confirmed branch booking for loyalty tracking.

        Calls POST /api/v1/promotions/internal/loyalty/record/ which increments
        the counter, issues a LoyaltyReward on the 5th booking, resets counter to 0,
        and emits a loyalty.rewarded SQS event when a reward is issued.

        Args:
            customer_id: UUID of the customer.
            service_id: UUID of the service (from ushauth).
            booking_id: UUID of the confirmed booking.
            service_arrangement_id: Optional UUID of the service arrangement.
            booking_type: Booking type ("branch").
            customer_name: Optional customer name for SQS event context.
            customer_email: Optional customer email for SQS event context.
            customer_phone: Optional customer phone for SQS event context.
            service_name: Optional service name for SQS event context.
            is_eligible_for_loyalty: Whether the service is eligible for loyalty.
                                     If False, the endpoint skips tracker creation.
            correlation_id: Propagated correlation ID.
        """
        payload: dict[str, Any] = {
            "customer_id": customer_id,
            "service_id": service_id,
            "booking_id": booking_id,
            "booking_type": booking_type,
            "customer_name": customer_name,
            "customer_email": customer_email,
            "customer_phone": customer_phone,
            "service_name": service_name,
            "is_eligible_for_loyalty": is_eligible_for_loyalty,
        }
        if service_arrangement_id:
            payload["service_arrangement_id"] = service_arrangement_id

        return await self._client.post(
            "/api/v1/promotions/internal/loyalty/record/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
