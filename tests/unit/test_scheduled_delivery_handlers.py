"""
tests/unit/test_scheduled_delivery_handlers.py
──────────────────────────────────────────────
Tests for VoucherScheduledHandler and VoucherDeliveryDueHandler in ushnotice:
1. VoucherScheduledHandler:
   - Triggers accounting invoice
   - Creates payment record in ushbooknpay
   - Sends short Arabic confirmation SMS to sender with scheduled date
   - Does NOT send any notification to recipient
2. VoucherDeliveryDueHandler:
   - Consumes voucher.delivery_due
   - Calls ushbooknpay client activate_scheduled_voucher endpoint
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.handlers.voucher.voucher_delivery_due import VoucherDeliveryDueHandler
from app.events.handlers.voucher.voucher_scheduled import (
    VoucherScheduledHandler,
    _sender_scheduled_confirmation_sms_ar,
)
from app.events.schemas.envelope import EventEnvelope


def _make_envelope(event_type: str, data: dict) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type=event_type,
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )


def test_sender_scheduled_confirmation_sms_ar():
    """Verify Arabic SMS text contains sender name, recipient name, and scheduled date."""
    from app.events.handlers.voucher.voucher_scheduled import _fmt_scheduled_datetime_ar

    vctx = {
        "sender_name": "سارة",
        "recipient_name": "نورة",
    }
    sms = _sender_scheduled_confirmation_sms_ar(vctx, "2026-11-15T15:00:00")
    assert "سارة" in sms
    assert "نورة" in sms
    assert "2026-11-15 03:00 PM" in sms

    # Test that trailing Z does not shift Kuwait time by +3h
    formatted = _fmt_scheduled_datetime_ar("2026-11-15T03:40:00Z")
    assert "03:40 AM" in formatted
    assert "2026-11-15" in sms
    assert "جدولة إرسال بطاقة الهدية" in sms
    assert "يوش سبا" in sms


@pytest.mark.asyncio
async def test_voucher_scheduled_handler_creates_payment_and_notifies_sender_only():
    """VoucherScheduledHandler creates payment and notifies only the sender."""
    envelope = _make_envelope(
        "voucher.scheduled",
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "voucher_number": "VOU/2026/10/000001",
            "status": "scheduled",
            "delivery_mode": "scheduled",
            "scheduled_delivery_at": "2026-11-10T14:00:00",
            "total_amount": "50.000",
            "sender_phone": "+96599998888",
            "sender_details": {"name": "Fatima", "phone_number": "+96599998888"},
            "recipient_phone": "+96566665555",
            "recipient_details": {"name": "Laila", "phone_number": "+96566665555"},
        },
    )

    mock_notification_service = AsyncMock()
    mock_context = MagicMock()
    mock_context.notification_service = mock_notification_service

    mock_client = AsyncMock()
    mock_client._client.post = AsyncMock(return_value={"success": True})
    mock_client.aclose = AsyncMock()

    handler = VoucherScheduledHandler()

    with (
        patch("app.events.handlers.voucher.voucher_scheduled.trigger_voucher_invoice", new_callable=AsyncMock) as mock_inv,
        patch("app.events.handlers.voucher.voucher_scheduled.UshBookNPayClient", return_value=mock_client),
    ):
        mock_inv.return_value = {"invoice_id": "inv-1", "invoice_name": "INV/2026/001"}
        await handler.handle(envelope, mock_context)

        # 1. Invoice triggered
        mock_inv.assert_awaited_once()

        # 2. Payment record created with required fields
        mock_client._client.post.assert_awaited_once()
        post_url, post_kwargs = mock_client._client.post.call_args
        assert post_url[0] == "/api/v1/payments/"
        assert post_kwargs["json"]["voucher_id"] == "11111111-1111-1111-1111-111111111111"
        assert post_kwargs["json"]["customer_id"] == "11111111-1111-1111-1111-111111111111"  # falls back to voucher_id when no sender_id/recipient_id
        assert post_kwargs["json"]["total_amount"] == "50.000"
        assert post_kwargs["json"]["currency"] == "KWD"
        assert "total_duration" in post_kwargs["json"]

        # 3. Notification sent ONLY to sender (call_count == 1)
        assert mock_notification_service.send.await_count == 1
        sent_req = mock_notification_service.send.call_args[0][0]
        # Sent to sender phone
        assert sent_req.recipient.address == "+96599998888"


@pytest.mark.asyncio
async def test_voucher_scheduled_handler_fallback_to_recipient_id_when_sender_id_empty():
    """VoucherScheduledHandler derives customer_id from recipient_id when sender_id is empty."""
    envelope = _make_envelope(
        "voucher.scheduled",
        {
            "id": "8290340d-72e3-46b0-b371-5009b61358d8",
            "voucher_number": "VOU/2026/10/000136",
            "status": "scheduled",
            "delivery_mode": "scheduled",
            "scheduled_delivery_at": "2026-10-31T06:00:00+03:00",
            "total_amount": "66.000",
            "sender_id": "",
            "recipient_id": "8a507202-df5d-4fc1-9b12-9d62f36fb14b",
            "recipient_phone": "+96541028983",
        },
    )

    mock_client = AsyncMock()
    mock_client._client.post = AsyncMock(return_value={"success": True})
    mock_client.aclose = AsyncMock()

    handler = VoucherScheduledHandler()

    with (
        patch("app.events.handlers.voucher.voucher_scheduled.trigger_voucher_invoice", new_callable=AsyncMock) as mock_inv,
        patch("app.events.handlers.voucher.voucher_scheduled.UshBookNPayClient", return_value=mock_client),
    ):
        await handler.handle(envelope, MagicMock())

        mock_inv.assert_awaited_once()
        mock_client._client.post.assert_awaited_once()
        _, post_kwargs = mock_client._client.post.call_args
        assert post_kwargs["json"]["customer_id"] == "8a507202-df5d-4fc1-9b12-9d62f36fb14b"
        assert post_kwargs["json"]["total_amount"] == "66.000"


@pytest.mark.asyncio
async def test_voucher_delivery_due_handler_activates_voucher():
    """VoucherDeliveryDueHandler calls ushbooknpay client activate_scheduled_voucher."""
    envelope = _make_envelope(
        "voucher.delivery_due",
        {
            "voucher_id": "22222222-2222-2222-2222-222222222222",
            "scheduled_delivery_at": "2026-11-10T14:00:00",
            "source": "aws_eventbridge_scheduler",
        },
    )

    mock_client = AsyncMock()
    mock_client.activate_scheduled_voucher = AsyncMock(return_value={"success": True})
    mock_client._client.aclose = AsyncMock()

    handler = VoucherDeliveryDueHandler()

    with patch("app.events.handlers.voucher.voucher_delivery_due.UshBookNPayClient", return_value=mock_client):
        await handler.handle(envelope, MagicMock())
        mock_client.activate_scheduled_voucher.assert_awaited_once_with(
            "22222222-2222-2222-2222-222222222222",
            delivery_result={
                "delivered": True,
                "source": "eventbridge_scheduler",
                "event_id": envelope.event_id_str,
            },
            correlation_id=envelope.correlation_id_str,
        )
