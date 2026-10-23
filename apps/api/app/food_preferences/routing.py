from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def _is_private_food_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    if normalized == "/api/v1/food-preferences" or normalized.startswith(
        "/api/v1/food-preferences/"
    ):
        return True
    if normalized == "/api/v1/me/food-preferences":
        return True
    segments = normalized.strip("/").split("/")
    return (
        len(segments) == 5
        and segments[:3] == ["api", "v1", "people"]
        and segments[3] != ""
        and segments[4] == "food-preferences"
    )


class PrivateFoodPreferenceResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_private_food_path(scope["path"]):
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
