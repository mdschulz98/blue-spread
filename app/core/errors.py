"""Domain errors, and handlers rendering every error as ``{"error": {code, message, details}}``."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors raised by services and dependencies; mapped 1:1 to a response."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details
        self.headers = dict(headers or {})
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    message = "Bad request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Not authenticated"

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message, code=code, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Request conflicts with the current state of the resource"


class PreconditionFailedError(AppError):
    status_code = 412
    code = "precondition_failed"
    message = "The If-Match version does not match a known version of this resource"


class PreconditionRequiredError(AppError):
    status_code = 428
    code = "precondition_required"
    message = "This request requires an If-Match header"


class UnprocessableError(AppError):
    status_code = 422
    code = "validation_error"
    message = "Request validation failed"


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": jsonable_encoder(details)}}


async def _app_error_handler(_: Request, raw: Exception) -> JSONResponse:
    exc = cast(AppError, raw)
    return JSONResponse(
        error_body(exc.code, exc.message, exc.details),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _http_exception_handler(_: Request, raw: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, raw)
    try:
        phrase = HTTPStatus(exc.status_code).phrase
    except ValueError:
        phrase = "error"
    code = phrase.lower().replace(" ", "_").replace("-", "_")
    message = exc.detail if isinstance(exc.detail, str) else phrase
    details = None if isinstance(exc.detail, str) else exc.detail
    return JSONResponse(
        error_body(code, message, details),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _validation_error_handler(_: Request, raw: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, raw)
    # Deliberately omit "input" (could echo passwords) and "ctx" (may hold exception objects).
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return JSONResponse(
        error_body("validation_error", "Request validation failed", errors),
        status_code=422,
    )


async def _integrity_error_handler(_: Request, raw: Exception) -> JSONResponse:
    exc = cast(IntegrityError, raw)
    logger.info("integrity error", extra={"db_error": str(exc.orig)})
    constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    return JSONResponse(
        error_body(
            "conflict",
            "The request conflicts with existing data (e.g. a duplicate unique value)",
            {"constraint": constraint} if constraint else None,
        ),
        status_code=409,
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(IntegrityError, _integrity_error_handler)
    # Unhandled exceptions are rendered by RequestContextMiddleware (see app.core.middleware).
