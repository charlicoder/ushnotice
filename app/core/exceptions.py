"""
Domain and infrastructure exceptions for the ushnotice service.

Exception hierarchy:
    USHNoticeError
    ├── EventValidationError          — malformed event envelope
    ├── UnknownEventTypeError         — no handler registered for this event type
    ├── IdempotencyConflict           — duplicate processing detected (safe to skip)
    ├── ProviderError                 — base for notification-provider failures
    │   ├── RetryableProviderError    — transient; retry with backoff
    │   └── NonRetryableProviderError — permanent; do not retry
    ├── ServiceClientError            — inter-service HTTP call failure
    │   ├── ServiceTimeoutError
    │   └── ServiceUnavailableError
    ├── TemplateRenderError           — Jinja2 rendering failure
    └── ConfigurationError            — missing or invalid configuration
"""
from __future__ import annotations


class USHNoticeError(Exception):
    """Base exception for all ushnotice domain errors."""

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict = details or {}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r}, details={self.details!r})"


# ── Event processing ───────────────────────────────────────────────────────────


class EventValidationError(USHNoticeError):
    """Raised when an incoming SQS message fails schema validation.

    The raw payload will be persisted to the ``events`` table with status
    ``INVALID`` before the message is routed to the DLQ.
    """


class UnknownEventTypeError(USHNoticeError):
    """Raised when the event router receives an unregistered event type.

    Handlers can be added to the registry without touching the consumer.
    """

    def __init__(self, event_type: str) -> None:
        super().__init__(
            f"No handler registered for event type: {event_type!r}",
            details={"event_type": event_type},
        )
        self.event_type = event_type


class IdempotencyConflict(USHNoticeError):
    """Raised when a duplicate event is detected for the same handler.

    The caller should acknowledge the SQS message without reprocessing it.
    """

    def __init__(self, event_id: str, handler_name: str) -> None:
        super().__init__(
            f"Duplicate processing detected: event_id={event_id!r}, handler={handler_name!r}",
            details={"event_id": event_id, "handler_name": handler_name},
        )
        self.event_id = event_id
        self.handler_name = handler_name


# ── Provider errors ────────────────────────────────────────────────────────────


class ProviderError(USHNoticeError):
    """Base class for notification-provider errors."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        provider_code: str | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.provider = provider
        self.provider_code = provider_code


class RetryableProviderError(ProviderError):
    """Transient provider failure — safe to retry with exponential backoff.

    Examples: HTTP 5xx, connection timeout, HTTP 429 (rate limit), queue full.
    """


class NonRetryableProviderError(ProviderError):
    """Permanent provider failure — do not retry.

    Examples: invalid recipient, banned sender ID, invalid payload, account blocked.
    """


# ── Service client errors ─────────────────────────────────────────────────────


class ServiceClientError(USHNoticeError):
    """Base class for inter-service API gateway errors."""

    def __init__(
        self,
        message: str,
        *,
        service: str,
        status_code: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, details=details)
        self.service = service
        self.status_code = status_code


class ServiceTimeoutError(ServiceClientError):
    """HTTP request to a downstream service timed out."""


class ServiceUnavailableError(ServiceClientError):
    """Downstream service returned a 5xx error or is unreachable."""


# ── Template errors ────────────────────────────────────────────────────────────


class TemplateRenderError(USHNoticeError):
    """Jinja2 template could not be rendered.

    Args:
        template_name: The template key that failed (e.g. ``"user/otp.en"``).
    """

    def __init__(self, template_name: str, cause: Exception) -> None:
        super().__init__(
            f"Failed to render template {template_name!r}: {cause}",
            details={"template_name": template_name, "cause": str(cause)},
        )
        self.template_name = template_name
        self.__cause__ = cause


# ── Configuration errors ───────────────────────────────────────────────────────


class ConfigurationError(USHNoticeError):
    """Missing or invalid configuration detected at startup."""
