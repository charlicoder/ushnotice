"""
UshBookNPay service client — booking status update API.

All calls go through the API Gateway at ``/booknpay``.
ushbooknpay remains the source of truth for all booking and payment state.

Payment records are created by this service on booking.confirmed events,
using the payment_data data from the SQS event payload.
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
        booking_id: str | None = None,
        voucher_id: str | None = None,
        customer_id: str,
        total_amount: str,
        total_duration: int = 0,
        currency: str = "KWD",
        payment_provider: str = "MyFatoorah",
        payment_through: str = "ushspa",
        payment_gateway: str | None = None,
        payment_for: str = "branch_service",
        payment_method: str = "card",
        status: str = "success",
        country: str | None = None,
        payment_id: str | None = None,
        transaction_id: str | None = None,
        invoice_id: str | None = None,
        reference_id: str | None = None,
        track_id: str | None = None,
        transaction_status: str | None = None,
        transaction_date: str | None = None,
        payment_url: str | None = None,
        payment_data: dict | None = None,
        booking_data: dict | None = None,
        customer_data: dict | None = None,
        created_by_user: str | None = None,
        created_by_user_data: dict | None = None,
        created_by: str | None = None,
        service_id: str | None = None,
        service_data: dict | None = None,
        branch_id: str | None = None,
        branch_data: dict | None = None,
        service_arrangement_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a payment record in ushbooknpay.

        Args:
            booking_id: UUID of the confirmed booking.
            voucher_id: UUID of the gift voucher (if applicable).
            customer_id: UUID of the customer (required).
            total_amount: Total payment amount (required).
            total_duration: Service duration in minutes (required).
            currency: Currency code (default "KWD").
            payment_provider: Provider name — MyFatoorah, DirectLink, Deema, Other.
            payment_through: Channel — ushspa, desk, other.
            payment_gateway: Gateway — KNET, TAP, Other.
            payment_for: Purpose — branch_service, home_service, gift_voucher, product_items.
            payment_method: Method — card, knet, apple_pay, etc.
            status: Payment status (default "success").
            country: Country name or ISO code.
            payment_id: Gateway payment ID.
            transaction_id: Gateway transaction ID.
            invoice_id: Gateway invoice ID.
            reference_id: Bank/KNET reference ID.
            track_id: Gateway track ID.
            transaction_status: Raw or normalized transaction status.
            transaction_date: Raw transaction timestamp.
            payment_url: Gateway payment URL.
            payment_data: Full payment_data dict from the event.
            booking_data: Booking snapshot dict.
            customer_data: Customer snapshot dict.
            created_by_user: User UUID/ID who initiated the booking/payment.
            created_by_user_data: JSONB snapshot of creator user.
            created_by: Backward-compat alias for created_by_user.
            service_id: Service UUID.
            service_data: Service snapshot dict.
            branch_id: Branch UUID.
            branch_data: Branch snapshot dict.
            service_arrangement_id: Service arrangement UUID.
            correlation_id: Propagated correlation ID.
        """
        meta = payment_data or {}

        # Normalise payment_gateway to spec values
        _gw = str(payment_gateway or meta.get("payment_gateway") or "").upper()
        if "KNET" in _gw or "K-NET" in _gw:
            normalised_gw = "KNET"
        elif "TAP" in _gw:
            normalised_gw = "TAP"
        elif _gw:
            normalised_gw = "Other"
        else:
            normalised_gw = None

        # Resolve creator and creator snapshot data
        _creator = (
            created_by_user
            or created_by
            or meta.get("created_by_user")
            or meta.get("created_by")
            or customer_id
        )
        resolved_creator: str | None = str(_creator).strip() if _creator else None

        _c_user_data = created_by_user_data or meta.get("created_by_user_data")
        if isinstance(_c_user_data, dict) and _c_user_data:
            resolved_creator_data: dict[str, Any] = dict(_c_user_data)
            if resolved_creator and not resolved_creator_data.get("id"):
                resolved_creator_data["id"] = resolved_creator
        else:
            _c_snapshot = customer_data or {}
            resolved_creator_data = {
                "id": resolved_creator or customer_id,
                "name": _c_snapshot.get("name") or meta.get("customer_name") or "",
                "phone": _c_snapshot.get("phone") or _c_snapshot.get("mobile") or meta.get("customer_mobile") or meta.get("customer_phone") or "",
                "email": _c_snapshot.get("email") or meta.get("customer_email") or "",
                "role": "customer" if (resolved_creator == customer_id or not resolved_creator) else "user",
            }

        # Build comprehensive payment_data dictionary preserving all raw fields
        built_payment_data: dict[str, Any] = dict(payment_data) if isinstance(payment_data, dict) else {}
        for k, v in [
            ("payment_id", payment_id or meta.get("payment_id") or meta.get("transaction_id")),
            ("transaction_id", transaction_id or meta.get("transaction_id")),
            ("invoice_id", invoice_id or meta.get("invoice_id")),
            ("reference_id", reference_id or meta.get("reference_id")),
            ("track_id", track_id or meta.get("track_id")),
            ("country", country or meta.get("country")),
            ("transaction_date", transaction_date or meta.get("transaction_date")),
            ("transaction_status", transaction_status or meta.get("transaction_status")),
            ("payment_url", payment_url or meta.get("payment_url")),
            ("payment_gateway", normalised_gw),
            ("payment_provider", payment_provider),
            ("payment_through", payment_through),
            ("payment_method", payment_method),
            ("invoice_reference", meta.get("invoice_reference")),
            ("customer_reference", meta.get("customer_reference")),
            ("authorization_id", meta.get("authorization_id")),
            ("vat_amount", meta.get("vat_amount")),
            ("created_date", meta.get("created_date")),
        ]:
            if v is not None and k not in built_payment_data:
                built_payment_data[k] = v

        payload: dict[str, Any] = {
            # ── Required fields ────────────────────────────────────────────
            "customer_id": customer_id,
            "total_amount": total_amount,
            "total_duration": total_duration,
            "currency": currency,
            # ── Associations ───────────────────────────────────────────────
            "booking_id": booking_id,
            "voucher_id": voucher_id,
            # ── Status & classification ────────────────────────────────────
            "status": status,
            "payment_for": payment_for,
            "payment_provider": payment_provider,
            "payment_through": payment_through,
            "payment_gateway": normalised_gw,
            "payment_method": payment_method,
            # ── Invoice & transaction identifiers ──────────────────────────
            "country": country or meta.get("country"),
            "payment_id": payment_id or meta.get("payment_id") or meta.get("transaction_id"),
            "transaction_id": transaction_id or meta.get("transaction_id") or payment_id,
            "invoice_id": invoice_id or meta.get("invoice_id"),
            "invoice_value": meta.get("invoice_value") or total_amount,
            "reference_id": reference_id or meta.get("reference_id"),
            "track_id": track_id or meta.get("track_id"),
            "transaction_status": transaction_status or meta.get("transaction_status"),
            "transaction_date": transaction_date or meta.get("transaction_date"),
            "payment_url": payment_url or meta.get("payment_url"),
            # ── Service & location ─────────────────────────────────────────
            "service_id": service_id,
            "service_data": service_data,
            "branch_id": branch_id,
            "branch_data": branch_data,
            "service_arrangement_id": service_arrangement_id,
            # ── Snapshots ──────────────────────────────────────────────────
            "booking_data": booking_data,
            "customer_data": customer_data or {
                "name": meta.get("customer_name"),
                "mobile": meta.get("customer_mobile") or meta.get("customer_phone"),
                "email": meta.get("customer_email"),
            },
            # ── Creator ────────────────────────────────────────────────────
            "created_by_user": resolved_creator,
            "created_by_user_data": resolved_creator_data,
            "created_by": resolved_creator,
            # ── Raw gateway identifiers → payment_data JSONB ───────────────
            "payment_data": built_payment_data,
        }
        return await self._client.post(
            "/api/v1/payments/",
            json=payload,
            correlation_id=correlation_id,
        )


    async def create_shop_order_payment(
        self,
        *,
        order_id: str,
        customer_id: str,
        total_amount: str,
        currency: str = "KWD",
        payment_status: str = "success",
        payment_method: str = "",
        payment_type: str = "",
        payment_provider: str = "",
        payment_gateway: str | None = None,
        reference_id: str | None = None,
        track_id: str | None = None,
        country: str | None = None,
        payment_id: str | None = None,
        transaction_id: str | None = None,
        invoice_id: str | None = None,
        transaction_date: str | None = None,
        transaction_status: str | None = None,
        created_by: str | None = None,
        created_by_user: str | None = None,
        created_by_user_data: dict | None = None,
        payment_url: str | None = None,
        payment_data: dict | None = None,
        customer_data: dict | None = None,
        product_order_items: list | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a payment record for a shop (product) order.

        Called by the ShopOrderCreatedHandler in ushnotice after receiving the
        ``shop.order_created`` SQS event. Maps order data to the Payment model.

        Args:
            order_id:            UUID of the shop_order (→ product_order_id on Payment).
            customer_id:         UUID of the customer (required by Payment model).
            total_amount:        Order total as a string Decimal.
            currency:            ISO currency code (default "KWD").
            payment_status:      Payment status — always "success" when this is called.
            payment_method:      How the customer paid: card, knet, cash, apple_pay, etc.
            payment_type:        Payment channel: gateway, desk, gift_voucher, etc.
            payment_provider:    Provider: MyFatoorah, DirectLink, Deema, Other.
            payment_gateway:     Gateway network: KNET, TAP, Other.
            reference_id:        Bank/KNET reference ID.
            track_id:            Gateway track ID.
            country:             Country name or ISO code.
            payment_id:          Gateway payment ID.
            transaction_id:      Gateway transaction ID.
            invoice_id:          Gateway invoice ID.
            transaction_date:    Raw transaction date from gateway.
            transaction_status:  Raw transaction status from gateway.
            created_by:          UUID of user requesting order (backward-compat alias).
            created_by_user:     UUID of user requesting order.
            created_by_user_data: JSONB snapshot of creator user.
            payment_url:         Payment URL from gateway.
            payment_data:        Full gateway payload / extra data (JSONB).
            customer_data:       Customer snapshot dict {id, name, phone, contact_number}.
            product_order_items: Items list from the SQS event (snapshot at order time).
            correlation_id:      Propagated tracing ID.
        """
        # Normalise payment_provider to spec values
        _prov = (payment_provider or "").strip().lower()
        if "fatoorah" in _prov:
            normalised_provider = "MyFatoorah"
        elif "directlink" in _prov or "direct" in _prov:
            normalised_provider = "DirectLink"
        elif "deema" in _prov:
            normalised_provider = "Deema"
        elif _prov:
            normalised_provider = "Other"
        else:
            normalised_provider = "Other"

        # Normalise payment_gateway to spec values
        normalised_gw = None
        if payment_gateway:
            _gw_raw = payment_gateway.upper()
            if "KNET" in _gw_raw or "K-NET" in _gw_raw:
                normalised_gw = "KNET"
            elif "TAP" in _gw_raw:
                normalised_gw = "TAP"
            else:
                normalised_gw = "Other"

        # Normalise payment_method
        _method = (payment_method or "").strip().lower()
        if not _method or _method in ("", "unknown"):
            _method = "knet" if normalised_gw == "KNET" else "card"

        # Normalise payment_through from payment_type
        _type = (payment_type or "").strip().lower()
        if "desk" in _type:
            normalised_through = "desk"
        elif "ushspa" in _type:
            normalised_through = "ushspa"
        else:
            normalised_through = "ushspa"

        # Build customer snapshot
        _customer_data: dict[str, Any] = customer_data or {}
        built_customer_data: dict[str, Any] = {
            "id": customer_id,
            "name": _customer_data.get("name", ""),
            "phone": _customer_data.get("phone") or _customer_data.get("contact_number", ""),
        }

        # Resolve creator and creator data snapshot
        resolved_creator = str(created_by_user or created_by).strip() if (created_by_user or created_by) else None
        if isinstance(created_by_user_data, dict) and created_by_user_data:
            resolved_creator_data: dict[str, Any] = dict(created_by_user_data)
            if resolved_creator and not resolved_creator_data.get("id"):
                resolved_creator_data["id"] = resolved_creator
        else:
            resolved_creator_data = {
                "id": resolved_creator or customer_id,
                "name": built_customer_data.get("name", ""),
                "phone": built_customer_data.get("phone", ""),
                "role": "customer" if (resolved_creator == customer_id or not resolved_creator) else "user",
            }

        payload: dict[str, Any] = {
            # ── Required fields ──────────────────────────────────────
            "customer_id": customer_id,
            "total_amount": total_amount,
            "total_duration": 0,           # product orders have no duration
            "currency": currency,
            # ── Product order association ────────────────────────────
            "product_order_id": order_id,
            "product_order_items": product_order_items or [],
            # ── Status & classification ──────────────────────────────
            "status": payment_status,
            "payment_for": "product_items",
            "payment_provider": normalised_provider,
            "payment_through": normalised_through,
            "payment_method": _method,
            "payment_gateway": normalised_gw,
            # ── Invoice & transaction identifiers ────────────────────
            "reference_id": reference_id,
            "track_id": track_id,
            "country": country,
            "payment_id": payment_id,
            "transaction_id": transaction_id,
            "invoice_id": invoice_id,
            "transaction_date": transaction_date,
            "transaction_status": transaction_status,
            "created_by": resolved_creator,
            "created_by_user": resolved_creator,
            "created_by_user_data": resolved_creator_data,
            "payment_url": payment_url,
            "payment_data": payment_data or {},
            # ── Customer snapshot ────────────────────────────────────
            "customer_data": built_customer_data,
        }

        return await self._client.post(
            "/api/v1/payments/",
            json=payload,
            correlation_id=correlation_id,
        )

    # ── Loyalty endpoints ──────────────────────────────────────────────────────

    async def credit_loyalty_points(
        self,
        *,
        customer_id: str,
        booking_id: str | None = None,
        booking_number: str | None = None,
        loyalty_points: int = 0,
        arrangement_loyalty_points: int | None = None,
        created_by: str = "ushnotice",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Credit loyalty points to a customer after a confirmed booking.

        Calls POST /api/v1/loyalty/internal/credit/ on ushbooknpay.
        The effective points (arrangement override vs service level) are
        resolved server-side by ushbooknpay's domain rules.

        Args:
            customer_id:                Customer UUID string.
            booking_id:                 Confirmed booking UUID string (optional).
            booking_number:             Human-readable booking reference (optional).
            loyalty_points:             Service-level earn points.
            arrangement_loyalty_points: Arrangement override (None = use service level).
            created_by:                 Source identifier tag.
            correlation_id:             Propagated correlation ID.

        Returns:
            Response dict from ushbooknpay.
        """
        payload: dict[str, Any] = {
            "customer_id": customer_id,
            "loyalty_points": loyalty_points,
            "created_by": created_by,
        }
        if booking_id:
            payload["booking_id"] = booking_id
        if booking_number:
            payload["booking_number"] = booking_number
        if arrangement_loyalty_points is not None:
            payload["arrangement_loyalty_points"] = arrangement_loyalty_points

        return await self._client.post(
            "/api/v1/loyalty/internal/credit/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def cancel_loyalty_points(
        self,
        *,
        customer_id: str,
        points: int,
        booking_id: str | None = None,
        booking_number: str | None = None,
        created_by: str = "ushnotice",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Reverse earned loyalty points when a booking is cancelled.

        Calls POST /api/v1/loyalty/internal/cancel/ on ushbooknpay.
        Balance is clamped to ≥ 0 server-side.

        Args:
            customer_id:    Customer UUID string.
            points:         Points originally earned (to be reversed).
            booking_id:     Cancelled booking UUID string (optional).
            booking_number: Human-readable booking reference (optional).
            created_by:     Source identifier tag.
            correlation_id: Propagated correlation ID.

        Returns:
            Response dict from ushbooknpay.
        """
        payload: dict[str, Any] = {
            "customer_id": customer_id,
            "points": points,
            "created_by": created_by,
        }
        if booking_id:
            payload["booking_id"] = booking_id
        if booking_number:
            payload["booking_number"] = booking_number

        return await self._client.post(
            "/api/v1/loyalty/internal/cancel/",
            json=payload,
            correlation_id=correlation_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
