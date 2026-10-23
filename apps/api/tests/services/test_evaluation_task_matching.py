from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import func, literal, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.matching_service import EvaluationMatchingService
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.query_service import EvaluationQueryService
from app.evaluations.review_schemas import EvaluationTaskMatchInput
from app.evaluations.task_models import (
    EvaluationIdempotencyKey,
    SystemEvaluationEvent,
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
    SystemEvaluationUploadSession,
)
from app.identity.models import StudentProfile
from app.teaching.models import GameProject
from tests.services.test_evaluation_uploads import NOW as SCENARIO_NOW
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_uploads",)

MATCH_AT = SCENARIO_NOW
IDEMPOTENCY_KEY = b"evaluation-task-match-idempotency-key"


@pytest.fixture
def task_matching_db(
    task_creation_scenario: TaskCreationScenario,
) -> Iterator[tuple[Session, TaskCreationScenario]]:
    with Session(task_creation_scenario.engine) as db:
        transaction = db.begin()
        try:
            yield db, task_creation_scenario
        finally:
            transaction.rollback()


def _pending_task(
    db: Session,
    scenario: TaskCreationScenario,
    *,
    mode: Literal["manual_only", "automatic"] = "manual_only",
) -> tuple[SystemEvaluationTask, EvaluationMediaAsset]:
    upload = SystemEvaluationUploadSession(
        created_by_user_id=scenario.admin_user_id,
        project_id=scenario.project_id,
        status="committed",
        expires_at=MATCH_AT + timedelta(hours=1),
        committed_at=MATCH_AT,
        delegated_upload_reason="管理员处理待匹配任务",
    )
    db.add(upload)
    db.flush()
    task = SystemEvaluationTask(
        public_number=f"EV-{uuid4().hex[:20].upper()}",
        upload_session_id=upload.id,
        created_by_user_id=scenario.admin_user_id,
        student_person_id=None,
        enrollment_id=None,
        cohort_id=None,
        project_id=scenario.project_id,
        class_id=None,
        analysis_profile_id=scenario.profile_id,
        analysis_profile_version=1,
        analysis_config_snapshot={
            "mode": mode,
            "adapter_id": "lol" if mode == "automatic" else None,
            "adapter_version": "1.0.0" if mode == "automatic" else None,
            "scoring_rule_version": "1" if mode == "automatic" else None,
            "required_fields": ["score", "grade"],
            "media_slots": [{"key": "profile", "label": "个人数据", "required": True}],
            "result_schema_version": "1",
            "confidence_thresholds": {},
            "auto_publish_warning_levels": [],
            "publish_mode": "review_required",
        },
        upload_window_start=MATCH_AT - timedelta(days=7),
        upload_window_end=MATCH_AT + timedelta(days=7),
        status="pending_match",
        lock_version=1,
    )
    db.add(task)
    blob = EvaluationMediaBlob(
        storage_key=f"task-match/{uuid4().hex}.png",
        preview_storage_key=f"task-match/{uuid4().hex}.webp",
        sha256=uuid4().hex + uuid4().hex,
        mime_type="image/png",
        canonical_extension="png",
        size_bytes=100,
        width_pixels=10,
        height_pixels=10,
        storage_status="available",
        validation_status="validated",
        retention_status="retained",
    )
    db.add(blob)
    db.flush()
    asset = EvaluationMediaAsset(
        blob_id=blob.id,
        uploaded_by_user_id=scenario.admin_user_id,
        project_id=scenario.project_id,
        delegated_upload_reason="管理员处理待匹配任务",
    )
    db.add(asset)
    db.flush()
    db.add(
        SystemEvaluationTaskMedia(
            task_id=task.id,
            media_id=asset.id,
            position=1,
            slot_key="profile",
        )
    )
    db.add_all(
        [
            SystemEvaluationEvent(
                task_id=task.id,
                sequence=1,
                event_type="status_changed",
                from_status="uploaded",
                to_status="identifying",
                actor_user_id=scenario.admin_user_id,
                event_metadata={},
                occurred_at=MATCH_AT - timedelta(minutes=1),
            ),
            SystemEvaluationEvent(
                task_id=task.id,
                sequence=2,
                event_type="status_changed",
                from_status="identifying",
                to_status="pending_match",
                actor_user_id=scenario.admin_user_id,
                event_metadata={},
                occurred_at=MATCH_AT - timedelta(minutes=1),
            ),
        ]
    )
    db.flush()
    return task, asset


def _input(
    scenario: TaskCreationScenario,
    *,
    expected_lock_version: int = 1,
) -> EvaluationTaskMatchInput:
    return EvaluationTaskMatchInput(
        expected_lock_version=expected_lock_version,
        student_person_id=scenario.actor_person_id,
        enrollment_id=scenario.enrollment_id,
        cohort_id=scenario.cohort_id,
        class_id=scenario.class_id,
    )


def _service(db: Session) -> EvaluationMatchingService:
    from app.security.encryption import LookupDigest

    return EvaluationMatchingService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=IDEMPOTENCY_KEY,
        ),
    )


def test_confirm_match_atomically_assigns_task_media_event_and_audit(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)

    matched = _service(db).confirm_task_match(
        task.id,
        _input(scenario),
        actor_user_id=scenario.admin_user_id,
        idempotency_key="confirm-task-match-1",
        occurred_at=MATCH_AT,
        request_id="evaluation-task-match-test",
    )
    db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    db.refresh(task)
    db.refresh(asset)

    assert matched.task_id == task.id
    assert matched.task_status == "manual_input"
    assert matched.task_version == 2
    assert matched.student_person_id == scenario.actor_person_id
    assert matched.enrollment_id == scenario.enrollment_id
    assert matched.cohort_id == scenario.cohort_id
    assert matched.project_id == scenario.project_id
    assert matched.class_id == scenario.class_id
    assert task.status == "manual_input"
    assert task.lock_version == 2
    assert task.matched_at == MATCH_AT
    assert task.matched_by_user_id == scenario.admin_user_id
    assert asset.student_person_id == scenario.actor_person_id
    assert asset.enrollment_id == scenario.enrollment_id
    assert asset.class_id == scenario.class_id
    assert asset.matched_at == MATCH_AT
    assert asset.matched_by_user_id == scenario.admin_user_id
    assert asset.match_reason == "task_target_confirmed"
    event = db.scalar(
        select(SystemEvaluationEvent).where(
            SystemEvaluationEvent.task_id == task.id,
            SystemEvaluationEvent.event_type == "task_matched",
        )
    )
    assert event is not None
    assert event.from_status == "pending_match"
    assert event.to_status == "manual_input"
    assert event.event_metadata == {"task_version": 2}
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == task.id,
            AuditEvent.action == "evaluation.task.matched",
        )
    )
    assert audit is not None
    assert audit.event_metadata == {
        "task_id": str(task.id),
        "task_status": "manual_input",
        "evaluation_action": "confirm_match",
        "task_version": 2,
        "media_count": 1,
    }
    assert "reason" not in audit.event_metadata


def test_match_target_directory_reuses_the_authorized_formal_enrollment_selector(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, _asset = _pending_task(db, scenario)
    student = db.get(StudentProfile, scenario.actor_person_id)
    assert student is not None
    service = EvaluationQueryService(db)
    loader = getattr(service, "list_match_targets", None)
    assert loader is not None

    page = loader(
        task.id,
        actor_user_id=scenario.admin_user_id,
        query=student.student_number,
        page=1,
        page_size=20,
        occurred_at=MATCH_AT,
    )

    assert page.total == 1
    assert len(page.items) == 1
    target = page.items[0]
    assert target.student_person_id == scenario.actor_person_id
    assert target.enrollment_id == scenario.enrollment_id
    assert target.cohort_id == scenario.cohort_id
    assert target.class_id == scenario.class_id
    assert "phone" not in repr(target).casefold()
    assert "identity" not in repr(target).casefold()


def test_confirm_match_is_idempotent_and_rejects_changed_payload(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, _asset = _pending_task(db, scenario)
    service = _service(db)
    first = service.confirm_task_match(
        task.id,
        _input(scenario),
        actor_user_id=scenario.admin_user_id,
        idempotency_key="confirm-task-match-replay",
        occurred_at=MATCH_AT,
        request_id="evaluation-task-match-first",
    )
    replay = service.confirm_task_match(
        task.id,
        _input(scenario),
        actor_user_id=scenario.admin_user_id,
        idempotency_key="confirm-task-match-replay",
        occurred_at=MATCH_AT + timedelta(seconds=1),
        request_id="evaluation-task-match-replay",
    )

    assert replay == first
    assert (
        db.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task.id,
                SystemEvaluationEvent.event_type == "task_matched",
            )
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.resource_id == task.id,
                AuditEvent.action == "evaluation.task.matched",
            )
        )
        == 1
    )
    with pytest.raises(EvaluationIdempotencyConflict):
        service.confirm_task_match(
            task.id,
            _input(scenario, expected_lock_version=2),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-replay",
            occurred_at=MATCH_AT + timedelta(seconds=2),
            request_id="evaluation-task-match-conflict",
        )
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 1


def test_confirm_match_replay_reauthorizes_the_current_target(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, _asset = _pending_task(db, scenario)
    service = _service(db)
    service.confirm_task_match(
        task.id,
        _input(scenario),
        actor_user_id=scenario.admin_user_id,
        idempotency_key="confirm-task-match-replay-authorization",
        occurred_at=MATCH_AT,
        request_id="evaluation-task-match-first",
    )
    project = db.get(GameProject, scenario.project_id)
    assert project is not None
    project.is_active = False
    db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        service.confirm_task_match(
            task.id,
            _input(scenario),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-replay-authorization",
            occurred_at=MATCH_AT + timedelta(seconds=1),
            request_id="evaluation-task-match-replay",
        )

    assert (
        db.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task.id,
                SystemEvaluationEvent.event_type == "task_matched",
            )
        )
        == 1
    )
    assert (
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.resource_id == task.id,
                AuditEvent.action == "evaluation.task.matched",
            )
        )
        == 1
    )
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 1
    assert db.scalar(select(literal(1))) == 1


@pytest.mark.parametrize(
    ("status", "expected_lock_version", "error"),
    [
        ("identifying", 1, EvaluationTaskStateConflict),
        ("pending_match", 2, EvaluationTaskVersionConflict),
    ],
)
def test_confirm_match_conflicts_leave_task_and_media_unmodified(
    task_matching_db: tuple[Session, TaskCreationScenario],
    status: str,
    expected_lock_version: int,
    error: type[Exception],
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)
    task.status = status
    db.flush()

    with pytest.raises(error):
        _service(db).confirm_task_match(
            task.id,
            _input(scenario, expected_lock_version=expected_lock_version),
            actor_user_id=scenario.admin_user_id,
            idempotency_key=f"confirm-task-match-{status}-{expected_lock_version}",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-conflict",
        )

    db.refresh(task)
    db.refresh(asset)
    assert task.status == status
    assert task.student_person_id is None
    assert asset.student_person_id is None
    assert (
        db.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task.id,
                SystemEvaluationEvent.event_type == "task_matched",
            )
        )
        == 0
    )
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 0
    assert db.scalar(select(literal(1))) == 1


def test_confirm_match_requires_match_capability_before_any_write(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)

    with pytest.raises(EvaluationOperationDenied):
        _service(db).confirm_task_match(
            task.id,
            _input(scenario),
            actor_user_id=scenario.actor_user_id,
            idempotency_key="confirm-task-match-denied",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-denied",
        )

    db.refresh(task)
    db.refresh(asset)
    assert task.student_person_id is None
    assert asset.student_person_id is None
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 0


def test_confirm_match_uses_the_frozen_automatic_mode_for_queueing(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, _asset = _pending_task(db, scenario, mode="automatic")

    matched = _service(db).confirm_task_match(
        task.id,
        _input(scenario),
        actor_user_id=scenario.admin_user_id,
        idempotency_key="confirm-task-match-automatic",
        occurred_at=MATCH_AT,
        request_id="evaluation-task-match-automatic",
    )

    assert matched.task_status == "queued"
    assert task.status == "queued"


def test_confirm_match_hides_mismatched_or_now_ineligible_targets(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)
    service = _service(db)
    mismatched = _input(scenario).model_copy(update={"student_person_id": uuid4()})

    with pytest.raises(EvaluationResourceNotFound):
        service.confirm_task_match(
            task.id,
            mismatched,
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-mismatch",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-mismatch",
        )
    student = db.get(StudentProfile, scenario.actor_person_id)
    assert student is not None
    student.status = "inactive"
    db.flush()
    with pytest.raises(EvaluationResourceNotFound):
        service.confirm_task_match(
            task.id,
            _input(scenario),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-ineligible",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-ineligible",
        )

    db.refresh(task)
    db.refresh(asset)
    assert task.student_person_id is None
    assert asset.student_person_id is None
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 0


def test_confirm_match_rejects_duplicate_target_blob_before_updates(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)
    existing = EvaluationMediaAsset(
        blob_id=asset.blob_id,
        uploaded_by_user_id=scenario.admin_user_id,
        project_id=scenario.project_id,
        student_person_id=scenario.actor_person_id,
        enrollment_id=scenario.enrollment_id,
        class_id=scenario.class_id,
        matched_at=MATCH_AT - timedelta(days=1),
        matched_by_user_id=scenario.admin_user_id,
        match_reason="existing_target_media",
    )
    db.add(existing)
    db.flush()

    with pytest.raises(EvaluationTaskStateConflict):
        _service(db).confirm_task_match(
            task.id,
            _input(scenario),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-duplicate-media",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-duplicate-media",
        )

    db.refresh(task)
    db.refresh(asset)
    assert task.student_person_id is None
    assert asset.student_person_id is None
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 0


def test_confirm_match_rejects_duplicate_blob_within_the_pending_task(
    task_matching_db: tuple[Session, TaskCreationScenario],
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)
    duplicate = EvaluationMediaAsset(
        blob_id=asset.blob_id,
        uploaded_by_user_id=scenario.admin_user_id,
        project_id=scenario.project_id,
        delegated_upload_reason="管理员处理待匹配任务",
    )
    db.add(duplicate)
    db.flush()
    db.add(
        SystemEvaluationTaskMedia(
            task_id=task.id,
            media_id=duplicate.id,
            position=2,
            slot_key="match_result",
        )
    )
    db.flush()

    with pytest.raises(EvaluationTaskStateConflict):
        _service(db).confirm_task_match(
            task.id,
            _input(scenario),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-duplicate-task-blob",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-duplicate-task-blob",
        )

    db.refresh(task)
    db.refresh(asset)
    db.refresh(duplicate)
    assert task.student_person_id is None
    assert asset.student_person_id is None
    assert duplicate.student_person_id is None
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 0
    assert db.scalar(select(literal(1))) == 1


def test_confirm_match_audit_failure_rolls_back_every_business_write(
    task_matching_db: tuple[Session, TaskCreationScenario],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, scenario = task_matching_db
    task, asset = _pending_task(db, scenario)

    def fail_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced match audit failure")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="forced match audit failure"):
        _service(db).confirm_task_match(
            task.id,
            _input(scenario),
            actor_user_id=scenario.admin_user_id,
            idempotency_key="confirm-task-match-audit-failure",
            occurred_at=MATCH_AT,
            request_id="evaluation-task-match-audit-failure",
        )

    db.refresh(task)
    db.refresh(asset)
    assert task.status == "pending_match"
    assert task.lock_version == 1
    assert task.student_person_id is None
    assert asset.student_person_id is None
    assert (
        db.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task.id,
                SystemEvaluationEvent.event_type == "task_matched",
            )
        )
        == 0
    )
    assert db.scalar(select(func.count(EvaluationIdempotencyKey.id))) == 0
    assert db.scalar(select(literal(1))) == 1
