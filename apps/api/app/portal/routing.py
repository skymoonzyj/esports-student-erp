from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PORTAL_PREFIXES = (
    "/api/v1/portal/",
    "/api/v1/coach/",
    "/api/v1/family/",
)
_PORTAL_EXACT_PATHS = {
    "/api/v1/me/housing",
    "/api/v1/me/payments",
}


def _is_private_portal_path(path: str) -> bool:
    return path in _PORTAL_EXACT_PATHS or path.startswith(_PORTAL_PREFIXES)


class PrivatePortalResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_private_portal_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        state["private_portal_response"] = True

        async def send_private(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store, private"
            await send(message)

        await self.app(scope, receive, send_private)
