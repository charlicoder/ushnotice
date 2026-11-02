"""
Security utilities for the ushnotice service.

Responsibilities:
- Inject the ``USHSPA_TOKEN`` header into all outbound inter-service requests.
- Validate the ``USHSPA_TOKEN`` on inbound internal dashboard requests.
- Provide a FastAPI dependency for employee/admin authorisation.

The ``USHSPA_TOKEN`` is treated as a shared application secret.
It is loaded from :class:`~app.core.config.Settings` and NEVER logged.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

# Header name for the inter-service application token.
_TOKEN_HEADER = "USHSPA-TOKEN"

_api_key_scheme = APIKeyHeader(name=_TOKEN_HEADER, auto_error=False)


def get_ushspa_token() -> str:
    """Return the plaintext USHSPA_TOKEN for outbound request injection.

    This value must never appear in log output.
    """
    return get_settings().USHSPA_TOKEN.get_secret_value()


async def require_service_token(
    request: Request,
    api_key: Annotated[str | None, Depends(_api_key_scheme)] = None,
) -> None:
    """FastAPI dependency that validates the ``USHSPA-TOKEN`` header.

    Use on dashboard and internal API routes that must not be accessible
    without a valid application token.

    Raises:
        HTTPException: 401 if the header is missing.
        HTTPException: 403 if the token is invalid.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="USHSPA-TOKEN header is required.",
            headers={"WWW-Authenticate": "APIKey"},
        )

    expected = get_settings().USHSPA_TOKEN.get_secret_value()

    # Constant-time comparison to prevent timing attacks.
    if not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid USHSPA-TOKEN.",
        )


# Type alias for clean FastAPI dependency injection.
RequireServiceAuth = Annotated[None, Depends(require_service_token)]


def verify_meta_wa_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Verify Meta WhatsApp Webhook X-Hub-Signature-256 header.

    Meta sends:
        X-Hub-Signature-256: sha256=<hex_hmac>
    where <hex_hmac> is the HMAC-SHA256 signature of the raw request payload
    using the Meta App Secret.

    Returns:
        True if signature matches (or if app_secret is not set), False otherwise.
    """
    if not app_secret:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    received_sig = signature_header.split("sha256=", 1)[1].strip()
    expected_sig = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return secrets.compare_digest(received_sig, expected_sig)

