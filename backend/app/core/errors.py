"""Errors, and the one shape every error response has (docs/api.md §1):

    {"error": {"code": "not_found", "message": "…", "request_id": "…"}}

Code raises an `AppError` subclass; the handlers here turn it, FastAPI's own errors
and request-validation failures into that shape. Anything unexpected is logged with
its cause and answered with one generic sentence (see `core/logging.py`).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_context import request_id_var

INTERNAL_MESSAGE = "Something went wrong on our side. Please try again."


class AppError(Exception):
    """Base of every error the application raises on purpose."""

    status = 500
    code = "internal"
    message = INTERNAL_MESSAGE

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        super().__init__(self.message)


class BadRequestError(AppError):
    status, code, message = 400, "bad_request", "The request is not valid."


class NotAuthenticatedError(AppError):
    status, code, message = 401, "not_authenticated", "Please sign in."


class PermissionDeniedError(AppError):
    status, code, message = 403, "permission_denied", "You do not have access to this."


class NotFoundError(AppError):
    status, code, message = 404, "not_found", "Not found."


class ConflictError(AppError):
    status, code, message = 409, "conflict", "This conflicts with something that already exists."


class TooLargeError(AppError):
    status, code, message = 413, "too_large", "This is larger than allowed."


class RateLimitedError(AppError):
    status, code, message = 429, "rate_limited", "Too many requests. Please wait and try again."


class UnavailableError(AppError):
    status, code, message = 503, "unavailable", "The service is not available right now."


# For errors raised by FastAPI and Starlette themselves (an unknown path, a wrong method).
_STATUS_TO_ERROR: dict[int, type[AppError]] = {
    400: BadRequestError,
    401: NotAuthenticatedError,
    403: PermissionDeniedError,
    404: NotFoundError,
    405: BadRequestError,
    409: ConflictError,
    413: TooLargeError,
    429: RateLimitedError,
    503: UnavailableError,
}


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    fields: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if fields:
        error["fields"] = fields
    return JSONResponse(status_code=status, content={"error": error}, headers=headers)


def internal_error_response() -> JSONResponse:
    return error_response(500, AppError.code, INTERNAL_MESSAGE)


async def _handle_app_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return error_response(exc.status, exc.code, exc.message)


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    kind = _STATUS_TO_ERROR.get(exc.status_code)
    if kind is None:
        # a status we did not plan for: keep it, say nothing specific
        return error_response(exc.status_code, "error", "The request could not be completed.")
    message = "This method is not allowed here." if exc.status_code == 405 else kind.message
    return error_response(exc.status_code, kind.code, message, headers=exc.headers)  # type: ignore[arg-type]


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields = [
        {
            # ("body", "email") -> "email"; the submitted value is never echoed back
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "message": str(error["msg"]),
        }
        for error in exc.errors()
    ]
    return error_response(422, "validation_failed", "Some fields are not valid.", fields=fields)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
