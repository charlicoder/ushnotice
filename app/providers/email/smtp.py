"""
SMTP email provider using Python smtplib with asyncio.to_thread (and optional aiosmtplib).

Supports STARTTLS (port 587), SSL/TLS (port 465), or standard SMTP.
Configured via environment variables:
    EMAIL_ENABLED: Enable or disable email sending (default: True)
    SMTP_HOST: SMTP server hostname (e.g. smtp.gmail.com)
    SMTP_PORT: SMTP port (default: 587)
    SMTP_USERNAME: SMTP authentication username / email
    SMTP_PASSWORD: App password or SMTP password
    EMAIL_FROM_NAME: Display name for the sender (e.g. "USH SPA")
    EMAIL_FROM_ADDRESS: Sender email address
    SMTP_USE_TLS: Whether to use TLS/STARTTLS (default: True)
    SMTP_TIMEOUT: Connection timeout in seconds (default: 30.0)

Retryable: connection errors, timeouts, SMTP 4xx transient errors.
Non-retryable: SMTP 5xx permanent errors (invalid address, authentication failures).
"""
from __future__ import annotations

import asyncio
import smtplib
import ssl
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import get_settings
from app.core.logging import get_logger
from app.notifications.domain.value_objects import DeliveryResult

logger = get_logger(__name__)


class SmtpProvider:
    """Async email provider using SMTP with STARTTLS / SSL support."""

    provider_name: str = "smtp"

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_name: str,
        from_address: str,
        use_tls: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._username = username
        self._password = password
        self._from_name = from_name
        self._from_address = from_address
        self._use_tls = use_tls
        self._timeout = timeout

    @classmethod
    def from_settings(cls) -> "SmtpProvider":
        """Factory constructor that builds SmtpProvider from app settings."""
        settings = get_settings()
        return cls(
            smtp_host=settings.effective_smtp_host,
            smtp_port=settings.effective_smtp_port,
            username=settings.effective_smtp_username,
            password=settings.effective_smtp_password,
            from_name=settings.effective_email_from_name,
            from_address=settings.effective_email_from_address,
            use_tls=settings.SMTP_USE_TLS,
            timeout=settings.SMTP_TIMEOUT,
        )

    def _send_sync(self, msg: MIMEMultipart, to: str) -> None:
        """Synchronous SMTP delivery helper executed in worker thread."""
        is_port_465 = (self._smtp_port == 465)

        if is_port_465:
            context = ssl.create_default_context() if self._use_tls else None
            with smtplib.SMTP_SSL(
                host=self._smtp_host,
                port=self._smtp_port,
                context=context,
                timeout=self._timeout,
            ) as server:
                if self._username and self._password:
                    server.login(self._username, self._password)
                server.send_message(msg, to_addrs=[to])
        else:
            with smtplib.SMTP(
                host=self._smtp_host,
                port=self._smtp_port,
                timeout=self._timeout,
            ) as server:
                if self._use_tls:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                if self._username and self._password:
                    server.login(self._username, self._password)
                server.send_message(msg, to_addrs=[to])

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        reply_to: str | None = None,
    ) -> DeliveryResult:
        """Send an email via SMTP.

        Args:
            to: Recipient email address.
            subject: Email subject line.
            html_body: HTML message body.
            text_body: Plain-text fallback.
            reply_to: Optional Reply-To address.

        Returns:
            :class:`DeliveryResult`.
        """
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        if self._from_name:
            msg["From"] = f"{self._from_name} <{self._from_address}>"
        else:
            msg["From"] = self._from_address
        msg["To"] = to
        if reply_to:
            msg["Reply-To"] = reply_to

        if text_body:
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body:
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        start = time.monotonic()
        try:
            await asyncio.to_thread(self._send_sync, msg, to)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            message_id = str(uuid.uuid4())
            logger.info(
                "SMTP email sent",
                provider=self.provider_name,
                to=to,
                subject=subject,
                latency_ms=elapsed_ms,
                message_id=message_id,
            )
            return DeliveryResult.ok(
                provider_message_id=message_id,
                raw_response={
                    "smtp_host": self._smtp_host,
                    "smtp_port": self._smtp_port,
                    "to": to,
                    "subject": subject,
                },
            )

        except smtplib.SMTPResponseException as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            code = getattr(exc, "smtp_code", 0) or getattr(exc, "code", 0) or 0
            is_retryable = 400 <= code < 500
            error_msg = str(exc.smtp_error.decode("utf-8", errors="replace")) if hasattr(exc, "smtp_error") and isinstance(exc.smtp_error, bytes) else str(exc)
            logger.warning(
                "SMTP server response error",
                error=error_msg,
                smtp_code=code,
                is_retryable=is_retryable,
                provider=self.provider_name,
                to=to,
            )
            return DeliveryResult.fail(
                error_code=f"SMTP_{code}" if code else "SMTP_ERROR",
                error_message=error_msg,
                is_retryable=is_retryable,
            )
        except smtplib.SMTPException as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "SMTP protocol error",
                error=str(exc),
                is_retryable=True,
                provider=self.provider_name,
                to=to,
            )
            return DeliveryResult.fail(
                error_code="SMTP_ERROR",
                error_message=str(exc),
                is_retryable=True,
            )
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "SMTP unexpected error",
                error=str(exc),
                provider=self.provider_name,
                to=to,
            )
            return DeliveryResult.fail(
                error_code="UNEXPECTED",
                error_message=str(exc),
                is_retryable=True,
            )
