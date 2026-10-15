"""
Handler for ``payment_link.paid`` event.

When a customer pays through a payment link and the gateway webhook succeeds:
  1. Updates booking with payment_status=success in ushbooknpay and ushauth cache
  2. Updates invoice status=paid in ushanr
  3. Creates a record into payments in ushbooknpay with all booking & invoice data
  4. Sends SMS/WhatsApp confirmation message to the customer
"""
from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.events.handlers.base import HandlerContext
from app.events.handlers.booking.booking_confirmed import BookingConfirmedHandler
from app.events.schemas.envelope import EventEnvelope
from app.integrations.ushauth_client import UshAuthClient
from app.integrations.ushbooknpay_client import UshBookNPayClient
from app.integrations.ushanr_client import UshanrClient

logger = get_logger(__name__)


class PaymentLinkPaidHandler:
    """Processes ``payment_link.paid`` events."""

    event_type: str = "payment_link.paid"

    async def handle(self, envelope: EventEnvelope, ctx: HandlerContext) -> None:
        data = envelope.data
        correlation_id = envelope.correlation_id_str or envelope.event_id_str
        target_type = str(data.get("target_type") or "").lower()
        booking_id = str(data.get("booking_id") or data.get("target_id") or "")

        logger.info(
            "payment_link_paid_event_received",
            payment_link_id=data.get("payment_link_id"),
            target_type=target_type,
            booking_id=booking_id,
            amount=data.get("amount"),
            correlation_id=correlation_id,
        )

        if target_type == "booking" or booking_id:
            await self._process_booking_payment(data, envelope, ctx, correlation_id)
        else:
            logger.info("payment_link_paid_non_booking_target", target_type=target_type, data=data)

    async def _process_booking_payment(
        self,
        data: dict[str, Any],
        envelope: EventEnvelope,
        ctx: HandlerContext,
        correlation_id: str,
    ) -> None:
        booking_id = str(data.get("booking_id") or data.get("target_id") or "")
        booking_data = dict(data.get("booking") or {})

        # Ensure booking_id in snapshot
        if not booking_data.get("booking_id"):
            booking_data["booking_id"] = booking_id

        # ── 1. Update booking payment_status = success in ushbooknpay & ushauth ───
        booknpay_client = UshBookNPayClient()
        try:
            await booknpay_client.update_booking_status(
                booking_id=booking_id,
                status="confirmed",
                payment_status="success",
                reason="Payment link paid",
                source="payment_link",
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.warning("payment_link_paid_booking_update_warn", booking_id=booking_id, error=str(exc))

        ushauth_client = UshAuthClient()
        try:
            await ushauth_client.update_appointment_cache_payment_status(
                booking_id=booking_id,
                payment_status="success",
                correlation_id=correlation_id,
            )
        except Exception as exc:
            logger.warning("payment_link_paid_cache_update_warn", booking_id=booking_id, error=str(exc))

        # ── 2. Update invoice status = paid in ushanr ─────────────────────────
        ushanr_client = UshanrClient()
        try:
            booking_number = booking_data.get("booking_number") or data.get("booking_number") or data.get("target_ref")
            await ushanr_client.mark_invoice_paid(
                invoice_id=data.get("invoice_id"),
                invoice_name=data.get("invoice_number") or booking_data.get("invoice_number"),
                source_document_type="booking",
                source_document_id=booking_id,
                source_document_ref=booking_number,
                amount_paid=data.get("amount") or booking_data.get("total_amount"),
                payment_id=data.get("gateway_payment_id") or data.get("payment_link_id"),
            )
        except Exception as exc:
            logger.warning("payment_link_paid_mark_invoice_warn", booking_id=booking_id, error=str(exc))

        # ── 3 & 4. Create payments record & send SMS/WhatsApp notifications ──
        # Merge verified gateway payment fields into booking event data
        booking_data["status"] = "confirmed"
        booking_data["payment_status"] = "success"
        booking_data["is_paid"] = True
        booking_data["payment_id"] = data.get("gateway_payment_id") or data.get("payment_link_id")
        booking_data["transaction_id"] = data.get("transaction_id")
        booking_data["track_id"] = data.get("track_id")
        booking_data["reference_id"] = data.get("reference_id")
        booking_data["invoice_id"] = data.get("gateway_invoice_id")
        booking_data["invoice_number"] = data.get("invoice_number") or booking_data.get("invoice_number")
        booking_data["payment_method"] = data.get("payment_method") or "payment_link"
        booking_data["payment_provider"] = data.get("provider") or data.get("gateway")
        booking_data["payment_gateway"] = data.get("gateway")
        booking_data["payment_through"] = "ushdesk"
        if data.get("amount"):
            booking_data["total_amount"] = data.get("amount")

        # Payment data dict for Payment record creation
        existing_pd = booking_data.get("payment_data") if isinstance(booking_data.get("payment_data"), dict) else {}
        booking_data["payment_data"] = {
            **existing_pd,
            "payment_link_id": data.get("payment_link_id"),
            "gateway_invoice_id": data.get("gateway_invoice_id"),
            "gateway_payment_id": data.get("gateway_payment_id"),
            "transaction_id": data.get("transaction_id"),
            "track_id": data.get("track_id"),
            "reference_id": data.get("reference_id"),
            "invoice_id": data.get("gateway_invoice_id"),
            "invoice_number": data.get("invoice_number") or booking_data.get("invoice_number"),
            "invoice_value": data.get("amount") or booking_data.get("total_amount"),
            "payment_provider": data.get("provider") or data.get("gateway"),
            "payment_method": data.get("payment_method") or "payment_link",
        }

        # Delegate to BookingConfirmedHandler to create payment record in ushbooknpay and send notifications
        confirmed_envelope = envelope.model_copy(
            update={
                "event_type": "booking.confirmed",
                "data": booking_data,
            }
        )
        handler = BookingConfirmedHandler()
        await handler.handle(confirmed_envelope, ctx)
        logger.info("payment_link_paid_processing_completed", booking_id=booking_id)
