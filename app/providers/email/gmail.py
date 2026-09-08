"""
Gmail SMTP email provider using aiosmtplib.

Configuration:
    GMAIL_SMTP_HOST: smtp.gmail.com
    GMAIL_SMTP_PORT: 587 (STARTTLS)
    GMAIL_USERNAME: your-address@gmail.com
    GMAIL_APP_PASSWORD: App password (NOT your account password).
        Generate at: Google Account → Security → App passwords.
    GMAIL_FROM_NAME: "USHSPA"
    GMAIL_FROM_ADDRESS: your-address@gmail.com

Note: Gmail App Passwords require 2-Step Verification to be enabled.
For high volumes, consider switching to Gmail API or a dedicated provider.

Retryable: connection errors, SMTP 4xx transient errors.
Non-retryable: SMTP 5xx permanent errors (invalid address, etc.).
"""
from __future__ import annotations

import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)


from app.providers.email.smtp import SmtpProvider

# Alias for backwards compatibility
GmailProvider = SmtpProvider
