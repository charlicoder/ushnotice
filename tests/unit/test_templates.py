"""
Unit tests for Jinja2 template rendering (EN and AR).
"""
from __future__ import annotations

from app.templates.renderer import TemplateRenderer


def test_render_otp_template_en() -> None:
    renderer = TemplateRenderer()
    result = renderer.render(
        "user/otp",
        lang="en",
        context={"otp": "987654", "expiry_minutes": 5},
    )
    assert "987654" in result
    assert "5 minutes" in result


def test_render_otp_template_ar() -> None:
    renderer = TemplateRenderer()
    result = renderer.render(
        "user/otp",
        lang="ar",
        context={"otp": "987654", "expiry_minutes": 5},
    )
    assert "987654" in result
    assert "رمز التحقق" in result


def test_render_booking_confirmed_en() -> None:
    renderer = TemplateRenderer()
    result = renderer.render(
        "booking/confirmed",
        lang="en",
        context={
            "customer_name": "Sarah",
            "booking_reference": "USH-1001",
            "branch_name": "Salmiya",
            "service_name": "Deep Tissue Massage",
            "appointment_date": "2026-09-01",
            "appointment_time": "14:00",
            "amount": "45.000",
        },
    )
    assert "Sarah" in result
    assert "USH-1001" in result
    assert "Salmiya" in result
    assert "45.000" in result


def test_render_booking_confirmed_sms_en_and_ar() -> None:
    renderer = TemplateRenderer()
    ctx = {
        "customer_name": "K Md Mamunur Rashid",
        "booking_reference": "REF-1234",
        "appointment_date": "2026-08-26",
        "appointment_time": "11:00",
    }
    result_en = renderer.render("booking/confirmed_sms", lang="en", context=ctx)
    assert "K" in result_en
    assert "CONFIRMED" in result_en
    assert "REF-1234" in result_en

    result_ar = renderer.render("booking/confirmed_sms", lang="ar", context=ctx)
    assert "تم تأكيد حجزك" in result_ar
    assert "REF-1234" in result_ar


def test_render_booking_confirmed_whatsapp_en_and_ar() -> None:
    renderer = TemplateRenderer()
    ctx = {
        "customer_name": "Mamun",
        "booking_reference": "REF-1234",
        "appointment_date": "2026-08-26",
        "appointment_time": "11:00",
        "appointment_end_time": "12:30",
        "service_name": "Swedish Massage",
        "therapist_name": "Fatima",
        "total_amount": "42.000",
        "currency": "KWD",
    }
    result_en = renderer.render("booking/confirmed_whatsapp", lang="en", context=ctx)
    assert "Booking Confirmed" in result_en
    assert "Swedish Massage" in result_en
    assert "Fatima" in result_en
    assert "42.000 KWD" in result_en

    result_ar = renderer.render("booking/confirmed_whatsapp", lang="ar", context=ctx)
    assert "تم تأكيد الحجز" in result_ar
    assert "Fatima" in result_ar


def test_render_booking_confirmed_email_en_and_ar() -> None:
    renderer = TemplateRenderer()
    ctx = {
        "customer_name": "Mamun",
        "booking_reference": "REF-1234",
        "appointment_date": "2026-08-26",
        "appointment_time": "11:00",
        "appointment_end_time": "12:30",
        "service_name": "Swedish Massage",
        "total_amount": "42.000",
    }
    result_en = renderer.render("booking/confirmed_email", lang="en", context=ctx, is_html=True)
    assert "Booking Confirmed" in result_en
    assert "REF-1234" in result_en

    result_ar = renderer.render("booking/confirmed_email", lang="ar", context=ctx, is_html=True)
    assert "تم تأكيد الحجز" in result_ar

