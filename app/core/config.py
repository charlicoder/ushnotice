"""
Application configuration loaded from environment variables.

All settings are read via :class:`pydantic_settings.BaseSettings`.
Sensitive values (tokens, API keys, DB passwords) are never logged.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object for the ushnotice service.

    Values are sourced from environment variables.  An ``.env`` file in the
    project root is loaded automatically when ``python-dotenv`` is installed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "ushnotice"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    LOG_LEVEL: str = "INFO"

    # ── Database (PostgreSQL async) ───────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ushnotice",
        description="Async SQLAlchemy connection string.",
    )
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    DATABASE_POOL_TIMEOUT: int = 30

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = Field(
        default="redis://localhost:6379/2",
        description="Redis connection URL.",
    )
    REDIS_MAX_CONNECTIONS: int = 20
    REDIS_SOCKET_TIMEOUT: int = 5

    # ── Security ──────────────────────────────────────────────────────────────
    USHSPA_TOKEN: SecretStr = Field(
        description="Inter-service application token. NEVER log this value.",
    )

    # ── API Gateway ───────────────────────────────────────────────────────────
    API_GATEWAY_BASE_URL: str = "http://api.ushspa.local"
    USHAUTH_BASE_PATH: str = "/uauth"
    USHBOOKNPAY_BASE_PATH: str = "/booknpay"
    USHNOTICE_BASE_PATH: str = "/unotice"
    GATEWAY_TIMEOUT: float = 10.0

    # Public-facing base URL for shop order tracking pages sent to customers.
    # Format: https://ushspa.co/order-tracking  (no trailing slash)
    # The full tracking link will be: {USH_ORDER_TRACKING_BASE_URL}/{public_token}
    USH_ORDER_TRACKING_BASE_URL: str = ""

    # ── AWS ───────────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: SecretStr = SecretStr("")
    AWS_REGION: str = "ap-south-1"
    AWS_SESSION_TOKEN: str = ""

    # SQS
    AWS_SQS_NOTIFICATION_QUEUE_URL: str = ""
    AWS_SQS_NOTIFICATION_DLQ_URL: str = ""

    # SQS consumer tuning
    SQS_MAX_MESSAGES: int = Field(default=10, ge=1, le=10)
    SQS_WAIT_TIME_SECONDS: int = Field(default=20, ge=1, le=20)
    SQS_VISIBILITY_TIMEOUT: int = Field(default=60, ge=30, le=3600)
    SQS_MAX_CONCURRENCY: int = Field(default=5, ge=1, le=20)
    SQS_MAX_RETRIES: int = Field(default=3, ge=0)

    # ── SMS Provider (KWT SMS) ────────────────────────────────────────────────
    KWTSMS_USERNAME: str = ""
    KWTSMS_PASSWORD: SecretStr = SecretStr("")
    KWTSMS_SENDER: str = ""
    KWTSMS_SENDER_ID: str = ""
    KWTSMS_TEST_MODE: bool = False  # set True in non-prod to avoid charging credits

    # SMS Provider (Twilio — fallback)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: SecretStr = SecretStr("")
    TWILIO_SMS_FROM: str = ""

    # Active SMS provider name
    SMS_PROVIDER: Literal["kwtsms", "twilio", "stub"] = "kwtsms"

    # ── WhatsApp Provider (Meta Cloud API) ────────────────────────────────────
    META_WA_PHONE_NUMBER_ID: str = ""
    META_WA_ACCESS_TOKEN: SecretStr = SecretStr("")
    META_WA_API_VERSION: str = "v20.0"

    # Active WhatsApp provider name
    WHATSAPP_PROVIDER: Literal["meta", "stub"] = "meta"

    # ── Email Provider (SMTP / Gmail) ─────────────────────────────────────────
    EMAIL_ENABLED: bool = True

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    EMAIL_FROM_ADDRESS: str = ""
    EMAIL_FROM_NAME: str = "USH SPA"
    SMTP_USE_TLS: bool = True
    SMTP_TIMEOUT: float = 30.0

    # Backwards compatibility fields for legacy GMAIL_* config
    GMAIL_SMTP_HOST: str = ""
    GMAIL_SMTP_PORT: int = 587
    GMAIL_USERNAME: str = ""
    GMAIL_APP_PASSWORD: SecretStr = SecretStr("")
    GMAIL_FROM_NAME: str = ""
    GMAIL_FROM_ADDRESS: str = ""

    # Active email provider name
    EMAIL_PROVIDER: Literal["smtp", "gmail", "stub"] = "smtp"

    # ── Notification behaviour ────────────────────────────────────────────────
    DEFAULT_LANGUAGE: Literal["en", "ar"] = "en"
    NOTIFICATION_MAX_RETRIES: int = 3
    NOTIFICATION_RETRY_BASE_DELAY: float = 2.0  # seconds (exponential base)
    NOTIFICATION_RETRY_MAX_DELAY: float = 60.0

    # ── Rate limiting (Redis-backed) ──────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # ── Caching ───────────────────────────────────────────────────────────────
    BRANCH_CONTACT_CACHE_TTL: int = 300  # seconds

    # ── Statistics cache ──────────────────────────────────────────────────────
    STATS_CACHE_TTL: int = 60  # seconds

    # ─── Validators ──────────────────────────────────────────────────────────

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Ensure the DATABASE_URL uses the async asyncpg dialect."""
        if "postgresql://" in v and "asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def effective_kwtsms_sender_id(self) -> str:
        """Return the configured KWT SMS sender ID."""
        return self.KWTSMS_SENDER or self.KWTSMS_SENDER_ID or "USHSPA"

    @property
    def effective_smtp_host(self) -> str:
        """Return the configured SMTP host, falling back to legacy GMAIL_SMTP_HOST."""
        return self.SMTP_HOST or self.GMAIL_SMTP_HOST or "smtp.gmail.com"

    @property
    def effective_smtp_port(self) -> int:
        """Return the configured SMTP port."""
        return self.SMTP_PORT or self.GMAIL_SMTP_PORT or 587

    @property
    def effective_smtp_username(self) -> str:
        """Return the configured SMTP username."""
        return self.SMTP_USERNAME or self.GMAIL_USERNAME or ""

    @property
    def effective_smtp_password(self) -> str:
        """Return the configured SMTP password."""
        val = self.SMTP_PASSWORD.get_secret_value()
        if val:
            return val
        return self.GMAIL_APP_PASSWORD.get_secret_value()

    @property
    def effective_email_from_name(self) -> str:
        """Return the configured sender name for outgoing emails."""
        return self.EMAIL_FROM_NAME or self.GMAIL_FROM_NAME or "USH SPA"

    @property
    def effective_email_from_address(self) -> str:
        """Return the configured sender email address."""
        return self.EMAIL_FROM_ADDRESS or self.GMAIL_FROM_ADDRESS or self.effective_smtp_username

    @property
    def ushauth_base_url(self) -> str:
        """Full base URL for the ushauth service through the API gateway."""
        return f"{self.API_GATEWAY_BASE_URL.rstrip('/')}{self.USHAUTH_BASE_PATH}"

    @property
    def ushbooknpay_base_url(self) -> str:
        """Full base URL for the ushbooknpay service through the API gateway."""
        return f"{self.API_GATEWAY_BASE_URL.rstrip('/')}{self.USHBOOKNPAY_BASE_PATH}"

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton.

    Use :func:`get_settings` everywhere rather than importing ``Settings``
    directly so the cache can be invalidated in tests by calling
    ``get_settings.cache_clear()``.
    """
    return Settings()
