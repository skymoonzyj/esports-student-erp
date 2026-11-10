import re
from collections.abc import Callable, Coroutine
from contextlib import closing
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.client_ip import request_source_ip
from app.core.database import get_db
from app.core.errors import ProblemError
from app.reactionx.pairing import PairingService
from app.reactionx.schemas import MAX_BATCH_BYTES, MAX_RESULT_BYTES

_RESULT_PATH = re.compile(
    r"/api/v1/reactionx-client/runs/"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/result\Z"
)


def _rate_claim_before_body(request: Request) -> None:
    # Use the app's ordinary synchronous DB provider, including its configured
    # dependency override. Closing the provider releases a real request session
    # without closing a borrowed session supplied by an embedding application.
    provider = request.app.dependency_overrides.get(get_db, get_db)
    with closing(provider()) as sessions:
        service = PairingService(
            next(sessions), origin=request.app.state.settings.reactionx_public_origin
        )
        service.rate_claim(
            request_source_ip(
                request, trusted_proxy_cidrs=request.app.state.settings.trusted_proxy_cidrs
            )
        )


class ReactionxRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handle(request: Request) -> Response:
            request.state.private_portal_response = True
            browser = request.url.path.startswith("/api/v1/reactionx/")
            if browser and request.headers.get("authorization", "").startswith("ReactionX "):
                raise ProblemError(
                    status=401,
                    code="reactionx_machine_not_browser",
                    detail="连接器不可访问网页人员接口",
                )
            settings = request.app.state.settings
            origins = {settings.reactionx_public_origin}
            if browser and settings.app_env in {"test", "development"}:
                configured_web = urlsplit(settings.erp_web_base_url)
                if (
                    configured_web.scheme in {"http", "https"}
                    and configured_web.hostname
                    and configured_web.username is None
                    and configured_web.password is None
                ):
                    origins.add(f"{configured_web.scheme}://{configured_web.netloc}")
            if (
                request.headers.get("origin") is not None
                and request.headers["origin"] not in origins
            ):
                raise ProblemError(
                    status=403, code="reactionx_origin_rejected", detail="站点校验失败"
                )
            try:
                return await original(request)
            except RequestValidationError:
                raise ProblemError(
                    status=422, code="validation_error", detail="ReactionX 请求格式无效"
                ) from None

        return handle


class PrivateReactionxResponseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        prefixes = ("/api/v1/reactionx", "/api/v1/reactionx-client")
        if scope["type"] != "http" or not any(
            scope["path"] == prefix or scope["path"].startswith(prefix + "/") for prefix in prefixes
        ):
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["private_portal_response"] = True
        if (
            scope.get("method") == "POST"
            and scope["path"] == "/api/v1/reactionx-client/pairings/claim"
            and not scope["state"].get("reactionx_claim_rate_applied")
        ):
            try:
                await run_in_threadpool(_rate_claim_before_body, Request(scope))
            except ProblemError as error:
                if error.status != 429:
                    raise
                await JSONResponse(
                    {
                        "type": "about:blank",
                        "title": "Too Many Requests",
                        "status": 429,
                        "code": error.code,
                        "detail": error.detail,
                    },
                    status_code=429,
                    headers={
                        "Cache-Control": "no-store, private",
                        "Retry-After": str(error.retry_after_seconds),
                    },
                )(scope, receive, send)
                return
            scope["state"]["reactionx_claim_rate_applied"] = True
        limit = (
            MAX_RESULT_BYTES
            if scope.get("method") == "POST" and _RESULT_PATH.fullmatch(scope["path"])
            else MAX_BATCH_BYTES
        )
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b"")[: max(0, limit + 1 - len(body))])
            if len(body) > limit:
                await JSONResponse(
                    {"code": "reactionx_body_too_large", "detail": "请求内容过大"},
                    status_code=413,
                    headers={"Cache-Control": "no-store, private"},
                )(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def private(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store, private"
            await send(message)

        await self.app(scope, replay, private)
