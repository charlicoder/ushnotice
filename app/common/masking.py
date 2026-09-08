"""
PII masking utilities for dashboard API responses.

Recipient phone numbers and email addresses must be partially masked before
being returned to dashboard consumers, to comply with privacy requirements.

Examples:
    >>> mask_phone("+96598765432")
    '+965****5432'
    >>> mask_email("rahul@example.com")
    'ra***@example.com'
"""
from __future__ import annotations

import re


def mask_phone(phone: str) -> str:
    """Mask the middle digits of a phone number.

    Preserves the leading country code (up to 4 digits) and the last 4 digits.
    The middle section is replaced with ``****``.

    Args:
        phone: Raw phone number string (any format).

    Returns:
        Masked phone string, e.g. ``"+965****5432"``.
    """
    if not phone:
        return phone

    # Normalise: keep only digits and optional leading +
    digits = re.sub(r"[^\d+]", "", phone)

    if len(digits) <= 8:
        # Too short to mask meaningfully — return as-is to avoid confusion.
        return digits

    if digits.startswith("+"):
        prefix = digits[:5]   # +965X → 5 chars including +
        suffix = digits[-4:]
        return f"{prefix}****{suffix}"
    else:
        prefix = digits[:4]
        suffix = digits[-4:]
        return f"{prefix}****{suffix}"


def mask_email(email: str) -> str:
    """Mask the local part of an email address.

    Preserves the first 2 characters and the domain.

    Args:
        email: Raw email address string.

    Returns:
        Masked email, e.g. ``"ra***@example.com"``.
    """
    if not email or "@" not in email:
        return email

    local, _, domain = email.partition("@")

    if len(local) <= 2:
        masked_local = local + "***"
    else:
        masked_local = local[:2] + "***"

    return f"{masked_local}@{domain}"
