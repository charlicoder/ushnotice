"""Unit tests for SendSMSHandler in ushnotice."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.events.handlers.base import HandlerContext
from app.events.handlers.sms.send_sms import SendSMSHandler
from app.events.schemas.envelope import EventEnvelope
from app.notifications.domain.enums import NotificationChannel
from app.events.registry.handler_registry import build_default_registry


def _make_sms_envelope(data: dict) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="sms.send",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushauth",
        data=data,
    )


@pytest.mark.asyncio
async def test_send_sms_handler_single_number():
    """Verify SendSMSHandler dispatches SMS for a single recipient."""
    handler = SendSMSHandler()
    data = {
        "phone_number": "+96512345678",
        "content": "Your appointment has been confirmed.",
        "language": "en",
    }
    envelope = _make_sms_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notif_service = AsyncMock()
    mock_notif_service.send = AsyncMock()

    with patch("app.events.handlers.sms.send_sms.NotificationService", return_value=mock_notif_service):
        await handler.handle(envelope, mock_ctx)

    assert mock_notif_service.send.call_count == 1
    req = mock_notif_service.send.call_args[0][0]
    assert req.recipient.channel == NotificationChannel.SMS
    assert req.recipient.address == "+96512345678"
    assert req.recipient.language == "en"
    assert req.template_name == "sms/direct"
    assert req.template_context["raw_body"] == "Your appointment has been confirmed."


@pytest.mark.asyncio
async def test_send_sms_handler_mass_numbers():
    """Verify SendSMSHandler dispatches SMS to all recipients in a mass send."""
    handler = SendSMSHandler()
    numbers = ["+96511111111", "+96522222222", "+96533333333"]
    data = {
        "phone_numbers": numbers,
        "content": "خصم خاص 20% على جميع باقات المساج لنهاية الأسبوع!",
        "language": "ar",
    }
    envelope = _make_sms_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notif_service = AsyncMock()
    mock_notif_service.send = AsyncMock()

    with patch("app.events.handlers.sms.send_sms.NotificationService", return_value=mock_notif_service):
        await handler.handle(envelope, mock_ctx)

    assert mock_notif_service.send.call_count == 3
    called_phones = [call[0][0].recipient.address for call in mock_notif_service.send.call_args_list]
    assert called_phones == numbers
    for call in mock_notif_service.send.call_args_list:
        assert call[0][0].recipient.language == "ar"
        assert call[0][0].template_context["raw_body"] == "خصم خاص 20% على جميع باقات المساج لنهاية الأسبوع!"


@pytest.mark.asyncio
async def test_send_sms_handler_empty_content_is_noop():
    """Verify empty or whitespace content is safely ignored without errors."""
    handler = SendSMSHandler()
    data = {
        "phone_numbers": ["+96512345678"],
        "content": "   ",
    }
    envelope = _make_sms_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notif_service = AsyncMock()
    with patch("app.events.handlers.sms.send_sms.NotificationService", return_value=mock_notif_service):
        await handler.handle(envelope, mock_ctx)

    assert mock_notif_service.send.call_count == 0


@pytest.mark.asyncio
async def test_send_sms_handler_empty_phone_numbers_is_noop():
    """Verify empty phone numbers list is safely ignored without errors."""
    handler = SendSMSHandler()
    data = {
        "phone_numbers": [],
        "content": "Hello World",
    }
    envelope = _make_sms_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)

    mock_notif_service = AsyncMock()
    with patch("app.events.handlers.sms.send_sms.NotificationService", return_value=mock_notif_service):
        await handler.handle(envelope, mock_ctx)

    assert mock_notif_service.send.call_count == 0


def test_registry_resolves_sms_events():
    """Verify default registry resolves sms.send and its aliases."""
    registry = build_default_registry()
    for ev in ("sms.send", "sms_send", "sms.mass_send", "sms_mass_send"):
        assert registry.has_handler(ev), f"Registry should have handler for {ev}"
        handlers = registry.resolve(ev)
        assert len(handlers) >= 1
        assert any(isinstance(h, SendSMSHandler) for h in handlers)
