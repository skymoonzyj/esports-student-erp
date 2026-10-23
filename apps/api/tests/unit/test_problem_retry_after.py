import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.errors import ProblemError, install_exception_handlers


@pytest.mark.parametrize(("status", "seconds"), [(429, 60), (423, 900)])
def test_problem_response_preserves_retry_delay_and_private_headers(status, seconds):
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/limited")
    def limited(request: Request):
        request.state.private_portal_response = True
        raise ProblemError(
            status=status,
            code="test_limited",
            detail="请稍后重试",
            retry_after_seconds=seconds,
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/limited")

    assert response.status_code == status
    assert response.headers["Retry-After"] == str(seconds)
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["X-Request-ID"] == "unknown"
    assert response.json()["code"] == "test_limited"
    assert "retry_after_seconds" not in response.json()


def test_ordinary_problem_does_not_add_retry_after():
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/denied")
    def denied():
        raise ProblemError(status=401, code="invalid_credentials", detail="登录失败")

    with TestClient(app) as client:
        response = client.get("/denied")

    assert response.status_code == 401
    assert "Retry-After" not in response.headers
    assert response.json()["code"] == "invalid_credentials"
