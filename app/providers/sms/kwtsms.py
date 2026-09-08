"""
KWT SMS provider implementation.

API: REST/JSON — ``POST https://www.kwtsms.com/API/send/``
Documentation: https://www.kwtsms.com/api-documentation.html (v4.1)

Key behaviours:
- Authentication via ``username`` + ``password`` in the JSON body (NOT headers).
- Phone numbers must be digits only — no ``+``, ``00``, spaces, or dashes.
- Arabic messages encoded as UTF-8 (70 chars/page); English 160 chars/page.
- ``"test": "1"`` queues but does NOT deliver or charge credits.
- ``ERR028``: Must wait 15 s before sending to the same number again.
- Rate limit: max 5 requests/second per IP; recommend ≤ 2/s.

Retryable error codes: ERR013 (queue full), ERR028 (15 s dupe limit),
    HTTP 5xx, connection errors.
Non-retryable: ERR003 (bad creds), ERR006/025 (bad number), ERR008 (banned
    sender), ERR009 (empty message), ERR010/011 (no balance), ERR027 (HTML),
    ERR031/032 (spam/bad lang), ERR034 (banned IP).
"""
from __future__ import annotations

import re
import time

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)

_KWTSMS_SEND_URL = "https://www.kwtsms.com/API/send/"

# Error codes that are NOT safe to retry.
_NON_RETRYABLE_CODES = frozenset(
    {
        "ERR001",  # API off on this account
        "ERR002",  # missing parameter
        "ERR003",  # wrong username or password
        "ERR004",  # no API access
        "ERR005",  # account blocked
        "ERR006",  # no valid numbers submitted
        "ERR007",  # >200 numbers at once
        "ERR008",  # sender ID banned
        "ERR009",  # message empty
        "ERR010",  # balance zero
        "ERR011",  # insufficient balance
        "ERR012",  # message too long (>6 pages)
        "ERR025",  # invalid number format
        "ERR026",  # no route for country
        "ERR027",  # HTML tags in message
        "ERR031",  # bad language
        "ERR032",  # spam detected
        "ERR034",  # banned proxy/VPN IP
        "ERR035",  # daily international limit
        "ERR036",  # emoji not supported
    }
)

# Error codes that ARE safe to retry.
_RETRYABLE_CODES = frozenset(
    {
        "ERR013",  # queue full — wait and retry
        "ERR028",  # 15 s dupe limit — retry after delay
    }
)


def _normalise_phone(phone: str) -> str:
    """Strip ``+``, ``00``, spaces, dashes, and parentheses from a phone number.

    KWT SMS requires pure digits with country code and no leading zeros.

    Args:
        phone: Raw phone number in any format.

    Returns:
        Digits-only phone number.
    """
    # Remove all non-digit characters
    digits = re.sub(r"[^\d]", "", phone)
    # Strip leading double-zero (international dialling prefix)
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


class KwtSmsProvider:
    """SMS provider implementation for the KWT SMS REST API v4.1.

    Thread-safe and async-safe.  A single ``httpx.AsyncClient`` instance is
    created per provider instance and should be shared across calls.

    Args:
        username: KWT SMS API username (from account settings, not mobile number).
        password: KWT SMS API password.
        sender_id: Default sender ID registered on the account.
        test_mode: When ``True``, passes ``"test": "1"`` to avoid charging.
        timeout: HTTP request timeout in seconds.
    """

    provider_name: str = "kwtsms"

    def __init__(
        self,
        *,
        username: str,
        password: str,
        sender_id: str,
        test_mode: bool = False,
        timeout: float = 15.0,
    ) -> None:
        self._username = username
        self._password = password
        self._sender_id = sender_id
        self._test_mode = test_mode
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )

    @classmethod
    def from_settings(cls) -> "KwtSmsProvider":
        """Construct an instance from the application settings singleton."""
        settings = get_settings()
        return cls(
            username=settings.KWTSMS_USERNAME,
            password=settings.KWTSMS_PASSWORD.get_secret_value(),
            sender_id=settings.effective_kwtsms_sender_id,
            test_mode=settings.KWTSMS_TEST_MODE,
        )

    async def send_sms(
        self,
        *,
        to: str,
        body: str,
        sender_id: str | None = None,
    ) -> DeliveryResult:
        """Send an SMS via the KWT SMS API.

        Args:
            to: Recipient phone number (any format; normalised internally).
            body: Message body.  Must be UTF-8; no HTML, no emojis.
            sender_id: Override sender ID for this message.

        Returns:
            :class:`DeliveryResult` describing the provider outcome.
        """
        normalised = _normalise_phone(to)
        if not normalised.isdigit():
            return DeliveryResult.fail(
                error_code="ERR025",
                error_message=f"Invalid phone number after normalisation: {to!r}",
                is_retryable=False,
            )

        payload = {
            "username": self._username,
            "password": self._password,
            "sender": sender_id or self._sender_id,
            "mobile": normalised,
            "message": body,
            "test": "1" if self._test_mode else "0",
        }

        start = time.monotonic()
        try:
            response = await self._client.post(_KWTSMS_SEND_URL, json=payload)
            elapsed_ms = int((time.monotonic() - start) * 1000)

            data: dict = response.json()

            if data.get("result") == "OK":
                logger.info(
                    "KWT SMS sent successfully",
                    provider="kwtsms",
                    msg_id=data.get("msg-id"),
                    numbers=data.get("numbers"),
                    latency_ms=elapsed_ms,
                )
                return DeliveryResult.ok(
                    provider_message_id=str(data.get("msg-id", "")),
                    raw_response=data,
                )
            else:
                error_code = data.get("code", "UNKNOWN")
                is_retryable = error_code in _RETRYABLE_CODES or (
                    error_code not in _NON_RETRYABLE_CODES
                )
                logger.warning(
                    "KWT SMS send failed",
                    provider="kwtsms",
                    error_code=error_code,
                    description=data.get("description"),
                    is_retryable=is_retryable,
                )
                return DeliveryResult.fail(
                    error_code=error_code,
                    error_message=data.get("description", "Unknown error"),
                    is_retryable=is_retryable,
                    raw_response=data,
                )

        except httpx.TimeoutException as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning("KWT SMS request timed out", latency_ms=elapsed_ms)
            return DeliveryResult.fail(
                error_code="TIMEOUT",
                error_message=str(exc),
                is_retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            is_retryable = exc.response.status_code >= 500
            logger.warning(
                "KWT SMS HTTP error",
                status_code=exc.response.status_code,
                is_retryable=is_retryable,
            )
            return DeliveryResult.fail(
                error_code=f"HTTP_{exc.response.status_code}",
                error_message=str(exc),
                is_retryable=is_retryable,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("KWT SMS unexpected error", error=str(exc))
            return DeliveryResult.fail(
                error_code="UNEXPECTED",
                error_message=str(exc),
                is_retryable=True,
            )

    async def aclose(self) -> None:
        """Close the underlying HTTPX client."""
        await self._client.aclose()
