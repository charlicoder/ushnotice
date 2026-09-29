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
    def extract_language(
        payload: dict[str, Any],
        default_lang: str | None = None,
    ) -> str:
        """Extract and normalize language preference from payload or nested dicts.

        Returns 'ar' if Arabic, else 'en'.
        """
        keys = (
            "language_preference",
            "language",
            "preferred_language",
            "customer_language",
            "recipient_language",
            "sender_language",
            "lang",
        )
        for k in keys:
            val = payload.get(k)
            if val and isinstance(val, str) and val.strip():
                clean = val.strip().lower()
                if clean.startswith("ar"):
                    return "ar"
                if clean.startswith("en"):
                    return "en"

        nested_wrappers = (
            "customer",
            "customer_data",
            "user",
            "user_data",
            "recipient",
            "recipient_data",
            "recipient_details",
            "sender",
            "sender_data",
            "sender_details",
        )
        for wrap in nested_wrappers:
            nested = payload.get(wrap)
            if isinstance(nested, dict):
                for k in keys:
                    val = nested.get(k)
                    if val and isinstance(val, str) and val.strip():
                        clean = val.strip().lower()
                        if clean.startswith("ar"):
                            return "ar"
                        if clean.startswith("en"):
                            return "en"

        try:
            default_from_settings = get_settings().DEFAULT_LANGUAGE
        except Exception:
            default_from_settings = "en"
        fallback = default_lang or default_from_settings or "en"
        return "ar" if str(fallback).strip().lower().startswith("ar") else "en"

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
            for wrap in ("customer", "customer_data", "user", "user_data"):
                nested = payload.get(wrap)
                if isinstance(nested, dict):
                    phone = (
                        nested.get("phone")
                        or nested.get("phone_number")
                        or nested.get("mobile")
                    )
                    if phone:
                        break
        if not phone:
            return None

        name = (
            payload.get("customer_name")
            or payload.get("name")
            or (
                isinstance(payload.get("customer"), dict)
                and (payload["customer"].get("name") or payload["customer"].get("full_name"))
            )
            or (
                isinstance(payload.get("user"), dict)
                and (payload["user"].get("name") or payload["user"].get("full_name"))
            )
            or ""
        )
        lang = ChannelResolver.extract_language(payload, default_lang)

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
            for wrap in ("customer", "customer_data", "user", "user_data"):
                nested = payload.get(wrap)
                if isinstance(nested, dict):
                    email = nested.get("email")
                    if email:
                        break
        if not email:
            return None

        name = (
            payload.get("customer_name")
            or payload.get("name")
            or (
                isinstance(payload.get("customer"), dict)
                and (payload["customer"].get("name") or payload["customer"].get("full_name"))
            )
            or (
                isinstance(payload.get("user"), dict)
                and (payload["user"].get("name") or payload["user"].get("full_name"))
            )
            or ""
        )
        lang = ChannelResolver.extract_language(payload, default_lang)

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
            for wrap in ("customer", "customer_data", "user", "user_data"):
                nested = payload.get(wrap)
                if isinstance(nested, dict):
                    phone = (
                        nested.get("whatsapp_number")
                        or nested.get("phone")
                        or nested.get("phone_number")
                        or nested.get("mobile")
                    )
                    if phone:
                        break
        if not phone:
            return None

        name = (
            payload.get("customer_name")
            or payload.get("name")
            or (
                isinstance(payload.get("customer"), dict)
                and (payload["customer"].get("name") or payload["customer"].get("full_name"))
            )
            or (
                isinstance(payload.get("user"), dict)
                and (payload["user"].get("name") or payload["user"].get("full_name"))
            )
            or ""
        )
        lang = ChannelResolver.extract_language(payload, default_lang)

        return Recipient(
            channel=NotificationChannel.WHATSAPP,
            address=str(phone),
            name=str(name),
            language=str(lang),
        )
