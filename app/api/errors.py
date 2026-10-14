"""Turn exceptions into one consistent JSON error shape.

Every error response looks like:

    {"error": {"code": "invalid_transition", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import (
    ConflictError,
    DomainError,
    InsufficientStockError,
    InvalidTransitionError,
    NoStationAvailableError,
    NotFoundError,
    PermissionDeniedError,
)

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: 404,
    PermissionDeniedError: 403,
    InvalidTransitionError: 409,
    InsufficientStockError: 409,
    NoStationAvailableError: 409,
    ConflictError: 409,
}


def error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": details or {}}}
    return JSONResponse(status_code=status, content=jsonable_encoder(body))


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status = next((code for cls, code in STATUS_BY_ERROR.items() if isinstance(exc, cls)), 400)
        return error_response(status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            422, "validation_error", "Request is invalid", {"errors": exc.errors()}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(OperationalError)
    async def db_error(_: Request, exc: OperationalError) -> JSONResponse:
        # Includes deadlocks and lock timeouts; the client can safely retry.
        return error_response(503, "database_unavailable", "Database error, please retry")
