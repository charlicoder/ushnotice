"""
tests/unit/test_loyalty_rewarded.py
───────────────────────────────────
Unit tests for LoyaltyRewardedHandler in ushnotice.

Covers:
- Handling loyalty.rewarded SQS event
- WhatsApp notification sent if whatsapp_verified is True
- SMS fallback sent if WhatsApp is NOT sent
- Email sent independently if customer_email is present
- Handler registration in build_default_registry
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_loyalty_rewarded_envelope(data: dict):
    from app.events.schemas.envelope import EventEnvelope
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="loyalty.rewarded",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushbooknpay",
        data=data,
    )


REWARDED_EVENT_DATA = {
    "reward_id": "r1111111-1111-1111-1111-111111111111",
    "tracker_id": "t2222222-2222-2222-2222-222222222222",
    "customer_id": "c3333333-3333-3333-3333-333333333333",
    "customer_name": "Amina Ali",
    "customer_phone": "+96599887766",
    "customer_email": "amina@example.com",
    "service_id": "s4444444-4444-4444-4444-444444444444",
    "service_name": "Full Body Massage",
    "bookings_required": 5,
    "total_rewards_earned": 1,
    "reward_status": "available",
    "expires_at": "2026-09-10T12:00:00+00:00",
    "booking_id": "b5555555-5555-5555-5555-555555555555",
    "whatsapp_verified": True,
}


def test_loyalty_rewarded_handler_is_registered():
    """LoyaltyRewardedHandler is registered in build_default_registry."""
    from app.events.registry.handler_registry import build_default_registry
    registry = build_default_registry()
    assert registry.has_handler("loyalty.rewarded")
    assert registry.has_handler("loyalty_rewarded")


@pytest.mark.asyncio
async def test_loyalty_rewarded_handler_sends_whatsapp_and_email():
    """Handler sends WhatsApp and Email when whatsapp_verified=True and email present."""
    from app.events.handlers.loyalty.loyalty_rewarded import LoyaltyRewardedHandler
    from app.events.handlers.base import HandlerContext

    handler = LoyaltyRewardedHandler()
    envelope = _make_loyalty_rewarded_envelope(REWARDED_EVENT_DATA)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notification_service = AsyncMock()

    with patch("app.events.handlers.loyalty.loyalty_rewarded.NotificationService", return_value=mock_notification_service):
        await handler.handle(envelope, mock_ctx)

    # Should call send twice (WhatsApp + Email)
    assert mock_notification_service.send.call_count == 2
    sent_requests = [call.args[0] for call in mock_notification_service.send.call_args_list]
    channels = [r.recipient.channel for r in sent_requests]
    assert "whatsapp" in channels
    assert "email" in channels
    assert "sms" not in channels


@pytest.mark.asyncio
async def test_loyalty_rewarded_handler_fallback_to_sms():
    """Handler falls back to SMS when whatsapp_verified is False."""
    from app.events.handlers.loyalty.loyalty_rewarded import LoyaltyRewardedHandler
    from app.events.handlers.base import HandlerContext

    handler = LoyaltyRewardedHandler()
    data = {**REWARDED_EVENT_DATA, "whatsapp_verified": False}
    envelope = _make_loyalty_rewarded_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notification_service = AsyncMock()

    with patch("app.events.handlers.loyalty.loyalty_rewarded.NotificationService", return_value=mock_notification_service):
        await handler.handle(envelope, mock_ctx)

    # Should call send twice (SMS fallback + Email)
    assert mock_notification_service.send.call_count == 2
    sent_requests = [call.args[0] for call in mock_notification_service.send.call_args_list]
    channels = [r.recipient.channel for r in sent_requests]
    assert "sms" in channels
    assert "email" in channels
    assert "whatsapp" not in channels
