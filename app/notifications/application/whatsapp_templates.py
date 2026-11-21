"""
WhatsApp Business API template component builders for USH SPA.

Each function builds the ``components`` list that is passed to the Meta
Cloud API template message payload for a specific approved template.

Template registry (verified against Meta Graph API)
----------------------------------------------------
+----------------------------------+---------------------+-------------------------------+
| Template name                    | Template ID         | Trigger                       |
+==================================+=====================+===============================+
| ush_gift_voucher_notification    | 3303773023154347    | Gift voucher sent to recipient|
|   Body: {{1}} recipient_name     |                     |                               |
|         {{2}} sender_name        |                     |                               |
|         {{3}} secret_code        |                     |                               |
|   Button[0]: {{1}} public_token  |                     |                               |
+----------------------------------+---------------------+-------------------------------+
| ush_gitf_booking_confirmation    | 29463619926566333   | Booking confirmed (voucher    |
|                                  |                     | redeemed)                     |
+----------------------------------+---------------------+-------------------------------+

Parameter ordering follows the approved template definition in Meta Business
Manager.  If a template is updated in Meta, update the corresponding builder
below to match the new parameter positions.
"""
from __future__ import annotations

from app.core.config import get_settings

# ── Template names (constants) ────────────────────────────────────────────────

# Sent to the RECIPIENT when a gift voucher is created by a sender.
TEMPLATE_GIFT_VOUCHER_NOTIFICATION = "ush_gift_voucher_notification"
TEMPLATE_GIFT_VOUCHER_NOTIFICATION_ID = "3303773023154347"

# Sent to the RECIPIENT when a gift voucher booking is confirmed (redeemed).
TEMPLATE_GIFT_BOOKING_CONFIRMATION = "ush_gitf_booking_confirmation"
TEMPLATE_GIFT_BOOKING_CONFIRMATION_ID = "29463619926566333"


def _text_param(value: str) -> dict:
    """Shorthand for a single text-type body parameter."""
    return {"type": "text", "text": str(value or "").strip()}


# ── Template 1: ush_gift_voucher_notification ─────────────────────────────────

def build_gift_voucher_notification_components(gctx: dict) -> list[dict]:
    """Build Meta template components for ``ush_gift_voucher_notification``.

    Sent to the gift **recipient** when a sender purchases and sends a voucher.

    Approved template components in Meta:
        1. Header (format: IMAGE) -> requires image link parameter
        2. Body (3 text parameters):
            {{1}} recipient_name  — name of the gift recipient
            {{2}} sender_name     — name of the person who sent the gift
            {{3}} secret_code     — voucher redemption code
        3. Button (URL type, index 0):
            {{1}} public_token    — dynamic URL suffix for https://ushwellness.com/gift/

    Args:
        gctx: Gift context dict built by ``_build_gift_context()`` in
              ``gift_purchase_completed.py`` or ``_build_voucher_context()``
              in ``voucher_active.py``.
    """
    try:
        settings = get_settings()
        default_img = getattr(settings, "USH_GIFT_VOUCHER_HEADER_IMAGE", "")
    except Exception:
        default_img = ""

    header_img = (
        gctx.get("header_image_url")
        or gctx.get("image")
        or default_img
        or "https://ushspa.co/images/bg-01.jpg"
    )

    body_params = [
        _text_param(gctx.get("recipient_name") or "Valued Customer"),
        _text_param(gctx.get("sender_name") or "A generous friend"),
        _text_param(gctx.get("secret_code") or ""),
    ]

    public_token = gctx.get("public_token") or ""
    gift_url = gctx.get("gift_card_url") or ""
    token_suffix = public_token or (gift_url.rstrip("/").split("/")[-1] if gift_url else "gift")

    return [
        {
            "type": "header",
            "parameters": [
                {
                    "type": "image",
                    "image": {
                        "link": header_img,
                    },
                }
            ],
        },
        {
            "type": "body",
            "parameters": body_params,
        },
        {
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [
                {
                    "type": "text",
                    "text": token_suffix,
                }
            ],
        },
    ]


# ── Template 2: ush_gitf_booking_confirmation ─────────────────────────────────

def build_gift_booking_confirmation_components(vctx: dict) -> list[dict]:
    """Build Meta template components for ``ush_gitf_booking_confirmation``.

    Sent to the gift **recipient** (and optionally sender) when a voucher
    booking appointment is confirmed.

    Approved template body (5 parameters in order):
        1. recipient_name     — name of the person who redeemed the voucher
        2. service_name       — spa service(s) booked
        3. appointment_date   — date of the appointment (e.g. "21 Nov 2026")
        4. appointment_time   — start time (e.g. "10:00 AM")
        5. booking_number     — booking reference number (e.g. BOK/2026/11/000284)

    Args:
        vctx: Voucher-redeemed context dict built by ``_build_redeemed_context()``
              in ``voucher_redeemed.py``.
    """
    # Format date nicely if it is a raw ISO date
    appt_date = vctx.get("appointment_date") or ""
    try:
        from datetime import datetime as _dt
        if appt_date and len(appt_date) == 10:
            appt_date = _dt.strptime(appt_date, "%Y-%m-%d").strftime("%d %b %Y")
    except Exception:
        pass

    # Format time nicely (strip seconds if present)
    appt_time = vctx.get("appointment_time") or ""
    try:
        from datetime import datetime as _dt
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                appt_time = _dt.strptime(appt_time[:8], fmt).strftime("%I:%M %p").lstrip("0")
                break
            except Exception:
                continue
    except Exception:
        pass

    body_params = [
        _text_param(vctx.get("recipient_name") or vctx.get("username") or "Valued Customer"),
        _text_param(vctx.get("service_name") or "Spa Experience"),
        _text_param(appt_date),
        _text_param(appt_time),
        _text_param(vctx.get("booking_number") or ""),
    ]

    return [
        {
            "type": "body",
            "parameters": body_params,
        }
    ]
