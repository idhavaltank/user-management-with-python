"""Helpers for constructing the API's consistent HTTP error payloads."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def api_error(status_code: int, code: str, message: str, details: Any = None) -> HTTPException:
    """Create a FastAPI exception using the contract's error field names."""

    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    headers = {"WWW-Authenticate": "Session"} if status_code == 401 else None
    return HTTPException(status_code=status_code, detail=payload, headers=headers)
