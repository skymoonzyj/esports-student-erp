from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import Response
from starlette.requests import Request

from app.auth.dependencies import require_session
from app.auth.service import InvalidCsrfToken, InvalidSession
from app.core.errors import ProblemError


def _request(method: str) -> Request:
    return Request({
        "type": "http", "method": method, "scheme": "http", "server": ("test", 80),
        "path": "/api/v1/people", "query_string": b"", "headers": [(b"cookie", b"erp_session=valid")],
        "state": {"request_id": "safe-request"},
        "app": SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(session_cookie_secure=False))),
    })


def test_known_account_is_attributed_before_csrf_denial() -> None:
    account_id = UUID("00000000-0000-4000-8000-000000000123")
    context = SimpleNamespace(account=SimpleNamespace(id=account_id))

    class Service:
        def authenticate(self, token: str, *, request_id: str):
            assert token == "valid" and request_id == "safe-request"
            return context

        def verify_csrf(self, context, token) -> None:
            del context, token
            raise InvalidCsrfToken()

    request = _request("PATCH")
    with pytest.raises(ProblemError) as error:
        require_session(request, Response(), Service())
    assert error.value.status == 403
    assert request.state.operations_authenticated_account_id == account_id


def test_invalid_session_cannot_be_attributed() -> None:
    class Service:
        def authenticate(self, token: str, *, request_id: str):
            del token, request_id
            raise InvalidSession()

    request = _request("GET")
    with pytest.raises(ProblemError) as error:
        require_session(request, Response(), Service())
    assert error.value.status == 401
    assert not hasattr(request.state, "operations_authenticated_account_id")
