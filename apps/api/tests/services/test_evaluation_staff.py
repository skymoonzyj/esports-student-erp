from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.errors import (
    StaffAssignmentConflict,
    StaffAssignmentScopeInvalid,
    StaffNotEligible,
    StaffPermissionNotAllowed,
    StaffRoleInactive,
)
from app.evaluations.schemas import (
    StaffAssignmentCreate,
    StaffAssignmentEnd,
    StaffRoleCreate,
    StaffServiceItem,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.evaluations.staff_service import CohortStaffService
from app.identity.models import Person, StaffProfile
from app.rbac.models import Permission, UserRole
from app.rbac.seed import ROLES
from app.teaching.coach_service import CoachService
from app.teaching.errors import CoachAssignmentConflict, CohortArchived
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from app.teaching.schemas import CoachAssignmentCreate
from app.teaching.seed import INITIAL_PROJECTS
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
LIFE_TEACHER_ROLE_ID = UUID("41000000-0000-4000-8000-000000000001")


def _person(db: Session, name: str) -> Person:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"cipher:{marker}",
        identity_digest=(marker * 2)[:64],
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    db.add(person)
    db.flush()
    return person


def _staff_account(
    db: Session,
    name: str,
    *,
    staff_type: str = "other",
    coach_role: bool = False,
) -> tuple[Person, UserAccount]:
    person = _person(db, name)
    marker = uuid4().hex
    db.add(StaffProfile(person_id=person.id, staff_type=staff_type))
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=(marker * 2)[:64],
        email_normalized=f"{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    add_test_primary_login_identifier(db, account)
    if coach_role:
        db.add(UserRole(user_id=account.id, role_id=ROLES["coach"].id))
        db.flush()
    return person, account


@pytest.fixture
def staff_setup(db: Session):
    actor, actor_account = _staff_account(db, "工作人员管理员")
    del actor
    staff, staff_account = _staff_account(
        db,
        "营期工作人员",
        staff_type="coach",
        coach_role=True,
    )
    primary, _ = _staff_account(db, "主教练")
    assistant, _ = _staff_account(db, "助教")
    cohort = Cohort(
        name=f"工作人员营期-{uuid4().hex[:8]}",
        starts_at=NOW,
        ends_at=NOW + timedelta(days=21),
        default_check_in_at=NOW,
        default_check_out_at=NOW + timedelta(days=21),
        status="active",
        created_by_user_id=actor_account.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[0].id,
        name=f"工作人员班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        created_by_user_id=actor_account.id,
    )
    db.add(teaching_class)
    db.flush()
    return {
        "actor_id": actor_account.id,
        "staff": staff,
        "staff_account": staff_account,
        "primary": primary,
        "assistant": assistant,
        "cohort": cohort,
        "class": teaching_class,
        "service": CohortStaffService(db),
    }


@contextmanager
def _committed_staff_setup(postgres_engine: Engine) -> Iterator[dict[str, UUID]]:
    with Session(postgres_engine) as setup, setup.begin():
        _, actor_account = _staff_account(setup, "并发工作人员管理员")
        staff, staff_account = _staff_account(
            setup,
            "并发营期工作人员",
            staff_type="coach",
            coach_role=True,
        )
        cohort = Cohort(
            name=f"并发工作人员营期-{uuid4().hex[:8]}",
            starts_at=NOW,
            ends_at=NOW + timedelta(days=21),
            default_check_in_at=NOW,
            default_check_out_at=NOW + timedelta(days=21),
            status="active",
            created_by_user_id=actor_account.id,
        )
        setup.add(cohort)
        setup.flush()
        teaching_class = TeachingClass(
            cohort_id=cohort.id,
            project_id=INITIAL_PROJECTS[0].id,
            name=f"并发工作人员班-{uuid4().hex[:8]}",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
            created_by_user_id=actor_account.id,
        )
        setup.add(teaching_class)
        setup.flush()
        ids = {
            "actor_id": actor_account.id,
            "actor_person_id": actor_account.person_id,
            "staff_id": staff.id,
            "staff_account_id": staff_account.id,
            "cohort_id": cohort.id,
            "class_id": teaching_class.id,
            "project_id": teaching_class.project_id,
        }
    try:
        yield ids
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            assignment_ids = list(
                cleanup.scalars(
                    select(CohortStaffAssignment.id).where(
                        CohortStaffAssignment.cohort_id == ids["cohort_id"]
                    )
                )
            )
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            try:
                cleanup.execute(
                    delete(AuditEvent).where(
                        AuditEvent.actor_user_id.in_(
                            [ids["actor_id"], ids["staff_account_id"]]
                        )
                    )
                )
            finally:
                cleanup.execute(
                    text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
                )
            if assignment_ids:
                cleanup.execute(
                    delete(CohortStaffAssignmentPermission).where(
                        CohortStaffAssignmentPermission.assignment_id.in_(assignment_ids)
                    )
                )
                cleanup.execute(
                    delete(CohortStaffAssignment).where(
                        CohortStaffAssignment.id.in_(assignment_ids)
                    )
                )
            cleanup.execute(
                delete(EvaluationStaffRole).where(
                    EvaluationStaffRole.created_by_user_id == ids["actor_id"]
                )
            )
            cleanup.execute(
                delete(CoachAssignment).where(
                    CoachAssignment.class_id == ids["class_id"]
                )
            )
            cleanup.execute(
                delete(TeachingClass).where(TeachingClass.id == ids["class_id"])
            )
            cleanup.execute(delete(Cohort).where(Cohort.id == ids["cohort_id"]))
            cleanup.execute(
                delete(UserRole).where(
                    UserRole.user_id.in_([ids["actor_id"], ids["staff_account_id"]])
                )
            )
            cleanup.execute(
                delete(UserAccount).where(
                    UserAccount.id.in_([ids["actor_id"], ids["staff_account_id"]])
                )
            )
            cleanup.execute(
                delete(StaffProfile).where(
                    StaffProfile.person_id.in_(
                        [ids["actor_person_id"], ids["staff_id"]]
                    )
                )
            )
            cleanup.execute(
                delete(Person).where(
                    Person.id.in_([ids["actor_person_id"], ids["staff_id"]])
                )
            )


def _create_assignment(staff_setup, *, valid_from: datetime = NOW, valid_until: datetime | None = None):
    return staff_setup["service"].create_assignment(
        staff_setup["cohort"].id,
        StaffAssignmentCreate(
            role_id=LIFE_TEACHER_ROLE_ID,
            staff_person_id=staff_setup["staff"].id,
            scope_kind="project",
            scope_id=staff_setup["class"].project_id,
            valid_from=valid_from,
            valid_until=valid_until,
            permission_keys=["evaluation_media.upload"],
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )


def test_unified_history_projects_coaches_without_copying_them_to_staff_table(
    db: Session, staff_setup
) -> None:
    teaching_class = staff_setup["class"]
    db.add_all(
        [
            CoachAssignment(
                class_id=teaching_class.id,
                coach_person_id=staff_setup["primary"].id,
                assignment_type="primary",
                valid_from=NOW,
                created_by_user_id=staff_setup["actor_id"],
            ),
            CoachAssignment(
                class_id=teaching_class.id,
                coach_person_id=staff_setup["assistant"].id,
                assignment_type="assistant",
                valid_from=NOW,
                created_by_user_id=staff_setup["actor_id"],
            ),
        ]
    )
    db.flush()
    _create_assignment(staff_setup)

    history = staff_setup["service"].list_service_history(
        staff_setup["cohort"].id, occurred_at=NOW
    )

    assert {(item.source, item.role_name) for item in history} == {
        ("coach_assignment", "主教练"),
        ("coach_assignment", "助教"),
        ("cohort_staff_assignment", "生活老师"),
    }
    assert all(
        item.manage_allowed is False
        for item in history
        if item.source == "coach_assignment"
    )
    assert db.scalar(select(func.count(CohortStaffAssignment.id))) == 1


def test_class_scoped_service_history_query_count_is_constant(
    db: Session,
    staff_setup,
) -> None:
    cohort = staff_setup["cohort"]
    actor_id = staff_setup["actor_id"]
    classes = [
        TeachingClass(
            cohort_id=cohort.id,
            project_id=INITIAL_PROJECTS[0].id,
            name=f"查询计数班-{index}-{uuid4().hex[:6]}",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
            created_by_user_id=actor_id,
        )
        for index in range(25)
    ]
    db.add_all(classes)
    db.flush()
    assignments = [
        CohortStaffAssignment(
            cohort_id=cohort.id,
            role_id=LIFE_TEACHER_ROLE_ID,
            staff_person_id=staff_setup["staff"].id,
            class_id=teaching_class.id,
            role_name_snapshot="生活老师",
            role_category_snapshot="life_service",
            valid_from=NOW,
            created_by_user_id=actor_id,
        )
        for teaching_class in classes
    ]
    db.add(assignments[0])
    db.flush()
    bind = db.get_bind()

    def count_history_queries() -> tuple[int, list[StaffServiceItem]]:
        statements: list[str] = []

        def record_statement(*args: object) -> None:
            statement = args[2]
            if isinstance(statement, str) and statement.lstrip().lower().startswith("select"):
                statements.append(statement)

        event.listen(bind, "before_cursor_execute", record_statement)
        try:
            history = staff_setup["service"].list_service_history(
                cohort.id,
                occurred_at=NOW,
            )
        finally:
            event.remove(bind, "before_cursor_execute", record_statement)
        return len(statements), history

    small_query_count, small_history = count_history_queries()
    db.add_all(assignments[1:])
    db.flush()
    large_query_count, large_history = count_history_queries()

    assert len(small_history) == 1
    assert len(large_history) == 25
    assert {item.project_name for item in large_history} == {INITIAL_PROJECTS[0].name}
    assert large_query_count <= small_query_count + 1


def test_assignment_overlap_conflicts_but_adjacent_half_open_intervals_succeed(
    staff_setup,
) -> None:
    first = _create_assignment(staff_setup, valid_until=NOW + timedelta(hours=1))

    with pytest.raises(StaffAssignmentConflict) as error:
        _create_assignment(
            staff_setup,
            valid_from=NOW + timedelta(minutes=30),
            valid_until=NOW + timedelta(hours=2),
        )
    assert error.value.code == "staff_assignment_conflict"

    adjacent = _create_assignment(
        staff_setup,
        valid_from=first.valid_until,
        valid_until=NOW + timedelta(hours=2),
    )
    assert adjacent.valid_from == first.valid_until


def test_concurrent_overlapping_assignments_commit_one_and_leave_sessions_usable(
    postgres_engine: Engine,
) -> None:
    with _committed_staff_setup(postgres_engine) as ids:
        barrier = threading.Barrier(2)
        successes: list[UUID] = []
        failures: list[StaffAssignmentConflict] = []
        usable_counts: list[int] = []

        def create_assignment(sequence: int) -> None:
            with Session(postgres_engine) as worker:
                barrier.wait(timeout=5)
                try:
                    assignment = CohortStaffService(worker).create_assignment(
                        ids["cohort_id"],
                        StaffAssignmentCreate(
                            role_id=LIFE_TEACHER_ROLE_ID,
                            staff_person_id=ids["staff_id"],
                            scope_kind="project",
                            scope_id=ids["project_id"],
                            valid_from=NOW,
                            valid_until=NOW + timedelta(hours=1),
                        ),
                        actor_user_id=ids["actor_id"],
                        request_id=f"concurrent-staff-{sequence}",
                    )
                    successes.append(assignment.id)
                except StaffAssignmentConflict as exc:
                    failures.append(exc)
                    usable_counts.append(
                        worker.scalar(
                            select(func.count(CohortStaffAssignment.id)).where(
                                CohortStaffAssignment.cohort_id == ids["cohort_id"]
                            )
                        )
                        or 0
                    )

        workers = [
            threading.Thread(target=create_assignment, args=(sequence,))
            for sequence in (1, 2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)

        assert all(not worker.is_alive() for worker in workers)
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == "staff_assignment_conflict"
        assert usable_counts == [1]
        with Session(postgres_engine) as verification:
            assert verification.scalar(
                select(func.count(CohortStaffAssignment.id)).where(
                    CohortStaffAssignment.cohort_id == ids["cohort_id"]
                )
            ) == 1


def test_end_preserves_snapshots_and_removes_manage_access_at_half_open_boundary(
    db: Session,
    staff_setup,
) -> None:
    cohort_id = staff_setup["cohort"].id
    project_id = staff_setup["class"].project_id
    staff_account_id = staff_setup["staff_account"].id
    assignment = _create_assignment(staff_setup)
    ended_at = NOW + timedelta(hours=1)
    ended = staff_setup["service"].end_assignment(
        cohort_id,
        assignment.id,
        StaffAssignmentEnd(reason="服务结束"),
        actor_user_id=staff_setup["actor_id"],
        occurred_at=ended_at,
        request_id=f"staff-{uuid4().hex}",
    )

    resource = EvaluationResource(cohort_id=cohort_id, project_id=project_id)
    access = EvaluationAccessService(db)
    assert access.decide(
        account_id=staff_account_id,
        permission="evaluation_media.upload",
        resource=resource,
        occurred_at=ended_at - timedelta(microseconds=1),
    ).allowed
    assert not access.decide(
        account_id=staff_account_id,
        permission="evaluation_media.upload",
        resource=resource,
        occurred_at=ended_at,
    ).allowed

    db.expire_all()
    stored = db.get(CohortStaffAssignment, assignment.id)
    assert stored is not None
    assert (stored.ended_by_user_id, stored.ended_at, stored.end_reason) == (
        staff_setup["actor_id"],
        ended_at,
        "服务结束",
    )
    history = staff_setup["service"].list_service_history(
        cohort_id,
        occurred_at=ended.ended_at,
    )
    item = next(item for item in history if item.id == assignment.id)
    assert (item.role_name, item.role_category, item.scope_kind) == (
        "生活老师",
        "life_service",
        "project",
    )
    assert item.project_id == project_id
    assert item.class_id is None
    assert item.end_reason == "服务结束"
    assert item.manage_allowed is False


def test_global_role_lifecycle_is_independent_from_archived_cohort_assignments(
    db: Session,
    staff_setup,
) -> None:
    assignment = _create_assignment(staff_setup)
    before = (
        assignment.role_name_snapshot,
        assignment.role_category_snapshot,
        assignment.project_id,
        assignment.class_id,
        assignment.valid_from,
        assignment.valid_until,
        assignment.ended_at,
    )
    staff_setup["cohort"].status = "archived"
    db.flush()

    role = staff_setup["service"].create_role(
        StaffRoleCreate(
            key=f"operations_{uuid4().hex[:8]}",
            name="营地运营",
            category="operations",
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    disabled = staff_setup["service"].set_role_active(
        role.id,
        is_active=False,
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    assert disabled.is_active is False

    with pytest.raises(CohortArchived):
        _create_assignment(
            staff_setup,
            valid_from=NOW + timedelta(hours=1),
            valid_until=NOW + timedelta(hours=2),
        )
    db.expire_all()
    stored = db.get(CohortStaffAssignment, assignment.id)
    assert stored is not None
    assert (
        stored.role_name_snapshot,
        stored.role_category_snapshot,
        stored.project_id,
        stored.class_id,
        stored.valid_from,
        stored.valid_until,
        stored.ended_at,
    ) == before
    assert db.scalar(
        select(func.count(CohortStaffAssignmentPermission.assignment_id)).where(
            CohortStaffAssignmentPermission.assignment_id == assignment.id
        )
    ) == 1
    history = staff_setup["service"].list_service_history(
        staff_setup["cohort"].id, occurred_at=NOW
    )
    assert [item.id for item in history] == [assignment.id]


def test_class_assignment_scope_must_belong_to_cohort(staff_setup) -> None:
    service = staff_setup["service"]
    input = StaffAssignmentCreate(
        role_id=LIFE_TEACHER_ROLE_ID,
        staff_person_id=staff_setup["staff"].id,
        scope_kind="class",
        scope_id=uuid4(),
        valid_from=NOW,
    )

    with pytest.raises(StaffAssignmentScopeInvalid):
        service.create_assignment(
            staff_setup["cohort"].id,
            input,
            actor_user_id=staff_setup["actor_id"],
            request_id=f"staff-{uuid4().hex}",
        )


def test_project_assignment_scope_requires_a_cohort_class(staff_setup) -> None:
    with pytest.raises(StaffAssignmentScopeInvalid):
        staff_setup["service"].create_assignment(
            staff_setup["cohort"].id,
            StaffAssignmentCreate(
                role_id=LIFE_TEACHER_ROLE_ID,
                staff_person_id=staff_setup["staff"].id,
                scope_kind="project",
                scope_id=INITIAL_PROJECTS[1].id,
                valid_from=NOW,
            ),
            actor_user_id=staff_setup["actor_id"],
            request_id=f"staff-{uuid4().hex}",
        )


def test_valid_project_and_class_scopes_persist_mutually_exclusive_columns(
    staff_setup,
) -> None:
    project_assignment = _create_assignment(
        staff_setup,
        valid_until=NOW + timedelta(hours=1),
    )
    class_assignment = staff_setup["service"].create_assignment(
        staff_setup["cohort"].id,
        StaffAssignmentCreate(
            role_id=LIFE_TEACHER_ROLE_ID,
            staff_person_id=staff_setup["staff"].id,
            scope_kind="class",
            scope_id=staff_setup["class"].id,
            valid_from=NOW,
            valid_until=NOW + timedelta(hours=1),
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )

    assert (project_assignment.project_id, project_assignment.class_id) == (
        staff_setup["class"].project_id,
        None,
    )
    assert (class_assignment.project_id, class_assignment.class_id) == (
        None,
        staff_setup["class"].id,
    )


@pytest.mark.parametrize(
    "permission_key",
    [
        "teaching.read",
        "evaluation_jobs.match",
        "evaluation_adapters.certify",
    ],
)
def test_assignment_permissions_reject_non_assignable_capabilities(
    staff_setup,
    permission_key: str,
) -> None:
    with pytest.raises(StaffPermissionNotAllowed):
        staff_setup["service"].create_assignment(
            staff_setup["cohort"].id,
            StaffAssignmentCreate(
                role_id=LIFE_TEACHER_ROLE_ID,
                staff_person_id=staff_setup["staff"].id,
                scope_kind="project",
                scope_id=staff_setup["class"].project_id,
                valid_from=NOW,
                permission_keys=[permission_key],
            ),
            actor_user_id=staff_setup["actor_id"],
            request_id=f"staff-{uuid4().hex}",
        )


def test_allowed_permissions_attach_to_assignment_and_every_mutation_is_audited(
    db: Session,
    staff_setup,
) -> None:
    request_ids = {
        "evaluation_staff_role.create": f"role-create-{uuid4().hex}",
        "cohort_staff_assignment.create": f"assignment-create-{uuid4().hex}",
        "cohort_staff_assignment.replace_permissions": f"permission-replace-{uuid4().hex}",
        "cohort_staff_assignment.end": f"assignment-end-{uuid4().hex}",
        "evaluation_staff_role.set_active": f"role-disable-{uuid4().hex}",
    }
    role = staff_setup["service"].create_role(
        StaffRoleCreate(
            key=f"life_support_{uuid4().hex[:8]}",
            name="生活支持",
            category="life_service",
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=request_ids["evaluation_staff_role.create"],
    )
    assignment = staff_setup["service"].create_assignment(
        staff_setup["cohort"].id,
        StaffAssignmentCreate(
            role_id=role.id,
            staff_person_id=staff_setup["staff"].id,
            scope_kind="project",
            scope_id=staff_setup["class"].project_id,
            valid_from=NOW,
            permission_keys=[
                "evaluation_media.upload",
                "evaluation_media.original.read",
                "evaluation_jobs.manual_input",
                "evaluation_jobs.review",
                "student_reports.write",
                "student_reports.review",
            ],
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=request_ids["cohort_staff_assignment.create"],
    )
    attached = set(
        db.scalars(
            select(Permission.key)
            .join(
                CohortStaffAssignmentPermission,
                CohortStaffAssignmentPermission.permission_id == Permission.id,
            )
            .where(CohortStaffAssignmentPermission.assignment_id == assignment.id)
        )
    )
    assert attached == {
        "evaluation_media.upload",
        "evaluation_media.original.read",
        "evaluation_jobs.manual_input",
        "evaluation_jobs.review",
        "student_reports.write",
        "student_reports.review",
    }

    staff_setup["service"].replace_assignment_permissions(
        staff_setup["cohort"].id,
        assignment.id,
        ["evaluation_media.original.read", "evaluation_jobs.review"],
        actor_user_id=staff_setup["actor_id"],
        request_id=request_ids["cohort_staff_assignment.replace_permissions"],
    )
    assert set(
        db.scalars(
            select(Permission.key)
            .join(
                CohortStaffAssignmentPermission,
                CohortStaffAssignmentPermission.permission_id == Permission.id,
            )
            .where(CohortStaffAssignmentPermission.assignment_id == assignment.id)
        )
    ) == {"evaluation_media.original.read", "evaluation_jobs.review"}

    staff_setup["service"].end_assignment(
        staff_setup["cohort"].id,
        assignment.id,
        StaffAssignmentEnd(reason="审计结束"),
        actor_user_id=staff_setup["actor_id"],
        occurred_at=NOW + timedelta(hours=1),
        request_id=request_ids["cohort_staff_assignment.end"],
    )
    staff_setup["service"].set_role_active(
        role.id,
        is_active=False,
        actor_user_id=staff_setup["actor_id"],
        request_id=request_ids["evaluation_staff_role.set_active"],
    )

    history_item = next(
        item
        for item in staff_setup["service"].list_service_history(
            staff_setup["cohort"].id,
            occurred_at=NOW + timedelta(hours=1),
        )
        if item.id == assignment.id
    )
    assert (history_item.role_name, history_item.role_category) == (
        "生活支持",
        "life_service",
    )
    audits = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.request_id.in_(request_ids.values()))
        )
    )
    assert {audit.action for audit in audits} == set(request_ids)
    for audit in audits:
        assert audit.actor_user_id == staff_setup["actor_id"]
        assert audit.request_id == request_ids[audit.action]
        assert audit.outcome == "allowed"
        assert set(audit.event_metadata) <= {
            "staff_role_id",
            "staff_assignment_id",
            "role_key",
            "cohort_id",
            "scope_kind",
            "scope_id",
        }


@pytest.mark.parametrize("reason", ["deleted", "missing_profile", "disabled_account"])
def test_new_assignment_requires_current_eligible_staff(
    db: Session,
    staff_setup,
    reason: str,
) -> None:
    if reason == "deleted":
        staff_setup["staff"].deleted_at = NOW
    elif reason == "missing_profile":
        db.delete(db.get(StaffProfile, staff_setup["staff"].id))
    else:
        staff_setup["staff_account"].status = "disabled"
    db.flush()

    with pytest.raises(StaffNotEligible):
        _create_assignment(staff_setup)


def test_inactive_role_is_rejected_and_naive_datetimes_are_never_normalized(
    staff_setup,
) -> None:
    role = staff_setup["service"].create_role(
        StaffRoleCreate(
            key=f"inactive_{uuid4().hex[:8]}",
            name="已停用角色",
            category="operations",
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    staff_setup["service"].set_role_active(
        role.id,
        is_active=False,
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    with pytest.raises(StaffRoleInactive):
        staff_setup["service"].create_assignment(
            staff_setup["cohort"].id,
            StaffAssignmentCreate(
                role_id=role.id,
                staff_person_id=staff_setup["staff"].id,
                scope_kind="project",
                scope_id=staff_setup["class"].project_id,
                valid_from=NOW,
            ),
            actor_user_id=staff_setup["actor_id"],
            request_id=f"staff-{uuid4().hex}",
        )
    with pytest.raises(ValueError, match="offset"):
        _create_assignment(
            staff_setup,
            valid_from=NOW.replace(tzinfo=None),
        )
    with pytest.raises(ValueError, match="offset"):
        _create_assignment(
            staff_setup,
            valid_until=(NOW + timedelta(hours=1)).replace(tzinfo=None),
        )


def test_assignment_waits_for_concurrent_role_deactivation_and_then_rejects_it(
    postgres_engine: Engine,
) -> None:
    with _committed_staff_setup(postgres_engine) as ids:
        with Session(postgres_engine) as role_session:
            role = CohortStaffService(role_session).create_role(
                StaffRoleCreate(
                    key=f"role_race_{uuid4().hex[:8]}",
                    name="并发停用角色",
                    category="operations",
                ),
                actor_user_id=ids["actor_id"],
                request_id=f"role-race-create-{uuid4().hex}",
            )
            role_id = role.id

        worker_started = threading.Event()
        worker_done = threading.Event()
        backend_pid: list[int] = []
        failures: list[BaseException] = []
        successes: list[UUID] = []

        def create_assignment() -> None:
            try:
                with Session(postgres_engine) as worker:
                    pid = worker.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    backend_pid.append(pid)
                    worker_started.set()
                    assignment = CohortStaffService(worker).create_assignment(
                        ids["cohort_id"],
                        StaffAssignmentCreate(
                            role_id=role_id,
                            staff_person_id=ids["staff_id"],
                            scope_kind="project",
                            scope_id=ids["project_id"],
                            valid_from=NOW,
                        ),
                        actor_user_id=ids["actor_id"],
                        request_id=f"role-race-assignment-{uuid4().hex}",
                    )
                    successes.append(assignment.id)
            except Exception as exc:  # noqa: BLE001 - asserted after thread joins
                failures.append(exc)
            finally:
                worker_done.set()

        worker = threading.Thread(target=create_assignment)
        with Session(postgres_engine) as deactivator, deactivator.begin():
            locked_role = deactivator.scalar(
                select(EvaluationStaffRole)
                .where(EvaluationStaffRole.id == role_id)
                .with_for_update()
            )
            assert locked_role is not None
            locked_role.is_active = False
            deactivator.flush()
            worker.start()
            assert worker_started.wait(timeout=2)
            deadline = time.monotonic() + 5
            waiting_on_lock = False
            while time.monotonic() < deadline and not worker_done.is_set():
                with postgres_engine.connect() as observer:
                    wait_event_type = observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :backend_pid"
                        ),
                        {"backend_pid": backend_pid[0]},
                    )
                if wait_event_type == "Lock":
                    waiting_on_lock = True
                    break
                time.sleep(0.01)
            assert waiting_on_lock
            assert not worker_done.is_set()

        worker.join(timeout=10)
        assert not worker.is_alive()
        assert successes == []
        assert len(failures) == 1
        assert isinstance(failures[0], StaffRoleInactive)


def test_teaching_role_cannot_duplicate_a_coach_service_for_same_class(
    db: Session, staff_setup
) -> None:
    db.add(
        CoachAssignment(
            class_id=staff_setup["class"].id,
            coach_person_id=staff_setup["staff"].id,
            assignment_type="assistant",
            valid_from=NOW,
            created_by_user_id=staff_setup["actor_id"],
        )
    )
    db.flush()
    role = staff_setup["service"].create_role(
        StaffRoleCreate(
            key=f"teaching_{uuid4().hex[:8]}",
            name="教学支持",
            category="teaching",
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )

    with pytest.raises(StaffAssignmentConflict):
        staff_setup["service"].create_assignment(
            staff_setup["cohort"].id,
            StaffAssignmentCreate(
                role_id=role.id,
                staff_person_id=staff_setup["staff"].id,
                scope_kind="class",
                scope_id=staff_setup["class"].id,
                valid_from=NOW,
            ),
            actor_user_id=staff_setup["actor_id"],
            request_id=f"staff-{uuid4().hex}",
        )


def test_coach_assignment_cannot_duplicate_generic_teaching_service(
    db: Session,
    staff_setup,
) -> None:
    role = staff_setup["service"].create_role(
        StaffRoleCreate(
            key=f"reverse_teaching_{uuid4().hex[:8]}",
            name="反向教学支持",
            category="teaching",
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    generic = staff_setup["service"].create_assignment(
        staff_setup["cohort"].id,
        StaffAssignmentCreate(
            role_id=role.id,
            staff_person_id=staff_setup["staff"].id,
            scope_kind="class",
            scope_id=staff_setup["class"].id,
            valid_from=NOW,
            valid_until=NOW + timedelta(hours=2),
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )

    with pytest.raises(CoachAssignmentConflict):
        CoachService(db).create_assignment(
            staff_setup["class"].id,
            CoachAssignmentCreate(
                coach_person_id=staff_setup["staff"].id,
                assignment_type="assistant",
                valid_from=NOW + timedelta(hours=1),
                valid_until=NOW + timedelta(hours=3),
            ),
            actor_user_id=staff_setup["actor_id"],
        )

    assert db.get(CohortStaffAssignment, generic.id) is not None
    assert db.scalar(
        select(func.count(CoachAssignment.id)).where(
            CoachAssignment.class_id == staff_setup["class"].id,
            CoachAssignment.coach_person_id == staff_setup["staff"].id,
        )
    ) == 0


@pytest.mark.parametrize("boundary_kind", ["valid_until", "ended_at"])
def test_coach_assignment_allows_adjacent_or_ended_generic_teaching_service(
    db: Session,
    staff_setup,
    boundary_kind: str,
) -> None:
    role = staff_setup["service"].create_role(
        StaffRoleCreate(
            key=f"boundary_teaching_{uuid4().hex[:8]}",
            name="边界教学支持",
            category="teaching",
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    boundary = NOW + timedelta(hours=1)
    generic = staff_setup["service"].create_assignment(
        staff_setup["cohort"].id,
        StaffAssignmentCreate(
            role_id=role.id,
            staff_person_id=staff_setup["staff"].id,
            scope_kind="class",
            scope_id=staff_setup["class"].id,
            valid_from=NOW,
            valid_until=boundary if boundary_kind == "valid_until" else None,
        ),
        actor_user_id=staff_setup["actor_id"],
        request_id=f"staff-{uuid4().hex}",
    )
    if boundary_kind == "ended_at":
        staff_setup["service"].end_assignment(
            staff_setup["cohort"].id,
            generic.id,
            StaffAssignmentEnd(reason="边界结束教学支持"),
            actor_user_id=staff_setup["actor_id"],
            occurred_at=boundary,
            request_id=f"staff-{uuid4().hex}",
        )

    coach = CoachService(db).create_assignment(
        staff_setup["class"].id,
        CoachAssignmentCreate(
            coach_person_id=staff_setup["staff"].id,
            assignment_type="assistant",
            valid_from=boundary,
            valid_until=boundary + timedelta(hours=1),
        ),
        actor_user_id=staff_setup["actor_id"],
    )

    assert coach.valid_from == boundary


def test_concurrent_generic_and_coach_teaching_writes_commit_only_one(
    postgres_engine: Engine,
) -> None:
    with _committed_staff_setup(postgres_engine) as ids:
        with Session(postgres_engine) as role_session:
            role = CohortStaffService(role_session).create_role(
                StaffRoleCreate(
                    key=f"cross_table_{uuid4().hex[:8]}",
                    name="并发跨表教学支持",
                    category="teaching",
                ),
                actor_user_id=ids["actor_id"],
                request_id=f"cross-table-role-{uuid4().hex}",
            )
            role_id = role.id

        generic_thread_id: list[int] = []
        generic_locked_cohort = threading.Event()
        release_generic = threading.Event()
        coach_started = threading.Event()
        coach_backend_pid: list[int] = []
        successes: list[str] = []
        failures: list[BaseException] = []
        usable_sessions: list[str] = []

        def pause_generic_after_cohort_lock(
            _connection,
            _cursor,
            statement: str,
            _parameters,
            _context,
            _executemany: bool,
        ) -> None:
            if (
                generic_thread_id
                and threading.get_ident() == generic_thread_id[0]
                and "FROM cohorts" in statement
                and "FOR UPDATE" in statement
            ):
                generic_locked_cohort.set()
                assert release_generic.wait(timeout=10)

        event.listen(
            postgres_engine,
            "after_cursor_execute",
            pause_generic_after_cohort_lock,
        )

        def create_generic() -> None:
            generic_thread_id.append(threading.get_ident())
            with Session(postgres_engine) as worker:
                try:
                    CohortStaffService(worker).create_assignment(
                        ids["cohort_id"],
                        StaffAssignmentCreate(
                            role_id=role_id,
                            staff_person_id=ids["staff_id"],
                            scope_kind="class",
                            scope_id=ids["class_id"],
                            valid_from=NOW,
                            valid_until=NOW + timedelta(hours=1),
                        ),
                        actor_user_id=ids["actor_id"],
                        request_id=f"cross-table-generic-{uuid4().hex}",
                    )
                    successes.append("generic")
                except BaseException as exc:  # noqa: BLE001 - asserted after joins
                    failures.append(exc)
                finally:
                    assert worker.scalar(select(func.count(Cohort.id))) is not None
                    usable_sessions.append("generic")

        def create_coach() -> None:
            with Session(postgres_engine) as worker:
                try:
                    pid = worker.scalar(text("SELECT pg_backend_pid()"))
                    assert isinstance(pid, int)
                    coach_backend_pid.append(pid)
                    coach_started.set()
                    CoachService(worker).create_assignment(
                        ids["class_id"],
                        CoachAssignmentCreate(
                            coach_person_id=ids["staff_id"],
                            assignment_type="assistant",
                            valid_from=NOW,
                            valid_until=NOW + timedelta(hours=1),
                        ),
                        actor_user_id=ids["actor_id"],
                    )
                    successes.append("coach")
                except BaseException as exc:  # noqa: BLE001 - asserted after joins
                    failures.append(exc)
                finally:
                    assert worker.scalar(select(func.count(Cohort.id))) is not None
                    usable_sessions.append("coach")

        generic_worker = threading.Thread(target=create_generic)
        coach_worker = threading.Thread(target=create_coach)
        try:
            generic_worker.start()
            assert generic_locked_cohort.wait(timeout=5)
            coach_worker.start()
            assert coach_started.wait(timeout=5)
            deadline = time.monotonic() + 5
            coach_waiting = False
            while time.monotonic() < deadline:
                with postgres_engine.connect() as observer:
                    wait_event_type = observer.scalar(
                        text(
                            "SELECT wait_event_type FROM pg_stat_activity "
                            "WHERE pid = :backend_pid"
                        ),
                        {"backend_pid": coach_backend_pid[0]},
                    )
                if wait_event_type == "Lock":
                    coach_waiting = True
                    break
                time.sleep(0.01)
            assert coach_waiting
        finally:
            release_generic.set()
            generic_worker.join(timeout=10)
            coach_worker.join(timeout=10)
            event.remove(
                postgres_engine,
                "after_cursor_execute",
                pause_generic_after_cohort_lock,
            )

        assert not generic_worker.is_alive()
        assert not coach_worker.is_alive()
        assert successes == ["generic"]
        assert len(failures) == 1
        assert isinstance(failures[0], CoachAssignmentConflict)
        assert sorted(usable_sessions) == ["coach", "generic"]
        with Session(postgres_engine) as verification:
            generic_count = verification.scalar(
                select(func.count(CohortStaffAssignment.id)).where(
                    CohortStaffAssignment.cohort_id == ids["cohort_id"]
                )
            )
            coach_count = verification.scalar(
                select(func.count(CoachAssignment.id)).where(
                    CoachAssignment.class_id == ids["class_id"]
                )
            )
            assert (generic_count or 0) + (coach_count or 0) == 1
