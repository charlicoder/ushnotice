"""
Channel resolver — determines the target recipient and preferred delivery channel.

Inspects event payload and recipient preferences to resolve recipient value objects.
"""
from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.notifications.domain.enums import NotificationChannel
from app.notifications.domain.value_objects import Recipient


class ChannelResolver:
    """Resolves recipient and channel routing for event notifications."""

    @staticmethod
    def resolve_sms_recipient(
        payload: dict[str, Any],
        default_lang: str | None = None,
    ) -> Recipient | None:
        """Extract SMS recipient from payload."""
        phone = (
            payload.get("phone")
            or payload.get("phone_number")
            or payload.get("mobile")
            or payload.get("customer_phone")
        )
        if not phone:
            return None

        name = payload.get("customer_name") or payload.get("name") or ""
        lang = payload.get("language") or payload.get("preferred_language") or default_lang or get_settings().DEFAULT_LANGUAGE

        return Recipient(
            channel=NotificationChannel.SMS,
            address=str(phone),
            name=str(name),
            language=str(lang),
        )

    @staticmethod
    def resolve_email_recipient(
        payload: dict[str, Any],
        default_lang: str | None = None,
    ) -> Recipient | None:
        """Extract Email recipient from payload."""
        email = payload.get("email") or payload.get("customer_email")
        if not email:
            return None

        name = payload.get("customer_name") or payload.get("name") or ""
        lang = payload.get("language") or payload.get("preferred_language") or default_lang or get_settings().DEFAULT_LANGUAGE

        return Recipient(
            channel=NotificationChannel.EMAIL,
            address=str(email),
            name=str(name),
            language=str(lang),
        )

    @staticmethod
    def resolve_whatsapp_recipient(
        payload: dict[str, Any],
        default_lang: str | None = None,
    ) -> Recipient | None:
        """Extract WhatsApp recipient from payload."""
        phone = (
            payload.get("whatsapp_number")
            or payload.get("phone")
            or payload.get("phone_number")
            or payload.get("mobile")
            or payload.get("customer_phone")
        )
        if not phone:
            return None

        name = payload.get("customer_name") or payload.get("name") or ""
        lang = payload.get("language") or payload.get("preferred_language") or default_lang or get_settings().DEFAULT_LANGUAGE

        return Recipient(
            channel=NotificationChannel.WHATSAPP,
            address=str(phone),
            name=str(name),
            language=str(lang),
        )
