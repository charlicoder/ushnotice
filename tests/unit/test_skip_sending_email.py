"""
Unit tests verifying SKIP_SENDING_EMIAL behavior across ushnotice.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import Settings, get_settings
from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.application.notification_service import NotificationService
from app.notifications.domain.enums import NotificationChannel, NotificationStatus
from app.notifications.domain.value_objects import NotificationRequest, Recipient
from app.providers.email.stub import StubEmailProvider
from app.providers.factory import get_email_provider, reset_providers


def test_settings_skip_sending_emial_flag():
    # Test default
    s_default = Settings(SKIP_SENDING_EMIAL=False, SKIP_SENDING_EMAIL=False, EMAIL_ENABLED=True)
    assert s_default.SKIP_SENDING_EMIAL is False
    assert s_default.skip_sending_email is False

    # Test with SKIP_SENDING_EMIAL=True
    s_skip = Settings(SKIP_SENDING_EMIAL=True, EMAIL_ENABLED=True)
    assert s_skip.SKIP_SENDING_EMIAL is True
    assert s_skip.skip_sending_email is True

    # Test with SKIP_SENDING_EMAIL=True
    s_skip_standard = Settings(SKIP_SENDING_EMAIL=True, EMAIL_ENABLED=True)
    assert s_skip_standard.skip_sending_email is True


def test_channel_resolver_skips_when_flag_enabled():
    payload = {
        "email": "customer@example.com",
        "customer_name": "Test User",
        "language_preference": "en",
    }

    mock_settings = Settings(SKIP_SENDING_EMIAL=True, EMAIL_ENABLED=True)
    with patch("app.notifications.application.channel_resolver.get_settings", return_value=mock_settings):
        recipient = ChannelResolver.resolve_email_recipient(payload)
        assert recipient is None

    mock_settings_disabled = Settings(SKIP_SENDING_EMIAL=False, SKIP_SENDING_EMAIL=False, EMAIL_ENABLED=True)
    with patch("app.notifications.application.channel_resolver.get_settings", return_value=mock_settings_disabled):
        recipient = ChannelResolver.resolve_email_recipient(payload)
        assert recipient is not None
        assert recipient.address == "customer@example.com"


@pytest.mark.asyncio
async def test_notification_service_send_skips_email():
    mock_settings = Settings(SKIP_SENDING_EMIAL=True, EMAIL_ENABLED=True)
    mock_ctx = MagicMock()

    service = NotificationService(mock_ctx)
    req = NotificationRequest(
        event_id="test-event-123",
        recipient=Recipient(
            channel=NotificationChannel.EMAIL,
            address="customer@example.com",
            language="en",
        ),
        template_name="booking/confirmed_email",
        template_context={},
    )

    with patch("app.notifications.application.notification_service.get_settings", return_value=mock_settings):
        notification = await service.send(req)
        assert notification.status == NotificationStatus.CANCELLED.value
        assert notification.channel == NotificationChannel.EMAIL.value
        assert notification.recipient == "customer@example.com"
        # Repo create should NOT have been called
        mock_ctx.notification_repo.create.assert_not_called()


def test_factory_returns_stub_when_skip_sending_emial():
    mock_settings = Settings(SKIP_SENDING_EMIAL=True, EMAIL_ENABLED=True, EMAIL_PROVIDER="smtp")
    reset_providers()
    with patch("app.providers.factory.get_settings", return_value=mock_settings):
        provider = get_email_provider()
        assert isinstance(provider, StubEmailProvider)
    reset_providers()
