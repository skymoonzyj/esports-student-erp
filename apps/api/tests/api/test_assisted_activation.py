from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.errors import install_exception_handlers
from app.registration.routing import PrivateRegistrationResponseMiddleware


@pytest.mark.parametrize("path", ["start", "verify", "complete"])
def test_activation_is_mounted_in_application_with_early_origin_guard(client, path):
    response = client.post(
        f"/api/v1/auth/profile-activation/{path}",
        json={},
        headers={"origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"


def test_global_mail_configuration_failure_is_uniform_before_profile_lookup(
    test_settings, monkeypatch
):
    from app.core.database import get_db
    from app.mail.settings_service import MailSettingsNotConfigured, MailSettingsService
    from app.main import create_app

    class NoProfileQueries:
        def scalar(self, *args, **kwargs):
            raise AssertionError("profile lookup must not run without global mail service")

    def no_db():
        yield NoProfileQueries()

    def unavailable(self):
        raise MailSettingsNotConfigured("synthetic unavailable configuration")

    monkeypatch.setattr(MailSettingsService, "credentials", unavailable)
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = no_db
    with TestClient(app) as client:
        bodies = []
        for email in ("approved@example.com", "unknown@example.com"):
            response = client.post("/api/v1/auth/profile-activation/start", json={"email": email})
            assert response.status_code == 503
            assert response.headers["cache-control"] == "no-store, private"
            assert "set-cookie" not in response.headers
            body = response.json()
            body.pop("request_id")
            bodies.append(body)
        assert bodies[0] == bodies[1]


@pytest.fixture
def activation_client(test_settings):
    from app.personnel_intake.activation_router import get_activation_service, router
    from app.personnel_intake.activation_schemas import (
        ActivationCompleted,
        ActivationIssued,
        ActivationPreview,
        ActivationStart,
    )

    class Service:
        def __init__(self):
            self.calls = []
            self.dependency_calls = 0

        def start(self, email, request_source):
            self.calls.append(("start", email, request_source))
            now = datetime.now(UTC)
            return ActivationIssued(
                public=ActivationStart(
                    resend_available_at=now + timedelta(seconds=60),
                    expires_at=now + timedelta(minutes=10),
                ),
                session_token=SecretStr("x" * 43),
            )

        def verify(self, session_token, code):
            self.calls.append(("verify", session_token, code.get_secret_value()))
            return ActivationPreview(masked_name="张*", kind="student")

        def finish(self, session_token, code, password):
            self.calls.append(
                ("complete", session_token, code.get_secret_value(), password.get_secret_value())
            )
            return ActivationCompleted()

    service = Service()
    app = FastAPI()
    app.state.settings = test_settings.model_copy(
        update={"erp_web_base_url": "https://erp.example.com"}
    )
    install_exception_handlers(app)
    app.add_middleware(PrivateRegistrationResponseMiddleware)
    app.include_router(router, prefix="/api/v1")

    def provide_service():
        service.dependency_calls += 1
        return service

    app.dependency_overrides[get_activation_service] = provide_service
    with TestClient(app, base_url="https://erp.example.com") as client:
        yield client, service


def test_finish_busy_maps_to_retryable_409_and_preserves_cookie_for_retry(
    activation_client, monkeypatch
):
    from app.registration.verification_service import VerificationBusy

    client, service = activation_client
    client.post("/api/v1/auth/profile-activation/start", json={"email": "retry@example.com"})
    original_finish = service.finish

    def busy(*args):
        raise VerificationBusy("verification is busy; retry later")

    monkeypatch.setattr(service, "finish", busy)
    request = {"code": "123456", "password": "SecurePass1"}
    response = client.post("/api/v1/auth/profile-activation/complete", json=request)
    assert response.status_code == 409
    assert response.json()["code"] == "activation_retry"
    assert "重试" in response.json()["detail"]
    assert response.headers["cache-control"] == "no-store, private"
    assert "set-cookie" not in response.headers
    assert client.cookies.get("erp_profile_activation") == "x" * 43
    monkeypatch.setattr(service, "finish", original_finish)
    retried = client.post("/api/v1/auth/profile-activation/complete", json=request)
    assert retried.status_code == 200 and retried.json() == {"completed": True}
    assert client.cookies.get("erp_profile_activation") is None


def test_activation_wire_cookie_and_no_auto_login(activation_client):
    client, service = activation_client
    start = client.post(
        "/api/v1/auth/profile-activation/start", json={"email": "example@example.com"}
    )
    assert start.status_code == 200
    assert set(start.json()) == {"accepted", "expires_at", "resend_available_at"}
    cookie = start.headers["set-cookie"]
    assert "erp_profile_activation=" in cookie
    assert "Path=/api/v1/auth/profile-activation" in cookie
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "erp_session" not in cookie and "x" * 43 not in start.text
    preview = client.post("/api/v1/auth/profile-activation/verify", json={"code": "123456"})
    assert preview.json() == {"masked_name": "张*", "kind": "student"}
    assert "set-cookie" not in preview.headers
    complete = client.post(
        "/api/v1/auth/profile-activation/complete",
        json={"code": "123456", "password": "SecurePass1"},
    )
    assert complete.json() == {"completed": True}
    assert "erp_session" not in complete.headers.get("set-cookie", "")
    assert service.calls[1][1] == "x" * 43


@pytest.mark.parametrize(
    "path,body",
    [
        ("start", {"email": "example@example.com"}),
        ("verify", {"code": "123456"}),
        ("complete", {"code": "123456", "password": "SecurePass1"}),
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        [("origin", "https://evil.example.com")],
        [("origin", "null")],
        [("origin", "https://erp.example.com"), ("origin", "https://erp.example.com")],
        [("sec-fetch-site", "cross-site")],
    ],
)
def test_origin_rejected_before_any_activation_operation(activation_client, path, body, headers):
    client, service = activation_client
    response = client.post(f"/api/v1/auth/profile-activation/{path}", json=body, headers=headers)
    assert response.status_code == 403
    assert service.calls == []
    assert service.dependency_calls == 0
    assert response.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize(
    "path,body",
    [("verify", {"code": "123456"}), ("complete", {"code": "123456", "password": "SecurePass1"})],
)
def test_missing_or_repeated_activation_cookie_rejected(activation_client, path, body):
    client, service = activation_client
    for headers in ({}, {"cookie": "erp_profile_activation=a; erp_profile_activation=b"}):
        response = client.post(
            f"/api/v1/auth/profile-activation/{path}", json=body, headers=headers
        )
        assert response.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize("path", ["start", "verify", "complete"])
def test_non_json_rejected_before_service_dependency(activation_client, path):
    client, service = activation_client
    response = client.post(
        f"/api/v1/auth/profile-activation/{path}",
        content="{}",
        headers={"content-type": "text/plain"},
    )
    assert response.status_code == 415
    assert service.dependency_calls == 0
    assert response.headers["cache-control"] == "no-store, private"
