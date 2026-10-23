from __future__ import annotations

from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler


class ProblemError(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        detail: str,
        errors: list[dict[str, str]] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.detail = detail
        self.errors = errors
        super().__init__(detail)


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _problem(
    request: Request,
    *,
    status: int,
    detail: str,
    code: str,
    errors: list[dict[str, str]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": _request_id(request),
    }
    if errors is not None:
        body["errors"] = errors
    response = JSONResponse(status_code=status, content=body)
    response.headers["X-Request-ID"] = _request_id(request)
    if getattr(request.state, "private_portal_response", False):
        response.headers["Cache-Control"] = "no-store, private"
    return response


async def _http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    missing_route = exc.status_code == 404 and exc.detail == "Not Found"
    return _problem(
        request,
        status=exc.status_code,
        detail="Route not found" if missing_route else str(exc.detail),
        code="route_not_found" if missing_route else "http_error",
    )


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": str(error["msg"]),
        }
        for error in exc.errors()
    ]
    return _problem(
        request,
        status=422,
        detail="Request validation failed",
        code="validation_error",
        errors=errors,
    )


async def _unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    del exc
    return _problem(
        request,
        status=500,
        detail="An unexpected error occurred",
        code="internal_error",
    )


async def _problem_exception_handler(request: Request, exc: ProblemError) -> JSONResponse:
    return _problem(
        request,
        status=exc.status,
        detail=exc.detail,
        code=exc.code,
        errors=exc.errors,
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        ProblemError,
        cast(ExceptionHandler, _problem_exception_handler),
    )
    app.add_exception_handler(
        StarletteHTTPException,
        cast(ExceptionHandler, _http_exception_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, _validation_exception_handler),
    )
    app.add_exception_handler(Exception, _unexpected_exception_handler)
