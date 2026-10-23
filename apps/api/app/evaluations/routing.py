from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_EVALUATION_PREFIXES = (
    "/api/v1/evaluations/",
    "/api/v1/integrations/evaluations/",
)


class PrivateEvaluationResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(
            _EVALUATION_PREFIXES
        ):
            await self.app(scope, receive, send)
            return

        scope.setdefault("state", {})["private_portal_response"] = True

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
                headers["Pragma"] = "no-cache"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, send_private)
