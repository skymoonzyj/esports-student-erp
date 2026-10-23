from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.evaluations.dependencies import (
    get_evaluation_bridge_service,
    get_evaluation_context_service,
    get_evaluation_task_service,
)
from app.evaluations.errors import (
    EvaluationAccessDenied,
    EvaluationDelegatedSessionInvalid,
)
from app.evaluations.schemas import EvaluationToolSessionContext
from app.evaluations.task_service import EvaluationTaskRecord
from app.evaluations.upload_service import EvaluationUploadService
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS

V1_SERVICE_TOKEN = "evaluation-local-service-token-2026-change-me"
WEB_SERVICE_TOKEN = "evaluation-local-web-service-token-2026-change-me"
WORKER_SERVICE_TOKEN = "evaluation-future-worker-token-2026-change-me"
V1_HEADERS = {"Authorization": f"Bearer {V1_SERVICE_TOKEN}"}
WEB_HEADERS = {"Authorization": f"Bearer {WEB_SERVICE_TOKEN}"}
DELEGATED_HEADER = "X-Evaluation-Delegated-Session"
SIGNED_STATE = "v2-web-integration-state-" + "a" * 32


def _assert_private_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


def _login(client: TestClient, platform_users, key: str) -> str:
    phone, password, _role = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _real_delegated_session(
    client: TestClient,
    platform_users,
    *,
    key: str,
) -> UUID:
    registered = client.post(
        "/api/v1/integrations/evaluations/v1/login-requests",
        headers=V1_HEADERS,
        json={
            "client_id": "game-score-tool",
            "mode": "standalone",
            "state": SIGNED_STATE,
        },
    )
    assert registered.status_code == 201
    csrf = _login(client, platform_users, key)
    issued = client.post(
        "/api/v1/evaluations/bridge/tickets",
        headers={"X-CSRF-Token": csrf},
        json={"request_id": registered.json()["request_id"]},
    )
    assert issued.status_code == 201
    exchanged = client.post(
        "/api/v1/integrations/evaluations/v1/login-tickets/exchange",
        headers=V1_HEADERS,
        json={"ticket": issued.json()["ticket"]},
    )
    assert exchanged.status_code == 200
    return UUID(exchanged.json()["session_id"])


class RecordingBridgeService:
    def __init__(
        self,
        *,
        result: EvaluationToolSessionContext | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[UUID] = []

    def session_context(self, session_id: UUID) -> EvaluationToolSessionContext:
        self.calls.append(session_id)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class FakeContextService:
    def __init__(self, targets: list[object] | None = None) -> None:
        self.targets = targets or []
        self.calls: list[UUID] = []

    def upload_targets(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> list[object]:
        assert occurred_at.tzinfo is not None
        self.calls.append(account_id)
        return self.targets

    def upload_operation_allowed(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        assert occurred_at.tzinfo is not None
        self.calls.append(account_id)
        return bool(self.targets)


class BombContextService:
    def upload_targets(self, **_kwargs: object) -> NoReturn:
        raise AssertionError("selector must not run")

    def upload_operation_allowed(self, **_kwargs: object) -> NoReturn:
        raise AssertionError("capability must not run")


def _bridge_context(session_id: UUID) -> EvaluationToolSessionContext:
    return EvaluationToolSessionContext(
        account_id=UUID("10000000-0000-4000-8000-000000000001"),
        person_id=UUID("10000000-0000-4000-8000-000000000002"),
        display_name="委派测试用户",
        session_id=session_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def _install_services(
    client: TestClient,
    bridge: RecordingBridgeService,
    context: object,
) -> None:
    client.app.dependency_overrides[get_evaluation_bridge_service] = lambda: bridge
    client.app.dependency_overrides[get_evaluation_context_service] = lambda: context


def _clear_services(client: TestClient) -> None:
    client.app.dependency_overrides.pop(get_evaluation_bridge_service, None)
    client.app.dependency_overrides.pop(get_evaluation_context_service, None)


def test_v2_web_openapi_is_an_exact_web_gate(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]
    methods = {
        path: {
            method
            for method in operations
            if method in {"get", "post", "put", "patch", "delete"}
        }
        for path, operations in paths.items()
        if path.startswith("/api/v1/integrations/evaluations/v2/web/")
    }

    assert methods == {
        "/api/v1/integrations/evaluations/v2/web/session-context": {"post"},
        "/api/v1/integrations/evaluations/v2/web/upload-context": {"get"},
        "/api/v1/integrations/evaluations/v2/web/upload-sessions": {"post"},
        "/api/v1/integrations/evaluations/v2/web/upload-sessions/{session_id}/slots/{slot_key}": {
            "put",
            "delete",
        },
        "/api/v1/integrations/evaluations/v2/web/upload-sessions/{session_id}/tasks": {
            "post"
        },
        "/api/v1/integrations/evaluations/v2/web/tasks": {"get"},
        "/api/v1/integrations/evaluations/v2/web/tasks/{task_id}": {"get"},
    }


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        f"Bearer {V1_SERVICE_TOKEN}",
        f"Bearer {WORKER_SERVICE_TOKEN}",
        f"Bearer {WEB_SERVICE_TOKEN}, Bearer {WEB_SERVICE_TOKEN}",
        f"bearer {WEB_SERVICE_TOKEN}",
    ],
)
def test_v2_web_accepts_only_its_unique_exact_bearer(
    client: TestClient,
    authorization: str | None,
) -> None:
    headers = (
        {
            "Authorization": authorization,
            DELEGATED_HEADER: str(uuid4()),
        }
        if authorization is not None
        else {DELEGATED_HEADER: str(uuid4())}
    )

    response = client.post(
        "/api/v1/integrations/evaluations/v2/web/session-context",
        headers=headers,
    )

    assert response.status_code == 401
    _assert_private_headers(response)
    assert response.json()["code"] == "integration_auth_invalid"
    assert WEB_SERVICE_TOKEN not in response.text


def test_duplicate_web_authorization_headers_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/integrations/evaluations/v2/web/session-context",
        headers=[
            (b"authorization", f"Bearer {WEB_SERVICE_TOKEN}".encode("ascii")),
            (b"authorization", f"Bearer {WEB_SERVICE_TOKEN}".encode("ascii")),
            (DELEGATED_HEADER.encode("ascii"), str(uuid4()).encode("ascii")),
        ],
    )

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_web_token_query_and_cookie_cannot_replace_authorization(
    client: TestClient,
) -> None:
    client.cookies.set("evaluation_tool_web_service_token", WEB_SERVICE_TOKEN)
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/session-context",
            params={"access_token": WEB_SERVICE_TOKEN},
            headers={DELEGATED_HEADER: str(uuid4())},
        )
    finally:
        client.cookies.delete("evaluation_tool_web_service_token")

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_v1_and_web_service_identities_remain_isolated(client: TestClient) -> None:
    v1_with_web = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=WEB_HEADERS,
    )
    v1_with_v1 = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=V1_HEADERS,
    )
    web_with_v1 = client.post(
        "/api/v1/integrations/evaluations/v2/web/session-context",
        headers={**V1_HEADERS, DELEGATED_HEADER: str(uuid4())},
    )

    assert v1_with_web.status_code == 401
    assert v1_with_v1.status_code == 200
    assert v1_with_v1.json() == {
        "api_version": "v1",
        "features": ["auth_bridge", "page_shell"],
        "legacy_writes_enabled": False,
    }
    assert web_with_v1.status_code == 401


def test_invalid_web_bearer_precedes_delegated_session_and_bridge(
    client: TestClient,
) -> None:
    session_id = uuid4()
    bridge = RecordingBridgeService(result=_bridge_context(session_id))
    _install_services(client, bridge, BombContextService())
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/session-context",
            headers={
                "Authorization": "Bearer wrong-web-token",
                DELEGATED_HEADER: str(session_id),
            },
        )
    finally:
        _clear_services(client)

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"
    assert bridge.calls == []


@pytest.mark.parametrize(
    "delegated_value",
    [None, "not-a-uuid", "{10000000-0000-4000-8000-000000000001}", "10000000000040008000000000000001", "10000000-0000-4000-8000-000000000001,10000000-0000-4000-8000-000000000001"],
)
def test_delegated_header_is_unique_canonical_and_precedes_bridge(
    client: TestClient,
    delegated_value: str | None,
) -> None:
    session_id = uuid4()
    bridge = RecordingBridgeService(result=_bridge_context(session_id))
    _install_services(client, bridge, BombContextService())
    headers = dict(WEB_HEADERS)
    if delegated_value is not None:
        headers[DELEGATED_HEADER] = delegated_value
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/session-context",
            headers=headers,
        )
    finally:
        _clear_services(client)

    assert response.status_code == 401
    _assert_private_headers(response)
    assert response.json()["code"] == "delegated_session_invalid"
    assert bridge.calls == []


def test_duplicate_delegated_headers_precede_bridge(client: TestClient) -> None:
    session_id = uuid4()
    bridge = RecordingBridgeService(result=_bridge_context(session_id))
    _install_services(client, bridge, BombContextService())
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/session-context",
            headers=[
                (b"authorization", f"Bearer {WEB_SERVICE_TOKEN}".encode("ascii")),
                (DELEGATED_HEADER.encode("ascii"), str(session_id).encode("ascii")),
                (DELEGATED_HEADER.encode("ascii"), str(session_id).encode("ascii")),
            ],
        )
    finally:
        _clear_services(client)

    assert response.status_code == 401
    assert response.json()["code"] == "delegated_session_invalid"
    assert bridge.calls == []


def test_invalid_delegated_session_precedes_selector_and_write_service(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = uuid4()
    bridge = RecordingBridgeService(
        error=EvaluationDelegatedSessionInvalid("secret delegated session detail")
    )
    create_calls: list[object] = []

    def forbidden_create(*args: object, **kwargs: object) -> NoReturn:
        create_calls.append((args, kwargs))
        raise AssertionError("write service must not run")

    monkeypatch.setattr(EvaluationUploadService, "create_session", forbidden_create)
    _install_services(client, bridge, BombContextService())
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/upload-sessions",
            headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
            json={
                "project_id": str(uuid4()),
                "upload_window_start": datetime.now(UTC).isoformat(),
            },
        )
    finally:
        _clear_services(client)

    assert response.status_code == 401
    _assert_private_headers(response)
    assert response.json()["code"] == "delegated_session_invalid"
    assert bridge.calls == [session_id]
    assert create_calls == []


def test_revoked_evaluation_access_is_stable_403(client: TestClient) -> None:
    session_id = uuid4()
    bridge = RecordingBridgeService(
        error=EvaluationAccessDenied("secret permission detail")
    )
    _install_services(client, bridge, BombContextService())
    try:
        response = client.get(
            "/api/v1/integrations/evaluations/v2/web/upload-context",
            headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
        )
    finally:
        _clear_services(client)

    assert response.status_code == 403
    _assert_private_headers(response)
    assert response.json()["code"] == "evaluation_access_denied"
    assert "secret permission detail" not in response.text


def test_v2_session_context_response_is_exact_and_never_echoes_delegation(
    client: TestClient,
) -> None:
    session_id = uuid4()
    context = _bridge_context(session_id)
    bridge = RecordingBridgeService(result=context)
    upload_context = FakeContextService()
    _install_services(client, bridge, upload_context)
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/session-context",
            headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
        )
    finally:
        _clear_services(client)

    assert response.status_code == 200
    _assert_private_headers(response)
    assert set(response.json()) == {
        "api_version",
        "identity_summary",
        "upload_available",
    }
    assert response.json()["api_version"] == "v2"
    assert response.json()["upload_available"] is False
    assert context.display_name not in response.text
    assert str(session_id) not in response.text
    assert str(context.account_id) not in response.text
    assert str(context.person_id) not in response.text
    assert bridge.calls == [session_id]
    assert upload_context.calls == [context.account_id]


def test_real_delegated_session_is_revalidated_on_every_v2_request(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    session_id = _real_delegated_session(
        db_client,
        platform_users,
        key="admin",
    )

    current = db_client.post(
        "/api/v1/integrations/evaluations/v2/web/session-context",
        headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
    )
    assert current.status_code == 200
    _assert_private_headers(current)
    assert str(session_id) not in current.text

    account = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()

    revoked = db_client.post(
        "/api/v1/integrations/evaluations/v2/web/session-context",
        headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
    )
    assert revoked.status_code == 403
    _assert_private_headers(revoked)
    assert revoked.json()["code"] == "evaluation_access_denied"


def test_v2_upload_dto_forbids_unknown_window_before_write(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client.app.state.settings.evaluation_media_root = tmp_path / "media"
    client.app.state.settings.evaluation_media_quarantine_root = tmp_path / "quarantine"
    session_id = uuid4()
    bridge_context = _bridge_context(session_id)
    bridge = RecordingBridgeService(result=bridge_context)
    upload_context = FakeContextService(targets=[object()])
    create_calls: list[object] = []

    def forbidden_create(*args: object, **kwargs: object) -> NoReturn:
        create_calls.append((args, kwargs))
        raise AssertionError("strict DTO must reject before the write call")

    monkeypatch.setattr(EvaluationUploadService, "create_session", forbidden_create)
    _install_services(client, bridge, upload_context)
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/upload-sessions",
            headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
            json={
                "project_id": str(uuid4()),
                "upload_window_start": datetime.now(UTC).isoformat(),
            },
        )
    finally:
        _clear_services(client)

    assert response.status_code == 422
    _assert_private_headers(response)
    assert response.json()["code"] == "validation_error"
    assert create_calls == []


def test_delegated_session_cannot_be_smuggled_into_task_audit_request_id(
    client: TestClient,
) -> None:
    session_id = uuid4()
    bridge_context = _bridge_context(session_id)
    bridge = RecordingBridgeService(result=bridge_context)
    upload_context = FakeContextService(targets=[object()])
    captured_request_ids: list[str] = []

    class RecordingTaskService:
        def create_task(self, *_args: object, **kwargs: object) -> EvaluationTaskRecord:
            captured_request_ids.append(str(kwargs["request_id"]))
            return EvaluationTaskRecord(
                id=uuid4(),
                public_number="EV-V2-AUDIT-TEST",
                status="manual_input",
                lock_version=1,
            )

    _install_services(client, bridge, upload_context)
    client.app.dependency_overrides[get_evaluation_task_service] = RecordingTaskService
    smuggled_request_id = f"web-{session_id}"
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/web/upload-sessions/{uuid4()}/tasks",
            headers={
                **WEB_HEADERS,
                DELEGATED_HEADER: str(session_id),
                "X-Request-ID": smuggled_request_id,
                "Idempotency-Key": f"audit-{uuid4()}",
            },
            json={},
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_task_service, None)
        _clear_services(client)

    assert response.status_code == 201
    assert len(captured_request_ids) == 1
    assert str(session_id) not in captured_request_ids[0]
    assert str(session_id) not in response.text


def test_web_service_token_cannot_be_smuggled_into_task_audit_request_id(
    client: TestClient,
) -> None:
    session_id = uuid4()
    web_token = str(uuid4())
    original_token = client.app.state.settings.evaluation_tool_web_service_token
    bridge = RecordingBridgeService(result=_bridge_context(session_id))
    upload_context = FakeContextService(targets=[object()])
    captured_request_ids: list[str] = []

    class RecordingTaskService:
        def create_task(self, *_args: object, **kwargs: object) -> EvaluationTaskRecord:
            captured_request_ids.append(str(kwargs["request_id"]))
            return EvaluationTaskRecord(
                id=uuid4(),
                public_number="EV-V2-TOKEN-AUDIT-TEST",
                status="manual_input",
                lock_version=1,
            )

    client.app.state.settings.evaluation_tool_web_service_token = SecretStr(web_token)
    _install_services(client, bridge, upload_context)
    client.app.dependency_overrides[get_evaluation_task_service] = RecordingTaskService
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/web/upload-sessions/{uuid4()}/tasks",
            headers={
                "Authorization": f"Bearer {web_token}",
                DELEGATED_HEADER: str(session_id),
                "X-Request-ID": f"web-{web_token}",
                "Idempotency-Key": f"audit-{uuid4()}",
            },
            json={},
        )
    finally:
        client.app.state.settings.evaluation_tool_web_service_token = original_token
        client.app.dependency_overrides.pop(get_evaluation_task_service, None)
        _clear_services(client)

    assert response.status_code == 201
    assert len(captured_request_ids) == 1
    assert web_token not in captured_request_ids[0]
    assert web_token not in response.text


def test_v2_write_without_upload_capability_precedes_write_service(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client.app.state.settings.evaluation_media_root = tmp_path / "media"
    client.app.state.settings.evaluation_media_quarantine_root = tmp_path / "quarantine"
    session_id = uuid4()
    bridge_context = _bridge_context(session_id)
    bridge = RecordingBridgeService(result=bridge_context)
    upload_context = FakeContextService()
    create_calls: list[object] = []

    def forbidden_create(*args: object, **kwargs: object) -> NoReturn:
        create_calls.append((args, kwargs))
        raise AssertionError("write service must not run without upload capability")

    monkeypatch.setattr(EvaluationUploadService, "create_session", forbidden_create)
    _install_services(client, bridge, upload_context)
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/web/upload-sessions",
            headers={**WEB_HEADERS, DELEGATED_HEADER: str(session_id)},
            json={"project_id": str(uuid4())},
        )
    finally:
        _clear_services(client)

    assert response.status_code == 403
    _assert_private_headers(response)
    assert response.json()["code"] == "evaluation_operation_denied"
    assert create_calls == []


def test_v2_private_headers_cover_unknown_child_and_method_not_allowed(
    client: TestClient,
) -> None:
    responses = [
        client.get("/api/v1/integrations/evaluations/v2/web/unknown-child"),
        client.get("/api/v1/integrations/evaluations/v2/web/session-context"),
    ]

    assert [response.status_code for response in responses] == [404, 405]
    for response in responses:
        _assert_private_headers(response)


def test_bridge_is_not_parameterized_by_a_settings_field() -> None:
    # The v1 wrapper must remain a dedicated identity check rather than a generic
    # helper whose caller can select arbitrary Settings attributes.
    from app.evaluations.dependencies import require_evaluation_tool_service

    assert require_evaluation_tool_service.__code__.co_argcount == 1
    assert "evaluation_tool_service_token" in require_evaluation_tool_service.__code__.co_names
    assert "evaluation_tool_web_service_token" not in (
        require_evaluation_tool_service.__code__.co_names
    )
