from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_REGISTRATION_PATHS = (
    "/api/v1/personnel-intake",
    "/api/v1/people",
    "/api/v1/accounts",
    "/api/v1/registration",
    "/api/v1/registration-admin",
    "/api/v1/auth/password-reset",
    "/api/v1/auth/profile-activation",
    "/api/v1/auth/identifiers",
)


def _is_registration_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in _REGISTRATION_PATHS
    )


class PrivateRegistrationResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_registration_path(scope["path"]):
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
