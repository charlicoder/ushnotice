"""
tests/unit/test_customer_new_created.py
───────────────────────────────────────
Unit tests for CustomerNewCreatedHandler in ushnotice.
Verifies that:
- WhatsApp is attempted first, and SMS is only used as a fallback if WhatsApp fails.
- When a voucher recipient was already notified or is pending, duplicate welcome notifications are skipped.
- Both templates correctly render the temporary password.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.events.schemas.envelope import EventEnvelope
from app.events.handlers.auth.customer_new_created import CustomerNewCreatedHandler
from app.events.handlers.base import HandlerContext
from app.notifications.domain.enums import NotificationChannel, NotificationStatus


def _make_customer_new_created_envelope(data: dict):
    return EventEnvelope(
        event_id=uuid.uuid4(),
        event_type="customer.new_created",
        version=1,
        occurred_at=datetime.now(tz=timezone.utc),
        source="ushauth",
        data=data,
    )


@pytest.mark.asyncio
async def test_customer_new_created_sends_whatsapp_first_with_password():
    handler = CustomerNewCreatedHandler()
    data = {
        "user_id": str(uuid.uuid4()),
        "name": "Sarah Connor",
        "phone_number": "+96599112233",
        "password": "654321",
    }
    envelope = _make_customer_new_created_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)
    mock_svc = AsyncMock()

    # Simulate successful WhatsApp send
    mock_notification = MagicMock()
    mock_notification.status = NotificationStatus.SENT
    mock_svc.send.return_value = mock_notification

    with patch(
        "app.events.handlers.auth.customer_new_created.NotificationService",
        return_value=mock_svc,
    ):
        await handler.handle(envelope, mock_ctx)

    # WhatsApp-first: only 1 call to send (WhatsApp), no duplicate SMS
    assert mock_svc.send.call_count == 1
    sent_request = mock_svc.send.call_args_list[0].args[0]
    assert sent_request.recipient.channel == NotificationChannel.WHATSAPP
    assert sent_request.template_name == "user/customer_new_created"
    assert sent_request.template_context["password"] == "654321"
    assert sent_request.template_context["customer_name"] == "Sarah Connor"


@pytest.mark.asyncio
async def test_customer_new_created_falls_back_to_sms_when_whatsapp_fails():
    handler = CustomerNewCreatedHandler()
    data = {
        "user_id": str(uuid.uuid4()),
        "name": "Sarah Connor",
        "phone_number": "+96599112233",
        "password": "654321",
    }
    envelope = _make_customer_new_created_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)
    mock_svc = AsyncMock()

    # First send (WhatsApp) raises exception, triggering SMS fallback
    mock_notification_sms = MagicMock()
    mock_notification_sms.status = NotificationStatus.SENT
    mock_svc.send.side_effect = [Exception("WhatsApp provider unavailable"), mock_notification_sms]

    with patch(
        "app.events.handlers.auth.customer_new_created.NotificationService",
        return_value=mock_svc,
    ):
        await handler.handle(envelope, mock_ctx)

    # Two attempts: WhatsApp failed -> SMS fallback succeeded
    assert mock_svc.send.call_count == 2
    wa_req = mock_svc.send.call_args_list[0].args[0]
    sms_req = mock_svc.send.call_args_list[1].args[0]
    assert wa_req.recipient.channel == NotificationChannel.WHATSAPP
    assert sms_req.recipient.channel == NotificationChannel.SMS
    assert sms_req.template_name == "user/customer_new_created"
    assert sms_req.template_context["password"] == "654321"


@pytest.mark.asyncio
async def test_customer_new_created_skipped_when_voucher_recipient_notified():
    handler = CustomerNewCreatedHandler()
    data = {
        "user_id": str(uuid.uuid4()),
        "name": "Sarah Connor",
        "phone_number": "+96599112233",
        "password": "654321",
    }
    envelope = _make_customer_new_created_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)
    mock_svc = AsyncMock()

    mock_redis = AsyncMock()
    mock_redis.get.return_value = "1"

    with patch("app.core.redis.get_redis", return_value=mock_redis), \
         patch("app.events.handlers.auth.customer_new_created.NotificationService", return_value=mock_svc):
        await handler.handle(envelope, mock_ctx)

    # Skipped entirely because recipient was already notified via voucher.active
    assert mock_svc.send.call_count == 0


@pytest.mark.asyncio
async def test_customer_new_created_skipped_when_voucher_recipient_pending():
    handler = CustomerNewCreatedHandler()
    data = {
        "user_id": str(uuid.uuid4()),
        "name": "Sarah Connor",
        "phone_number": "+96599112233",
        "password": "654321",
    }
    envelope = _make_customer_new_created_envelope(data)
    mock_ctx = MagicMock(spec=HandlerContext)
    mock_svc = AsyncMock()

    mock_redis = AsyncMock()
    # notified is None, but pending is "1"
    mock_redis.get.side_effect = lambda k: "1" if "pending" in k else None

    with patch("app.core.redis.get_redis", return_value=mock_redis), \
         patch("app.events.handlers.auth.customer_new_created.NotificationService", return_value=mock_svc):
        await handler.handle(envelope, mock_ctx)

    # Skipped entirely because voucher is pending and will notify on active
    assert mock_svc.send.call_count == 0


@pytest.mark.asyncio
async def test_customer_new_created_template_renders_password():
    from app.templates.renderer import TemplateRenderer

    renderer = TemplateRenderer()
    body_en = renderer.render(
        "user/customer_new_created",
        lang="en",
        context={"customer_name": "Sarah", "password": "654321"},
    )
    assert "Your password is: 654321" in body_en

    body_ar = renderer.render(
        "user/customer_new_created",
        lang="ar",
        context={"customer_name": "سارة", "password": "654321"},
    )
    assert "كلمة المرور الخاصة بك: 654321" in body_ar
