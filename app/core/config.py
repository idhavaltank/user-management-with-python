"""Environment-backed application settings.

Keeping configuration in one Pydantic model gives local, test, staging, and
production processes the same validation rules while allowing secrets to be
injected through environment variables instead of source control.
"""

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration for the API process."""

    # General process identity and environment selection.
    app_name: str = "User Management API"
    environment: Literal["local", "test", "staging", "production"] = "local"
    # SQLAlchemy uses the asyncpg driver for PostgreSQL connections.
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/user_management"

    # Browser security settings: only configured frontend origins may use the
    # credentialed CORS path.
    frontend_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3002",
            "http://127.0.0.1:3002",
        ]
    )
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )
    trusted_proxy_ips: list[str] = Field(default_factory=list)

    # Session, correlation, and cookie policy settings.
    request_id_header: str = "X-Request-ID"
    session_cookie_name: str = "session_id"
    session_cookie_secure: bool = False
    session_cookie_same_site: Literal["lax", "strict", "none"] = "lax"
    session_ttl_seconds: int = Field(default=3600, ge=300, le=2_592_000)

    # Rate limits are local-memory by default and Redis-backed when configured.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=120, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_login_requests: int = Field(default=5, ge=1)
    redis_url: str | None = None
    # Optional local/test bootstrap. Production credentials must come from a
    # secret manager or another approved provisioning workflow.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "Administrator"
    bootstrap_admin_birth_date: date = date(1980, 1, 1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_production_cookie(self) -> "Settings":
        """Reject insecure cookie/rate-limit combinations before startup."""

        if self.environment == "production" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true in production")
        if self.session_cookie_same_site == "none" and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true when SameSite=None")
        if (
            self.environment in {"staging", "production"}
            and self.rate_limit_enabled
            and not self.redis_url
        ):
            raise ValueError("REDIS_URL is required for rate limiting outside local/test")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached settings object used by the application factory."""

    return Settings()
