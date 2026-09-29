"""The API's one error format (MASTERSPEC §12): ``{error: {code, message}}``.

Single responsibility: turn every failure the API can produce, including
FastAPI's own validation and routing errors, into that envelope with the right
HTTP status, so the frontend never has to parse two shapes.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models import ApiError, ApiErrorEnvelope, ErrorCode

logger = logging.getLogger(__name__)

__all__ = ["ERROR_STATUS", "ApiException", "error_response", "install_error_handlers"]

#: HTTP status per code, for errors raised synchronously by a route. Scan
#: failures (TIMEOUT, PAGE_TOO_LARGE, NAV_FAILED, CANCELLED) normally arrive
#: over SSE instead; they have statuses here only so the table is total.
ERROR_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.URL_BLOCKED: 400,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.TIMEOUT: 504,
    ErrorCode.PAGE_TOO_LARGE: 413,
    ErrorCode.NAV_FAILED: 502,
    ErrorCode.CANCELLED: 409,
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.PATCH_FAILED: 500,
}


class ApiException(Exception):
    """Raise from a route to answer with the standard error envelope."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.headers = headers
        super().__init__(message)


def error_response(
    code: ErrorCode, message: str, *, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ApiErrorEnvelope(error=ApiError(code=code, message=message))
    return JSONResponse(
        status_code=ERROR_STATUS[code],
        content=body.model_dump(mode="json"),
        headers=headers,
    )


def _describe(exc: RequestValidationError) -> tuple[bool, str]:
    """Whether the ``url`` field is at fault, and a readable message."""
    url_problem = False
    parts: list[str] = []
    for error in exc.errors():
        location = [str(item) for item in error.get("loc", ()) if item != "body"]
        if location[:1] == ["url"]:
            url_problem = True
        where = ".".join(location) or "body"
        parts.append(f"{where}: {error.get('msg', 'invalid')}")
    return url_problem, "; ".join(parts) or "invalid request"


def install_error_handlers(app: FastAPI) -> None:
    """Register the envelope for every error path FastAPI can take."""

    @app.exception_handler(ApiException)
    async def _api(_: Request, exc: ApiException) -> JSONResponse:
        return error_response(exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        url_problem, message = _describe(exc)
        if url_problem:
            # An unparseable or non-http(s) URL is refused for the same reason
            # the SSRF guard refuses one: it is not something we will fetch.
            return error_response(ErrorCode.URL_BLOCKED, message)
        return error_response(ErrorCode.INVALID_REQUEST, message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(ErrorCode.NOT_FOUND, "not found", headers=exc.headers)
        response = error_response(ErrorCode.INVALID_REQUEST, str(exc.detail), headers=exc.headers)
        # Keep the real status (405 method not allowed, etc.), not 422.
        response.status_code = exc.status_code
        return response
