"""
tests/unit/test_customer_new_created.py
───────────────────────────────────────
Unit tests for CustomerNewCreatedHandler in ushnotice.
Verifies that when a new customer account is created (e.g. during gift voucher purchase),
both WhatsApp and SMS welcome notifications are dispatched with the random password.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.events.schemas.envelope import EventEnvelope
from app.events.handlers.auth.customer_new_created import CustomerNewCreatedHandler
from app.events.handlers.base import HandlerContext
from app.notifications.domain.value_objects import NotificationChannel


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
async def test_customer_new_created_sends_whatsapp_and_sms_with_password():
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

    with patch(
        "app.events.handlers.auth.customer_new_created.NotificationService",
        return_value=mock_svc,
    ):
        await handler.handle(envelope, mock_ctx)

    assert mock_svc.send.call_count == 2
    sent_requests = [call.args[0] for call in mock_svc.send.call_args_list]

    channels = [r.recipient.channel for r in sent_requests]
    assert NotificationChannel.WHATSAPP in channels
    assert NotificationChannel.SMS in channels

    for req in sent_requests:
        assert req.template_name == "user/customer_new_created"
        assert req.template_context["password"] == "654321"
        assert req.template_context["customer_name"] == "Sarah Connor"


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
