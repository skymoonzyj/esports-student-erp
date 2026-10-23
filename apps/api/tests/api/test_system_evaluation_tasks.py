from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.dependencies import get_evaluation_task_service
from app.evaluations.errors import EvaluationIdempotencyConflict
from app.evaluations.media_models import EvaluationMediaAsset
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.task_models import (
    EvaluationIdempotencyKey,
    SystemEvaluationTask,
    SystemEvaluationUploadSession,
    SystemEvaluationUploadSessionMedia,
)
from app.evaluations.task_service import EvaluationTaskRecord
from app.identity.models import StudentProfile
from app.rbac.models import Permission, UserPermissionOverride
from app.teaching.models import Cohort, GameProject, TeachingClass


@dataclass(frozen=True)
class SystemEvaluationApiScenario:
    account_id: UUID
    student_person_id: UUID
    project_id: UUID
    cohort_id: UUID
    class_id: UUID
    enrollment_id: UUID


def _login(client: TestClient, platform_users, key: str) -> str:
    phone, password, _role = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _png_bytes(color: tuple[int, int, int] = (22, 88, 154)) -> bytes:
    output = io.BytesIO()
    with Image.new("RGB", (32, 24), color) as image:
        image.save(output, format="PNG")
    return output.getvalue()


def _assert_private_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.fixture
def system_evaluation_api_scenario(
    db_client: TestClient,
    db: Session,
    platform_users,
    tmp_path: Path,
) -> SystemEvaluationApiScenario:
    db_client.app.state.settings.evaluation_media_root = tmp_path / "media"
    db_client.app.state.settings.evaluation_media_quarantine_root = (
        tmp_path / "quarantine"
    )
    settings = db_client.app.state.settings
    settings.evaluation_media_store_id = uuid4()
    store = PrivateMediaStore(
        root=settings.evaluation_media_root,
        quarantine_root=settings.evaluation_media_quarantine_root,
        max_bytes=settings.evaluation_media_max_bytes,
        max_pixels=settings.evaluation_media_max_pixels,
        preview_max_edge=settings.evaluation_media_preview_max_edge,
        expected_identity=settings.evaluation_media_store_id,
    )
    store.initialize_store_identity()
    store.close()
    occurred_at = datetime.now(UTC)
    account = platform_users["accounts"]["self_student"]
    person = platform_users["people"]["self_student"]
    student_profile = db.get(StudentProfile, person.id)
    assert student_profile is not None
    student_profile.status = "active"

    project = GameProject(
        name=f"HTTP 评价项目 {uuid4().hex[:8]}",
        sort_order=990,
        is_active=True,
        created_by_user_id=account.id,
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectAnalysisProfile(
            project_id=project.id,
            version=1,
            mode="manual_only",
            adapter_id=None,
            adapter_version=None,
            scoring_rule_version=None,
            required_fields=[],
            media_slots=[
                {
                    "key": "match_result",
                    "label": "结算战绩",
                    "required": True,
                }
            ],
            result_schema_version="1",
            confidence_thresholds={},
            auto_publish_warning_levels=[],
            publish_mode="review_required",
            effective_from=occurred_at - timedelta(days=1),
            configured_by_user_id=account.id,
            change_reason="system evaluation HTTP API test profile",
        )
    )
    cohort = Cohort(
        name=f"HTTP 评价营期 {uuid4().hex[:8]}",
        starts_at=occurred_at - timedelta(days=1),
        ends_at=occurred_at + timedelta(days=1),
        default_check_in_at=occurred_at - timedelta(days=1),
        default_check_out_at=occurred_at + timedelta(days=1),
        status="active",
        created_by_user_id=account.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=project.id,
        name=f"HTTP 评价班级 {uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        is_active=True,
        created_by_user_id=account.id,
    )
    db.add(teaching_class)
    db.flush()
    admission_case = AdmissionCase(
        student_person_id=person.id,
        status="ready",
        agreed_tuition_cents=0,
        created_by_user_id=account.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=person.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=0,
        status="active",
        confirmed_at=occurred_at - timedelta(days=2),
        created_by_user_id=account.id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    permission = db.scalar(
        select(Permission).where(Permission.key == "evaluation_media.upload")
    )
    assert permission is not None
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=permission.id,
            effect="allow",
            scope_kind="self",
            scope_id=None,
            valid_from=occurred_at - timedelta(days=2),
            valid_until=occurred_at + timedelta(days=2),
            created_by_user_id=account.id,
            grant_reason="system evaluation HTTP API test",
        )
    )
    db.commit()
    return SystemEvaluationApiScenario(
        account_id=account.id,
        student_person_id=person.id,
        project_id=project.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        enrollment_id=enrollment.id,
    )


def _session_request(scenario: SystemEvaluationApiScenario) -> dict[str, str]:
    return {
        "project_id": str(scenario.project_id),
        "student_person_id": str(scenario.student_person_id),
        "enrollment_id": str(scenario.enrollment_id),
    }


def _create_upload_session(
    client: TestClient,
    scenario: SystemEvaluationApiScenario,
    *,
    csrf: str,
) -> str:
    response = client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json=_session_request(scenario),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _put_required_slot(
    client: TestClient,
    session_id: str,
    *,
    csrf: str,
    color: tuple[int, int, int] = (22, 88, 154),
):
    return client.put(
        f"/api/v1/evaluations/system/upload-sessions/{session_id}/slots/match_result",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("evaluation.png", _png_bytes(color), "image/png")},
    )


def test_system_context_returns_only_authorized_masked_upload_contract(
    db_client: TestClient,
    platform_users,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    scenario = system_evaluation_api_scenario
    _login(db_client, platform_users, "self_student")

    response = db_client.get("/api/v1/evaluations/system/context")
    foundation = db_client.get("/api/v1/evaluations/context")

    assert response.status_code == 200
    _assert_private_headers(response)
    assert set(response.json()) == {"api_version", "upload_available", "targets"}
    assert response.json()["api_version"] == "v2"
    assert response.json()["upload_available"] is True
    assert len(response.json()["targets"]) == 1
    target = response.json()["targets"][0]
    assert set(target) == {
        "student_person_id",
        "enrollment_id",
        "cohort_id",
        "project_id",
        "class_id",
        "identity_summary",
        "project_name",
        "cohort_name",
        "class_name",
        "authorization_source",
        "requires_delegated_upload_reason",
        "upload_window_start",
        "upload_window_end",
        "profile_id",
        "profile_version",
        "profile_mode",
        "slots",
    }
    assert target["project_id"] == str(scenario.project_id)
    assert target["enrollment_id"] == str(scenario.enrollment_id)
    assert target["authorization_source"] == "self"
    assert target["requires_delegated_upload_reason"] is False
    assert target["slots"] == [
        {
            "key": "match_result",
            "label": "结算战绩",
            "position": 1,
            "required": True,
        }
    ]
    assert platform_users["people"]["self_student"].full_name not in response.text
    assert foundation.status_code == 200
    assert foundation.json()["upload_available"] is True


def test_upload_session_dto_forbids_unknown_and_client_window_fields_before_write(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    scenario = system_evaluation_api_scenario
    csrf = _login(db_client, platform_users, "self_student")
    payload = {
        **_session_request(scenario),
        "upload_window_start": datetime.now(UTC).isoformat(),
        "unknown": "forbidden",
    }

    response = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 422
    _assert_private_headers(response)
    assert response.json()["code"] == "validation_error"
    assert db.scalar(select(func.count(SystemEvaluationUploadSession.id))) == 0


def test_private_target_failure_is_exact_404_with_zero_side_effects(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    scenario = system_evaluation_api_scenario
    csrf = _login(db_client, platform_users, "self_student")
    before = {
        "sessions": db.scalar(select(func.count(SystemEvaluationUploadSession.id))),
        "slots": db.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id))
        ),
        "assets": db.scalar(select(func.count(EvaluationMediaAsset.id))),
        "tasks": db.scalar(select(func.count(SystemEvaluationTask.id))),
        "idempotency": db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
        "audit": db.scalar(select(func.count(AuditEvent.id))),
    }

    response = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": str(scenario.project_id),
            "student_person_id": str(scenario.student_person_id),
            "enrollment_id": str(uuid4()),
        },
    )

    assert response.status_code == 404
    _assert_private_headers(response)
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "评价资源不存在",
        "code": "evaluation_resource_not_found",
    }
    after = {
        "sessions": db.scalar(select(func.count(SystemEvaluationUploadSession.id))),
        "slots": db.scalar(
            select(func.count(SystemEvaluationUploadSessionMedia.id))
        ),
        "assets": db.scalar(select(func.count(EvaluationMediaAsset.id))),
        "tasks": db.scalar(select(func.count(SystemEvaluationTask.id))),
        "idempotency": db.scalar(select(func.count(EvaluationIdempotencyKey.id))),
        "audit": db.scalar(select(func.count(AuditEvent.id))),
    }
    assert after == before


def test_cookie_upload_flow_has_exact_safe_responses_and_server_slot_position(
    db_client: TestClient,
    platform_users,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    scenario = system_evaluation_api_scenario
    csrf = _login(db_client, platform_users, "self_student")

    created = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json=_session_request(scenario),
    )
    assert created.status_code == 201
    _assert_private_headers(created)
    assert set(created.json()) == {"id", "status", "expires_at"}
    assert created.json()["status"] == "open"

    uploaded = _put_required_slot(db_client, created.json()["id"], csrf=csrf)
    assert uploaded.status_code == 200
    _assert_private_headers(uploaded)
    assert set(uploaded.json()) == {
        "id",
        "session_id",
        "slot_key",
        "position",
        "mime_type",
        "size_bytes",
        "width_pixels",
        "height_pixels",
    }
    assert uploaded.json()["position"] == 1
    assert uploaded.json()["slot_key"] == "match_result"
    assert not {
        "quarantine_storage_key",
        "storage_key",
        "physical_path",
        "sha256",
        "canonical_extension",
    } & set(uploaded.json())

    calls: list[dict[str, object]] = []
    task_id = uuid4()

    class RecordingTaskService:
        def create_task(self, upload_session_id: UUID, **kwargs: object):
            calls.append({"upload_session_id": upload_session_id, **kwargs})
            return EvaluationTaskRecord(
                id=task_id,
                public_number="EV-HTTP-ADAPTER-TEST",
                status="manual_input",
                lock_version=1,
            )

    db_client.app.dependency_overrides[get_evaluation_task_service] = (
        RecordingTaskService
    )
    idempotency_key = f"task-{uuid4()}"
    try:
        task = db_client.post(
            f"/api/v1/evaluations/system/upload-sessions/{created.json()['id']}/tasks",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": idempotency_key,
            },
            json={},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_task_service, None)
    assert task.status_code == 201
    _assert_private_headers(task)
    assert set(task.json()) == {"id", "public_number", "status", "lock_version"}
    assert task.json()["status"] == "manual_input"
    assert task.json()["lock_version"] == 1
    assert calls[0]["upload_session_id"] == UUID(created.json()["id"])
    assert calls[0]["actor_user_id"] == scenario.account_id
    assert calls[0]["idempotency_key"] == idempotency_key
    target = calls[0]["target"]
    assert target.student_person_id is None
    assert target.enrollment_id is None
    assert target.cohort_id is None
    assert target.class_id is None
    assert target.upload_window_start == target.upload_window_end
    assert not {
        "student_person_id",
        "candidate_count",
        "candidate_identity",
        "upload_window_start",
        "upload_window_end",
    } & set(task.json())


def test_upload_adapter_passes_declared_mime_and_maps_validation_safely(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    session_id = _create_upload_session(
        db_client,
        system_evaluation_api_scenario,
        csrf=csrf,
    )

    response = db_client.put(
        f"/api/v1/evaluations/system/upload-sessions/{session_id}/slots/match_result",
        headers={"X-CSRF-Token": csrf},
        files={"file": ("evaluation.png", _png_bytes(), "text/plain")},
    )

    assert response.status_code == 422
    _assert_private_headers(response)
    assert response.json()["code"] == "media_validation_failed"
    assert "declared_mime" not in response.text
    assert db.scalar(
        select(func.count(SystemEvaluationUploadSessionMedia.id))
    ) == 0


def test_upload_multipart_forbids_client_slot_position_before_write(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    session_id = _create_upload_session(
        db_client,
        system_evaluation_api_scenario,
        csrf=csrf,
    )

    response = db_client.put(
        f"/api/v1/evaluations/system/upload-sessions/{session_id}/slots/match_result",
        headers={"X-CSRF-Token": csrf},
        data={"position": "1"},
        files={"file": ("evaluation.png", _png_bytes(), "image/png")},
    )

    assert response.status_code == 422
    _assert_private_headers(response)
    assert response.json()["code"] == "validation_error"
    assert db.scalar(
        select(func.count(SystemEvaluationUploadSessionMedia.id))
    ) == 0


def test_delete_slot_uses_existing_csrf_rule_and_safe_response(
    db_client: TestClient,
    platform_users,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    session_id = _create_upload_session(
        db_client,
        system_evaluation_api_scenario,
        csrf=csrf,
    )
    assert _put_required_slot(db_client, session_id, csrf=csrf).status_code == 200
    path = (
        f"/api/v1/evaluations/system/upload-sessions/{session_id}"
        "/slots/match_result"
    )

    missing_csrf = db_client.delete(path)
    deleted = db_client.delete(path, headers={"X-CSRF-Token": csrf})

    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_invalid"
    _assert_private_headers(missing_csrf)
    assert deleted.status_code == 200
    _assert_private_headers(deleted)
    assert deleted.json() == {
        "session_id": session_id,
        "slot_key": "match_result",
        "deleted": True,
    }


def test_idempotency_payload_conflict_is_safe_409_without_writes(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    before_tasks = db.scalar(select(func.count(SystemEvaluationTask.id)))
    before_keys = db.scalar(select(func.count(EvaluationIdempotencyKey.id)))

    class ConflictingTaskService:
        def create_task(self, *_args: object, **_kwargs: object) -> NoReturn:
            raise EvaluationIdempotencyConflict("private prior resource sentinel")

    db_client.app.dependency_overrides[get_evaluation_task_service] = (
        ConflictingTaskService
    )
    try:
        response = db_client.post(
            f"/api/v1/evaluations/system/upload-sessions/{uuid4()}/tasks",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"conflict-{uuid4()}",
            },
            json={},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_task_service, None)

    assert response.status_code == 409
    _assert_private_headers(response)
    assert response.json()["code"] == "evaluation_idempotency_conflict"
    assert "private prior resource sentinel" not in response.text
    assert db.scalar(select(func.count(SystemEvaluationTask.id))) == before_tasks
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == before_keys


def test_idempotent_replay_can_return_a_task_that_has_already_progressed(
    db_client: TestClient,
    platform_users,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    task_id = uuid4()

    class ProgressedTaskService:
        def create_task(self, *_args: object, **_kwargs: object) -> EvaluationTaskRecord:
            return EvaluationTaskRecord(
                id=task_id,
                public_number="EV-IDEMPOTENT-REPLAY",
                status="processing",
                lock_version=4,
            )

    db_client.app.dependency_overrides[get_evaluation_task_service] = (
        ProgressedTaskService
    )
    try:
        response = db_client.post(
            f"/api/v1/evaluations/system/upload-sessions/{uuid4()}/tasks",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"replay-{uuid4()}",
            },
            json={},
        )
    finally:
        db_client.app.dependency_overrides.pop(get_evaluation_task_service, None)

    assert response.status_code == 201
    assert response.json() == {
        "id": str(task_id),
        "public_number": "EV-IDEMPOTENT-REPLAY",
        "status": "processing",
        "lock_version": 4,
    }


def test_missing_csrf_precedes_upload_session_write(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    _login(db_client, platform_users, "self_student")

    response = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        json=_session_request(system_evaluation_api_scenario),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_invalid"
    assert db.scalar(select(func.count(SystemEvaluationUploadSession.id))) == 0


def test_account_with_center_access_but_no_upload_capability_gets_403_before_write(
    db_client: TestClient,
    platform_users,
    db: Session,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    scenario = system_evaluation_api_scenario
    csrf = _login(db_client, platform_users, "customer_service")
    account = platform_users["accounts"]["customer_service"]
    permission = db.scalar(
        select(Permission).where(Permission.key == "evaluation_center.access")
    )
    assert permission is not None
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=permission.id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
            created_by_user_id=scenario.account_id,
            grant_reason="test center access without upload capability",
        )
    )
    db.commit()

    response = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": csrf},
        json={"project_id": str(scenario.project_id)},
    )

    assert response.status_code == 403
    _assert_private_headers(response)
    assert response.json()["code"] == "evaluation_operation_denied"
    assert db.scalar(select(func.count(SystemEvaluationUploadSession.id))) == 0


def test_global_upload_capability_reaches_private_selector_without_current_targets(
    db_client: TestClient,
    login_as_admin,
    db: Session,
) -> None:
    response = db_client.post(
        "/api/v1/evaluations/system/upload-sessions",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "project_id": str(uuid4()),
            "delegated_upload_reason": "管理员协助上传",
        },
    )

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "评价资源不存在",
        "code": "evaluation_resource_not_found",
    }
    assert db.scalar(select(func.count(SystemEvaluationUploadSession.id))) == 0


def test_private_headers_cover_success_and_all_private_error_classes(
    db_client: TestClient,
    platform_users,
    system_evaluation_api_scenario: SystemEvaluationApiScenario,
) -> None:
    responses = [db_client.get("/api/v1/evaluations/system/context")]
    _login(db_client, platform_users, "customer_service")
    responses.append(db_client.get("/api/v1/evaluations/system/context"))
    responses.append(db_client.get("/api/v1/evaluations/system/unknown-child"))
    responses.append(db_client.post("/api/v1/evaluations/system/context"))
    csrf = _login(db_client, platform_users, "self_student")
    responses.append(
        db_client.post(
            "/api/v1/evaluations/system/upload-sessions",
            headers={"X-CSRF-Token": csrf},
            json={**_session_request(system_evaluation_api_scenario), "extra": True},
        )
    )
    responses.append(db_client.get("/api/v1/evaluations/system/context"))

    assert [response.status_code for response in responses] == [
        401,
        403,
        404,
        405,
        422,
        200,
    ]
    for response in responses:
        _assert_private_headers(response)
