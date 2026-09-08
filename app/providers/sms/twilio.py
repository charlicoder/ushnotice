"""
Twilio SMS provider implementation.

Used as a fallback provider when KWT SMS is unavailable or for
international numbers outside KWT SMS's coverage.

Requires: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_SMS_FROM in settings.
"""
from __future__ import annotations

import time

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)

_TWILIO_BASE = "https://api.twilio.com/2010-04-01"


class TwilioSmsProvider:
    """SMS provider implementation for the Twilio Messaging REST API.

    Uses HTTPX with HTTP Basic Auth (account SID + auth token).
    Does NOT use the Twilio Python SDK to stay async-compatible.
    """

    provider_name: str = "twilio"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        timeout: float = 15.0,
    ) -> None:
        self._account_sid = account_sid
        self._from_number = from_number
        self._client = httpx.AsyncClient(
            auth=(account_sid, auth_token),
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls) -> "TwilioSmsProvider":
        settings = get_settings()
        return cls(
            account_sid=settings.TWILIO_ACCOUNT_SID,
            auth_token=settings.TWILIO_AUTH_TOKEN.get_secret_value(),
            from_number=settings.TWILIO_SMS_FROM,
        )

    async def send_sms(
        self,
        *,
        to: str,
        body: str,
        sender_id: str | None = None,
    ) -> DeliveryResult:
        """Send SMS via Twilio Messages API."""
        url = f"{_TWILIO_BASE}/Accounts/{self._account_sid}/Messages.json"

        start = time.monotonic()
        try:
            response = await self._client.post(
                url,
                data={
                    "From": sender_id or self._from_number,
                    "To": to if to.startswith("+") else f"+{to}",
                    "Body": body,
                },
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            response.raise_for_status()
            data = response.json()

            status = data.get("status", "")
            if status in ("queued", "sent", "delivered"):
                return DeliveryResult.ok(
                    provider_message_id=data.get("sid", ""),
                    raw_response=data,
                )
            else:
                error_code = str(data.get("code", "UNKNOWN"))
                return DeliveryResult.fail(
                    error_code=error_code,
                    error_message=data.get("message", "Unexpected Twilio status"),
                    is_retryable=True,
                    raw_response=data,
                )

        except httpx.TimeoutException as exc:
            return DeliveryResult.fail(
                error_code="TIMEOUT",
                error_message=str(exc),
                is_retryable=True,
            )
        except httpx.HTTPStatusError as exc:
            is_retryable = exc.response.status_code >= 500
            return DeliveryResult.fail(
                error_code=f"HTTP_{exc.response.status_code}",
                error_message=str(exc),
                is_retryable=is_retryable,
            )
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult.fail(
                error_code="UNEXPECTED",
                error_message=str(exc),
                is_retryable=True,
            )

    async def aclose(self) -> None:
        await self._client.aclose()
