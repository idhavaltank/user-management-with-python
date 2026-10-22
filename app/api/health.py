"""Liveness and dependency-readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.generated.schemas import HealthResponse, ReadinessResponse

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, operation_id="getHealth")
async def get_health(request: Request) -> HealthResponse:
    """Report that the Python process is running without requiring PostgreSQL."""

    request.app.state.telemetry.info(
        "health.check.succeeded",
        {"requestId": request.state.request_id},
    )
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse, operation_id="getReadiness")
async def get_readiness(request: Request) -> ReadinessResponse:
    """Report whether the API can reach its required PostgreSQL dependency."""

    database_ready = await request.app.state.database_check()
    if not database_ready:
        request.app.state.telemetry.warning(
            "readiness.check.failed",
            {"requestId": request.state.request_id, "dependency": "postgresql"},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "dependency_unavailable",
                "message": "PostgreSQL is unavailable.",
            },
        )

    request.app.state.telemetry.info(
        "readiness.check.succeeded",
        {"requestId": request.state.request_id},
    )
    return ReadinessResponse(status="ready", database="ok")
