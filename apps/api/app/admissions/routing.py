from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.core.errors import ProblemError


class PrivateAdmissionsRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError as exc:
                if request.method == "POST" and any(
                    error["loc"] == ("body", "idempotency_key") for error in exc.errors()
                ) and (
                    request.url.path.endswith("/payments")
                    or request.url.path.endswith("/refunds")
                ):
                    raise ProblemError(
                        status=422,
                        code="payment_operation_key_required",
                        detail="收退款操作标识缺失或无效，请刷新页面后重新操作；如曾提交，请先核对是否已登记",
                    ) from exc
                raise
            response.headers["Cache-Control"] = "no-store, private"
            return response

        return route_handler
