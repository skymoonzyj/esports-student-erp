from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.errors import ProblemError
from app.registration.routing import PrivateRegistrationResponseMiddleware


def _request(headers: list[tuple[str, str]]) -> Request:
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=SimpleNamespace(
                erp_web_base_url="https://erp.example.test/app",
            )
        )
    )
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "app": app,
            "headers": [(k.encode(), v.encode()) for k, v in headers],
        }
    )


@pytest.mark.parametrize(
    "headers",
    [
        [("origin", "https://evil.test")],
        [("origin", "null")],
        [("origin", "https://erp.example.test/")],
        [("origin", "https://erp.example.test/path")],
        [("origin", "https://erp.example.test, https://evil.test")],
        [("origin", "https://erp.example.test"), ("origin", "https://erp.example.test")],
        [("origin", "https://user@erp.example.test")],
        [("origin", "https://erp.example.test:443")],
        [("sec-fetch-site", "cross-site")],
        [("sec-fetch-site", "same-origin"), ("sec-fetch-site", "same-origin")],
        [("sec-fetch-site", "unknown")],
        [("sec-fetch-mode", "navigate")],
        [("sec-fetch-dest", "document")],
    ],
)
def test_activation_rejects_untrusted_or_malformed_request_metadata(headers):
    from app.personnel_intake.activation_security import require_activation_request

    with pytest.raises(ProblemError) as error:
        require_activation_request(_request([("content-type", "application/json"), *headers]))
    assert error.value.status == 403


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("origin", "https://erp.example.test")],
        [
            ("origin", "https://erp.example.test"),
            ("sec-fetch-site", "same-origin"),
            ("sec-fetch-mode", "cors"),
            ("sec-fetch-dest", "empty"),
        ],
    ],
)
def test_activation_allows_trusted_browser_and_fixed_origin_native(headers):
    from app.personnel_intake.activation_security import require_activation_request

    assert (
        require_activation_request(
            _request(
                [
                    ("content-type", "application/json; charset=utf-8"),
                    *headers,
                ]
            )
        )
        is None
    )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("content-type", "text/plain")],
        [("content-type", "application/x-www-form-urlencoded")],
        [("content-type", "application/json"), ("content-type", "application/json")],
    ],
)
def test_activation_requires_one_json_content_type(headers):
    from app.personnel_intake.activation_security import require_activation_request

    with pytest.raises(ProblemError) as error:
        require_activation_request(_request(headers))
    assert error.value.status == 415


@pytest.mark.parametrize("path", ["start", "verify", "complete"])
def test_activation_errors_are_private_and_not_cacheable(path):
    app = FastAPI()
    app.add_middleware(PrivateRegistrationResponseMiddleware)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/auth/profile-activation/{path}", json={})
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
