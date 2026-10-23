from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.evaluations.dependencies import (
    get_evaluation_adapter_certification_service,
    get_evaluation_context_service,
    get_evaluation_query_service,
    get_evaluation_result_service,
    get_evaluation_review_service,
)
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.result_service import (
    EvaluationDraftSaved,
    EvaluationResultSubmitted,
    EvaluationRevisionStarted,
)
from app.evaluations.review_schemas import (
    EvaluationAdapterCertificationInput,
    EvaluationArchiveInput,
    EvaluationManualDraftInput,
    EvaluationManualSubmitInput,
    EvaluationReopenInput,
    EvaluationResultRevisionInput,
    EvaluationReviewInput,
)
from app.evaluations.review_service import (
    EvaluationReviewed,
    EvaluationTaskStateChanged,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


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


class RecordingResultService:
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id
        self.calls: list[str] = []
        self.request_ids: list[str] = []

    def save_manual_draft(self, *_args: object, **kwargs: object) -> EvaluationDraftSaved:
        self.calls.append("draft")
        self.request_ids.append(str(kwargs["request_id"]))
        return EvaluationDraftSaved(
            task_id=self.task_id,
            task_status="manual_input",
            task_version=1,
            draft_revision=1,
            is_complete=False,
            missing_required_fields=("grade",),
            updated_at=NOW,
        )

    def submit_manual_result(
        self, *_args: object, **_kwargs: object
    ) -> EvaluationResultSubmitted:
        self.calls.append("submit")
        return EvaluationResultSubmitted(
            task_id=self.task_id,
            result_id=uuid4(),
            result_version=1,
            task_status="needs_review",
            task_version=2,
        )

    def start_correction(
        self, *_args: object, **_kwargs: object
    ) -> EvaluationRevisionStarted:
        self.calls.append("correction")
        return EvaluationRevisionStarted(
            task_id=self.task_id,
            task_status="correction_pending",
            task_version=3,
            draft_revision=1,
        )

    def start_revision(
        self, *_args: object, **_kwargs: object
    ) -> EvaluationRevisionStarted:
        self.calls.append("revision")
        return EvaluationRevisionStarted(
            task_id=self.task_id,
            task_status="manual_input",
            task_version=3,
            draft_revision=1,
        )


class RecordingReviewService:
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id

    def review(self, *_args: object, **_kwargs: object) -> EvaluationReviewed:
        return EvaluationReviewed(
            task_id=self.task_id,
            result_id=uuid4(),
            result_version=1,
            task_status="published",
            task_version=2,
            review_decision="approved",
            reviewed_at=NOW,
            published_at=NOW,
        )

    def archive(self, *_args: object, **_kwargs: object) -> EvaluationTaskStateChanged:
        return EvaluationTaskStateChanged(self.task_id, "archived", 3)

    def reopen(self, *_args: object, **_kwargs: object) -> EvaluationTaskStateChanged:
        return EvaluationTaskStateChanged(self.task_id, "queued", 4)


class RecordingCertificationService:
    def __init__(self, *, capability_error: Exception | None = None) -> None:
        self.capability_error = capability_error
        self.calls: list[str] = []
        self.certification = SimpleNamespace(
            id=uuid4(),
            project_id=uuid4(),
            adapter_id="lol",
            adapter_version="1.0.0",
            scoring_rule_version="1",
            result_schema_version="1",
            manifest_digest="a" * 64,
            certified_by_user_id=uuid4(),
            certified_at=NOW,
        )

    def require_capability(self, **_kwargs: object) -> None:
        self.calls.append("capability")
        if self.capability_error is not None:
            raise self.capability_error

    def create(self, *_args: object, **_kwargs: object) -> object:
        self.calls.append("create")
        return self.certification

    def list_certifications(self, **_kwargs: object) -> tuple[object, ...]:
        self.calls.append("list")
        return (self.certification,)


def _draft_body() -> dict[str, object]:
    return {
        "expected_lock_version": 1,
        "draft_revision": None,
        "fields": [],
        "confidences": [],
        "missing_fields": [],
        "warnings": [],
        "components": [],
        "score": None,
        "grade": None,
        "advice": None,
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


def test_mutation_routes_publish_their_strict_json_request_schemas(
    db_client: TestClient,
) -> None:
    paths_and_models = {
        (
            "/api/v1/evaluations/system/tasks/{task_id}/manual-draft",
            "put",
        ): EvaluationManualDraftInput,
        (
            "/api/v1/evaluations/system/tasks/{task_id}/submit-review",
            "post",
        ): EvaluationManualSubmitInput,
        (
            "/api/v1/evaluations/system/tasks/{task_id}/corrections",
            "post",
        ): EvaluationResultRevisionInput,
        (
            "/api/v1/evaluations/system/tasks/{task_id}/revisions",
            "post",
        ): EvaluationResultRevisionInput,
        (
            "/api/v1/evaluations/admin/system-tasks/{task_id}/manual-draft",
            "put",
        ): EvaluationManualDraftInput,
        (
            "/api/v1/evaluations/admin/system-tasks/{task_id}/reviews",
            "post",
        ): EvaluationReviewInput,
        (
            "/api/v1/evaluations/admin/system-tasks/{task_id}/archive",
            "post",
        ): EvaluationArchiveInput,
        (
            "/api/v1/evaluations/admin/system-tasks/{task_id}/reopen",
            "post",
        ): EvaluationReopenInput,
        (
            "/api/v1/evaluations/admin/adapter-certifications",
            "post",
        ): EvaluationAdapterCertificationInput,
    }
    paths = db_client.app.openapi()["paths"]

    for (path, method), model in paths_and_models.items():
        request_body = paths[path][method]["requestBody"]
        assert request_body["required"] is True
        assert (
            request_body["content"]["application/json"]["schema"]
            == _without_null_defaults(model.model_json_schema())
        )


def test_account_result_routes_preauthorize_and_return_minimal_responses(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    task_id = uuid4()
    query = RecordingActionQuery()
    service = RecordingResultService(task_id)
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_result_service] = lambda: service
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "route-idempotency-1"}
    try:
        draft = db_client.put(
            f"/api/v1/evaluations/system/tasks/{task_id}/manual-draft",
            headers={"X-CSRF-Token": csrf},
            json=_draft_body(),
        )
        submitted = db_client.post(
            f"/api/v1/evaluations/system/tasks/{task_id}/submit-review",
            headers=headers,
            json={"expected_lock_version": 1, "draft_revision": 1},
        )
        correction = db_client.post(
            f"/api/v1/evaluations/system/tasks/{task_id}/corrections",
            headers=headers,
            json={"expected_lock_version": 2, "mode": "manual"},
        )
        revision = db_client.post(
            f"/api/v1/evaluations/system/tasks/{task_id}/revisions",
            headers=headers,
            json={"expected_lock_version": 2, "mode": "manual"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_result_service, None)

    assert draft.status_code == 200, draft.text
    assert set(draft.json()) == {
        "api_version",
        "task_id",
        "task_status",
        "task_version",
        "draft_revision",
        "is_complete",
        "missing_required_fields",
        "updated_at",
    }
    assert submitted.status_code == 201, submitted.text
    assert set(submitted.json()) == {
        "api_version",
        "task_id",
        "result_id",
        "result_version",
        "task_status",
        "task_version",
    }
    assert correction.status_code == 201, correction.text
    assert revision.status_code == 201, revision.text
    assert service.calls == ["draft", "submit", "correction", "revision"]
    assert [call[2] for call in query.calls] == ["evaluation_jobs.manual_input"] * 4


def test_admin_review_lifecycle_and_certification_routes(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    task_id = uuid4()
    query = RecordingActionQuery()
    review = RecordingReviewService(task_id)
    certification = RecordingCertificationService()
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_review_service] = lambda: review
    db_client.app.dependency_overrides[get_evaluation_adapter_certification_service] = (
        lambda: certification
    )
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "admin-idempotency-1"}
    certificate_body = {
        "project_id": str(certification.certification.project_id),
        "adapter_id": "lol",
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "manifest_digest": "a" * 64,
    }
    try:
        reviewed = db_client.post(
            f"/api/v1/evaluations/admin/system-tasks/{task_id}/reviews",
            headers=headers,
            json={"expected_lock_version": 1, "decision": "approved"},
        )
        archived = db_client.post(
            f"/api/v1/evaluations/admin/system-tasks/{task_id}/archive",
            headers=headers,
            json={"expected_lock_version": 2, "reason": "结束处理"},
        )
        reopened = db_client.post(
            f"/api/v1/evaluations/admin/system-tasks/{task_id}/reopen",
            headers=headers,
            json={
                "expected_lock_version": 3,
                "resume": "automatic",
                "reason": "恢复处理",
            },
        )
        created = db_client.post(
            "/api/v1/evaluations/admin/adapter-certifications",
            headers=headers,
            json=certificate_body,
        )
        listed = db_client.get("/api/v1/evaluations/admin/adapter-certifications")
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_review_service, None)
        db_client.app.dependency_overrides.pop(
            get_evaluation_adapter_certification_service,
            None,
        )

    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["review_decision"] == "approved"
    assert set(reviewed.json()) == {
        "api_version",
        "task_id",
        "result_id",
        "result_version",
        "task_status",
        "task_version",
        "review_decision",
        "reviewed_at",
        "published_at",
    }
    assert archived.status_code == 200 and archived.json()["task_status"] == "archived"
    assert set(archived.json()) == {
        "api_version",
        "task_id",
        "task_status",
        "task_version",
    }
    assert reopened.status_code == 200 and reopened.json()["task_status"] == "queued"
    assert set(reopened.json()) == {
        "api_version",
        "task_id",
        "task_status",
        "task_version",
    }
    assert created.status_code == 201, created.text
    assert created.json()["certification"]["manifest_digest"] == "a" * 64
    assert set(created.json()) == {"api_version", "certification"}
    assert set(created.json()["certification"]) == {
        "id",
        "project_id",
        "adapter_id",
        "adapter_version",
        "scoring_rule_version",
        "result_schema_version",
        "manifest_digest",
        "certified_by_user_id",
        "certified_at",
    }
    assert listed.status_code == 200
    assert set(listed.json()) == {"api_version", "items"}
    assert listed.json()["items"][0]["id"] == created.json()["certification"]["id"]
    assert [call[2] for call in query.calls] == [
        "evaluation_jobs.review",
        "evaluation_jobs.match",
        "evaluation_jobs.match",
    ]
    assert certification.calls == ["capability", "create", "list"]


def test_adapter_certification_checks_global_capability_before_parsing_body(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    certification = RecordingCertificationService(
        capability_error=EvaluationOperationDenied("capability sentinel")
    )
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_adapter_certification_service] = (
        lambda: certification
    )
    malicious_key = "private_phone_13800138000"
    try:
        response = db_client.post(
            "/api/v1/evaluations/admin/adapter-certifications",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "certification-denied-1",
            },
            json={malicious_key: "secret"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(
            get_evaluation_adapter_certification_service,
            None,
        )

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_operation_denied"
    assert malicious_key not in response.text
    assert "capability sentinel" not in response.text
    assert certification.calls == ["capability"]


def test_adapter_certification_list_requires_global_capability(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")

    class DeniedCertificationService(RecordingCertificationService):
        def list_certifications(self, **_kwargs: object) -> tuple[object, ...]:
            raise EvaluationOperationDenied("capability sentinel")

    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_adapter_certification_service] = (
        lambda: DeniedCertificationService()
    )
    try:
        response = db_client.get(
            "/api/v1/evaluations/admin/adapter-certifications",
            headers={"X-CSRF-Token": csrf},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(
            get_evaluation_adapter_certification_service,
            None,
        )

    assert response.status_code == 403
    assert response.json()["code"] == "evaluation_operation_denied"
    assert "capability sentinel" not in response.text


def test_result_mutations_reject_missing_and_duplicate_idempotency_keys(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    task_id = uuid4()
    query = RecordingActionQuery()
    service = RecordingResultService(task_id)
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_result_service] = lambda: service
    try:
        missing = db_client.post(
            f"/api/v1/evaluations/system/tasks/{task_id}/submit-review",
            headers={"X-CSRF-Token": csrf},
            json={"expected_lock_version": 1, "draft_revision": 1},
        )
        duplicate = db_client.post(
            f"/api/v1/evaluations/system/tasks/{task_id}/submit-review",
            headers=[
                ("X-CSRF-Token", csrf),
                ("Idempotency-Key", "duplicate-a"),
                ("Idempotency-Key", "duplicate-b"),
            ],
            json={"expected_lock_version": 1, "draft_revision": 1},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_result_service, None)

    for response in (missing, duplicate):
        assert response.status_code == 422
        assert response.json()["code"] == "evaluation_idempotency_key_invalid"
    assert service.calls == []


def test_manual_body_text_cannot_be_smuggled_into_audit_request_id(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    task_id = uuid4()
    query = RecordingActionQuery()
    service = RecordingResultService(task_id)
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_result_service] = lambda: service
    private_request_fragment = "secretchunk"
    private_advice = f"{private_request_fragment}X"
    try:
        response = db_client.put(
            f"/api/v1/evaluations/system/tasks/{task_id}/manual-draft",
            headers={
                "X-CSRF-Token": csrf,
                "X-Request-ID": private_request_fragment,
            },
            json={**_draft_body(), "advice": private_advice},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_result_service, None)

    assert response.status_code == 200, response.text
    assert len(service.request_ids) == 1
    assert private_request_fragment not in service.request_ids[0]


def test_private_task_selection_precedes_dynamic_manual_body_validation(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    task_id = uuid4()
    query = RecordingActionQuery(
        error=EvaluationResourceNotFound("private target sentinel")
    )

    class BombResultService:
        def save_manual_draft(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("body and mutation service must not run")

    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_result_service] = BombResultService
    malicious_key = "private_phone_13800138000"
    try:
        response = db_client.put(
            f"/api/v1/evaluations/system/tasks/{task_id}/manual-draft",
            headers={"X-CSRF-Token": csrf},
            json={
                **_draft_body(),
                "fields": [{"key": malicious_key, "value": "secret"}],
            },
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_result_service, None)

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "评价资源不存在",
        "code": "evaluation_resource_not_found",
    }
    assert malicious_key not in response.text
    assert "private target sentinel" not in response.text
