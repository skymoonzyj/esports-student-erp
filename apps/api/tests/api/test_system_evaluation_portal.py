from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.evaluations.dependencies import (
    get_evaluation_context_service,
    get_evaluation_media_service,
    get_evaluation_query_service,
    require_evaluation_delegated_session,
)
from app.evaluations.errors import (
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    MediaAssetNotFound,
)
from app.evaluations.media_service import ProtectedMediaFile
from app.evaluations.query_service import (
    EvaluationAllowedActions,
    EvaluationQueryFilters,
    EvaluationResultProjection,
    EvaluationTaskFilterOption,
    EvaluationTaskFilterOptions,
    EvaluationTaskPage,
    EvaluationTaskProjection,
)
from app.evaluations.schemas import EvaluationToolSessionContext

WEB_TOKEN = "evaluation-local-web-service-token-2026-change-me"


def _login(client: TestClient, platform_users, key: str) -> None:
    phone, password, _role = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200


class CenterAllowed:
    def center_allowed(self, _account_id: UUID) -> bool:
        return True


def _projection() -> EvaluationTaskProjection:
    result_id = uuid4()
    result = EvaluationResultProjection(
        id=result_id,
        version=1,
        source="manual",
        fields={"kills": 12},
        confidences={"kills": 1},
        warnings=(),
        components=(
            {
                "key": "combat",
                "score": 88,
                "weight": 1,
                "summary": "表现稳定",
            },
        ),
        missing_fields=(),
        score=Decimal(88),
        grade="A",
        advice="继续保持训练节奏",
        adapter_id=None,
        adapter_version=None,
        scoring_rule_version=None,
        result_schema_version="1",
        review_decision="approved",
        reviewed_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        rejection_reason=None,
        published_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC),
        previous_version_id=None,
    )
    return EvaluationTaskProjection(
        id=uuid4(),
        public_number="EV-PORTAL-SAFE",
        status="published",
        lock_version=2,
        created_at=datetime(2026, 8, 8, 10, 0, tzinfo=UTC),
        student_person_id=uuid4(),
        enrollment_id=uuid4(),
        cohort_id=uuid4(),
        project_id=uuid4(),
        class_id=uuid4(),
        identity_summary="学* / S***01",
        cohort_name="2026 夏季营",
        project_name="英雄联盟",
        class_name="标准班",
        media_count=2,
        current_result=result,
        published_result=result,
        result_versions=(result,),
        allowed_actions=EvaluationAllowedActions(view_preview=True),
    )


class RecordingQueryService:
    def __init__(self, projection: EvaluationTaskProjection) -> None:
        self.projection = projection
        self.calls: list[tuple[str, UUID, object]] = []

    def list_account_tasks(
        self,
        *,
        actor_user_id: UUID,
        filters: EvaluationQueryFilters,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> EvaluationTaskPage:
        assert occurred_at.tzinfo is not None
        self.calls.append(("list_account", actor_user_id, filters))
        return EvaluationTaskPage((self.projection,), 1, page, page_size)

    def get_account_task(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskProjection:
        assert occurred_at.tzinfo is not None
        assert task_id == self.projection.id
        self.calls.append(("get_account", actor_user_id, task_id))
        return self.projection

    def list_admin_tasks(
        self,
        *,
        actor_user_id: UUID,
        filters: EvaluationQueryFilters,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> EvaluationTaskPage:
        assert occurred_at.tzinfo is not None
        self.calls.append(("list_admin", actor_user_id, filters))
        return EvaluationTaskPage((self.projection,), 1, page, page_size)

    def get_admin_task(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskProjection:
        assert occurred_at.tzinfo is not None
        assert task_id == self.projection.id
        self.calls.append(("get_admin", actor_user_id, task_id))
        return self.projection

    def dashboard_counts(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> SimpleNamespace:
        assert occurred_at.tzinfo is not None
        self.calls.append(("dashboard", actor_user_id, occurred_at))
        return SimpleNamespace(
            upload_available=True,
            processing=2,
            pending_match=3,
            manual_input=4,
            needs_review=5,
            rejected=6,
            published=7,
        )

    def _filter_options(self, actor_user_id: UUID, kind: str) -> EvaluationTaskFilterOptions:
        self.calls.append((kind, actor_user_id, self.projection.id))
        return EvaluationTaskFilterOptions(
            cohorts=(EvaluationTaskFilterOption(self.projection.cohort_id, "2026 夏季营"),),
            projects=(EvaluationTaskFilterOption(self.projection.project_id, "英雄联盟"),),
            classes=(EvaluationTaskFilterOption(self.projection.class_id, "标准班"),),
            students=(
                EvaluationTaskFilterOption(self.projection.student_person_id, "学* / S***01"),
            ),
        )

    def account_filter_options(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskFilterOptions:
        assert occurred_at.tzinfo is not None
        return self._filter_options(actor_user_id, "account_filters")

    def admin_filter_options(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskFilterOptions:
        assert occurred_at.tzinfo is not None
        return self._filter_options(actor_user_id, "admin_filters")

    def get_account_task_workspace(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> SimpleNamespace:
        assert occurred_at.tzinfo is not None
        assert task_id == self.projection.id
        self.calls.append(("workspace", actor_user_id, task_id))
        return SimpleNamespace(
            task_id=task_id,
            task_version=self.projection.lock_version,
            profile=SimpleNamespace(
                mode="manual_only",
                adapter_id=None,
                adapter_version=None,
                scoring_rule_version=None,
                result_schema_version="1",
                required_fields=("kills", "score", "grade"),
                media_slots=(
                    SimpleNamespace(
                        key="profile",
                        label="个人数据",
                        position=1,
                        required=True,
                    ),
                ),
            ),
            media=(
                SimpleNamespace(
                    id=UUID("30000000-0000-4000-8000-000000000001"),
                    slot_key="profile",
                    position=1,
                    mime_type="image/png",
                    size_bytes=100,
                    width_pixels=10,
                    height_pixels=10,
                ),
            ),
            draft=None,
        )


class RecordingTaskMediaQuery:
    def __init__(
        self,
        events: list[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.error = error

    def require_task_media(
        self,
        task_id: UUID,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> object:
        assert occurred_at.tzinfo is not None
        assert actor_user_id
        assert task_id
        assert media_id
        self.events.append("authorize-task-media")
        if self.error is not None:
            raise self.error
        return object()


class RecordingPreviewService:
    def __init__(
        self,
        events: list[str],
        path: Path,
        *,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.path = path
        self.error = error

    def read_preview(
        self,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> ProtectedMediaFile:
        assert occurred_at.tzinfo is not None
        assert actor_user_id
        assert media_id
        self.events.append("read-preview")
        if self.error is not None:
            raise self.error
        return ProtectedMediaFile(
            path=self.path,
            mime_type="image/webp",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": 'inline; filename="evaluation-image.webp"',
            },
        )


def _assert_safe_task_payload(payload: dict[str, object]) -> None:
    assert set(payload) == {
        "id",
        "public_number",
        "status",
        "lock_version",
        "created_at",
        "student_person_id",
        "enrollment_id",
        "cohort_id",
        "project_id",
        "class_id",
        "identity_summary",
        "cohort_name",
        "project_name",
        "class_name",
        "media_count",
        "current_result",
        "published_result",
        "result_versions",
        "allowed_actions",
        "allowed_reopen_modes",
    }
    assert payload["identity_summary"] == "学* / S***01"
    assert payload["allowed_actions"] == {
        "match": False,
        "save_manual_draft": False,
        "submit_review": False,
        "create_correction": False,
        "create_revision": False,
        "review": False,
        "archive": False,
        "reopen": False,
        "view_preview": True,
    }
    assert payload["allowed_reopen_modes"] == []
    serialized = repr(payload).lower()
    for forbidden in (
        "phone",
        "identity_number",
        "game_id",
        "storage_key",
        "physical_path",
        "raw_ocr",
    ):
        assert forbidden not in serialized


def test_account_and_admin_task_queries_share_the_safe_projection(
    db_client: TestClient,
    platform_users,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        account_page = db_client.get(
            "/api/v1/evaluations/system/tasks",
            params={"status": "published", "page": 1, "page_size": 20},
        )
        account_detail = db_client.get(f"/api/v1/evaluations/system/tasks/{projection.id}")
        _login(db_client, platform_users, "admin")
        admin_page = db_client.get(
            "/api/v1/evaluations/admin/system-tasks",
            params={"queue": "published", "page": 1, "page_size": 20},
        )
        admin_detail = db_client.get(f"/api/v1/evaluations/admin/system-tasks/{projection.id}")
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    for response in (account_page, account_detail, admin_page, admin_detail):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store, private"
    assert set(account_page.json()) == {"api_version", "items", "total", "page", "page_size"}
    assert account_page.json()["api_version"] == "v2"
    assert account_page.json()["total"] == 1
    _assert_safe_task_payload(account_page.json()["items"][0])
    _assert_safe_task_payload(account_detail.json()["task"])
    _assert_safe_task_payload(admin_page.json()["items"][0])
    _assert_safe_task_payload(admin_detail.json()["task"])
    assert service.calls[0][0] == "list_account"
    assert service.calls[0][2].status == "published"
    assert service.calls[2][0] == "list_admin"
    assert service.calls[2][2].queue == "published"


def test_account_dashboard_exposes_the_shared_safe_count_projection(
    db_client: TestClient,
    platform_users,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        response = db_client.get("/api/v1/evaluations/system/dashboard")
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "api_version": "v2",
        "upload_available": True,
        "processing": 2,
        "pending_match": 3,
        "manual_input": 4,
        "needs_review": 5,
        "rejected": 6,
        "published": 7,
    }
    assert service.calls[-1][0] == "dashboard"


def test_account_and_admin_filter_options_share_the_authorized_safe_projection(
    db_client: TestClient,
    platform_users,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        account = db_client.get("/api/v1/evaluations/system/task-filter-options")
        _login(db_client, platform_users, "admin")
        admin = db_client.get("/api/v1/evaluations/admin/system-task-filter-options")
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    for response in (account, admin):
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store, private"
        assert response.json() == {
            "api_version": "v2",
            "cohorts": [{"id": str(projection.cohort_id), "label": "2026 夏季营"}],
            "projects": [{"id": str(projection.project_id), "label": "英雄联盟"}],
            "classes": [{"id": str(projection.class_id), "label": "标准班"}],
            "students": [
                {"id": str(projection.student_person_id), "label": "学* / S***01"},
            ],
        }
    assert [call[0] for call in service.calls] == ["account_filters", "admin_filters"]


def test_account_workspace_exposes_only_safe_profile_media_and_draft_fields(
    db_client: TestClient,
    platform_users,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        response = db_client.get(f"/api/v1/evaluations/system/tasks/{projection.id}/workspace")
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json() == {
        "api_version": "v2",
        "task_id": str(projection.id),
        "task_version": 2,
        "profile": {
            "mode": "manual_only",
            "adapter_id": None,
            "adapter_version": None,
            "scoring_rule_version": None,
            "result_schema_version": "1",
            "required_fields": ["kills", "score", "grade"],
            "media_slots": [
                {
                    "key": "profile",
                    "label": "个人数据",
                    "position": 1,
                    "required": True,
                }
            ],
        },
        "media": [
            {
                "id": "30000000-0000-4000-8000-000000000001",
                "slot_key": "profile",
                "position": 1,
                "mime_type": "image/png",
                "size_bytes": 100,
                "width_pixels": 10,
                "height_pixels": 10,
            }
        ],
        "draft": None,
    }
    assert "snapshot" not in response.text.casefold()
    assert "storage" not in response.text.casefold()


def test_account_task_query_forwards_published_history_filter(
    db_client: TestClient,
    platform_users,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        response = db_client.get(
            "/api/v1/evaluations/system/tasks",
            params={"published_only": "true"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    assert response.status_code == 200, response.text
    assert service.calls[-1][0] == "list_account"
    assert service.calls[-1][2].published_only is True


def test_account_task_query_forwards_aggregate_queue_filter(
    db_client: TestClient,
    platform_users,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        response = db_client.get(
            "/api/v1/evaluations/system/tasks",
            params={"queue": "processing"},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    assert response.status_code == 200, response.text
    assert service.calls[-1][0] == "list_account"
    assert service.calls[-1][2].queue == "processing"


def test_task_media_preview_authorizes_the_task_binding_before_reading_bytes(
    db_client: TestClient,
    platform_users,
    tmp_path: Path,
) -> None:
    task_id = uuid4()
    media_id = uuid4()
    preview_path = tmp_path / "preview.webp"
    preview_path.write_bytes(b"safe-preview")
    events: list[str] = []
    query = RecordingTaskMediaQuery(events)
    media = RecordingPreviewService(events, preview_path)
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_media_service] = lambda: media
    try:
        _login(db_client, platform_users, "self_student")
        response = db_client.get(
            f"/api/v1/evaluations/system/tasks/{task_id}/media/{media_id}/preview"
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_media_service, None)

    assert response.status_code == 200, response.text
    assert response.content == b"safe-preview"
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert events == ["authorize-task-media", "read-preview"]


@pytest.mark.parametrize(
    ("query_error", "media_error", "expected_status", "expected_code", "events"),
    [
        (
            EvaluationOperationDenied("capability sentinel"),
            None,
            403,
            "evaluation_operation_denied",
            ["authorize-task-media"],
        ),
        (
            EvaluationResourceNotFound("private binding sentinel"),
            None,
            404,
            "evaluation_resource_not_found",
            ["authorize-task-media"],
        ),
        (
            None,
            MediaAssetNotFound("media race sentinel"),
            404,
            "evaluation_resource_not_found",
            ["authorize-task-media", "read-preview"],
        ),
    ],
)
def test_task_media_preview_failures_are_private_and_never_skip_task_binding(
    db_client: TestClient,
    platform_users,
    tmp_path: Path,
    query_error: Exception | None,
    media_error: Exception | None,
    expected_status: int,
    expected_code: str,
    events: list[str],
) -> None:
    recorded_events: list[str] = []
    query = RecordingTaskMediaQuery(recorded_events, error=query_error)
    media = RecordingPreviewService(
        recorded_events,
        tmp_path / "unused.webp",
        error=media_error,
    )
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    db_client.app.dependency_overrides[get_evaluation_query_service] = lambda: query
    db_client.app.dependency_overrides[get_evaluation_media_service] = lambda: media
    try:
        _login(db_client, platform_users, "self_student")
        response = db_client.get(
            f"/api/v1/evaluations/system/tasks/{uuid4()}/media/{uuid4()}/preview"
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_media_service, None)

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert "sentinel" not in response.text
    assert recorded_events == events


def test_v2_web_task_queries_use_the_delegated_account_without_scope_expansion(
    client: TestClient,
) -> None:
    projection = _projection()
    service = RecordingQueryService(projection)
    account_id = uuid4()
    session_id = uuid4()
    client.app.dependency_overrides[get_evaluation_query_service] = lambda: service
    client.app.dependency_overrides[require_evaluation_delegated_session] = lambda: (
        EvaluationToolSessionContext(
            account_id=account_id,
            person_id=uuid4(),
            display_name="委派学员",
            session_id=session_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    headers = {
        "Authorization": f"Bearer {WEB_TOKEN}",
        "X-Evaluation-Delegated-Session": str(session_id),
    }
    try:
        page = client.get(
            "/api/v1/integrations/evaluations/v2/web/tasks",
            headers=headers,
        )
        detail = client.get(
            f"/api/v1/integrations/evaluations/v2/web/tasks/{projection.id}",
            headers=headers,
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        client.app.dependency_overrides.pop(require_evaluation_delegated_session, None)

    assert page.status_code == 200, page.text
    assert detail.status_code == 200, detail.text
    _assert_safe_task_payload(page.json()["items"][0])
    _assert_safe_task_payload(detail.json()["task"])
    assert [call[1] for call in service.calls] == [account_id, account_id]


def test_task_query_private_404_and_operational_403_remain_distinct(
    db_client: TestClient,
    platform_users,
) -> None:
    class RaisingQueryService:
        def get_account_task(self, *_args: object, **_kwargs: object) -> None:
            raise EvaluationResourceNotFound("private target sentinel")

        def list_admin_tasks(self, **_kwargs: object) -> None:
            raise EvaluationOperationDenied("capability sentinel")

    db_client.app.dependency_overrides[get_evaluation_query_service] = RaisingQueryService
    db_client.app.dependency_overrides[get_evaluation_context_service] = CenterAllowed
    try:
        _login(db_client, platform_users, "self_student")
        private = db_client.get(f"/api/v1/evaluations/system/tasks/{uuid4()}")
        operational = db_client.get("/api/v1/evaluations/admin/system-tasks")
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_query_service, None)
        db_client.app.dependency_overrides.pop(get_evaluation_context_service, None)

    assert private.status_code == 404
    assert private.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "评价资源不存在",
        "code": "evaluation_resource_not_found",
    }
    assert "private target sentinel" not in private.text
    assert operational.status_code == 403
    assert operational.json()["code"] == "evaluation_operation_denied"
    assert "capability sentinel" not in operational.text
