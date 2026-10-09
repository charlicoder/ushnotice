"""
tests/unit/test_refund_completed_handler.py
───────────────────────────────────────────
Tests for RefundCompletedHandler in ushnotice:
1. Handles booking.refund_completed event
2. Sends SMS / Email notifications
3. Forwards full refund details to trigger_credit_note in ushanr
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.events.handlers.booking.refund_completed import RefundCompletedHandler
from app.events.schemas.envelope import EventEnvelope


@pytest.mark.asyncio
async def test_refund_completed_handler_triggers_credit_note_and_notifications():
    handler = RefundCompletedHandler()

    envelope = EventEnvelope(
        event_id=str(uuid.uuid4()),
        event_type="booking.refund_completed",
        occurred_at="2026-10-08T10:00:00Z",
        source="ushbooknpay",
        data={
            "booking_id": "booking-uuid-789",
            "booking_reference": "BOK/2026/10/000005",
            "refund_number": "REF/2026/10/000001",
            "refund_amount": "50.000",
            "cancellation_fee": "10.000",
            "currency": "KWD",
            "refund_method": "cash",
            "refund_type": "manual",
            "customer_id": "cust-123",
            "phone_number": "+96599112233",
            "email": "customer@example.com",
            "processed_by": "staff-456",
        },
    )

    ctx = AsyncMock()

    with patch("app.events.handlers.booking.refund_completed.trigger_credit_note", new_callable=AsyncMock) as mock_trigger, \
         patch("app.events.handlers.booking.refund_completed.NotificationService.send", new_callable=AsyncMock) as mock_send:

        await handler.handle(envelope, ctx)

        # Verify notifications sent
        assert mock_send.await_count >= 1

        # Verify trigger_credit_note called with correct arguments
        mock_trigger.assert_awaited_once_with(
            source_document_type="booking",
            source_document_id="booking-uuid-789",
            notes="Refund REF/2026/10/000001 (CASH) for booking BOK/2026/10/000005",
            correlation_id=envelope.correlation_id_str,
            cancellation_fee="10.000",
            refund_amount="50.000",
            refund_method="cash",
            refund_number="REF/2026/10/000001",
            branch_id=None,
            processed_by="staff-456",
        )
