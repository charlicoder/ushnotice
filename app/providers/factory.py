"""
Provider factory — resolves the active provider for each channel.

Business logic must call :func:`get_sms_provider`, :func:`get_whatsapp_provider`,
or :func:`get_email_provider` instead of instantiating providers directly.
This centralises provider selection and makes it trivial to swap providers
via environment variables.

Provider instances are cached as module-level singletons after first
construction so HTTPX clients (which hold connection pools) are reused.
"""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.base import EmailProvider, SmsProvider, WhatsAppProvider

logger = get_logger(__name__)

# Module-level provider singletons
_sms_provider: SmsProvider | None = None
_whatsapp_provider: WhatsAppProvider | None = None
_email_provider: EmailProvider | None = None


def get_sms_provider() -> SmsProvider:
    """Return the configured SMS provider singleton.

    Provider is selected by the ``SMS_PROVIDER`` environment variable:
    - ``"kwtsms"`` → :class:`~app.providers.sms.kwtsms.KwtSmsProvider` (default)
    - ``"twilio"`` → :class:`~app.providers.sms.twilio.TwilioSmsProvider`
    - ``"stub"``   → :class:`~app.providers.sms.stub.StubSmsProvider`
    """
    global _sms_provider  # noqa: PLW0603
    if _sms_provider is not None:
        return _sms_provider

    provider_name = get_settings().SMS_PROVIDER
    if provider_name == "kwtsms":
        from app.providers.sms.kwtsms import KwtSmsProvider
        _sms_provider = KwtSmsProvider.from_settings()
    elif provider_name == "twilio":
        from app.providers.sms.twilio import TwilioSmsProvider
        _sms_provider = TwilioSmsProvider.from_settings()
    else:
        from app.providers.sms.stub import StubSmsProvider
        _sms_provider = StubSmsProvider()

    logger.info("SMS provider initialised", provider=provider_name)
    return _sms_provider


def get_whatsapp_provider() -> WhatsAppProvider:
    """Return the configured WhatsApp provider singleton.

    Provider is selected by the ``WHATSAPP_PROVIDER`` environment variable:
    - ``"meta"`` → :class:`~app.providers.whatsapp.meta.MetaWhatsAppProvider` (default)
    - ``"stub"`` → :class:`~app.providers.whatsapp.stub.StubWhatsAppProvider`
    """
    global _whatsapp_provider  # noqa: PLW0603
    if _whatsapp_provider is not None:
        return _whatsapp_provider

    provider_name = get_settings().WHATSAPP_PROVIDER
    if provider_name == "meta":
        from app.providers.whatsapp.meta import MetaWhatsAppProvider
        _whatsapp_provider = MetaWhatsAppProvider.from_settings()
    else:
        from app.providers.whatsapp.stub import StubWhatsAppProvider
        _whatsapp_provider = StubWhatsAppProvider()

    logger.info("WhatsApp provider initialised", provider=provider_name)
    return _whatsapp_provider


def get_email_provider() -> EmailProvider:
    """Return the configured email provider singleton.

    Provider is selected by the ``EMAIL_PROVIDER`` and ``EMAIL_ENABLED`` environment variables:
    - If ``EMAIL_ENABLED`` is False → :class:`~app.providers.email.stub.StubEmailProvider`
    - ``"smtp"`` / ``"gmail"``       → :class:`~app.providers.email.smtp.SmtpProvider` (default)
    - ``"stub"``                     → :class:`~app.providers.email.stub.StubEmailProvider`
    """
    global _email_provider  # noqa: PLW0603
    if _email_provider is not None:
        return _email_provider

    settings = get_settings()
    if not settings.EMAIL_ENABLED:
        from app.providers.email.stub import StubEmailProvider
        _email_provider = StubEmailProvider()
        logger.info("Email provider disabled by EMAIL_ENABLED=false; using stub provider")
        return _email_provider

    provider_name = settings.EMAIL_PROVIDER.lower()
    if provider_name in ("smtp", "gmail"):
        from app.providers.email.smtp import SmtpProvider
        _email_provider = SmtpProvider.from_settings()
    else:
        from app.providers.email.stub import StubEmailProvider
        _email_provider = StubEmailProvider()

    logger.info("Email provider initialised", provider=provider_name)
    return _email_provider


def reset_providers() -> None:
    """Reset all provider singletons.

    Useful in tests to swap providers between test cases without restarting
    the process.  Call ``get_settings.cache_clear()`` first if you also need
    to change settings.
    """
    global _sms_provider, _whatsapp_provider, _email_provider  # noqa: PLW0603
    _sms_provider = None
    _whatsapp_provider = None
    _email_provider = None
