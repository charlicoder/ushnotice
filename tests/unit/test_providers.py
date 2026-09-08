"""
Unit tests for notification providers (KwtSms, Meta WA, Gmail, Stubs).
"""
from __future__ import annotations

import pytest

from app.providers.email.stub import StubEmailProvider
from app.providers.sms.kwtsms import _normalise_phone
from app.providers.sms.stub import StubSmsProvider
from app.providers.whatsapp.stub import StubWhatsAppProvider


def test_normalise_phone_kwtsms() -> None:
    assert _normalise_phone("+96598765432") == "96598765432"
    assert _normalise_phone("0096598765432") == "96598765432"
    assert _normalise_phone("965 9876 5432") == "96598765432"
    assert _normalise_phone("+965-9876-5432") == "96598765432"


@pytest.mark.asyncio
async def test_stub_sms_provider() -> None:
    provider = StubSmsProvider()
    result = await provider.send_sms(to="+96598765432", body="Test message")
    assert result.success is True
    assert result.provider_message_id is not None


@pytest.mark.asyncio
async def test_stub_whatsapp_provider() -> None:
    provider = StubWhatsAppProvider()
    result = await provider.send_message(to="+96598765432", body="Test WA")
    assert result.success is True
    assert result.provider_message_id is not None


@pytest.mark.asyncio
async def test_stub_email_provider() -> None:
    provider = StubEmailProvider()
    result = await provider.send_email(
        to="user@example.com",
        subject="Test Subject",
        html_body="<p>Test</p>",
    )
    assert result.success is True
    assert result.provider_message_id is not None


@pytest.mark.asyncio
async def test_smtp_provider_send_success() -> None:
    from unittest.mock import MagicMock, patch
    from app.providers.email.smtp import SmtpProvider

    provider = SmtpProvider(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        username="ushspa.kwd@gmail.com",
        password="test-password",
        from_name="USH SPA",
        from_address="ushspa.kwd@gmail.com",
        use_tls=True,
        timeout=30.0,
    )

    mock_server = MagicMock()
    with patch("smtplib.SMTP", return_value=mock_server) as mock_smtp:
        mock_server.__enter__.return_value = mock_server
        result = await provider.send_email(
            to="customer@example.com",
            subject="Test Confirmation",
            html_body="<h1>Confirmed</h1>",
            text_body="Confirmed",
        )
        assert result.success is True
        assert result.provider_message_id is not None
        mock_smtp.assert_called_once_with(host="smtp.gmail.com", port=587, timeout=30.0)
        assert mock_server.starttls.called
        mock_server.login.assert_called_once_with("ushspa.kwd@gmail.com", "test-password")
        assert mock_server.send_message.called


@pytest.mark.asyncio
async def test_smtp_provider_port_465_ssl() -> None:
    from unittest.mock import MagicMock, patch
    from app.providers.email.smtp import SmtpProvider

    provider = SmtpProvider(
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        username="ushspa.kwd@gmail.com",
        password="test-password",
        from_name="USH SPA",
        from_address="ushspa.kwd@gmail.com",
        use_tls=True,
    )

    mock_server = MagicMock()
    with patch("smtplib.SMTP_SSL", return_value=mock_server) as mock_smtp_ssl:
        mock_server.__enter__.return_value = mock_server
        result = await provider.send_email(
            to="customer@example.com",
            subject="Test Port 465",
            html_body="<p>SSL Test</p>",
        )
        assert result.success is True
        assert mock_smtp_ssl.called
        assert mock_server.send_message.called


@pytest.mark.asyncio
async def test_smtp_provider_transient_and_permanent_errors() -> None:
    from unittest.mock import patch
    import smtplib
    from app.providers.email.smtp import SmtpProvider

    provider = SmtpProvider(
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        username="ushspa.kwd@gmail.com",
        password="test-password",
        from_name="USH SPA",
        from_address="ushspa.kwd@gmail.com",
    )

    # 4xx transient error -> retryable
    exc_451 = smtplib.SMTPResponseException(451, b"Requested action aborted: local error in processing")
    with patch("smtplib.SMTP", side_effect=exc_451):
        result = await provider.send_email(
            to="customer@example.com",
            subject="Test 451",
            html_body="<p>Test</p>",
        )
        assert result.success is False
        assert result.is_retryable is True
        assert result.error_code == "SMTP_451"

    # 5xx permanent error -> not retryable
    exc_550 = smtplib.SMTPResponseException(550, b"User not found")
    with patch("smtplib.SMTP", side_effect=exc_550):
        result = await provider.send_email(
            to="invalid@example.com",
            subject="Test 550",
            html_body="<p>Test</p>",
        )
        assert result.success is False
        assert result.is_retryable is False
        assert result.error_code == "SMTP_550"
