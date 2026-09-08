"""
Unit tests for PII masking utilities.
"""
from __future__ import annotations

from app.common.masking import mask_email, mask_phone


def test_mask_phone() -> None:
    assert mask_phone("+96598765432") == "+9659****5432"
    assert mask_phone("96598765432") == "9659****5432"
    assert mask_phone("12345") == "12345"  # too short to mask


def test_mask_email() -> None:
    assert mask_email("rahul@example.com") == "ra***@example.com"
    assert mask_email("a@b.com") == "a***@b.com"
    assert mask_email("invalid-email") == "invalid-email"
