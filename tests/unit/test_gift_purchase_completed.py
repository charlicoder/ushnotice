"""
tests/unit/test_gift_purchase_completed.py
──────────────────────────────────────────
Unit tests for GiftPurchaseCompletedHandler in ushnotice.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.events.schemas.envelope import EventEnvelope
from app.events.handlers.gifts.gift_purchase_completed import (
    GiftPurchaseCompletedHandler,
    _build_gift_context,
    _recipient_whatsapp_message_en,
    _recipient_whatsapp_message_ar,
)
from app.events.handlers.base import HandlerContext
from app.notifications.domain.enums import NotificationChannel, NotificationStatus


SAMPLE_GIFT_DATA = {
    "id": str(uuid.uuid4()),
    "secret_code": "123456",
    "expire_date": "2026-12-31T23:59:59Z",
    "total_amount": "50.000",
    "public_token": "token-xyz-123",
    "sender_data": {
        "name": "Fatima",
        "phone_number": "+96599112233",
    },
    "recipient_data": {
        "name": "Noura",
        "phone_number": "+96599445566",
        "is_new_user": True,
    },
    "recipient_language": "en",
    "payment_through": "online",
}


def test_build_gift_context_extracts_is_new_user():
    ctx = _build_gift_context(SAMPLE_GIFT_DATA)
    assert ctx["is_new_user"] is True
    assert ctx["sender_name"] == "Fatima"
    assert ctx["recipient_name"] == "Noura"
    assert ctx["recipient_password"] == ""


def test_recipient_whatsapp_message_en_with_is_new_user():
    ctx = _build_gift_context(SAMPLE_GIFT_DATA)
    msg = _recipient_whatsapp_message_en(ctx)
    assert "A new account has been created for you. Download our app and set your password:" in msg
    assert "Your Login Password:" not in msg
    assert "123456" in msg


def test_recipient_whatsapp_message_ar_with_is_new_user():
    ctx = _build_gift_context({**SAMPLE_GIFT_DATA, "recipient_language": "ar"})
    msg = _recipient_whatsapp_message_ar(ctx)
    assert "تم إنشاء حساب جديد لك. حمّل تطبيقنا وعيّن كلمة المرور الخاصة بك:" in msg
    assert "كلمة المرور لتسجيل الدخول:" not in msg


def test_recipient_whatsapp_message_with_password():
    data_with_pass = {
        **SAMPLE_GIFT_DATA,
        "recipient_data": {
            **SAMPLE_GIFT_DATA["recipient_data"],
            "password": "temp_password_123",
        },
    }
    ctx = _build_gift_context(data_with_pass)
    msg_en = _recipient_whatsapp_message_en(ctx)
    assert "Your Login Password: temp_password_123" in msg_en
    assert "Download our app and set your password:" not in msg_en

    msg_ar = _recipient_whatsapp_message_ar(ctx)
    assert "كلمة المرور لتسجيل الدخول: temp_password_123" in msg_ar
