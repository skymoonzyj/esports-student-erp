from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.config import Settings
from app.evaluations.dependencies import get_evaluation_bridge_service
from app.evaluations.errors import (
    EvaluationAccessDenied,
    EvaluationBridgeError,
    EvaluationDelegatedSessionInvalid,
    EvaluationTicketInvalid,
)
from app.evaluations.models import EvaluationBridgeRequest
from app.main import create_app
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS

SERVICE_TOKEN = "evaluation-local-service-token-2026-change-me"
SERVICE_HEADERS = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
SIGNED_STATE = (
    "eyJleHBpcmVzX2F0IjoxNzg1Mzg5MTAwLCJtb2RlIjoic3RhbmRhbG9uZSIs"
    "Im5vbmNlIjoiYWJjIiwicmV0dXJuX3BhdGgiOiIvIiwidiI6MX0."
    "signature_abcdefghijklmnopqrstuvwxyz0123456789"
)
SECRET_SENTINEL = "internal-evaluation-secret-sentinel"
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "evaluation_integration_v1.json"


class RaisingBridgeService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def _raise(self) -> NoReturn:
        raise self.error

    def issue_ticket(self, **kwargs: object) -> NoReturn:
        del kwargs
        self._raise()

    def exchange_ticket(self, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        self._raise()

    def session_context(self, *args: object, **kwargs: object) -> NoReturn:
        del args, kwargs
        self._raise()


def test_frozen_evaluation_contract_matches_openapi(test_settings: Settings) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    openapi_paths = create_app(test_settings).openapi()["paths"]

    endpoints = {
        **contract["account_endpoints"],
        **contract["service_endpoints"],
    }
    for endpoint in endpoints.values():
        method, path = endpoint.split(" ", 1)
        assert path in openapi_paths
        assert method.lower() in openapi_paths[path]


def _register_request(
    client: TestClient,
    *,
    mode: str = "standalone",
    state: str = SIGNED_STATE,
) -> str:
    response = client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        headers=SERVICE_HEADERS,
        json={
            "client_id": "game-score-tool",
            "mode": mode,
            "state": state,
        },
    )
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store, private"
    return response.json()["request_id"]


def _login(client: TestClient, platform_users, key: str) -> dict[str, object]:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _deny_evaluation_access(db: Session, account_id: UUID) -> None:
    db.add(
        UserPermissionOverride(
            user_id=account_id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()


def test_evaluation_context_is_private_and_contains_fixed_start_urls(
    db_client: TestClient,
    login_as_admin,
) -> None:
    del login_as_admin

    response = db_client.get("/api/v1/evaluations/context")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "accessible": True,
        "system_evaluation_available": True,
        "embedded_start_url": ("/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F"),
        "standalone_start_url": (
            "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F"
        ),
        "upload_available": False,
        "video_available": False,
        # The admin has the global teaching-report workspace permissions.  The
        # report context may still have no live tasks until a cohort is
        # enrolled, but the module itself remains enterable.
        "reports_available": True,
        # The admin account has the seeded camp-survey print/identity
        # capabilities, so the evaluation center advertises the survey
        # surface alongside the existing system-evaluation routes.
        "surveys_available": True,
        "survey_sections": {
            "respondent": False,
            "staff_feedback": False,
            "administration": True,
        },
        "game_accounts_available": False,
        "admin_available": True,
            "admin_sections": {
                "cohort_staff": False,
                "student_matching": True,
                "analysis_profiles": True,
                "system_tasks": True,
                "adapter_certifications": True,
            },
        "integration_api_version": "v1",
    }


def test_service_registers_then_user_issues_and_service_exchanges_ticket(
    db_client: TestClient,
    login_as_admin,
) -> None:
    request_id = _register_request(db_client)

    issued = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": request_id},
    )
    assert issued.status_code == 201
    assert issued.headers["cache-control"] == "no-store, private"
    assert "ticket=" not in str(issued.request.url)
    assert issued.json()["state"] == SIGNED_STATE

    exchanged = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers=SERVICE_HEADERS,
        json={"ticket": issued.json()["ticket"]},
    )
    assert exchanged.status_code == 200
    assert exchanged.headers["cache-control"] == "no-store, private"
    assert exchanged.json()["account_id"] == str(login_as_admin.account_id)

    context = db_client.post(
        "/api/v1/integrations/evaluations/v1/session-context",
        headers=SERVICE_HEADERS,
        json={"session_id": exchanged.json()["session_id"]},
    )
    assert context.status_code == 200
    for payload in (exchanged.json(), context.json()):
        assert set(payload) == {
            "api_version",
            "account_id",
            "person_id",
            "display_name",
            "session_id",
            "expires_at",
            "page_shell_available",
            "upload_available",
        }
        assert not {"email", "phone", "permissions"} & set(payload)

    replay = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers=SERVICE_HEADERS,
        json={"ticket": issued.json()["ticket"]},
    )
    assert replay.status_code == 401
    assert replay.headers["cache-control"] == "no-store, private"
    assert replay.json()["code"] == "bridge_ticket_invalid"


def test_capabilities_are_frozen_and_private(client: TestClient) -> None:
    response = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=SERVICE_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "api_version": "v1",
        "features": ["auth_bridge", "page_shell"],
        "legacy_writes_enabled": False,
    }


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Bearer wrong-service-token"],
)
def test_service_api_rejects_missing_or_wrong_bearer(
    client: TestClient,
    authorization: str | None,
) -> None:
    headers = {"Authorization": authorization} if authorization else {}

    response = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=headers,
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "integration_auth_invalid"


def test_service_api_rejects_non_ascii_bearer_without_internal_error(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=[(b"authorization", b"Bearer \xff")],
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.json()["code"] == "integration_auth_invalid"
    assert SERVICE_TOKEN not in response.text


def test_service_api_rejects_duplicate_authorization_headers(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=[
            (b"authorization", f"Bearer {SERVICE_TOKEN}".encode("ascii")),
            (b"authorization", b"Bearer wrong-service-token"),
        ],
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "integration_auth_invalid"


def test_service_token_query_parameter_cannot_replace_authorization_header(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        params={"access_token": SERVICE_TOKEN},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


@pytest.mark.parametrize(
    ("cookie_name", "cookie_value"),
    [
        ("evaluation_tool_service_token", SERVICE_TOKEN),
        ("Authorization", f"Bearer {SERVICE_TOKEN}"),
    ],
)
def test_service_token_cookie_cannot_replace_authorization_header(
    client: TestClient,
    cookie_name: str,
    cookie_value: str,
) -> None:
    client.cookies.set(cookie_name, cookie_value)
    try:
        response = client.get("/api/v1/integrations/evaluations/v1/capabilities")
    finally:
        client.cookies.delete(cookie_name)

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_service_auth_precedes_body_validation_and_rejects_form_token(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        data={"service_token": SERVICE_TOKEN},
    )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "integration_auth_invalid"


def test_erp_session_cookie_cannot_replace_service_bearer(
    db_client: TestClient,
    login_as_admin,
) -> None:
    del login_as_admin

    response = db_client.get("/api/v1/integrations/evaluations/v1/capabilities")

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


@pytest.mark.parametrize("csrf", [None, "wrong-csrf-token"])
def test_ticket_issue_rejects_missing_and_wrong_csrf(
    db_client: TestClient,
    login_as_admin,
    csrf: str | None,
) -> None:
    request_id = _register_request(db_client, mode="embedded")
    headers = {"X-CSRF-Token": csrf} if csrf else {}

    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers=headers,
        json={"request_id": request_id},
    )

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "csrf_invalid"
    assert login_as_admin.account_id


def test_customer_service_has_no_evaluation_context_by_default(
    db_client: TestClient,
    login_as_customer_service,
) -> None:
    del login_as_customer_service

    response = db_client.get("/api/v1/evaluations/context")

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "evaluation_access_denied"


def test_customer_service_ticket_issue_masks_existing_and_missing_request(
    db_client: TestClient,
    login_as_customer_service,
) -> None:
    existing_id = _register_request(db_client)
    responses = [
        db_client.post(
            "/api/v1/evaluations/bridge/tickets",
            headers={"X-CSRF-Token": login_as_customer_service.csrf_token},
            json={"request_id": request_id},
        )
        for request_id in (existing_id, str(uuid4()))
    ]

    assert [response.status_code for response in responses] == [403, 403]
    assert [response.json()["code"] for response in responses] == [
        "evaluation_access_denied",
        "evaluation_access_denied",
    ]
    assert responses[0].json()["detail"] == responses[1].json()["detail"]


def test_evaluation_context_and_ticket_issue_recheck_permission_each_time(
    db_client: TestClient,
    login_as_admin,
    db: Session,
) -> None:
    request_id = _register_request(db_client)
    assert db_client.get("/api/v1/evaluations/context").status_code == 200

    _deny_evaluation_access(db, login_as_admin.account_id)

    context = db_client.get("/api/v1/evaluations/context")
    issued = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": request_id},
    )
    assert context.status_code == 403
    assert issued.status_code == 403
    assert context.json()["code"] == "evaluation_access_denied"
    assert issued.json()["code"] == "evaluation_access_denied"


@pytest.mark.parametrize(
    "state",
    ["a" * 31, "a" * 1025, "bad+state" * 4, "bad/state" * 4, "bad state" * 4],
)
def test_login_request_rejects_invalid_state(
    client: TestClient,
    state: str,
) -> None:
    response = client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        headers=SERVICE_HEADERS,
        json={
            "client_id": "game-score-tool",
            "mode": "standalone",
            "state": state,
        },
    )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "validation_error"


@pytest.mark.parametrize("length", [32, 1024])
def test_login_request_accepts_exact_state_boundaries(
    db_client: TestClient,
    length: int,
) -> None:
    state = "a" * length

    response = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        headers=SERVICE_HEADERS,
        json={
            "client_id": "game-score-tool",
            "mode": "standalone",
            "state": state,
        },
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store, private"


def test_erp_round_trips_semantically_opaque_state_unchanged(
    db_client: TestClient,
    login_as_admin,
) -> None:
    opaque_state = "opaque-but-not-a-score-tool-token-" + "a" * 32
    request_id = _register_request(db_client, state=opaque_state)

    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": request_id},
    )

    assert response.status_code == 201
    assert response.json()["state"] == opaque_state


def test_unknown_bridge_object_uses_stable_masked_error(
    db_client: TestClient,
    login_as_admin,
) -> None:
    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "bridge_request_not_found"
    assert "ticket-secret-sentinel" not in response.text
    assert "state-secret-sentinel" not in response.text


def test_expired_request_uses_stable_private_410(
    db_client: TestClient,
    login_as_admin,
    db: Session,
) -> None:
    request_id = UUID(_register_request(db_client))
    row = db.get(EvaluationBridgeRequest, request_id)
    assert row is not None
    now = datetime.now(UTC)
    row.created_at = now - timedelta(minutes=10)
    row.expires_at = now - timedelta(minutes=5)
    db.commit()

    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": str(request_id)},
    )

    assert response.status_code == 410
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "bridge_request_expired"


def test_second_account_receives_stable_private_conflict(
    db_client: TestClient,
    platform_users,
) -> None:
    admin = _login(db_client, platform_users, "admin")
    request_id = _register_request(db_client)
    first = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": str(admin["csrf_token"])},
        json={"request_id": request_id},
    )
    assert first.status_code == 201

    coach = _login(db_client, platform_users, "coach")
    response = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": str(coach["csrf_token"])},
        json={"request_id": request_id},
    )

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "bridge_request_conflict"


def test_bridge_audits_use_only_sanitized_request_state_id(
    db_client: TestClient,
    login_as_admin,
    db: Session,
) -> None:
    request_id = _register_request(db_client)
    unsafe_issue_id = "state-0123456789abcdef0123456789abcdef"
    issued = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={
            "X-CSRF-Token": login_as_admin.csrf_token,
            "X-Request-ID": unsafe_issue_id,
        },
        json={"request_id": request_id},
    )
    assert issued.status_code == 201

    unsafe_exchange_id = "ticket_" + issued.json()["ticket"]
    exchanged = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers={**SERVICE_HEADERS, "X-Request-ID": unsafe_exchange_id},
        json={"ticket": issued.json()["ticket"]},
    )
    assert exchanged.status_code == 200

    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.action.in_(
                    [
                        "evaluation.bridge.ticket_issued",
                        "evaluation.bridge.ticket_exchanged",
                    ]
                )
            )
        )
    )
    assert len(events) == 2
    assert {event.request_id for event in events} == {
        issued.headers["x-request-id"],
        exchanged.headers["x-request-id"],
    }
    assert all(str(UUID(event.request_id)) == event.request_id for event in events)
    assert unsafe_issue_id not in {event.request_id for event in events}
    assert unsafe_exchange_id not in {event.request_id for event in events}


def test_account_ticket_maps_concurrent_session_invalidation_to_stable_401(
    db_client: TestClient,
    login_as_admin,
) -> None:
    service = RaisingBridgeService(EvaluationDelegatedSessionInvalid(SECRET_SENTINEL))

    def override_service() -> RaisingBridgeService:
        return service

    db_client.app.dependency_overrides[get_evaluation_bridge_service] = override_service
    try:
        response = db_client.post(
            "/api/v1/evaluations/bridge/tickets",
            headers={"X-CSRF-Token": login_as_admin.csrf_token},
            json={"request_id": str(uuid4())},
        )
    finally:
        db_client.app.dependency_overrides.pop(
            get_evaluation_bridge_service,
            None,
        )

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "delegated_session_invalid"
    assert SECRET_SENTINEL not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (EvaluationTicketInvalid(SECRET_SENTINEL), 401, "bridge_ticket_invalid"),
        (
            EvaluationDelegatedSessionInvalid(SECRET_SENTINEL),
            401,
            "delegated_session_invalid",
        ),
        (EvaluationAccessDenied(SECRET_SENTINEL), 403, "evaluation_access_denied"),
        (EvaluationBridgeError(SECRET_SENTINEL), 500, "evaluation_bridge_error"),
    ],
)
def test_integration_errors_mask_internal_exception_details(
    client: TestClient,
    error: EvaluationBridgeError,
    status: int,
    code: str,
) -> None:
    service = RaisingBridgeService(error)

    def override_service() -> RaisingBridgeService:
        return service

    client.app.dependency_overrides[get_evaluation_bridge_service] = override_service
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
            headers=SERVICE_HEADERS,
            json={"ticket": "t" * 32},
        )
    finally:
        client.app.dependency_overrides.pop(
            get_evaluation_bridge_service,
            None,
        )

    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.json()["code"] == code
    assert SECRET_SENTINEL not in response.text


def test_unexpected_evaluation_error_is_private_and_has_request_id_header(
    client: TestClient,
) -> None:
    service = RaisingBridgeService(RuntimeError(SECRET_SENTINEL))

    def override_service() -> RaisingBridgeService:
        return service

    client.app.dependency_overrides[get_evaluation_bridge_service] = override_service
    try:
        with TestClient(client.app, raise_server_exceptions=False) as crashing_client:
            response = crashing_client.post(
                "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
                headers=SERVICE_HEADERS,
                json={"ticket": "t" * 32},
            )
    finally:
        client.app.dependency_overrides.pop(
            get_evaluation_bridge_service,
            None,
        )

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-request-id"] == response.json()["request_id"]
    assert response.json()["code"] == "internal_error"
    assert SECRET_SENTINEL not in response.text


def test_session_context_rechecks_permission_after_exchange(
    db_client: TestClient,
    login_as_admin,
    db: Session,
) -> None:
    request_id = _register_request(db_client)
    issued = db_client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"request_id": request_id},
    )
    exchanged = db_client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers=SERVICE_HEADERS,
        json={"ticket": issued.json()["ticket"]},
    )
    assert exchanged.status_code == 200

    _deny_evaluation_access(db, login_as_admin.account_id)

    response = db_client.post(
        "/api/v1/integrations/evaluations/v1/session-context",
        headers=SERVICE_HEADERS,
        json={"session_id": exchanged.json()["session_id"]},
    )
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["code"] == "evaluation_access_denied"
