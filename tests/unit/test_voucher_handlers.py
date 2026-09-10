"""
Unit tests for voucher event handlers.

Tests cover:
  - VoucherActiveHandler: message composers and voucher context builder
  - VoucherRedeemedHandler: message composers and context builder
  - Handler registration in the default registry
"""
from __future__ import annotations

import pytest

from app.events.handlers.voucher.voucher_active import (
    VoucherActiveHandler,
    _build_voucher_context,
    _fmt_expire,
    _recipient_email_subject,
    _recipient_sms_message,
    _recipient_whatsapp_message,
    _sender_email_subject,
    _sender_sms_message,
    _sender_whatsapp_message,
)
from app.events.handlers.voucher.voucher_redeemed import (
    VoucherRedeemedHandler,
    _build_redeemed_context,
    _redeemed_email_subject,
    _redeemed_sms_message,
    _redeemed_whatsapp_message,
)
from app.events.registry.handler_registry import build_default_registry


# ── Fixtures ───────────────────────────────────────────────────────────────────

SAMPLE_VOUCHER_DATA = {
    "id": "b20e155d-6070-49c4-bdf2-e1eb2b6b15f1",
    "status": "active",
    "public_token": "9cyHTytaEmgeaoARLjqy_Cr5rh9QQkBAEScnD6fpoxc",
    "secret_code": "655085",
    "service_id": "102e43d5-5018-4baf-8cfa-a4658167d91e",
    "service_data": {"name": "Deep Tissue Massage"},
    "branch_id": "a1b2c3d4-0000-0000-0000-000000000001",
    "branch_data": {"name": "USHSPA Kuwait City"},
    "expire_date": "2026-11-02T23:45:46.948155+00:00",
    "gift_message": "Enjoy your well-deserved break!",
    "total_amount": "45.000",
    "total_duration": "60",
    "sender_details": {"name": "K Md Mamunur Rashid", "phone_number": "+96541028983", "email": "sender@example.com"},
    "recipient_phone": "+96541028985",
    "recipient_details": {"name": "Alayna", "email": "alayna@example.com", "phone_number": "+96541028985"},
    "payment_id": "100624710000000255",
    "payment_url": "https://demo.myfatoorah.com/pay/123",
    "payment_data": {"invoiceId": "7145839", "isPaid": True},
}

SAMPLE_REDEEMED_DATA = {
    **SAMPLE_VOUCHER_DATA,
    "status": "redeemed",
    "redeemed_booking": {
        "id": "aabb1234-0000-0000-0000-000000000001",
        "booking_reference": "BK-20260901-001",
        "appointment_date": "2026-10-15",
        "appointment_starttime": "14:00",
        "appointment_endtime": "15:00",
    },
}


# ── VoucherActiveHandler — unit tests ─────────────────────────────────────────

class TestVoucherContext:
    def test_build_context_extracts_all_fields(self):
        ctx = _build_voucher_context(SAMPLE_VOUCHER_DATA)
        assert ctx["voucher_id"] == "b20e155d-6070-49c4-bdf2-e1eb2b6b15f1"
        assert ctx["secret_code"] == "655085"
        assert ctx["sender_name"] == "K Md Mamunur Rashid"
        assert ctx["recipient_name"] == "Alayna"
        assert ctx["service_name"] == "Deep Tissue Massage"
        assert ctx["branch_name"] == "USHSPA Kuwait City"
        assert ctx["total_amount"] == "45.000"
        assert "Nov 2026" in ctx["expire_date"]

    def test_build_context_gift_card_url_includes_public_token(self):
        ctx = _build_voucher_context(SAMPLE_VOUCHER_DATA)
        assert "9cyHTytaEmgeaoARLjqy_Cr5rh9QQkBAEScnD6fpoxc" in ctx["gift_card_url"]

    def test_build_context_fallback_for_missing_fields(self):
        ctx = _build_voucher_context({})
        assert ctx["sender_name"] == "A generous friend"
        assert ctx["recipient_name"] == "Valued Customer"
        assert ctx["service_name"] == "Spa Experience"

    def test_fmt_expire_formats_iso_date(self):
        assert _fmt_expire("2026-11-02T23:45:46+00:00") == "02 Nov 2026"

    def test_fmt_expire_returns_original_on_error(self):
        assert _fmt_expire("bad-date") == "bad-date"


class TestSenderMessages:
    def setup_method(self):
        self.ctx = _build_voucher_context(SAMPLE_VOUCHER_DATA)

    def test_whatsapp_message_contains_recipient_name(self):
        msg = _sender_whatsapp_message(self.ctx)
        assert "Alayna" in msg
        assert "Gift Sent Successfully" in msg

    def test_sms_message_is_short_and_contains_key_info(self):
        msg = _sender_sms_message(self.ctx)
        assert "USHSPA" in msg
        assert "Alayna" in msg
        assert "45.000 KWD" in msg

    def test_email_subject_contains_recipient(self):
        subject = _sender_email_subject(self.ctx)
        assert "Alayna" in subject


class TestRecipientMessages:
    def setup_method(self):
        self.ctx = _build_voucher_context(SAMPLE_VOUCHER_DATA)

    def test_whatsapp_message_contains_secret_code(self):
        msg = _recipient_whatsapp_message(self.ctx)
        assert "655085" in msg
        assert "K Md Mamunur Rashid" in msg

    def test_whatsapp_message_contains_gift_card_url(self):
        msg = _recipient_whatsapp_message(self.ctx)
        assert "ushspa.co" in msg

    def test_sms_message_contains_secret_code(self):
        msg = _recipient_sms_message(self.ctx)
        assert "655085" in msg

    def test_email_subject_contains_sender(self):
        subject = _recipient_email_subject(self.ctx)
        assert "K Md Mamunur Rashid" in subject


# ── VoucherRedeemedHandler — unit tests ───────────────────────────────────────

class TestRedeemedContext:
    def test_build_context_extracts_booking_fields(self):
        ctx = _build_redeemed_context(SAMPLE_REDEEMED_DATA)
        assert ctx["booking_reference"] == "BK-20260901-001"
        assert ctx["appointment_date"] == "2026-10-15"
        assert ctx["appointment_time"] == "14:00"
        assert ctx["appointment_end_time"] == "15:00"
        assert ctx["sender_name"] == "K Md Mamunur Rashid"
        assert ctx["recipient_name"] == "Alayna"

    def test_build_context_fallback_no_booking_data(self):
        ctx = _build_redeemed_context({**SAMPLE_REDEEMED_DATA, "redeemed_booking": None})
        assert ctx["appointment_date"] == ""


class TestRedeemedMessages:
    def setup_method(self):
        self.ctx = _build_redeemed_context(SAMPLE_REDEEMED_DATA)

    def test_recipient_whatsapp_message(self):
        msg = _redeemed_whatsapp_message(self.ctx, "Alayna", "recipient")
        assert "Gift Redeemed" in msg
        assert "2026-10-15" in msg
        assert "14:00" in msg

    def test_sender_whatsapp_message(self):
        msg = _redeemed_whatsapp_message(self.ctx, "K Md Mamunur Rashid", "sender")
        assert "Alayna" in msg
        assert "Gift Redeemed" in msg

    def test_recipient_sms(self):
        msg = _redeemed_sms_message(self.ctx, "recipient")
        assert "USHSPA" in msg
        assert "Deep Tissue Massage" in msg

    def test_sender_sms(self):
        msg = _redeemed_sms_message(self.ctx, "sender")
        assert "USHSPA" in msg
        assert "Alayna" in msg

    def test_recipient_email_subject(self):
        subject = _redeemed_email_subject(self.ctx, "recipient")
        assert "Confirmed" in subject

    def test_sender_email_subject(self):
        subject = _redeemed_email_subject(self.ctx, "sender")
        assert "Alayna" in subject
        assert "Redeemed" in subject


# ── Registry registration ─────────────────────────────────────────────────────

class TestRegistry:
    def test_voucher_handlers_registered(self):
        registry = build_default_registry()
        event_types = registry.registered_event_types()
        assert "voucher.active" in event_types
        assert "voucher.redeemed" in event_types
        # underscore aliases
        assert "voucher_active" in event_types
        assert "voucher_redeemed" in event_types

    def test_voucher_active_handler_resolves(self):
        registry = build_default_registry()
        handlers = registry.resolve("voucher.active")
        assert len(handlers) == 1
        assert isinstance(handlers[0], VoucherActiveHandler)

    def test_voucher_redeemed_handler_resolves(self):
        registry = build_default_registry()
        handlers = registry.resolve("voucher.redeemed")
        assert len(handlers) == 1
        assert isinstance(handlers[0], VoucherRedeemedHandler)
