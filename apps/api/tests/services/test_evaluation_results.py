from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationOperationDenied,
    EvaluationResourceNotFound,
    EvaluationResultInvalid,
    EvaluationTaskStateConflict,
    EvaluationTaskVersionConflict,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.result_service import EvaluationResultService
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.review_schemas import (
    EvaluationComponentInput,
    EvaluationConfidenceInput,
    EvaluationFieldInput,
    EvaluationManualDraftInput,
    EvaluationManualSubmitInput,
    EvaluationResultRevisionInput,
)
from app.evaluations.task_models import (
    EvaluationIdempotencyKey,
    SystemEvaluationEvent,
    SystemEvaluationTask,
)
from app.rbac.models import Permission, UserPermissionOverride
from tests.services.test_evaluation_queries import _result, _task
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_uploads",)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
IDEMPOTENCY_KEY = b"result-service-idempotency-key-32-bytes-minimum"


@dataclass(frozen=True)
class EvaluationResultScenario:
    base: TaskCreationScenario
    manual_task_id: UUID
    rejected_task_id: UUID
    rejected_result_id: UUID
    published_task_id: UUID
    published_result_id: UUID


def _draft(
    *,
    expected_lock_version: int,
    draft_revision: int | None,
    complete: bool,
) -> EvaluationManualDraftInput:
    return EvaluationManualDraftInput(
        expected_lock_version=expected_lock_version,
        draft_revision=draft_revision,
        fields=(
            [
                EvaluationFieldInput(key="score", value=88),
                EvaluationFieldInput(key="grade", value="A"),
            ]
            if complete
            else [EvaluationFieldInput(key="score", value=88)]
        ),
        confidences=(
            [
                EvaluationConfidenceInput(key="score", confidence=1),
                EvaluationConfidenceInput(key="grade", confidence=1),
            ]
            if complete
            else [EvaluationConfidenceInput(key="score", confidence=1)]
        ),
        missing_fields=[],
        warnings=[],
        components=(
            [
                EvaluationComponentInput(
                    key="overall",
                    score=88,
                    weight=1,
                    summary="训练表现稳定",
                )
            ]
            if complete
            else []
        ),
        score=88 if complete else None,
        grade="A" if complete else None,
        advice="继续保持训练节奏" if complete else None,
    )


@pytest.fixture
def evaluation_result_scenario(
    task_creation_scenario: TaskCreationScenario,
) -> Iterator[EvaluationResultScenario]:
    base = task_creation_scenario
    with Session(base.engine) as db, db.begin():
        permission = db.scalar(
            select(Permission).where(
                Permission.key == "evaluation_jobs.manual_input"
            )
        )
        assert permission is not None
        permission_id = permission.id
        db.add(
            UserPermissionOverride(
                user_id=base.actor_user_id,
                permission_id=permission.id,
                effect="allow",
                scope_kind="self",
                valid_from=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(days=1),
                created_by_user_id=base.admin_user_id,
                grant_reason="manual evaluation result test",
            )
        )

        manual = _task(
            db,
            replace(base, actor_user_id=base.admin_user_id),
            status="manual_input",
        )

        rejected = _task(db, base, status="rejected")
        rejected_result = SystemEvaluationResultVersion(
            task_id=rejected.id,
            version=1,
            source="manual",
            fields={"score": 70, "grade": "B"},
            confidences={"score": 1, "grade": 1},
            warnings=[],
            components=[
                {"key": "overall", "score": 70, "weight": 1, "summary": "待改进"}
            ],
            missing_fields=[],
            score=Decimal("70.00"),
            grade="B",
            advice="调整训练方法",
            result_schema_version="1",
            created_by_user_id=base.actor_user_id,
            review_decision="rejected",
            reviewed_by_user_id=base.admin_user_id,
            reviewed_at=NOW - timedelta(hours=1),
            rejection_reason="数据需要修正",
        )
        db.add(rejected_result)
        db.flush()
        rejected.current_result_version_id = rejected_result.id

        published = _task(db, base, status="published")
        published_result = _result(db, published, base, approved=True)
        db.flush()
        scenario = EvaluationResultScenario(
            base=base,
            manual_task_id=manual.id,
            rejected_task_id=rejected.id,
            rejected_result_id=rejected_result.id,
            published_task_id=published.id,
            published_result_id=published_result.id,
        )

    try:
        yield scenario
    finally:
        with Session(base.engine) as db, db.begin():
            db.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == base.actor_user_id,
                    UserPermissionOverride.permission_id == permission_id,
                )
            )


def _service(db: Session) -> EvaluationResultService:
    return EvaluationResultService(
        db,
        idempotency_service=EvaluationIdempotencyService(
            db,
            digest_key=IDEMPOTENCY_KEY,
        ),
    )


def test_manual_draft_can_be_incomplete_without_creating_a_result(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        saved = _service(db).save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=False,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-draft",
        )
        assert saved.task_status == "manual_input"
        assert saved.draft_revision == 1
        assert saved.is_complete is False
        assert saved.missing_required_fields == ("grade",)

    with Session(scenario.base.engine) as observer:
        draft = observer.get(SystemEvaluationManualDraft, scenario.manual_task_id)
        task = observer.get(SystemEvaluationTask, scenario.manual_task_id)
        assert draft is not None
        assert draft.fields == {"score": 88}
        assert task is not None and task.status == "manual_input"
        assert observer.scalar(
            select(func.count(SystemEvaluationResultVersion.id)).where(
                SystemEvaluationResultVersion.task_id == scenario.manual_task_id
            )
        ) == 0


def test_complete_manual_submit_is_immutable_authored_by_editor_and_idempotent(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        saved = service.save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-complete-draft",
        )
        submitted = service.submit_manual_result(
            scenario.manual_task_id,
            EvaluationManualSubmitInput(
                expected_lock_version=1,
                draft_revision=saved.draft_revision,
            ),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="manual-submit-1",
            occurred_at=NOW,
            request_id="evaluation-result-submit",
        )
        replay = service.submit_manual_result(
            scenario.manual_task_id,
            EvaluationManualSubmitInput(
                expected_lock_version=1,
                draft_revision=saved.draft_revision,
            ),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="manual-submit-1",
            occurred_at=NOW + timedelta(seconds=1),
            request_id="evaluation-result-submit-replay",
        )
        assert replay == submitted

    with Session(scenario.base.engine) as observer:
        task = observer.get(SystemEvaluationTask, scenario.manual_task_id)
        result = observer.get(SystemEvaluationResultVersion, submitted.result_id)
        assert task is not None and task.status == "needs_review"
        assert task.current_result_version_id == submitted.result_id
        assert task.published_result_version_id is None
        assert result is not None
        assert result.created_by_user_id == scenario.base.actor_user_id
        assert result.created_by_user_id != task.created_by_user_id
        assert result.review_decision == "pending"
        assert observer.get(SystemEvaluationManualDraft, task.id) is None
        assert observer.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.resource_id == result.id
            )
        ) == 1


def test_submit_revalidates_required_fields_and_preserves_the_draft(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        saved = service.save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=False,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-incomplete-draft",
        )
        with pytest.raises(EvaluationResultInvalid):
            service.submit_manual_result(
                scenario.manual_task_id,
                EvaluationManualSubmitInput(
                    expected_lock_version=1,
                    draft_revision=saved.draft_revision,
                ),
                actor_user_id=scenario.base.actor_user_id,
                idempotency_key="manual-submit-incomplete",
                occurred_at=NOW,
                request_id="evaluation-result-incomplete-submit",
            )
        assert db.scalar(select(1)) == 1

    with Session(scenario.base.engine) as observer:
        assert observer.get(SystemEvaluationManualDraft, scenario.manual_task_id) is not None
        assert observer.scalar(
            select(func.count(SystemEvaluationResultVersion.id)).where(
                SystemEvaluationResultVersion.task_id == scenario.manual_task_id
            )
        ) == 0


def test_draft_and_task_optimistic_conflicts_leave_the_session_usable(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        saved = service.save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-conflict-draft",
        )
        with pytest.raises(EvaluationTaskVersionConflict):
            service.save_manual_draft(
                scenario.manual_task_id,
                _draft(
                    expected_lock_version=2,
                    draft_revision=saved.draft_revision,
                    complete=True,
                ),
                actor_user_id=scenario.base.actor_user_id,
                occurred_at=NOW,
                request_id="evaluation-result-task-conflict",
            )
        assert db.scalar(select(1)) == 1
        with pytest.raises(EvaluationTaskVersionConflict):
            service.save_manual_draft(
                scenario.manual_task_id,
                _draft(
                    expected_lock_version=1,
                    draft_revision=999,
                    complete=True,
                ),
                actor_user_id=scenario.base.actor_user_id,
                occurred_at=NOW,
                request_id="evaluation-result-draft-conflict",
            )
        assert db.scalar(select(1)) == 1


def test_correction_and_revision_copy_the_previous_version_into_a_draft(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        correction = service.start_correction(
            scenario.rejected_task_id,
            EvaluationResultRevisionInput(expected_lock_version=1, mode="manual"),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="correction-start-1",
            occurred_at=NOW,
            request_id="evaluation-result-correction",
        )
        revision = service.start_revision(
            scenario.published_task_id,
            EvaluationResultRevisionInput(expected_lock_version=1, mode="manual"),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="revision-start-1",
            occurred_at=NOW,
            request_id="evaluation-result-revision",
        )
        assert correction.task_status == "correction_pending"
        assert revision.task_status == "manual_input"

    with Session(scenario.base.engine) as observer:
        correction_draft = observer.get(
            SystemEvaluationManualDraft,
            scenario.rejected_task_id,
        )
        revision_draft = observer.get(
            SystemEvaluationManualDraft,
            scenario.published_task_id,
        )
        published_task = observer.get(SystemEvaluationTask, scenario.published_task_id)
        assert correction_draft is not None and correction_draft.score == Decimal(70)
        assert revision_draft is not None and revision_draft.score == Decimal(88)
        assert published_task is not None
        assert published_task.published_result_version_id == scenario.published_result_id
        assert published_task.current_result_version_id == scenario.published_result_id


def test_correction_start_replay_returns_the_original_draft_revision(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    revision_input = EvaluationResultRevisionInput(
        expected_lock_version=1,
        mode="manual",
    )
    with Session(scenario.base.engine) as db:
        service = _service(db)
        started = service.start_correction(
            scenario.rejected_task_id,
            revision_input,
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="correction-start-replay",
            occurred_at=NOW,
            request_id="evaluation-result-correction-replay-start",
        )
        saved = service.save_manual_draft(
            scenario.rejected_task_id,
            _draft(
                expected_lock_version=started.task_version,
                draft_revision=started.draft_revision,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW + timedelta(seconds=1),
            request_id="evaluation-result-correction-replay-edit",
        )
        assert saved.draft_revision == 2

        replay = service.start_correction(
            scenario.rejected_task_id,
            revision_input,
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="correction-start-replay",
            occurred_at=NOW + timedelta(seconds=2),
            request_id="evaluation-result-correction-replay",
        )
        assert replay == started


def test_submit_revalidates_stored_component_keys_and_preserves_invalid_draft(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        saved = _service(db).save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-revalidate-draft",
        )

    duplicate_components = [
        {"key": "overall", "score": 88, "weight": 1, "summary": "稳定"},
        {"key": "overall", "score": 90, "weight": 1, "summary": "重复"},
    ]
    with Session(scenario.base.engine) as tamper, tamper.begin():
        tamper.execute(
            update(SystemEvaluationManualDraft)
            .where(SystemEvaluationManualDraft.task_id == scenario.manual_task_id)
            .values(components=duplicate_components)
        )

    with Session(scenario.base.engine) as db:
        with pytest.raises(EvaluationResultInvalid):
            _service(db).submit_manual_result(
                scenario.manual_task_id,
                EvaluationManualSubmitInput(
                    expected_lock_version=1,
                    draft_revision=saved.draft_revision,
                ),
                actor_user_id=scenario.base.actor_user_id,
                idempotency_key="manual-submit-invalid-stored-draft",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-result-revalidate-submit",
            )
        assert db.scalar(select(1)) == 1

    with Session(scenario.base.engine) as observer:
        assert observer.get(SystemEvaluationManualDraft, scenario.manual_task_id) is not None
        assert observer.scalar(
            select(func.count(SystemEvaluationResultVersion.id)).where(
                SystemEvaluationResultVersion.task_id == scenario.manual_task_id
            )
        ) == 0


def test_correction_and_revision_submissions_chain_from_the_previous_version(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        correction = service.start_correction(
            scenario.rejected_task_id,
            EvaluationResultRevisionInput(expected_lock_version=1, mode="manual"),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="correction-chain-start",
            occurred_at=NOW,
            request_id="evaluation-result-correction-chain-start",
        )
        correction_draft = service.save_manual_draft(
            scenario.rejected_task_id,
            _draft(
                expected_lock_version=correction.task_version,
                draft_revision=correction.draft_revision,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW + timedelta(seconds=1),
            request_id="evaluation-result-correction-chain-edit",
        )
        corrected = service.submit_manual_result(
            scenario.rejected_task_id,
            EvaluationManualSubmitInput(
                expected_lock_version=correction.task_version,
                draft_revision=correction_draft.draft_revision,
            ),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="correction-chain-submit",
            occurred_at=NOW + timedelta(seconds=2),
            request_id="evaluation-result-correction-chain-submit",
        )

        revision = service.start_revision(
            scenario.published_task_id,
            EvaluationResultRevisionInput(expected_lock_version=1, mode="manual"),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="revision-chain-start",
            occurred_at=NOW,
            request_id="evaluation-result-revision-chain-start",
        )
        revision_draft = service.save_manual_draft(
            scenario.published_task_id,
            _draft(
                expected_lock_version=revision.task_version,
                draft_revision=revision.draft_revision,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW + timedelta(seconds=1),
            request_id="evaluation-result-revision-chain-edit",
        )
        revised = service.submit_manual_result(
            scenario.published_task_id,
            EvaluationManualSubmitInput(
                expected_lock_version=revision.task_version,
                draft_revision=revision_draft.draft_revision,
            ),
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="revision-chain-submit",
            occurred_at=NOW + timedelta(seconds=2),
            request_id="evaluation-result-revision-chain-submit",
        )

    with Session(scenario.base.engine) as observer:
        corrected_result = observer.get(
            SystemEvaluationResultVersion,
            corrected.result_id,
        )
        revised_result = observer.get(SystemEvaluationResultVersion, revised.result_id)
        revised_task = observer.get(SystemEvaluationTask, scenario.published_task_id)
        assert corrected_result is not None
        assert corrected_result.version == 2
        assert corrected_result.source == "correction"
        assert corrected_result.previous_version_id == scenario.rejected_result_id
        assert corrected_result.created_by_user_id == scenario.base.actor_user_id
        assert revised_result is not None
        assert revised_result.version == 2
        assert revised_result.source == "revision"
        assert revised_result.previous_version_id == scenario.published_result_id
        assert revised_result.created_by_user_id == scenario.base.actor_user_id
        assert revised_task is not None
        assert revised_task.current_result_version_id == revised.result_id
        assert revised_task.published_result_version_id == scenario.published_result_id


def test_manual_submit_replay_reauthorizes_before_idempotency(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    input = EvaluationManualSubmitInput(expected_lock_version=1, draft_revision=1)
    with Session(scenario.base.engine) as db:
        service = _service(db)
        service.save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-reauth-draft",
        )
        service.submit_manual_result(
            scenario.manual_task_id,
            input,
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="manual-submit-reauth",
            occurred_at=NOW,
            request_id="evaluation-result-reauth-submit",
        )

    with Session(scenario.base.engine) as setup, setup.begin():
        actor = setup.get(UserAccount, scenario.base.actor_user_id)
        assert actor is not None
        actor.status = "disabled"

    try:
        with Session(scenario.base.engine) as db:
            before = (
                db.scalar(
                    select(func.count(SystemEvaluationEvent.id)).where(
                        SystemEvaluationEvent.task_id == scenario.manual_task_id
                    )
                ),
                db.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.resource_id == scenario.manual_task_id
                    )
                ),
            )
            with pytest.raises(EvaluationOperationDenied):
                _service(db).submit_manual_result(
                    scenario.manual_task_id,
                    input,
                    actor_user_id=scenario.base.actor_user_id,
                    idempotency_key="manual-submit-reauth",
                    occurred_at=NOW + timedelta(seconds=1),
                    request_id="evaluation-result-reauth-replay",
                )
            assert db.scalar(select(1)) == 1
            assert before == (
                db.scalar(
                    select(func.count(SystemEvaluationEvent.id)).where(
                        SystemEvaluationEvent.task_id == scenario.manual_task_id
                    )
                ),
                db.scalar(
                    select(func.count(AuditEvent.id)).where(
                        AuditEvent.resource_id == scenario.manual_task_id
                    )
                ),
            )
    finally:
        with Session(scenario.base.engine) as cleanup, cleanup.begin():
            actor = cleanup.get(UserAccount, scenario.base.actor_user_id)
            assert actor is not None
            actor.status = "active"


def test_submit_audit_failure_rolls_back_all_writes_and_keeps_session_usable(
    evaluation_result_scenario: EvaluationResultScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        saved = _service(db).save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-audit-rollback-draft",
        )

        def fail_audit(_self: AuditService, **_kwargs: object) -> None:
            raise RuntimeError("audit storage unavailable")

        monkeypatch.setattr(AuditService, "append", fail_audit)
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            _service(db).submit_manual_result(
                scenario.manual_task_id,
                EvaluationManualSubmitInput(
                    expected_lock_version=1,
                    draft_revision=saved.draft_revision,
                ),
                actor_user_id=scenario.base.actor_user_id,
                idempotency_key="manual-submit-audit-rollback",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-result-audit-rollback-submit",
            )
        assert db.scalar(select(1)) == 1

    with Session(scenario.base.engine) as observer:
        task = observer.get(SystemEvaluationTask, scenario.manual_task_id)
        assert task is not None
        assert task.status == "manual_input"
        assert task.lock_version == 1
        assert observer.get(SystemEvaluationManualDraft, task.id) is not None
        assert observer.scalar(
            select(func.count(SystemEvaluationResultVersion.id)).where(
                SystemEvaluationResultVersion.task_id == task.id
            )
        ) == 0
        assert observer.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.task_id == task.id
            )
        ) == 0
        assert observer.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.scope == "evaluation.result.manual.submit"
            )
        ) == 0


def test_manual_action_capability_and_private_not_found_are_distinct(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    missing_id = uuid4()
    with Session(scenario.base.engine) as db:
        with pytest.raises(EvaluationResourceNotFound):
            _service(db).save_manual_draft(
                missing_id,
                _draft(
                    expected_lock_version=1,
                    draft_revision=None,
                    complete=False,
                ),
                actor_user_id=scenario.base.actor_user_id,
                occurred_at=NOW,
                request_id="evaluation-result-private-missing",
            )
        assert db.scalar(select(1)) == 1


def test_new_key_after_submission_observes_state_conflict_and_payload_mismatch_conflicts(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        service = _service(db)
        saved = service.save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-state-draft",
        )
        input = EvaluationManualSubmitInput(
            expected_lock_version=1,
            draft_revision=saved.draft_revision,
        )
        service.submit_manual_result(
            scenario.manual_task_id,
            input,
            actor_user_id=scenario.base.actor_user_id,
            idempotency_key="manual-submit-state",
            occurred_at=NOW,
            request_id="evaluation-result-state-submit",
        )
        with pytest.raises(EvaluationIdempotencyConflict):
            service.submit_manual_result(
                scenario.manual_task_id,
                EvaluationManualSubmitInput(
                    expected_lock_version=999,
                    draft_revision=saved.draft_revision,
                ),
                actor_user_id=scenario.base.actor_user_id,
                idempotency_key="manual-submit-state",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-result-payload-conflict",
            )
        assert db.scalar(select(1)) == 1
        with pytest.raises(EvaluationTaskStateConflict):
            service.submit_manual_result(
                scenario.manual_task_id,
                input,
                actor_user_id=scenario.base.actor_user_id,
                idempotency_key="manual-submit-new-key",
                occurred_at=NOW + timedelta(seconds=1),
                request_id="evaluation-result-state-conflict",
            )
        assert db.scalar(select(1)) == 1


def test_result_audit_metadata_never_contains_result_body(
    evaluation_result_scenario: EvaluationResultScenario,
) -> None:
    scenario = evaluation_result_scenario
    with Session(scenario.base.engine) as db:
        _service(db).save_manual_draft(
            scenario.manual_task_id,
            _draft(
                expected_lock_version=1,
                draft_revision=None,
                complete=True,
            ),
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
            request_id="evaluation-result-audit",
        )
    with Session(scenario.base.engine) as observer:
        events = list(
            observer.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == scenario.manual_task_id
                )
            )
        )
        assert events
        serialized = repr([event.event_metadata for event in events]).lower()
        for forbidden in ("advice", "训练", "fields", "confidence", "reason"):
            assert forbidden not in serialized
