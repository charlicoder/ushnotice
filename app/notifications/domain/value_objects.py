"""
Domain value objects for the notifications domain.

Value objects are immutable, identity-free objects that encapsulate
domain concepts.  They carry no database identity and are used for
in-memory computations and passing data between layers.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.notifications.domain.enums import NotificationChannel


@dataclass(frozen=True)
class Recipient:
    """Encapsulates the delivery target for a notification.

    Attributes:
        channel: The delivery channel (SMS, WhatsApp, Email).
        address: The channel-specific address (phone number, email address).
        name: Optional display name of the recipient.
        language: Preferred language code (``"en"`` or ``"ar"``).
    """

    channel: NotificationChannel
    address: str
    name: str = ""
    language: str = "en"

    def __post_init__(self) -> None:
        if not self.address:
            raise ValueError("Recipient address must not be empty.")
        if self.language not in ("en", "ar"):
            # Use object.__setattr__ because the dataclass is frozen.
            object.__setattr__(self, "language", "en")

    @property
    def masked_address(self) -> str:
        """Return a partially masked version of the address for safe logging."""
        from app.common.masking import mask_email, mask_phone

        if self.channel in (NotificationChannel.SMS, NotificationChannel.WHATSAPP):
            return mask_phone(self.address)
        if self.channel == NotificationChannel.EMAIL:
            return mask_email(self.address)
        return "***"


@dataclass(frozen=True)
class DeliveryResult:
    """The outcome of a single provider send attempt.

    Attributes:
        success: ``True`` if the provider accepted the message.
        provider_message_id: The message identifier returned by the provider.
        raw_response: The raw response body from the provider (dict or str).
        error_code: Provider-specific error code on failure.
        error_message: Human-readable error description.
        is_retryable: Whether this failure is eligible for retry.
    """

    success: bool
    provider_message_id: str | None = None
    raw_response: dict | str | None = None
    error_code: str | None = None
    error_message: str | None = None
    is_retryable: bool = False

    @classmethod
    def ok(
        cls,
        provider_message_id: str,
        raw_response: dict | str | None = None,
    ) -> "DeliveryResult":
        """Convenience constructor for a successful delivery."""
        return cls(
            success=True,
            provider_message_id=provider_message_id,
            raw_response=raw_response,
        )

    @classmethod
    def fail(
        cls,
        error_code: str,
        error_message: str,
        *,
        is_retryable: bool,
        raw_response: dict | str | None = None,
    ) -> "DeliveryResult":
        """Convenience constructor for a failed delivery."""
        return cls(
            success=False,
            error_code=error_code,
            error_message=error_message,
            is_retryable=is_retryable,
            raw_response=raw_response,
        )


@dataclass(frozen=True)
class NotificationRequest:
    """Input to the notification service for creating and dispatching a notification.

    Attributes:
        event_id: The originating event UUID.
        recipient: The delivery target.
        template_name: Jinja2 template key (e.g. ``"user/otp"``).
        template_context: Variables passed to the template renderer.
        subject: Email subject line (only relevant for the email channel).
        customer_id: Optional customer UUID for traceability.
        booking_id: Optional booking UUID for traceability.
        correlation_id: Propagated from the originating event envelope.
    """

    event_id: str
    recipient: Recipient
    template_name: str
    template_context: dict
    subject: str = ""
    customer_id: str | None = None
    booking_id: str | None = None
    correlation_id: str | None = None
