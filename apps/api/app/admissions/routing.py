from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute


class PrivateAdmissionsRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            response = await original(request)
            response.headers["Cache-Control"] = "no-store, private"
            return response

        return route_handler
