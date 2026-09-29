"""
Unit tests for customer language_preference routing across all SMS and WhatsApp notifications.
"""
from __future__ import annotations

import re
from typing import Any

from app.notifications.application.channel_resolver import ChannelResolver
from app.notifications.domain.enums import NotificationChannel
from app.notifications.domain.value_objects import Recipient
from app.templates.renderer import TemplateRenderer


def _has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))


def test_channel_resolver_language_extraction() -> None:
    # 1. Top-level keys
    assert ChannelResolver.extract_language({"language_preference": "ar"}) == "ar"
    assert ChannelResolver.extract_language({"language_preference": "en"}) == "en"
    assert ChannelResolver.extract_language({"language_preference": "AR"}) == "ar"
    assert ChannelResolver.extract_language({"language_preference": "  ar  "}) == "ar"
    assert ChannelResolver.extract_language({"language_preference": "ar-kw"}) == "ar"
    assert ChannelResolver.extract_language({"language_preference": "arabic"}) == "ar"

    assert ChannelResolver.extract_language({"language": "ar"}) == "ar"
    assert ChannelResolver.extract_language({"preferred_language": "ar"}) == "ar"
    assert ChannelResolver.extract_language({"customer_language": "ar"}) == "ar"
    assert ChannelResolver.extract_language({"recipient_language": "ar"}) == "ar"
    assert ChannelResolver.extract_language({"sender_language": "ar"}) == "ar"
    assert ChannelResolver.extract_language({"lang": "ar"}) == "ar"

    # 2. Nested dictionary keys
    assert ChannelResolver.extract_language({"customer": {"language_preference": "ar"}}) == "ar"
    assert ChannelResolver.extract_language({"customer_data": {"language_preference": "ar"}}) == "ar"
    assert ChannelResolver.extract_language({"user": {"language_preference": "ar"}}) == "ar"
    assert ChannelResolver.extract_language({"recipient_details": {"language_preference": "ar"}}) == "ar"
    assert ChannelResolver.extract_language({"sender_details": {"language_preference": "ar"}}) == "ar"
    assert ChannelResolver.extract_language({"recipient": {"language_preference": "ar"}}) == "ar"

    # 3. Default fallback
    assert ChannelResolver.extract_language({}) == "en"
    assert ChannelResolver.extract_language({}, default_lang="ar") == "ar"
    assert ChannelResolver.extract_language({"language_preference": "unknown"}) == "en"


def test_recipient_language_sanitization() -> None:
    r_ar = Recipient(channel=NotificationChannel.SMS, address="+96512345678", language="ar")
    assert r_ar.language == "ar"

    r_ar_caps = Recipient(channel=NotificationChannel.SMS, address="+96512345678", language="AR")
    assert r_ar_caps.language == "ar"

    r_ar_full = Recipient(channel=NotificationChannel.SMS, address="+96512345678", language="arabic")
    assert r_ar_full.language == "ar"

    r_en = Recipient(channel=NotificationChannel.SMS, address="+96512345678", language="en")
    assert r_en.language == "en"

    r_default = Recipient(channel=NotificationChannel.SMS, address="+96512345678", language="other")
    assert r_default.language == "en"


def test_all_sms_and_whatsapp_templates_render_both_languages() -> None:
    renderer = TemplateRenderer()

    test_templates = [
        # User templates
        ("user/customer_new_created", {"customer_name": "Rashed", "password": "pass"}),
        ("user/otp", {"customer_name": "Rashed", "otp": "123456", "expiry_minutes": 5}),
        ("user/password_changed", {"customer_name": "Rashed"}),
        ("user/password_reset", {"customer_name": "Rashed", "otp": "123456", "expiry_minutes": 15}),
        ("user/verified", {"customer_name": "Rashed"}),
        ("user/email_verify", {"customer_name": "Rashed", "otp": "123456"}),
        # Booking templates
        (
            "booking/confirmed_sms",
            {
                "customer_name": "Rashed",
                "appointment_date": "2026-09-30",
                "appointment_time": "10:00 AM",
                "booking_reference": "BK-123",
                "booking_number": "BK-123",
            },
        ),
        (
            "booking/confirmed_whatsapp",
            {
                "customer_name": "Rashed",
                "appointment_date": "2026-09-30",
                "appointment_time": "10:00 AM",
                "booking_reference": "BK-123",
                "service_name": "Massage",
                "branch_name": "Kuwait City",
            },
        ),
        (
            "booking/confirmed_pending_payment_sms",
            {
                "customer_name": "Rashed",
                "appointment_date": "2026-09-30",
                "appointment_time": "10:00 AM",
                "total_amount": "25.000",
                "payment_link": "https://pay.ushspa.co/123",
            },
        ),
        (
            "booking/confirmed_pending_payment_whatsapp",
            {
                "customer_name": "Rashed",
                "appointment_date": "2026-09-30",
                "appointment_time": "10:00 AM",
                "total_amount": "25.000",
                "payment_link": "https://pay.ushspa.co/123",
                "service_name": "Massage",
                "branch_name": "Kuwait City",
            },
        ),
        (
            "booking/payment_failed",
            {
                "customer_name": "Rashed",
                "booking_reference": "BK-123",
                "branch_name": "Kuwait City",
                "appointment_date": "2026-09-30",
                "appointment_time": "10:00 AM",
            },
        ),
        (
            "booking/payment_pending",
            {
                "customer_name": "Rashed",
                "booking_reference": "BK-123",
                "branch_name": "Kuwait City",
                "appointment_date": "2026-09-30",
                "appointment_time": "10:00 AM",
            },
        ),
        ("booking/reschedule_customer", {"customer_name": "Rashed", "booking_reference": "BK-123"}),
        # Shop templates
        (
            "shop/order_created_sms",
            {
                "order_number": "ORD-123",
                "total_amount": "45.000",
                "currency": "KWD",
                "tracking_url": "https://track.ushspa.co/123",
                "tracking_code": "TRK-123",
            },
        ),
        (
            "shop/order_created_whatsapp",
            {
                "customer_name": "Rashed",
                "order_number": "ORD-123",
                "total_amount": "45.000",
                "currency": "KWD",
                "tracking_url": "https://track.ushspa.co/123",
                "tracking_code": "TRK-123",
            },
        ),
        # Gifts templates
        ("gifts/claimed_sender_whatsapp", {"message_body": "🎁 تم فتح هديتك!"}),
        ("gifts/delivered_recipient_whatsapp", {"message_body": "📦 تم توصيل هديتك!"}),
        ("gifts/purchase_recipient_whatsapp", {"message_body": "🎁 لقد استلمت هدية!"}),
        ("gifts/purchase_sender_whatsapp", {"message_body": "🎁 تم إرسال الهدية بنجاح!"}),
        ("gifts/redeemed_recipient_whatsapp", {"message_body": "تم استخدام هديتك!"}),
        ("gifts/redeemed_sender_whatsapp", {"message_body": "🎁 تم استخدام الهدية!"}),
        # Voucher templates
        (
            "voucher/active_recipient_sms",
            {
                "message_body": "USHSPA: مرحباً، لقد استلمت هدية!",
                "recipient_name": "Rashed",
                "gift_from": "Sara",
                "gift_message": "Happy Birthday",
                "gift_card_url": "https://ushspa.co/gift/123",
            },
        ),
        ("voucher/active_recipient_whatsapp", {"message_body": "🎁 لقد استلمت هدية!"}),
        ("voucher/active_sender_sms", {"message_body": "USHSPA: مرحباً، تم إرسال هديتك!"}),
        ("voucher/active_sender_whatsapp", {"message_body": "🎁 تم إرسال الهدية بنجاح!"}),
        ("voucher/redeemed_recipient_sms", {"message_body": "USHSPA: مرحباً، تم تأكيد حجز هديتك!"}),
        ("voucher/redeemed_recipient_whatsapp", {"message_body": "✅ تم استخدام الهدية"}),
        ("voucher/redeemed_sender_sms", {"message_body": "USHSPA: مرحباً، تم استخدام هديتك!"}),
        ("voucher/redeemed_sender_whatsapp", {"message_body": "✅ تم استخدام الهدية"}),
    ]

    for tpl, ctx in test_templates:
        en_out = renderer.render(tpl, lang="en", context=ctx)
        ar_out = renderer.render(tpl, lang="ar", context=ctx)

        assert en_out.strip(), f"Empty EN output for {tpl}"
        assert ar_out.strip(), f"Empty AR output for {tpl}"
        assert _has_arabic(ar_out), f"AR output for {tpl} contains no Arabic characters: {ar_out!r}"


def test_customer_new_created_template_content() -> None:
    renderer = TemplateRenderer()
    ctx = {"customer_name": "Rashed", "password": "secret_password"}

    # English check
    en_body = renderer.render("user/customer_new_created", lang="en", context=ctx)
    assert "Welcome to USH Spa, Rashed! 🌿" in en_body
    assert "Your password is: secret_password" in en_body
    assert "App Store: https://apps.apple.com/kw/app/ushspa/id6771279814" in en_body
    assert "- USH Spa Team" in en_body
    assert "Website: https://ushspa.co/" not in en_body

    # Arabic check
    ar_body = renderer.render("user/customer_new_created", lang="ar", context=ctx)
    assert "أهلاً بك في USH Spa، Rashed! 🌿" in ar_body
    assert "كلمة المرور الخاصة بك: secret_password" in ar_body
    assert "App Store: https://apps.apple.com/kw/app/ushspa/id6771279814" in ar_body
    assert "- فريق USH Spa" in ar_body
    assert "https://ushspa.co/" not in ar_body
