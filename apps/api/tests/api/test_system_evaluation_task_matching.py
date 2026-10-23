from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.evaluations.dependencies import (
    get_evaluation_context_service,
    get_evaluation_matching_service,
    get_evaluation_query_service,
)
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.matching_service import EvaluationTaskMatched
from app.evaluations.review_schemas import EvaluationTaskMatchInput


def _login(client: TestClient, platform_users, key: str) -> str:
    phone, password, _role = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


class CenterAllowed:
    def center_allowed(self, _account_id: UUID) -> bool:
        return True


class RecordingActionQuery:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[UUID, UUID, str]] = []

    def require_action_task(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        permission: str,
        occurred_at: datetime,
        for_update: bool = False,
    ) -> object:
        assert occurred_at.tzinfo is not None
        assert for_update is False
        self.calls.append((task_id, actor_user_id, permission))
        if self.error is not None:
            raise self.error
        return object()

    def list_match_targets(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        query: str,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> SimpleNamespace:
        assert occurred_at.tzinfo is not None
        self.calls.append((task_id, actor_user_id, "evaluation_jobs.match"))
        return SimpleNamespace(
            items=(
                SimpleNamespace(
                    student_person_id=UUID("10000000-0000-4000-8000-000000000001"),
                    student_name="候选学员",
                    student_number="S-001",
                    enrollment_id=UUID("20000000-0000-4000-8000-000000000001"),
                    cohort_id=UUID("30000000-0000-4000-8000-000000000001"),
                    cohort_name="2026 夏季营",
                    class_id=UUID("40000000-0000-4000-8000-000000000001"),
                    class_name="英雄联盟一班",
                ),
            ),
            total=1,
            page=page,
            page_size=page_size,
        )


class RecordingMatchingService:
    def __init__(
        self,
        *,
        project_id: UUID,
        error: Exception | None = None,
    ) -> None:
        self.project_id = project_id
        self.error = error
        self.calls: list[dict[str, object]] = []

    def confirm_task_match(
        self,
        task_id: UUID,
        input: EvaluationTaskMatchInput,
        **kwargs: object,
    ) -> EvaluationTaskMatched:
        self.calls.append(
            {
                "task_id": task_id,
                "input": input,
                **kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        return EvaluationTaskMatched(
            task_id=task_id,
            student_person_id=input.student_person_id,
            enrollment_id=input.enrollment_id,
            cohort_id=input.cohort_id,
            project_id=self.project_id,
            class_id=input.class_id,
            task_status="manual_input",
            task_version=input.expected_lock_version + 1,
        )


def _match_body() -> dict[str, object]:
    return {
        "expected_lock_version": 1,
        "student_person_id": str(uuid4()),
        "enrollment_id": str(uuid4()),
        "cohort_id": str(uuid4()),
        "class_id": str(uuid4()),
    }


def _without_null_defaults(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_null_defaults(item)
            for key, item in value.items()
            if not (key == "default" and item is None)
        }
    if isinstance(value, list):
        return [_without_null_defaults(item) for item in value]
    return value


def test_match_routes_publish_their_strict_json_request_schema(
    db_client: TestClient,
) -> None:
    paths = db_client.app.openapi()["paths"]

    for path in (
        "/api/v1/evaluations/system/tasks/{task_id}/match",
        "/api/v1/evaluations/admin/system-tasks/{task_id}/match",
    ):
        request_body = paths[path]["post"]["requestBody"]
        assert request_body["required"] is True
        assert request_body["content"]["application/json"]["schema"] == _without_null_defaults(
            EvaluationTaskMatchInput.model_json_schema()
        )


def test_account_and_admin_match_routes_preauthorize_and_return_minimal_responses(
    db_client: TestClient,
    platform_users,
) -> None:
    task_id = uuid4()
    project_id = uuid4()
    query = RecordingActionQuery()
    service = RecordingMatchingService(project_id=project_id)
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_matching_service] = lambda: service
    responses = []
    private_request_fragment = "private-match-request-fragment"
    try:
        for user_key, path, idempotency_value in (
            (
                "self_student",
                f"/api/v1/evaluations/system/tasks/{task_id}/match",
                "account-match-route-1",
            ),
            (
                "admin",
                f"/api/v1/evaluations/admin/system-tasks/{task_id}/match",
                "admin-match-route-1",
            ),
        ):
            csrf = _login(db_client, platform_users, user_key)
            responses.append(
                db_client.post(
                    path,
                    headers={
                        "X-CSRF-Token": csrf,
                        "Idempotency-Key": idempotency_value,
                        "X-Request-ID": private_request_fragment,
                    },
                    json=_match_body(),
                )
            )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_matching_service, None)

    for response in responses:
        assert response.status_code == 200, response.text
        assert set(response.json()) == {
            "api_version",
            "task_id",
            "student_person_id",
            "enrollment_id",
            "cohort_id",
            "project_id",
            "class_id",
            "task_status",
            "task_version",
        }
        assert response.json()["project_id"] == str(project_id)
        assert response.json()["task_status"] == "manual_input"
        assert response.json()["task_version"] == 2
    assert [call[2] for call in query.calls] == ["evaluation_jobs.match"] * 2
    assert [call["idempotency_key"] for call in service.calls] == [
        "account-match-route-1",
        "admin-match-route-1",
    ]
    assert all(str(call["request_id"]).startswith("evaluation-mutation-") for call in service.calls)
    assert all(private_request_fragment not in str(call["request_id"]) for call in service.calls)


def test_admin_match_target_directory_is_task_bound_paginated_and_minimal(
    db_client: TestClient,
    platform_users,
) -> None:
    task_id = uuid4()
    query = RecordingActionQuery()
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    try:
        _login(db_client, platform_users, "admin")
        response = db_client.get(
            f"/api/v1/evaluations/admin/system-tasks/{task_id}/match-targets",
            params={"query": "候选", "page": 1, "page_size": 20},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "api_version": "v2",
        "items": [
            {
                "student_person_id": "10000000-0000-4000-8000-000000000001",
                "student_name": "候选学员",
                "student_number": "S-001",
                "enrollment_id": "20000000-0000-4000-8000-000000000001",
                "cohort_id": "30000000-0000-4000-8000-000000000001",
                "cohort_name": "2026 夏季营",
                "class_id": "40000000-0000-4000-8000-000000000001",
                "class_name": "英雄联盟一班",
            }
        ],
        "total": 1,
        "page": 1,
        "page_size": 20,
    }
    assert len(query.calls) == 1
    assert query.calls[0][0] == task_id
    assert query.calls[0][2] == "evaluation_jobs.match"


@pytest.mark.parametrize(
    ("query_error", "expected_status", "expected_code"),
    [
        (
            EvaluationOperationDenied("capability sentinel"),
            403,
            "evaluation_operation_denied",
        ),
        (
            EvaluationResourceNotFound("private target sentinel"),
            404,
            "evaluation_resource_not_found",
        ),
    ],
)
def test_match_routes_authorize_before_parsing_dynamic_body(
    db_client: TestClient,
    platform_users,
    query_error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    task_id = uuid4()
    query = RecordingActionQuery(error=query_error)

    class BombMatchingService:
        def confirm_task_match(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("body and mutation service must not run")

    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_matching_service] = BombMatchingService
    malicious_key = "private_phone_13800138000"
    try:
        for user_key, path in (
            (
                "self_student",
                f"/api/v1/evaluations/system/tasks/{task_id}/match",
            ),
            (
                "admin",
                f"/api/v1/evaluations/admin/system-tasks/{task_id}/match",
            ),
        ):
            csrf = _login(db_client, platform_users, user_key)
            response = db_client.post(
                path,
                headers={
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": "private-match-route-1",
                },
                json={malicious_key: "secret"},
            )
            assert response.status_code == expected_status
            assert response.json()["code"] == expected_code
            assert malicious_key not in response.text
            assert "sentinel" not in response.text
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_matching_service, None)


def test_match_routes_reject_missing_and_duplicate_idempotency_keys(
    db_client: TestClient,
    platform_users,
) -> None:
    task_id = uuid4()
    query = RecordingActionQuery()
    service = RecordingMatchingService(project_id=uuid4())
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_matching_service] = lambda: service
    try:
        csrf = _login(db_client, platform_users, "self_student")
        path = f"/api/v1/evaluations/system/tasks/{task_id}/match"
        missing = db_client.post(
            path,
            headers={"X-CSRF-Token": csrf},
            json=_match_body(),
        )
        duplicate = db_client.post(
            path,
            headers=[
                ("X-CSRF-Token", csrf),
                ("Idempotency-Key", "duplicate-a"),
                ("Idempotency-Key", "duplicate-b"),
            ],
            json=_match_body(),
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_matching_service, None)

    for response in (missing, duplicate):
        assert response.status_code == 422
        assert response.json()["code"] == "evaluation_idempotency_key_invalid"
    assert service.calls == []


@pytest.mark.parametrize(
    ("service_error", "expected_status", "expected_code"),
    [
        (
            EvaluationIdempotencyConflict("idempotency sentinel"),
            409,
            "evaluation_idempotency_conflict",
        ),
        (
            EvaluationTaskVersionConflict("version sentinel"),
            409,
            "evaluation_stale_version",
        ),
        (
            EvaluationTaskStateConflict("state sentinel"),
            409,
            "evaluation_invalid_transition",
        ),
        (
            EvaluationResourceNotFound("private target sentinel"),
            404,
            "evaluation_resource_not_found",
        ),
        (
            EvaluationOperationDenied("authorization race sentinel"),
            404,
            "evaluation_resource_not_found",
        ),
    ],
)
def test_match_route_maps_service_conflicts_without_leaking_details(
    db_client: TestClient,
    platform_users,
    service_error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    task_id = uuid4()
    query = RecordingActionQuery()
    service = RecordingMatchingService(project_id=uuid4(), error=service_error)
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_matching_service] = lambda: service
    try:
        response = db_client.post(
            f"/api/v1/evaluations/system/tasks/{task_id}/match",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "match-conflict-route-1",
            },
            json=_match_body(),
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_matching_service, None)

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert "sentinel" not in response.text
