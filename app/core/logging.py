"""
Structured JSON logging configuration using structlog.

Binds request-scoped context (request_id, correlation_id, event_id, …) to
every log record so that entire notification workflows can be traced from a
single log query.

Never log: passwords, JWTs, USHSPA_TOKEN, AWS secrets, provider API keys,
OTP values, or sensitive payment data.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor


def _drop_color_message_key(
    logger: Any,  # noqa: ANN401
    method: str,
    event_dict: EventDict,
) -> EventDict:
    """Remove the ``color_message`` key injected by uvicorn's logger."""
    event_dict.pop("color_message", None)
    return event_dict


def _censor_secrets(
    logger: Any,  # noqa: ANN401
    method: str,
    event_dict: EventDict,
) -> EventDict:
    """Redact known-sensitive keys before any renderer sees them.

    This is a defence-in-depth measure.  Developers must still exercise care
    not to pass secret values as arguments, but this processor will catch the
    most common mistakes.
    """
    _FORBIDDEN = frozenset(
        {
            "password",
            "token",
            "ushspa_token",
            "secret",
            "api_key",
            "access_key",
            "secret_key",
            "otp",
            "pin",
            "authorization",
        }
    )
    for key in list(event_dict.keys()):
        if key.lower() in _FORBIDDEN or any(f in key.lower() for f in _FORBIDDEN):
            event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog for the application.

    Call this once at startup, before any logging occurs.

    Args:
        log_level: Root logging level (e.g. ``"DEBUG"``, ``"INFO"``).
        json_logs: When ``True`` (the default for production), emit JSON.
            Set to ``False`` for local development with coloured console output.
    """
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message_key,
        _censor_secrets,
    ]

    if json_logs:
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Silence overly verbose third-party loggers.
    for noisy in ("botocore", "boto3", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to *name*.

    Prefer using this over ``logging.getLogger`` throughout the codebase.
    """
    return structlog.get_logger(name)


def bind_request_context(
    *,
    request_id: str | None = None,
    correlation_id: str | None = None,
    event_id: str | None = None,
    notification_id: str | None = None,
    booking_id: str | None = None,
) -> None:
    """Bind per-request context variables so they appear in every log record.

    Must be called at the start of each request / SQS message processing cycle.
    Context is automatically cleared by structlog's contextvars machinery when
    the async task exits.
    """
    ctx: dict[str, str] = {}
    if request_id:
        ctx["request_id"] = request_id
    if correlation_id:
        ctx["correlation_id"] = correlation_id
    if event_id:
        ctx["event_id"] = event_id
    if notification_id:
        ctx["notification_id"] = notification_id
    if booking_id:
        ctx["booking_id"] = booking_id

    structlog.contextvars.bind_contextvars(**ctx)


def clear_request_context() -> None:
    """Clear all context variables bound for the current async context."""
    structlog.contextvars.clear_contextvars()
