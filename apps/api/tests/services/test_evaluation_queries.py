from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.privacy import contains_phone_or_identity
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.query_service import (
    EvaluationQueryFilters,
    EvaluationQueryService,
)
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.task_models import (
    SystemEvaluationTask,
    SystemEvaluationTaskMedia,
    SystemEvaluationUploadSession,
)
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
)
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.schemas import PermissionOverrideCreate
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.teaching.models import CoachAssignment, Cohort
from tests.account_identifiers import add_test_primary_login_identifier
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_uploads",)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class EvaluationQueryScenario:
    base: TaskCreationScenario
    published_task_id: UUID
    pending_task_id: UUID
    processing_task_id: UUID
    published_result_id: UUID
    pending_result_id: UUID
    media_id: UUID
    guardian_user_id: UUID
    guardian_person_id: UUID
    guardian_relationship_id: UUID
    coach_user_id: UUID
    coach_person_id: UUID
    coach_assignment_id: UUID


def _task(
    db: Session,
    scenario: TaskCreationScenario,
    *,
    status: str,
    analysis_mode: str = "manual_only",
    current_attempt: int = 0,
) -> SystemEvaluationTask:
    created_at = datetime.now(UTC)
    upload = SystemEvaluationUploadSession(
        created_by_user_id=scenario.actor_user_id,
        project_id=scenario.project_id,
        student_person_id=scenario.actor_person_id,
        enrollment_id=scenario.enrollment_id,
        status="committed",
        expires_at=created_at + timedelta(hours=1),
        committed_at=created_at,
    )
    db.add(upload)
    db.flush()
    task = SystemEvaluationTask(
        public_number=f"EV-{uuid4().hex[:20].upper()}",
        upload_session_id=upload.id,
        created_by_user_id=scenario.actor_user_id,
        student_person_id=scenario.actor_person_id,
        enrollment_id=scenario.enrollment_id,
        cohort_id=scenario.cohort_id,
        project_id=scenario.project_id,
        class_id=scenario.class_id,
        analysis_profile_id=scenario.profile_id,
        analysis_profile_version=1,
        analysis_config_snapshot={
            "mode": analysis_mode,
            "adapter_id": "lol" if analysis_mode == "automatic" else None,
            "adapter_version": "1.0.0" if analysis_mode == "automatic" else None,
            "scoring_rule_version": "1" if analysis_mode == "automatic" else None,
            "required_fields": ["score", "grade"],
            "media_slots": [{"key": "profile", "label": "个人数据", "required": True}],
            "result_schema_version": "1",
            "confidence_thresholds": {},
            "auto_publish_warning_levels": [],
            "publish_mode": "review_required",
        },
        upload_window_start=NOW - timedelta(days=7),
        upload_window_end=NOW + timedelta(days=7),
        status=status,
        current_attempt=current_attempt,
        automatic_attempts=current_attempt,
    )
    db.add(task)
    db.flush()
    return task


def _result(
    db: Session,
    task: SystemEvaluationTask,
    scenario: TaskCreationScenario,
    *,
    approved: bool,
    source: str = "manual",
) -> SystemEvaluationResultVersion:
    result = SystemEvaluationResultVersion(
        task_id=task.id,
        version=1,
        source=source,
        fields={"score": 88, "grade": "A"},
        confidences={"score": 1, "grade": 1},
        warnings=[],
        components=[{"key": "overall", "score": 88, "weight": 1, "summary": "稳定"}],
        missing_fields=[],
        score=Decimal("88.00"),
        grade="A",
        advice="保持训练节奏，请联系13800138000",
        result_schema_version="1",
        created_by_user_id=None if source == "automatic" else scenario.actor_user_id,
        review_decision="approved" if approved else "pending",
        reviewed_by_user_id=scenario.actor_user_id if approved else None,
        reviewed_at=NOW if approved else None,
        published_at=NOW if approved else None,
    )
    db.add(result)
    db.flush()
    task.current_result_version_id = result.id
    if approved:
        task.published_result_version_id = result.id
        task.reviewed_at = NOW
        task.reviewed_by_user_id = scenario.actor_user_id
        task.published_at = NOW
        task.published_by_user_id = scenario.actor_user_id
    db.flush()
    return result


def _account(
    db: Session,
    *,
    name: str,
    sex: str,
) -> tuple[Person, UserAccount]:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"query:{marker}",
        identity_digest=(marker * 2)[:64],
        email=None,
        sex=sex,
        date_of_birth=date(1985, 1, 1),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=(marker[::-1] * 2)[:64],
        email_normalized=f"query-{marker[:16]}@example.test",
        password_hash="query-test-password-hash",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    add_test_primary_login_identifier(db, account)
    return person, account


@pytest.fixture
def evaluation_query_scenario(
    task_creation_scenario: TaskCreationScenario,
) -> Iterator[EvaluationQueryScenario]:
    base = task_creation_scenario
    with Session(base.engine) as db, db.begin():
        cohort = db.get(Cohort, base.cohort_id)
        assert cohort is not None
        cohort.starts_at = NOW - timedelta(days=21)
        cohort.ends_at = NOW
        cohort.default_check_in_at = cohort.starts_at
        cohort.default_check_out_at = cohort.ends_at

        published = _task(db, base, status="published")
        published_result = _result(db, published, base, approved=True)
        pending = _task(db, base, status="needs_review")
        pending_result = _result(db, pending, base, approved=False)
        processing = _task(db, base, status="processing")

        guardian_person, guardian = _account(
            db,
            name="评价查询监护人",
            sex="female",
        )
        db.add(UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id))
        relationship = GuardianRelationship(
            guardian_person_id=guardian_person.id,
            student_person_id=base.actor_person_id,
            relationship="mother",
            is_primary=True,
            valid_from=NOW.date() - timedelta(days=365),
        )
        db.add(relationship)

        coach_person, coach = _account(
            db,
            name="评价查询历史教练",
            sex="male",
        )
        db.add(StaffProfile(person_id=coach_person.id, staff_type="coach"))
        db.add(UserRole(user_id=coach.id, role_id=ROLES["coach"].id))
        assignment = CoachAssignment(
            class_id=base.class_id,
            coach_person_id=coach_person.id,
            assignment_type="assistant",
            valid_from=NOW - timedelta(days=60),
            ended_at=NOW - timedelta(days=1),
            ended_by_user_id=base.admin_user_id,
            end_reason="历史服务结束",
            created_by_user_id=base.admin_user_id,
        )
        db.add(assignment)

        blob = EvaluationMediaBlob(
            storage_key=f"query/{uuid4().hex}.png",
            preview_storage_key=f"query/{uuid4().hex}.webp",
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
            uploaded_by_user_id=base.actor_user_id,
            project_id=base.project_id,
            student_person_id=base.actor_person_id,
            enrollment_id=base.enrollment_id,
            class_id=base.class_id,
            matched_at=NOW,
            matched_by_user_id=base.admin_user_id,
            match_reason="query fixture",
        )
        db.add(asset)
        db.flush()
        db.add(
            SystemEvaluationTaskMedia(
                task_id=published.id,
                media_id=asset.id,
                position=1,
                slot_key="profile",
            )
        )
        db.flush()
        scenario = EvaluationQueryScenario(
            base=base,
            published_task_id=published.id,
            pending_task_id=pending.id,
            processing_task_id=processing.id,
            published_result_id=published_result.id,
            pending_result_id=pending_result.id,
            media_id=asset.id,
            guardian_user_id=guardian.id,
            guardian_person_id=guardian_person.id,
            guardian_relationship_id=relationship.id,
            coach_user_id=coach.id,
            coach_person_id=coach_person.id,
            coach_assignment_id=assignment.id,
        )

    try:
        yield scenario
    finally:
        with Session(base.engine) as db, db.begin():
            db.execute(
                delete(CoachAssignment).where(CoachAssignment.id == scenario.coach_assignment_id)
            )
            db.execute(
                delete(GuardianRelationship).where(
                    GuardianRelationship.id == scenario.guardian_relationship_id
                )
            )
            db.execute(
                delete(UserRole).where(
                    UserRole.user_id.in_([scenario.guardian_user_id, scenario.coach_user_id])
                )
            )
            db.execute(
                delete(StaffProfile).where(StaffProfile.person_id == scenario.coach_person_id)
            )
            db.execute(
                delete(UserAccount).where(
                    UserAccount.id.in_([scenario.guardian_user_id, scenario.coach_user_id])
                )
            )
            db.execute(
                delete(Person).where(
                    Person.id.in_([scenario.guardian_person_id, scenario.coach_person_id])
                )
            )


@pytest.mark.parametrize("actor", ["student", "guardian"])
def test_center_enrollment_deny_hides_relationship_task_history(evaluation_query_scenario, actor):
    scenario = evaluation_query_scenario
    account_id = scenario.base.actor_user_id if actor == "student" else scenario.guardian_user_id
    with Session(scenario.base.engine) as db:
        db.begin()  # Keep production override creation inside this test's rollback.
        service = EvaluationQueryService(db)
        assert service.list_account_tasks(actor_user_id=account_id, filters=EvaluationQueryFilters(),
                                          page=1, page_size=20, occurred_at=NOW).total == 3
        permission = db.scalar(select(Permission).where(Permission.key == "evaluation_center.access"))
        assert permission is not None
        RbacService(db).add_override(
            actor_user_id=scenario.base.actor_user_id, account_id=account_id,
            input=PermissionOverrideCreate(permission_id=permission.id, effect="deny", scope_kind="enrollment",
                                           scope_id=scenario.base.enrollment_id, grant_reason="Center deny regression"),
        )
        page = service.list_account_tasks(actor_user_id=account_id, filters=EvaluationQueryFilters(),
                                          page=1, page_size=20, occurred_at=NOW)
        assert page.total == 0
        assert page.items == ()
        with pytest.raises(EvaluationResourceNotFound):
            service.get_account_task(scenario.published_task_id, actor_user_id=account_id, occurred_at=NOW)


def test_student_and_guardian_see_task_status_but_only_published_result(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    for account_id in (
        scenario.base.actor_user_id,
        scenario.guardian_user_id,
    ):
        with Session(scenario.base.engine) as db:
            service = EvaluationQueryService(db)
            page = service.list_account_tasks(
                actor_user_id=account_id,
                filters=EvaluationQueryFilters(),
                page=1,
                page_size=20,
                occurred_at=NOW,
            )
            assert {item.id for item in page.items} == {
                scenario.published_task_id,
                scenario.pending_task_id,
                scenario.processing_task_id,
            }
            published = service.get_account_task(
                scenario.published_task_id,
                actor_user_id=account_id,
                occurred_at=NOW,
            )
            pending = service.get_account_task(
                scenario.pending_task_id,
                actor_user_id=account_id,
                occurred_at=NOW,
            )
            assert published.published_result is not None
            assert published.published_result.id == scenario.published_result_id
            assert published.current_result == published.published_result
            assert [result.id for result in published.result_versions] == [
                scenario.published_result_id
            ]
            assert pending.current_result is None
            assert pending.published_result is None
            assert pending.result_versions == ()


def test_admin_sees_working_result_and_list_detail_sets_are_identical(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        page = service.list_admin_tasks(
            actor_user_id=scenario.base.admin_user_id,
            filters=EvaluationQueryFilters(),
            page=1,
            page_size=20,
            occurred_at=NOW,
        )
        detail_ids = {
            service.get_admin_task(
                item.id,
                actor_user_id=scenario.base.admin_user_id,
                occurred_at=NOW,
            ).id
            for item in page.items
        }
        assert detail_ids == {item.id for item in page.items}
        pending = service.get_admin_task(
            scenario.pending_task_id,
            actor_user_id=scenario.base.admin_user_id,
            occurred_at=NOW,
        )
        assert pending.current_result is not None
        assert pending.current_result.id == scenario.pending_result_id
        assert [result.id for result in pending.result_versions] == [scenario.pending_result_id]
        assert pending.allowed_actions.review is True


def test_ended_coach_keeps_published_history_but_loses_working_result_and_media(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        published = service.get_account_task(
            scenario.published_task_id,
            actor_user_id=scenario.coach_user_id,
            occurred_at=NOW,
        )
        pending = service.get_account_task(
            scenario.pending_task_id,
            actor_user_id=scenario.coach_user_id,
            occurred_at=NOW,
        )
        assert published.published_result is not None
        assert pending.current_result is None
        assert pending.result_versions == ()
        assert pending.allowed_actions.review is False
        with pytest.raises(EvaluationResourceNotFound):
            service.require_task_media(
                scenario.published_task_id,
                scenario.media_id,
                actor_user_id=scenario.coach_user_id,
                occurred_at=NOW,
            )


def test_guardian_relationship_end_revokes_detail_and_media_without_writes(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db, db.begin():
        relationship = db.get(
            GuardianRelationship,
            scenario.guardian_relationship_id,
        )
        assert relationship is not None
        relationship.ended_at = NOW
        relationship.ended_by_user_id = scenario.base.admin_user_id
        relationship.end_reason = "关系结束"

    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        with pytest.raises(EvaluationResourceNotFound):
            service.get_account_task(
                scenario.published_task_id,
                actor_user_id=scenario.guardian_user_id,
                occurred_at=NOW,
            )
        with pytest.raises(EvaluationResourceNotFound):
            service.require_task_media(
                scenario.published_task_id,
                scenario.media_id,
                actor_user_id=scenario.guardian_user_id,
                occurred_at=NOW,
            )


def test_task_media_authorization_binds_media_to_the_requested_task(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        media = service.require_task_media(
            scenario.published_task_id,
            scenario.media_id,
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
        )
        assert media.media_id == scenario.media_id
        with pytest.raises(EvaluationResourceNotFound):
            service.require_task_media(
                scenario.pending_task_id,
                scenario.media_id,
                actor_user_id=scenario.base.actor_user_id,
                occurred_at=NOW,
            )


def test_action_selector_distinguishes_no_capability_from_private_task(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        with pytest.raises(EvaluationOperationDenied):
            service.require_action_task(
                scenario.pending_task_id,
                actor_user_id=scenario.base.actor_user_id,
                permission="evaluation_jobs.review",
                occurred_at=NOW,
            )
        with pytest.raises(EvaluationResourceNotFound):
            service.require_action_task(
                uuid4(),
                actor_user_id=scenario.base.admin_user_id,
                permission="evaluation_jobs.review",
                occurred_at=NOW,
            )


def test_action_selector_treats_a_global_explicit_deny_as_no_capability(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    override_id = uuid4()
    with Session(scenario.base.engine) as db, db.begin():
        db.add(
            UserPermissionOverride(
                id=override_id,
                user_id=scenario.base.admin_user_id,
                permission_id=PERMISSIONS["evaluation_jobs.review"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
                valid_from=NOW - timedelta(minutes=1),
                valid_until=NOW + timedelta(minutes=10),
                created_by_user_id=scenario.base.admin_user_id,
                grant_reason="暂停系统评价审核",
            )
        )

    try:
        with Session(scenario.base.engine) as db, pytest.raises(
            EvaluationOperationDenied
        ):
            EvaluationQueryService(db).require_action_task(
                scenario.pending_task_id,
                actor_user_id=scenario.base.admin_user_id,
                permission="evaluation_jobs.review",
                occurred_at=NOW,
            )
    finally:
        with Session(scenario.base.engine) as db, db.begin():
            db.execute(
                delete(UserPermissionOverride).where(
                    UserPermissionOverride.id == override_id
                )
            )


def test_archived_retry_exhaustion_exposes_only_the_valid_correction_reopen_mode(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db, db.begin():
        task = _task(
            db,
            scenario.base,
            status="archived",
            analysis_mode="automatic",
            current_attempt=3,
        )
        result = _result(
            db,
            task,
            scenario.base,
            approved=False,
            source="automatic",
        )
        result.review_decision = "rejected"
        result.reviewed_by_user_id = scenario.base.admin_user_id
        result.rejection_reason = "需要人工整改"
        result.reviewed_at = NOW
        task_id = task.id

    with Session(scenario.base.engine) as db:
        projected = EvaluationQueryService(db).get_admin_task(
            task_id,
            actor_user_id=scenario.base.admin_user_id,
            occurred_at=NOW,
        )

        assert projected.allowed_actions.reopen is True
        assert projected.allowed_reopen_modes == ("correction",)


def test_dashboard_counts_share_the_account_visibility_predicate(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        counts = EvaluationQueryService(db).dashboard_counts(
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
        )
        assert counts.processing == 1
        assert counts.pending_match == 0
        assert counts.manual_input == 0
        assert counts.needs_review == 1
        assert counts.rejected == 0
        assert counts.published == 1


def test_filter_options_use_the_same_account_and_operational_visibility(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        account_loader = getattr(service, "account_filter_options", None)
        admin_loader = getattr(service, "admin_filter_options", None)
        assert account_loader is not None
        assert admin_loader is not None

        account = account_loader(
            actor_user_id=scenario.guardian_user_id,
            occurred_at=NOW,
        )
        admin = admin_loader(
            actor_user_id=scenario.base.admin_user_id,
            occurred_at=NOW,
        )

    for options in (account, admin):
        assert [item.id for item in options.cohorts] == [scenario.base.cohort_id]
        assert [item.id for item in options.projects] == [scenario.base.project_id]
        assert [item.id for item in options.classes] == [scenario.base.class_id]
        assert [item.id for item in options.students] == [scenario.base.actor_person_id]
        assert all(
            item.label
            for group in (options.cohorts, options.projects, options.classes)
            for item in group
        )
        assert all(
            not contains_phone_or_identity(item.label)
            for item in options.students
        )


def test_published_history_filter_keeps_a_published_version_during_revision(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db, db.begin():
        task = db.get(SystemEvaluationTask, scenario.published_task_id)
        assert task is not None
        task.status = "revision_draft"

    filters = cast(
        Any,
        SimpleNamespace(
            status=None,
            queue=None,
            cohort_id=None,
            project_id=None,
            class_id=None,
            student_id=None,
            published_only=True,
        ),
    )
    with Session(scenario.base.engine) as db:
        page = EvaluationQueryService(db).list_account_tasks(
            actor_user_id=scenario.base.actor_user_id,
            filters=filters,
            page=1,
            page_size=20,
            occurred_at=NOW,
        )

    assert [item.id for item in page.items] == [scenario.published_task_id]
    assert page.items[0].status == "revision_draft"
    assert page.items[0].published_result is not None


def test_workspace_projects_safe_snapshot_media_and_authorized_manual_draft(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db, db.begin():
        task = _task(db, scenario.base, status="manual_input")
        draft = SystemEvaluationManualDraft(
            task_id=task.id,
            last_edited_by_user_id=scenario.base.admin_user_id,
            fields={"score": 82, "phone": "13800138000"},
            confidences={"score": 1},
            warnings=[],
            components=[
                {
                    "key": "overall",
                    "score": 82,
                    "weight": 1,
                    "summary": "保持稳定",
                }
            ],
            missing_fields=["grade"],
            score=Decimal("82.00"),
            grade=None,
            advice="继续训练",
        )
        db.add(draft)
        db.flush()
        task_id = task.id

    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        loader = getattr(service, "get_admin_task_workspace", None)
        assert loader is not None
        workspace = loader(
            task_id,
            actor_user_id=scenario.base.admin_user_id,
            occurred_at=NOW,
        )

    assert workspace.task_id == task_id
    assert workspace.profile.required_fields == ("score", "grade")
    assert workspace.profile.media_slots[0].key == "profile"
    assert workspace.draft is not None
    assert workspace.draft.fields == {"score": 82, "phone": None}
    assert "13800138000" not in repr(workspace)


def test_workspace_never_returns_media_or_draft_without_each_action_capability(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        service = EvaluationQueryService(db)
        loader = getattr(service, "get_account_task_workspace", None)
        assert loader is not None
        workspace = loader(
            scenario.published_task_id,
            actor_user_id=scenario.coach_user_id,
            occurred_at=NOW,
        )

    assert workspace.media == ()
    assert workspace.draft is None


def test_projection_redacts_sensitive_result_text(
    evaluation_query_scenario: EvaluationQueryScenario,
) -> None:
    scenario = evaluation_query_scenario
    with Session(scenario.base.engine) as db:
        detail = EvaluationQueryService(db).get_account_task(
            scenario.published_task_id,
            actor_user_id=scenario.base.actor_user_id,
            occurred_at=NOW,
        )
        assert "13800138000" not in repr(detail)
        assert detail.published_result is not None
        assert detail.published_result.advice == "已隐藏敏感内容"
