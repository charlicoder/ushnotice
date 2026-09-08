"""
Meta Cloud API WhatsApp provider implementation.

API: Graph API — ``POST /{phone_number_id}/messages``
Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/messages

Two message types are supported:
1. **Text** messages — free-form text within the 24-hour customer-service
   window.
2. **Template** messages — pre-approved templates for outbound business
   messages outside the customer-service window.

For the reschedule-request branch notification and booking confirmations,
template messages should be used.

Retryable: HTTP 429 (rate limit), HTTP 5xx, connection errors.
Non-retryable: HTTP 400 (invalid payload), HTTP 401 (bad token).
"""
from __future__ import annotations

import time

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)


class MetaWhatsAppProvider:
    """WhatsApp provider using the Meta Graph API (Cloud API).

    Args:
        phone_number_id: Meta business phone number ID.
        access_token: Permanent or temporary access token.
        api_version: Graph API version string (e.g. ``"v20.0"``).
        timeout: HTTP request timeout in seconds.
    """

    provider_name: str = "meta"

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        api_version: str = "v20.0",
        timeout: float = 15.0,
    ) -> None:
        self._phone_number_id = phone_number_id
        self._api_version = api_version
        self._base_url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    @classmethod
    def from_settings(cls) -> "MetaWhatsAppProvider":
        settings = get_settings()
        return cls(
            phone_number_id=settings.META_WA_PHONE_NUMBER_ID,
            access_token=settings.META_WA_ACCESS_TOKEN.get_secret_value(),
            api_version=settings.META_WA_API_VERSION,
        )

    async def send_message(
        self,
        *,
        to: str,
        body: str,
        template_name: str | None = None,
        template_language: str | None = None,
        template_components: list[dict] | None = None,
    ) -> DeliveryResult:
        """Send a WhatsApp message via the Meta Cloud API.

        If *template_name* is provided, sends a template message.
        Otherwise sends a free-form text message (only valid within the
        24-hour messaging window).
        """
        # Ensure E.164 format required by Meta
        wa_to = to if to.startswith("+") else f"+{to}"
        wa_to_clean = wa_to.lstrip("+")  # Meta wants digits only without +

        if template_name:
            payload = self._build_template_payload(
                to=wa_to_clean,
                template_name=template_name,
                language=template_language or "en",
                components=template_components or [],
            )
        else:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": wa_to_clean,
                "type": "text",
                "text": {"preview_url": False, "body": body},
            }

        start = time.monotonic()
        try:
            response = await self._client.post(self._base_url, json=payload)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            data: dict = response.json()

            if response.status_code == 200:
                msg_id = data.get("messages", [{}])[0].get("id", "")
                logger.info(
                    "Meta WhatsApp message sent",
                    provider="meta",
                    msg_id=msg_id,
                    latency_ms=elapsed_ms,
                )
                return DeliveryResult.ok(
                    provider_message_id=msg_id,
                    raw_response=data,
                )
            else:
                error = data.get("error", {})
                error_code = str(error.get("code", response.status_code))
                is_retryable = response.status_code in (429, 500, 502, 503, 504)
                logger.warning(
                    "Meta WhatsApp send failed",
                    status_code=response.status_code,
                    error_code=error_code,
                    is_retryable=is_retryable,
                )
                return DeliveryResult.fail(
                    error_code=error_code,
                    error_message=error.get("message", "Unknown Meta error"),
                    is_retryable=is_retryable,
                    raw_response=data,
                )

        except httpx.TimeoutException as exc:
            return DeliveryResult.fail(
                error_code="TIMEOUT",
                error_message=str(exc),
                is_retryable=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Meta WhatsApp unexpected error", error=str(exc))
            return DeliveryResult.fail(
                error_code="UNEXPECTED",
                error_message=str(exc),
                is_retryable=True,
            )

    @staticmethod
    def _build_template_payload(
        *,
        to: str,
        template_name: str,
        language: str,
        components: list[dict],
    ) -> dict:
        """Build a Meta template message payload."""
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components,
            },
        }

    async def aclose(self) -> None:
        await self._client.aclose()
