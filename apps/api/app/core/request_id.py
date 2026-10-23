from __future__ import annotations

import re
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.privacy import contains_sensitive_request_id

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id")
        request_id = (
            incoming
            if incoming
            and _SAFE_REQUEST_ID.fullmatch(incoming)
            and not contains_sensitive_request_id(incoming)
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
