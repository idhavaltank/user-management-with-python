"""Fast, database-independent tests for middleware and error contracts."""

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def build_settings(**overrides) -> Settings:
    """Build safe test settings without reading a developer's local .env."""

    values = {
        "environment": "test",
        "database_url": "postgresql+asyncpg://user:password@localhost:5432/user_management",
        "trusted_hosts": ["testserver"],
        "rate_limit_window_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


def test_health_returns_ok_and_request_id() -> None:
    """Health must work without PostgreSQL and return a correlation ID."""

    async def database_check() -> bool:
        return True

    client = TestClient(create_app(build_settings(), database_check=database_check))
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]
    assert response.headers["X-RateLimit-Limit"] == "120"


def test_readiness_returns_dependency_error_when_database_is_unavailable() -> None:
    """Readiness must distinguish dependency failure from process liveness."""

    async def database_check() -> bool:
        return False

    client = TestClient(create_app(build_settings(), database_check=database_check))
    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.json()["requestId"] == response.headers["X-Request-ID"]


def test_rate_limit_returns_standard_headers_and_429() -> None:
    """The local limiter exposes remaining quota and a retry interval."""

    async def database_check() -> bool:
        return True

    settings = build_settings(rate_limit_requests=2)
    client = TestClient(create_app(settings, database_check=database_check))

    first = client.get("/health")
    second = client.get("/health")
    third = client.get("/health")

    assert first.headers["X-RateLimit-Remaining"] == "1"
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert third.status_code == 429
    assert third.headers["Retry-After"]
    assert third.json()["code"] == "rate_limit_exceeded"


def test_cors_allows_only_configured_frontend_origin() -> None:
    """Credentialed browser access is limited to configured frontend origins."""

    async def database_check() -> bool:
        return True

    settings = build_settings(frontend_origins=["http://localhost:3000"])
    client = TestClient(create_app(settings, database_check=database_check))

    allowed = client.get("/health", headers={"Origin": "http://localhost:3000"})
    denied = client.get("/health", headers={"Origin": "http://localhost:5173"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers


def test_not_found_uses_contract_error_shape() -> None:
    """Unknown routes use the same machine-readable error envelope as API errors."""

    async def database_check() -> bool:
        return True

    client = TestClient(create_app(build_settings(), database_check=database_check))
    response = client.get("/missing")

    assert response.status_code == 404
    assert set(response.json()) == {"code", "message", "requestId"}
