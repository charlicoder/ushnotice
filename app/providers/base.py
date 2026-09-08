"""
Provider Protocol interfaces.

Business logic MUST depend only on these abstractions — never on concrete
provider SDKs or HTTP clients.  This enforces the Dependency Inversion
Principle and makes providers interchangeable and independently testable.

Each Protocol defines the minimal contract a provider must fulfil.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.notifications.domain.value_objects import DeliveryResult


@runtime_checkable
class SmsProvider(Protocol):
    """Contract for SMS delivery providers.

    Implementations: :class:`~app.providers.sms.kwtsms.KwtSmsProvider`,
    :class:`~app.providers.sms.twilio.TwilioSmsProvider`,
    :class:`~app.providers.sms.stub.StubSmsProvider`.
    """

    #: Human-readable provider identifier (e.g. ``"kwtsms"``).
    provider_name: str

    async def send_sms(
        self,
        *,
        to: str,
        body: str,
        sender_id: str | None = None,
    ) -> DeliveryResult:
        """Send an SMS message.

        Args:
            to: Recipient phone number (digits only, with country code).
            body: Message body.  Must not contain HTML tags or emojis (KWT SMS
                restriction).  Must not exceed provider limits.
            sender_id: Override the default sender ID, if the provider
                supports per-message sender IDs.

        Returns:
            :class:`~app.notifications.domain.value_objects.DeliveryResult`
            describing the outcome.
        """
        ...


@runtime_checkable
class WhatsAppProvider(Protocol):
    """Contract for WhatsApp delivery providers.

    Implementations: :class:`~app.providers.whatsapp.meta.MetaWhatsAppProvider`,
    :class:`~app.providers.whatsapp.stub.StubWhatsAppProvider`.
    """

    provider_name: str

    async def send_message(
        self,
        *,
        to: str,
        body: str,
        template_name: str | None = None,
        template_language: str | None = None,
        template_components: list[dict] | None = None,
    ) -> DeliveryResult:
        """Send a WhatsApp message.

        For the Meta Cloud API, messages to non-opted-in users must use
        approved message templates.  Free-form messages are only allowed
        within the 24-hour customer-initiated messaging window.

        Args:
            to: Recipient phone number (E.164 format with ``+``).
            body: Free-form message body (used when not sending a template).
            template_name: Approved Meta template name.
            template_language: BCP-47 language code for the template.
            template_components: Template variable substitutions.

        Returns:
            :class:`~app.notifications.domain.value_objects.DeliveryResult`.
        """
        ...


@runtime_checkable
class EmailProvider(Protocol):
    """Contract for email delivery providers.

    Implementations: :class:`~app.providers.email.gmail.GmailProvider`,
    :class:`~app.providers.email.stub.StubEmailProvider`.
    """

    provider_name: str

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        reply_to: str | None = None,
    ) -> DeliveryResult:
        """Send an email message.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            html_body: HTML version of the body.
            text_body: Plain-text fallback (recommended).
            reply_to: Optional Reply-To address.

        Returns:
            :class:`~app.notifications.domain.value_objects.DeliveryResult`.
        """
        ...
