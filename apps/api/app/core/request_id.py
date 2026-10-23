from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.privacy import contains_sensitive_request_id

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SENSITIVE_HEADER_NAMES = (
    "authorization",
    "idempotency-key",
    "x-csrf-token",
    "x-registration-csrf-token",
    "x-evaluation-delegated-session",
    "x-evaluation-lease-token",
    "x-evaluation-media-ticket",
)


def _contains_sensitive_header_value(headers: Headers, incoming: str) -> bool:
    sensitive_values: list[str] = []
    for name in _SENSITIVE_HEADER_NAMES:
        for value in headers.getlist(name):
            sensitive_values.append(value)
            if name == "authorization" and " " in value:
                sensitive_values.append(value.split(" ", 1)[1])
    return any(value and value in incoming for value in sensitive_values)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming = headers.get("x-request-id")
        request_id = (
            incoming
            if incoming
            and _SAFE_REQUEST_ID.fullmatch(incoming)
            and not contains_sensitive_request_id(incoming)
            and not _contains_sensitive_header_value(headers, incoming)
            else str(uuid4())
        )
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)
