from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.analysis_profile_service import AnalysisProfileService
from app.evaluations.dependencies import get_evaluation_processing_service
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationLeaseInvalid,
    EvaluationResourceNotFound,
    EvaluationResultInvalid,
)
from app.evaluations.processing_models import (
    EvaluationAdapterCertification,
    SystemEvaluationAttempt,
)
from app.evaluations.processing_schemas import (
    SystemEvaluationFailureInput,
    SystemEvaluationResultInput,
)
from app.evaluations.processing_service import EvaluationProcessingService
from app.evaluations.review_models import SystemEvaluationResultVersion
from app.evaluations.schemas import AnalysisProfileCreate
from app.evaluations.task_models import SystemEvaluationEvent, SystemEvaluationTask
from app.evaluations.task_service import EvaluationTaskService, EvaluationTaskTarget
from app.evaluations.worker_ticket import WorkerMediaTicketService
from tests.services.test_evaluation_uploads import (
    TaskCreationScenario,
    _create_three_slot_upload,
)

pytest_plugins = ("tests.services.test_evaluation_uploads",)

TOKEN_PEPPER = bytes(range(32))


def _result_input(**overrides: object) -> SystemEvaluationResultInput:
    values: dict[str, object] = {
        "contract_version": "v2",
        "adapter_id": "lol",
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "fields": {"kills": 12, "deaths": 3, "assists": 8},
        "confidences": {"kills": 0.98, "deaths": 0.97, "assists": 0.96},
        "missing_fields": [],
        "warnings": [],
        "score": 86,
        "grade": "A",
        "components": [
            {
                "key": "combat",
                "score": 86,
                "weight": 1.0,
                "summary": "作战数据稳定",
            }
        ],
        "advice": "继续保持团战前的信息确认。",
        "duration_ms": 1200,
        "idempotency_key": "result-task-1-attempt-1",
    }
    values.update(overrides)
    return SystemEvaluationResultInput.model_validate(values)


def _failure_input(**overrides: object) -> SystemEvaluationFailureInput:
    values: dict[str, object] = {
        "contract_version": "v2",
        "error_class": "network",
        "safe_error_summary": "worker network request failed",
        "trace_id": "trace-123",
        "idempotency_key": "failure-task-1-attempt-1",
    }
    values.update(overrides)
    return SystemEvaluationFailureInput.model_validate(values)


def test_result_input_freezes_the_strict_v2_contract() -> None:
    result = _result_input()

    assert result.contract_version == "v2"
    assert result.score == 86
    assert result.components[0].key == "combat"


@pytest.mark.parametrize(
    "overrides",
    [
        {"score": -1},
        {"score": 101},
        {
            "confidences": {
                "kills": 1.01,
                "deaths": 0.97,
                "assists": 0.96,
            }
        },
        {
            "warnings": [
                {"key": "blurred", "severity": "fatal", "summary": "模糊"}
            ]
        },
        {
            "components": [
                {
                    "key": "combat",
                    "score": 86,
                    "weight": 0.5,
                    "summary": "一",
                },
                {
                    "key": "combat",
                    "score": 80,
                    "weight": 0.5,
                    "summary": "二",
                },
            ]
        },
        {
            "fields": {
                "kills": float("nan"),
                "deaths": 3,
                "assists": 8,
            }
        },
        {"unexpected": "forbidden"},
    ],
)
def test_result_input_rejects_out_of_contract_payloads(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _result_input(**overrides)


@pytest.mark.parametrize(
    "error_class",
    [
        "network",
        "worker_interrupted",
        "ocr_timeout",
        "temporary_service",
        "adapter_incompatible",
        "media_invalid",
        "result_schema_invalid",
    ],
)
def test_failure_input_freezes_public_error_classes(error_class: str) -> None:
    assert _failure_input(error_class=error_class).error_class == error_class


def test_failure_input_rejects_unknown_error_classes() -> None:
    with pytest.raises(ValidationError):
        _failure_input(error_class="internal_stack_trace")


def _create_queued_task(
    scenario: TaskCreationScenario,
    *,
    occurred_at: datetime | None = None,
    publish_mode: str = "review_required",
    adapter_id: str = "lol",
) -> UUID:
    occurred_at = scenario.now if occurred_at is None else occurred_at
    with Session(scenario.engine) as db:
        profile_values = {
            "mode": "automatic",
            "adapter_id": adapter_id,
            "adapter_version": "1.0.0",
            "scoring_rule_version": "1",
            "result_schema_version": "1",
            "required_fields": ["kills", "deaths", "assists"],
            "media_slots": [
                {"key": "profile", "label": "个人数据", "required": True},
                {"key": "match_result", "label": "结算战绩", "required": True},
                {"key": "team_detail", "label": "队伍明细", "required": True},
            ],
            "confidence_thresholds": {
                "kills": 0.0,
                "deaths": 0.0,
                "assists": 0.0,
            },
            "auto_publish_warning_levels": [],
            "publish_mode": publish_mode,
            "effective_from": occurred_at - timedelta(seconds=1),
            "change_reason": "processing test automatic profile",
        }
        if adapter_id == "synthetic":
            db.add(
                ProjectAnalysisProfile(
                    project_id=scenario.project_id,
                    version=2,
                    configured_by_user_id=scenario.admin_user_id,
                    **profile_values,
                )
            )
            db.flush()
        else:
            AnalysisProfileService(db).create_version(
                scenario.project_id,
                AnalysisProfileCreate(
                    expected_current_version=1,
                    **profile_values,
                ),
                actor_user_id=scenario.admin_user_id,
                request_id="processing-profile-fixture",
            )

        upload_session = _create_three_slot_upload(
            scenario,
            db,
            occurred_at=occurred_at,
            student_person_id=scenario.actor_person_id,
            enrollment_id=scenario.enrollment_id,
        )
        task = EvaluationTaskService(db, store=scenario.store).create_task(
            upload_session.id,
            actor_user_id=scenario.actor_user_id,
            target=EvaluationTaskTarget(
                student_person_id=None,
                enrollment_id=None,
                cohort_id=None,
                class_id=None,
                upload_window_start=occurred_at,
                upload_window_end=occurred_at,
            ),
            occurred_at=occurred_at,
            request_id="processing-fixture-task",
        )
        assert task.status == "queued"
        return task.id


@pytest.fixture
def queued_task_id(task_creation_scenario: TaskCreationScenario) -> UUID:
    return _create_queued_task(task_creation_scenario)


def _counts(db: Session, task_id: UUID) -> tuple[int, int, int, int]:
    return (
        db.scalar(
            select(func.count(SystemEvaluationAttempt.id)).where(
                SystemEvaluationAttempt.task_id == task_id
            )
        )
        or 0,
        db.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task_id
            )
        )
        or 0,
        db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.resource_id == task_id
            )
        )
        or 0,
        db.scalar(
            select(func.count(SystemEvaluationResultVersion.id)).where(
                SystemEvaluationResultVersion.task_id == task_id
            )
        )
        or 0,
    )


def test_claim_next_creates_a_five_minute_digest_only_lease(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        claim = EvaluationProcessingService(
            db,
            token_pepper=TOKEN_PEPPER,
        ).claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1", "valorant.v1"},
            occurred_at=scenario.now,
        )

        assert claim is not None
        assert claim.task_id == queued_task_id
        assert claim.attempt == 1
        assert claim.task_version == 2
        assert claim.lease_expires_at == scenario.now + timedelta(minutes=5)
        assert len(claim.lease_token) >= 32
        assert claim.adapter_id == "lol"
        assert claim.adapter_version == "1.0.0"

        task = db.get(SystemEvaluationTask, queued_task_id)
        attempt = db.scalar(
            select(SystemEvaluationAttempt).where(
                SystemEvaluationAttempt.task_id == queued_task_id,
                SystemEvaluationAttempt.attempt == 1,
            )
        )
        assert task is not None
        assert attempt is not None
        assert task.status == "processing"
        assert task.current_attempt == 1
        assert task.automatic_attempts == 1
        assert task.lock_version == claim.task_version
        assert attempt.status == "active"
        assert attempt.worker_identity == "score-worker-local"
        assert attempt.task_lock_version == claim.task_version
        assert attempt.lease_token_digest != claim.lease_token
        assert len(attempt.lease_token_digest) == 64
        assert claim.lease_token not in repr(attempt.__dict__)


def test_claim_next_ignores_incompatible_and_not_due_tasks(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        task.available_at = scenario.now + timedelta(seconds=1)
        db.commit()

        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        assert (
            service.claim_next(
                worker_identity="score-worker-local",
                supported_adapters={"lol.v1"},
                occurred_at=scenario.now,
            )
            is None
        )
        task.available_at = scenario.now
        db.commit()
        assert (
            service.claim_next(
                worker_identity="score-worker-local",
                supported_adapters={"valorant.v1"},
                occurred_at=scenario.now,
            )
            is None
        )
        assert _counts(db, queued_task_id)[0] == 0


def test_test_dependency_claims_synthetic_without_expanding_production_registry(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    queued_task_id = _create_queued_task(scenario, adapter_id="synthetic")
    with Session(scenario.engine) as db:
        production_service = EvaluationProcessingService(
            db,
            token_pepper=TOKEN_PEPPER,
        )
        assert (
            production_service.claim_next(
                worker_identity="score-worker-local",
                supported_adapters={"synthetic.v1"},
                occurred_at=scenario.now,
            )
            is None
        )

        settings = SimpleNamespace(
            app_env="test",
            session_token_pepper=SecretStr(
                "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
            ),
        )
        request = cast(
            Request,
            SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(settings=settings),
                )
            ),
        )
        test_service = get_evaluation_processing_service(request, db)
        claim = test_service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"synthetic.v1"},
            occurred_at=scenario.now,
        )

        assert claim is not None
        assert claim.task_id == queued_task_id
        assert claim.adapter_id == "synthetic"
        assert claim.adapter_version == "1.0.0"


def test_renew_extends_the_current_lease_from_the_accepted_time(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        accepted_at = scenario.now + timedelta(minutes=2)

        renewed = service.renew(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            occurred_at=accepted_at,
            request_id="processing-renew",
        )

        assert renewed.lease_expires_at == accepted_at + timedelta(minutes=5)
        assert renewed.task_version == claim.task_version
        stored = db.scalar(
            select(SystemEvaluationAttempt).where(
                SystemEvaluationAttempt.task_id == queued_task_id,
                SystemEvaluationAttempt.attempt == 1,
            )
        )
        assert stored is not None
        assert stored.renewed_at == accepted_at
        assert stored.lease_expires_at == renewed.lease_expires_at


@pytest.mark.parametrize(
    "mutation",
    ["worker", "token", "version", "attempt", "expired", "state", "replaced"],
)
def test_renew_rejects_stale_or_wrong_lease_without_side_effects(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    mutation: str,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        before = _counts(db, queued_task_id)
        kwargs = {
            "task_id": claim.task_id,
            "attempt": claim.attempt,
            "task_version": claim.task_version,
            "worker_identity": "score-worker-local",
            "lease_token": claim.lease_token,
            "occurred_at": scenario.now + timedelta(minutes=1),
            "request_id": "processing-invalid-renew",
        }
        if mutation == "worker":
            kwargs["worker_identity"] = "another-worker"
        elif mutation == "token":
            kwargs["lease_token"] = "not-the-current-lease-token"
        elif mutation == "version":
            kwargs["task_version"] = claim.task_version + 1
        elif mutation == "attempt":
            kwargs["attempt"] = claim.attempt + 1
        elif mutation == "expired":
            kwargs["occurred_at"] = claim.lease_expires_at
        elif mutation == "state":
            task = db.get(SystemEvaluationTask, queued_task_id)
            assert task is not None
            task.status = "queued"
            db.commit()
        else:
            attempt = db.scalar(
                select(SystemEvaluationAttempt).where(
                    SystemEvaluationAttempt.task_id == queued_task_id,
                    SystemEvaluationAttempt.attempt == 1,
                )
            )
            assert attempt is not None
            attempt.lease_token_digest = "a" * 64
            db.commit()

        with pytest.raises(EvaluationLeaseInvalid):
            service.renew(**kwargs)

        assert _counts(db, queued_task_id) == before
        assert db.scalar(select(func.count(SystemEvaluationTask.id))) >= 1
        db.commit()


def test_out_of_order_renewal_never_shortens_the_accepted_lease(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    with Session(task_creation_scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=task_creation_scenario.now,
        )
        assert claim is not None
        newer_time = task_creation_scenario.now + timedelta(minutes=2)
        older_time = task_creation_scenario.now + timedelta(minutes=1)

        newer = service.renew(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            occurred_at=newer_time,
            request_id="processing-renew-newer",
        )
        older = service.renew(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            occurred_at=older_time,
            request_id="processing-renew-older",
        )

        assert newer.lease_expires_at == newer_time + timedelta(minutes=5)
        assert older.lease_expires_at == newer.lease_expires_at
        stored = db.scalar(
            select(SystemEvaluationAttempt).where(
                SystemEvaluationAttempt.task_id == queued_task_id,
                SystemEvaluationAttempt.attempt == 1,
            )
        )
        assert stored is not None
        assert stored.renewed_at == newer_time
        assert stored.lease_expires_at == newer.lease_expires_at


def test_submit_result_is_immutable_review_only_and_idempotent(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        input = _result_input()

        submitted = service.submit_result(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            input=input,
            occurred_at=scenario.now + timedelta(minutes=1),
            request_id="processing-result",
        )
        replayed = service.submit_result(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            input=input,
            occurred_at=scenario.now + timedelta(minutes=2),
            request_id="processing-result-replay",
        )

        assert replayed == submitted
        assert submitted.task_id == queued_task_id
        assert submitted.task_status == "needs_review"
        assert submitted.result_version == 1
        task = db.get(SystemEvaluationTask, queued_task_id)
        attempt = db.scalar(
            select(SystemEvaluationAttempt).where(
                SystemEvaluationAttempt.task_id == queued_task_id,
                SystemEvaluationAttempt.attempt == 1,
            )
        )
        results = list(
            db.scalars(
                select(SystemEvaluationResultVersion).where(
                    SystemEvaluationResultVersion.task_id == queued_task_id
                )
            )
        )
        assert task is not None
        assert attempt is not None
        assert task.status == "needs_review"
        assert task.published_result_version_id is None
        assert task.current_result_version_id == submitted.result_id
        assert attempt.status == "succeeded"
        assert attempt.completed_at == scenario.now + timedelta(minutes=1)
        assert attempt.result_idempotency_digest is not None
        assert attempt.result_idempotency_digest != input.idempotency_key
        assert len(results) == 1
        assert results[0].id == submitted.result_id
        assert results[0].source == "automatic"
        assert results[0].review_decision == "pending"
        assert results[0].published_at is None

        before = _counts(db, queued_task_id)
        with pytest.raises(EvaluationIdempotencyConflict):
            service.submit_result(
                task_id=claim.task_id,
                attempt=claim.attempt,
                task_version=claim.task_version,
                worker_identity="score-worker-local",
                lease_token=claim.lease_token,
                input=input.model_copy(update={"score": 85}),
                occurred_at=scenario.now + timedelta(minutes=2),
                request_id="processing-result-conflict",
            )
        assert _counts(db, queued_task_id) == before
        assert db.scalar(select(func.count(SystemEvaluationResultVersion.id))) >= 1
        db.commit()

        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        task.status = "published"
        task.lock_version += 1
        db.commit()
        replayed_after_state_change = service.submit_result(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            input=input,
            occurred_at=scenario.now + timedelta(minutes=3),
            request_id="processing-result-late-replay",
        )
        assert replayed_after_state_change == submitted

        before_expired_replay = _counts(db, queued_task_id)
        with pytest.raises(EvaluationLeaseInvalid):
            service.submit_result(
                task_id=claim.task_id,
                attempt=claim.attempt,
                task_version=claim.task_version,
                worker_identity="score-worker-local",
                lease_token=claim.lease_token,
                input=input,
                occurred_at=claim.lease_expires_at,
                request_id="processing-result-expired-replay",
            )
        assert _counts(db, queued_task_id) == before_expired_replay
        db.commit()


def test_submit_result_links_the_latest_working_version_and_preserves_published(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as setup, setup.begin():
        task = setup.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        published = SystemEvaluationResultVersion(
            task_id=task.id,
            version=1,
            source="manual",
            fields={"kills": 10, "deaths": 4, "assists": 7},
            confidences={"kills": 1, "deaths": 1, "assists": 1},
            warnings=[],
            components=[
                {
                    "key": "combat",
                    "score": 78,
                    "weight": 1,
                    "summary": "首版结果",
                }
            ],
            missing_fields=[],
            score=Decimal(78),
            grade="B",
            advice="保持训练节奏",
            result_schema_version="1",
            created_by_user_id=scenario.actor_user_id,
            review_decision="approved",
            reviewed_by_user_id=scenario.admin_user_id,
            reviewed_at=scenario.now - timedelta(hours=2),
            published_at=scenario.now - timedelta(hours=2),
        )
        setup.add(published)
        setup.flush()
        rejected = SystemEvaluationResultVersion(
            task_id=task.id,
            version=2,
            source="revision",
            fields={"kills": 11, "deaths": 4, "assists": 7},
            confidences={"kills": 1, "deaths": 1, "assists": 1},
            warnings=[],
            components=[
                {
                    "key": "combat",
                    "score": 80,
                    "weight": 1,
                    "summary": "被驳回修订",
                }
            ],
            missing_fields=[],
            score=Decimal(80),
            grade="B",
            advice="继续核对修订数据",
            result_schema_version="1",
            created_by_user_id=scenario.actor_user_id,
            previous_version_id=published.id,
            review_decision="rejected",
            reviewed_by_user_id=scenario.admin_user_id,
            reviewed_at=scenario.now - timedelta(hours=1),
            rejection_reason="需要自动整改",
        )
        setup.add(rejected)
        setup.flush()
        task.current_result_version_id = rejected.id
        task.published_result_version_id = published.id
        published_id = published.id
        rejected_id = rejected.id

    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-lineage",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None and claim.task_id == queued_task_id
        input = _result_input(idempotency_key="result-lineage-v3")
        submitted = service.submit_result(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-lineage",
            lease_token=claim.lease_token,
            input=input,
            occurred_at=scenario.now + timedelta(minutes=1),
            request_id="processing-result-lineage",
        )
        replay = service.submit_result(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-lineage",
            lease_token=claim.lease_token,
            input=input,
            occurred_at=scenario.now + timedelta(minutes=2),
            request_id="processing-result-lineage-replay",
        )
        assert replay == submitted

    with Session(scenario.engine) as observer:
        task = observer.get(SystemEvaluationTask, queued_task_id)
        result = observer.get(SystemEvaluationResultVersion, submitted.result_id)
        assert task is not None and result is not None
        assert result.version == 3
        assert result.source == "automatic"
        assert result.created_by_user_id is None
        assert result.previous_version_id == rejected_id
        assert task.current_result_version_id == result.id
        assert task.published_result_version_id == published_id
        assert observer.scalar(
            select(func.count(SystemEvaluationResultVersion.id)).where(
                SystemEvaluationResultVersion.task_id == queued_task_id
            )
        ) == 3


def test_claim_rejects_private_snapshot_keys_before_creating_a_lease(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    with Session(task_creation_scenario.engine) as db:
        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        snapshot = dict(task.analysis_config_snapshot)
        snapshot["required_fields"] = ["13800138000"]
        snapshot["confidence_thresholds"] = {"13800138000": 0.8}
        db.execute(
            text(
                "ALTER TABLE system_evaluation_tasks "
                "DISABLE TRIGGER system_evaluation_tasks_target_immutable"
            )
        )
        try:
            task.analysis_config_snapshot = snapshot
            db.commit()
        finally:
            db.execute(
                text(
                    "ALTER TABLE system_evaluation_tasks "
                    "ENABLE TRIGGER system_evaluation_tasks_target_immutable"
                )
            )
            db.commit()
        before = _counts(db, queued_task_id)

        with pytest.raises(EvaluationLeaseInvalid):
            EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER).claim_next(
                worker_identity="score-worker-local",
                supported_adapters={"lol.v1"},
                occurred_at=task_creation_scenario.now,
            )

        assert _counts(db, queued_task_id) == before
        db.expire_all()
        stored = db.get(SystemEvaluationTask, queued_task_id)
        assert stored is not None
        assert stored.status == "queued"
        assert stored.current_attempt == 0
        db.commit()


@pytest.mark.parametrize(
    "input",
    [
        _result_input(adapter_version="2.0.0"),
        _result_input(
            fields={"kills": 12, "deaths": 3, "assists": 8, "damage": 999}
        ),
        _result_input(
            fields={"deaths": 3, "assists": 8},
            confidences={"deaths": 0.97, "assists": 0.96},
        ),
        _result_input(confidences={"kills": 0.98, "deaths": 0.97}),
    ],
    ids=[
        "profile-version-mismatch",
        "unknown-field",
        "undeclared-missing-field",
        "confidence-key-mismatch",
    ],
)
def test_submit_result_rejects_profile_contract_mismatches_without_writes(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    input: SystemEvaluationResultInput,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        before = _counts(db, queued_task_id)

        with pytest.raises(EvaluationResultInvalid):
            service.submit_result(
                task_id=claim.task_id,
                attempt=claim.attempt,
                task_version=claim.task_version,
                worker_identity="score-worker-local",
                lease_token=claim.lease_token,
                input=input,
                occurred_at=scenario.now + timedelta(minutes=1),
                request_id="processing-invalid-result",
            )

        assert _counts(db, queued_task_id) == before
        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        assert task.status == "processing"
        db.commit()


@pytest.mark.parametrize(
    "input",
    [
        _result_input(
            fields={
                "kills": "身份证 11010519491231002X",
                "deaths": 3,
                "assists": 8,
            }
        ),
        _result_input(
            warnings=[
                {
                    "key": "contact_support",
                    "severity": "warning",
                    "summary": "请联系 13800138000",
                }
            ]
        ),
        _result_input(
            components=[
                {
                    "key": "combat",
                    "score": 86,
                    "weight": 1.0,
                    "summary": "账号手机号 13800138000",
                }
            ]
        ),
        _result_input(grade="13800138000"),
        _result_input(advice="请联系 13800138000 获取建议"),
    ],
    ids=[
        "field-value",
        "warning-summary",
        "component-summary",
        "grade",
        "advice",
    ],
)
def test_submit_result_rejects_sensitive_text_without_writes(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    input: SystemEvaluationResultInput,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        before = _counts(db, queued_task_id)

        with pytest.raises(EvaluationResultInvalid):
            service.submit_result(
                task_id=claim.task_id,
                attempt=claim.attempt,
                task_version=claim.task_version,
                worker_identity="score-worker-local",
                lease_token=claim.lease_token,
                input=input,
                occurred_at=scenario.now + timedelta(minutes=1),
                request_id="processing-sensitive-result",
            )

        assert _counts(db, queued_task_id) == before
        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        assert task.status == "processing"
        db.commit()


def test_declared_missing_and_blocking_results_still_require_review(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    _create_queued_task(scenario, publish_mode="auto_publish")
    with Session(scenario.engine) as db:
        db.add(
            EvaluationAdapterCertification(
                project_id=scenario.project_id,
                adapter_id="lol",
                adapter_version="1.0.0",
                scoring_rule_version="1",
                result_schema_version="1",
                manifest_digest="a" * 64,
                certified_by_user_id=scenario.admin_user_id,
                certified_at=scenario.now - timedelta(seconds=1),
            )
        )
        db.commit()
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        input = _result_input(
            fields={"deaths": 3, "assists": 8},
            confidences={"deaths": 0.97, "assists": 0.96},
            missing_fields=["kills"],
            warnings=[
                {
                    "key": "kills_missing",
                    "severity": "blocking",
                    "summary": "击杀数据无法识别",
                }
            ],
        )

        submitted = service.submit_result(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            input=input,
            occurred_at=scenario.now + timedelta(minutes=1),
            request_id="processing-blocking-result",
        )

        assert submitted.task_status == "needs_review"
        stored = db.get(SystemEvaluationResultVersion, submitted.result_id)
        assert stored is not None
        assert stored.missing_fields == ["kills"]
        assert stored.warnings == [
            {
                "key": "kills_missing",
                "severity": "blocking",
                "summary": "击杀数据无法识别",
            }
        ]
        assert stored.review_decision == "pending"
        assert stored.published_at is None


@pytest.mark.parametrize("operation", ["result", "failure"])
@pytest.mark.parametrize(
    "mutation",
    ["worker", "token", "version", "attempt", "expired", "state", "replaced"],
)
def test_result_and_failure_reject_stale_leases_without_side_effects(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    operation: str,
    mutation: str,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        common: dict[str, object] = {
            "task_id": claim.task_id,
            "attempt": claim.attempt,
            "task_version": claim.task_version,
            "worker_identity": "score-worker-local",
            "lease_token": claim.lease_token,
            "occurred_at": scenario.now + timedelta(minutes=1),
            "request_id": "processing-stale-submission",
        }
        if mutation == "worker":
            common["worker_identity"] = "another-worker"
        elif mutation == "token":
            common["lease_token"] = "not-the-current-lease-token"
        elif mutation == "version":
            common["task_version"] = claim.task_version + 1
        elif mutation == "attempt":
            common["attempt"] = claim.attempt + 1
        elif mutation == "expired":
            common["occurred_at"] = claim.lease_expires_at
        elif mutation == "state":
            task = db.get(SystemEvaluationTask, queued_task_id)
            assert task is not None
            task.status = "queued"
            db.commit()
        else:
            attempt_row = db.scalar(
                select(SystemEvaluationAttempt).where(
                    SystemEvaluationAttempt.task_id == queued_task_id,
                    SystemEvaluationAttempt.attempt == 1,
                )
            )
            assert attempt_row is not None
            attempt_row.lease_token_digest = "a" * 64
            db.commit()
        before = _counts(db, queued_task_id)

        with pytest.raises(EvaluationLeaseInvalid):
            if operation == "result":
                service.submit_result(input=_result_input(), **common)
            else:
                service.submit_failure(input=_failure_input(), **common)

        assert _counts(db, queued_task_id) == before
        assert db.scalar(select(func.count(SystemEvaluationTask.id))) >= 1
        db.commit()


@pytest.mark.parametrize("operation", ["renew", "result", "failure"])
def test_real_previous_attempt_cannot_overwrite_the_current_attempt(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    operation: str,
) -> None:
    with Session(task_creation_scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        first = service.claim_next(
            worker_identity="score-worker-one",
            supported_adapters={"lol.v1"},
            occurred_at=task_creation_scenario.now,
        )
        assert first is not None
        first_failure = service.submit_failure(
            task_id=first.task_id,
            attempt=first.attempt,
            task_version=first.task_version,
            worker_identity="score-worker-one",
            lease_token=first.lease_token,
            input=_failure_input(idempotency_key="first-attempt-failure"),
            occurred_at=task_creation_scenario.now + timedelta(seconds=1),
            request_id="processing-first-attempt-failure",
        )
        assert first_failure.available_at is not None
        second = service.claim_next(
            worker_identity="score-worker-two",
            supported_adapters={"lol.v1"},
            occurred_at=first_failure.available_at,
        )
        assert second is not None
        assert second.attempt == 2
        before = _counts(db, queued_task_id)
        common = {
            "task_id": first.task_id,
            "attempt": first.attempt,
            "task_version": first.task_version,
            "worker_identity": "score-worker-one",
            "lease_token": first.lease_token,
            "occurred_at": first_failure.available_at + timedelta(seconds=1),
            "request_id": f"processing-old-attempt-{operation}",
        }

        with pytest.raises(EvaluationLeaseInvalid):
            if operation == "renew":
                service.renew(**common)
            elif operation == "result":
                service.submit_result(input=_result_input(), **common)
            else:
                service.submit_failure(input=_failure_input(), **common)

        assert _counts(db, queued_task_id) == before
        assert db.scalar(select(func.count(SystemEvaluationTask.id))) >= 1
        db.commit()


def test_expired_leases_are_recovered_twice_then_move_to_manual_input(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    with Session(task_creation_scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        occurred_at = task_creation_scenario.now
        claims = []
        for attempt_number in (1, 2, 3):
            claim = service.claim_next(
                worker_identity=f"score-worker-{attempt_number}",
                supported_adapters={"lol.v1"},
                occurred_at=occurred_at,
            )
            assert claim is not None
            assert claim.attempt == attempt_number
            claims.append(claim)
            occurred_at = claim.lease_expires_at

        exhausted = service.claim_next(
            worker_identity="score-worker-four",
            supported_adapters={"lol.v1"},
            occurred_at=occurred_at,
        )

        assert exhausted is None
        task = db.get(SystemEvaluationTask, queued_task_id)
        attempts = list(
            db.scalars(
                select(SystemEvaluationAttempt)
                .where(SystemEvaluationAttempt.task_id == queued_task_id)
                .order_by(SystemEvaluationAttempt.attempt)
            )
        )
        assert task is not None
        assert task.status == "manual_input"
        assert task.current_attempt == 3
        assert task.failure_code == "automatic_retries_exhausted"
        assert [attempt.status for attempt in attempts] == [
            "expired",
            "expired",
            "expired",
        ]
        assert all(attempt.completed_at is not None for attempt in attempts)


def test_claim_failure_after_attempt_flush_rolls_back_and_keeps_session_usable(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(task_creation_scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)

        def fail_audit(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("injected claim audit failure")

        monkeypatch.setattr(AuditService, "append", fail_audit)
        before = _counts(db, queued_task_id)
        with pytest.raises(RuntimeError, match="injected claim audit failure"):
            service.claim_next(
                worker_identity="score-worker-local",
                supported_adapters={"lol.v1"},
                occurred_at=task_creation_scenario.now,
            )

        assert _counts(db, queued_task_id) == before
        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        assert task.status == "queued"
        assert task.current_attempt == 0
        assert db.scalar(select(func.count(SystemEvaluationTask.id))) >= 1
        db.commit()


def test_retryable_failures_back_off_twice_then_move_to_manual_input(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        due_at = scenario.now
        expected_delays = (30, 60)

        for attempt_number in (1, 2, 3):
            claim = service.claim_next(
                worker_identity="score-worker-local",
                supported_adapters={"lol.v1"},
                occurred_at=due_at,
            )
            assert claim is not None
            assert claim.attempt == attempt_number
            failed_at = due_at + timedelta(seconds=1)
            failure = _failure_input(
                idempotency_key=f"failure-task-1-attempt-{attempt_number}"
            )

            outcome = service.submit_failure(
                task_id=claim.task_id,
                attempt=claim.attempt,
                task_version=claim.task_version,
                worker_identity="score-worker-local",
                lease_token=claim.lease_token,
                input=failure,
                occurred_at=failed_at,
                request_id=f"processing-failure-{attempt_number}",
            )

            assert outcome.attempt == attempt_number
            if attempt_number < 3:
                due_at = failed_at + timedelta(
                    seconds=expected_delays[attempt_number - 1]
                )
                assert outcome.task_status == "queued"
                assert outcome.available_at == due_at
                assert outcome.failure_code is None
            else:
                assert outcome.task_status == "manual_input"
                assert outcome.available_at is None
                assert outcome.failure_code == "automatic_retries_exhausted"

        task = db.get(SystemEvaluationTask, queued_task_id)
        attempts = list(
            db.scalars(
                select(SystemEvaluationAttempt)
                .where(SystemEvaluationAttempt.task_id == queued_task_id)
                .order_by(SystemEvaluationAttempt.attempt)
            )
        )
        assert task is not None
        assert task.status == "manual_input"
        assert task.current_attempt == 3
        assert task.automatic_attempts == 3
        assert [item.status for item in attempts] == ["failed", "failed", "failed"]
        assert [item.retryable for item in attempts] == [True, True, True]
        assert all(item.safe_error_summary == "worker network request failed" for item in attempts)


@pytest.mark.parametrize(
    "error_class",
    ["adapter_incompatible", "media_invalid", "result_schema_invalid"],
)
def test_permanent_failure_moves_to_manual_input_without_retry(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    error_class: str,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        service = EvaluationProcessingService(db, token_pepper=TOKEN_PEPPER)
        claim = service.claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None

        outcome = service.submit_failure(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            worker_identity="score-worker-local",
            lease_token=claim.lease_token,
            input=_failure_input(error_class=error_class),
            occurred_at=scenario.now + timedelta(seconds=1),
            request_id="processing-permanent-failure",
        )

        assert outcome.task_status == "manual_input"
        assert outcome.available_at is None
        assert outcome.failure_code == error_class
        task = db.get(SystemEvaluationTask, queued_task_id)
        assert task is not None
        assert task.status == "manual_input"
        assert task.current_attempt == 1
        db.commit()


def test_claim_issues_task_bound_media_tickets_and_worker_can_read_bytes(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        claim = EvaluationProcessingService(
            db,
            token_pepper=TOKEN_PEPPER,
        ).claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        assert claim.task_id == queued_task_id
        assert [item.position for item in claim.media] == [1, 2, 3]
        assert [item.slot_key for item in claim.media] == [
            "profile",
            "match_result",
            "team_detail",
        ]
        assert all(item.ticket for item in claim.media)

        selected = claim.media[0]
        protected = WorkerMediaTicketService(
            db,
            store=scenario.store,
            secret=TOKEN_PEPPER,
        ).read_original(
            task_id=claim.task_id,
            attempt=claim.attempt,
            task_version=claim.task_version,
            media_id=selected.media_id,
            worker_identity="score-worker-local",
            ticket=selected.ticket,
            occurred_at=scenario.now + timedelta(minutes=1),
        )
        try:
            assert protected.path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            assert protected.mime_type == "image/png"
            assert protected.headers["Cache-Control"] == "private, no-store"
            assert protected.headers["X-Content-Type-Options"] == "nosniff"
        finally:
            protected.close()


@pytest.mark.parametrize(
    "mutation",
    ["task", "attempt", "media", "worker", "ticket", "expired", "replaced"],
)
def test_media_ticket_failures_are_private_and_have_no_side_effects(
    task_creation_scenario: TaskCreationScenario,
    queued_task_id: UUID,
    mutation: str,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as db:
        claim = EvaluationProcessingService(
            db,
            token_pepper=TOKEN_PEPPER,
        ).claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=scenario.now,
        )
        assert claim is not None
        selected = claim.media[0]
        kwargs: dict[str, object] = {
            "task_id": claim.task_id,
            "attempt": claim.attempt,
            "task_version": claim.task_version,
            "media_id": selected.media_id,
            "worker_identity": "score-worker-local",
            "ticket": selected.ticket,
            "occurred_at": scenario.now + timedelta(minutes=1),
        }
        if mutation == "task":
            kwargs["task_id"] = uuid4()
        elif mutation == "attempt":
            kwargs["attempt"] = claim.attempt + 1
        elif mutation == "media":
            kwargs["media_id"] = claim.media[1].media_id
        elif mutation == "worker":
            kwargs["worker_identity"] = "another-worker"
        elif mutation == "ticket":
            kwargs["ticket"] = f"{selected.ticket}x"
        elif mutation == "expired":
            kwargs["occurred_at"] = claim.lease_expires_at
        else:
            attempt = db.scalar(
                select(SystemEvaluationAttempt).where(
                    SystemEvaluationAttempt.task_id == queued_task_id,
                    SystemEvaluationAttempt.attempt == 1,
                )
            )
            assert attempt is not None
            attempt.lease_token_digest = "a" * 64
            db.commit()
        before = _counts(db, queued_task_id)

        with pytest.raises(EvaluationResourceNotFound):
            WorkerMediaTicketService(
                db,
                store=scenario.store,
                secret=TOKEN_PEPPER,
            ).read_original(**kwargs)

        assert _counts(db, queued_task_id) == before
        assert db.scalar(select(func.count(SystemEvaluationTask.id))) >= 1
        db.commit()
