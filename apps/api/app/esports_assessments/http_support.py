"""Private response handling and bounded request bodies for assessment APIs."""

from __future__ import annotations

import re
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import (
    AssessmentConflict,
    AssessmentError,
    AssessmentExpired,
    AssessmentForbidden,
    AssessmentNotFound,
    AssessmentRateLimited,
    AssessmentUnavailable,
)

PREFIX = "/api/v1/esports-assessments"
_BODY_LIMIT = 65536
_SAFE_ERRORS = {
    400: ("esports_invalid_request", "请求内容无效，请检查后重试"),
    404: ("esports_not_found", "天赋评测资源不存在"),
    413: ("esports_payload_too_large", "请求内容过大，请重新完成本项"),
    422: ("esports_invalid_data", "评测数据不符合当前规则，请重新完成本项"),
    500: ("esports_internal_error", "服务暂时不可用，请稍后重试"),
}
_CONFLICT_CODE = re.compile(r"esports_[a-z0-9_]+")


def _response(
    status: int,
    code: str,
    detail: str,
    *,
    retry_after: int | None = None,
    active_session_id: object | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": {
            403: "Forbidden",
            404: "Not Found",
            409: "Conflict",
            410: "Gone",
            429: "Too Many Requests",
        }.get(status, "Request Error"),
        "status": status,
        "detail": detail,
        "code": code,
    }
    if active_session_id is not None:
        body["active_session_id"] = str(active_session_id)
    headers = {
        "Cache-Control": "no-store, private",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
    }
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(body, status_code=status, headers=headers)


def private_error(status: int) -> JSONResponse:
    code, detail = _SAFE_ERRORS[status]
    return _response(status, code, detail)


class EsportsAssessmentRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def assessment_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except AssessmentNotFound:
                return private_error(404)
            except AssessmentForbidden:
                return _response(403, "esports_forbidden", "无权进行此天赋评测操作")
            except AssessmentRateLimited as exc:
                return _response(
                    429,
                    exc.code,
                    "操作过于频繁，请稍后重试",
                    retry_after=exc.retry_after,
                )
            except AssessmentExpired:
                return _response(410, "esports_expired", "天赋评测提交已过期")
            except AssessmentUnavailable:
                return _response(409, "esports_suite_unavailable", "该专项尚未开放")
            except AssessmentConflict as exc:
                code = (
                    exc.code if _CONFLICT_CODE.fullmatch(exc.code) else "esports_operation_conflict"
                )
                return _response(
                    409,
                    code,
                    "请选择近战或远程人物"
                    if code == "esports_attack_mode_required"
                    else "当前评测状态不允许此操作，请刷新后重试",
                    active_session_id=exc.active_session_id,
                )
            except (AssessmentError, ValidationError, ValueError):
                return private_error(422)

        return assessment_handler


class PrivateEsportsAssessmentResponseMiddleware:
    """Bound streamed input and protect every response under the exact prefix."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not (
            scope["path"] == PREFIX or scope["path"].startswith(PREFIX + "/")
        ):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            body.extend(chunk[: max(0, _BODY_LIMIT + 1 - len(body))])
            if len(body) > _BODY_LIMIT:
                await private_error(413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False
        started = False
        replaced = False

        async def replay_body() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def send_private(message: Message) -> None:
            nonlocal started, replaced
            if message["type"] == "http.response.start":
                started = True
                if message["status"] in _SAFE_ERRORS:
                    replaced = True
                    await private_error(message["status"])(scope, replay_body, send)
                    return
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
                headers["Pragma"] = "no-cache"
                headers["X-Content-Type-Options"] = "nosniff"
            if not replaced:
                await send(message)

        try:
            await self.app(scope, replay_body, send_private)
        except Exception:
            if started:
                raise
            await private_error(500)(scope, replay_body, send)
