from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment, EnrollmentEvent
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.teaching.access import TeachingVisibility
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.cohort_service import CohortService
from app.teaching.errors import (
    CohortArchived,
    CohortScheduleLocked,
    DuplicateClass,
    EliteTeamRequired,
    InactiveProfessionalTeam,
    InactiveProject,
    InvalidCohortTransitionError,
    PrimaryCoachRequired,
    ProjectTeamMismatch,
    StandardClassTeamForbidden,
    TeachingServiceError,
)
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    CohortStatusEvent,
    TeachingClass,
)
from app.teaching.schemas import (
    CohortCreate,
    CohortUpdate,
    ProfessionalTeamCreate,
    ProfessionalTeamUpdate,
    ProjectCreate,
    ProjectUpdate,
    TeachingClassCreate,
    TeachingClassUpdate,
)

PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)


@pytest.fixture
def actor_user_id(db: Session) -> UUID:
    person, account = _create_account(db, full_name="营期服务测试操作人")
    del person
    return account.id


def _create_account(
    db: Session,
    *,
    full_name: str,
    staff_type: str | None = None,
    active: bool = True,
    coach_role: bool = False,
) -> tuple[Person, UserAccount]:
    marker = uuid4().hex
    person = Person(
        full_name=full_name,
        identity_ciphertext="cohort-test-ciphertext",
        identity_digest=f"cohort-identity-{marker}",
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(1988, 1, 2),
    )
    db.add(person)
    db.flush()
    if staff_type is not None:
        db.add(StaffProfile(person_id=person.id, staff_type=staff_type))
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"cohort-phone-{marker}",
        email_normalized=f"cohort-{marker}@example.test",
        password_hash="cohort-test-password-hash",
        status="active" if active else "disabled",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    if coach_role:
        db.add(UserRole(user_id=account.id, role_id=ROLES["coach"].id))
    db.flush()
    return person, account


def _cohort_input(
    name: str,
    *,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    auto_start_enabled: bool = False,
    auto_complete_enabled: bool = False,
) -> CohortCreate:
    start = starts_at or NOW + timedelta(days=10)
    end = ends_at or start + timedelta(days=20)
    return CohortCreate(
        name=name,
        starts_at=start,
        ends_at=end,
        default_check_in_at=start + timedelta(hours=2),
        default_check_out_at=end - timedelta(hours=2),
        auto_start_enabled=auto_start_enabled,
        auto_complete_enabled=auto_complete_enabled,
    )


def _create_cohort(
    service: CohortService,
    actor_user_id: UUID,
    name: str,
    **kwargs,
) -> Cohort:
    return service.create_cohort(
        _cohort_input(name, **kwargs),
        actor_user_id=actor_user_id,
    )


def _add_class(
    db: Session,
    *,
    cohort_id: UUID,
    actor_user_id: UUID,
    name: str,
    is_active: bool = True,
) -> TeachingClass:
    row = TeachingClass(
        cohort_id=cohort_id,
        project_id=PROJECT_ID,
        name=name,
        class_type="standard",
        professional_team_id=None,
        capacity=20,
        default_tuition_cents=100_000,
        is_active=is_active,
        created_by_user_id=actor_user_id,
    )
    db.add(row)
    db.flush()
    return row


def _add_active_enrollment(
    db: Session,
    *,
    cohort: Cohort,
    teaching_class: TeachingClass,
    student_person_id: UUID,
    actor_user_id: UUID,
) -> Enrollment:
    admission_case = AdmissionCase(
        student_person_id=student_person_id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=actor_user_id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student_person_id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status="active",
        confirmed_at=NOW,
        created_by_user_id=actor_user_id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    db.flush()
    return enrollment


def _assign_primary(
    db: Session,
    *,
    class_id: UUID,
    coach_person_id: UUID,
    actor_user_id: UUID,
    valid_from: datetime = NOW - timedelta(days=1),
) -> CoachAssignment:
    assignment = CoachAssignment(
        class_id=class_id,
        coach_person_id=coach_person_id,
        assignment_type="primary",
        valid_from=valid_from,
        valid_until=None,
        created_by_user_id=actor_user_id,
    )
    db.add(assignment)
    db.flush()
    return assignment


@pytest.mark.parametrize(
    "field",
    ["starts_at", "ends_at", "default_check_in_at", "default_check_out_at"],
)
def test_cohort_inputs_reject_naive_datetimes(field: str) -> None:
    values = _cohort_input("无时区营期").model_dump()
    values[field] = values[field].replace(tzinfo=None)

    with pytest.raises(ValidationError):
        CohortCreate(**values)


def test_create_cohort_preserves_all_timestamps_exactly(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    input = _cohort_input("精确时间营期")

    cohort = service.create_cohort(input, actor_user_id=actor_user_id)

    assert cohort.starts_at == input.starts_at
    assert cohort.ends_at == input.ends_at
    assert cohort.default_check_in_at == input.default_check_in_at
    assert cohort.default_check_out_at == input.default_check_out_at
    assert cohort.status == "draft"


def test_cohort_input_rejects_invalid_schedule_and_default_stay() -> None:
    start = NOW + timedelta(days=1)
    with pytest.raises(ValidationError):
        _cohort_input("结束早于开始", starts_at=start, ends_at=start)

    values = _cohort_input("退房早于入住").model_dump()
    values["default_check_out_at"] = values["default_check_in_at"] - timedelta(minutes=1)
    with pytest.raises(ValidationError):
        CohortCreate(**values)


def test_cohort_lists_use_exact_filters_half_open_dates_and_stable_pagination(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    earliest = _create_cohort(
        service,
        actor_user_id,
        "筛选营期",
        starts_at=NOW + timedelta(days=1),
    )
    middle = _create_cohort(
        service,
        actor_user_id,
        "筛选营期扩展",
        starts_at=NOW + timedelta(days=2),
    )
    latest = _create_cohort(
        service,
        actor_user_id,
        "另一个营期",
        starts_at=NOW + timedelta(days=3),
    )
    service.transition(
        middle.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="cohort-filter-open",
    )

    exact, exact_total = service.list_cohorts(
        name="  筛选营期  ",
        status="draft",
        date_from=NOW + timedelta(days=1),
        date_to=NOW + timedelta(days=2),
        visibility=TeachingVisibility.global_scope(),
        page=1,
        page_size=1,
    )
    page_one, total = service.list_cohorts(
        name=None,
        status=None,
        date_from=None,
        date_to=None,
        visibility=TeachingVisibility.global_scope(),
        page=1,
        page_size=2,
    )
    page_two, second_total = service.list_cohorts(
        name=None,
        status=None,
        date_from=None,
        date_to=None,
        visibility=TeachingVisibility.global_scope(),
        page=2,
        page_size=2,
    )

    assert exact_total == 1 and [item.id for item in exact] == [earliest.id]
    assert total == second_total == 3
    assert [item.id for item in page_one] == [latest.id, middle.id]
    assert [item.id for item in page_two] == [earliest.id]


def test_opening_zero_class_cohort_succeeds_and_writes_manual_history(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "零班级开营")

    opened = service.transition(
        cohort.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="zero-class-open",
    )

    assert opened.status == "open"
    event = db.scalar(
        select(CohortStatusEvent).where(CohortStatusEvent.cohort_id == cohort.id)
    )
    assert event is not None
    assert (event.old_status, event.new_status, event.trigger) == (
        "draft",
        "open",
        "manual",
    )
    assert event.actor_user_id == actor_user_id
    assert event.request_id == "zero-class-open"


def test_illegal_transition_rolls_back_without_event_or_audit(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "非法流转营期")

    with pytest.raises(InvalidCohortTransitionError) as error:
        service.transition(
            cohort.id,
            target="active",
            trigger="manual",
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="illegal-transition",
        )

    assert error.value.code == "invalid_cohort_transition"
    db.expire_all()
    stored = db.get(Cohort, cohort.id)
    assert stored is not None and stored.status == "draft"
    assert db.scalar(
        select(func.count(CohortStatusEvent.id)).where(
            CohortStatusEvent.cohort_id == cohort.id
        )
    ) == 0
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.request_id == "illegal-transition"
        )
    ) == 0


def test_manual_completion_completes_every_active_enrollment_with_sorted_locks(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "手工结营报名联动")
    cohort.status = "active"
    first_class = _add_class(
        db,
        cohort_id=cohort.id,
        actor_user_id=actor_user_id,
        name="手工结营一班",
    )
    second_class = _add_class(
        db,
        cohort_id=cohort.id,
        actor_user_id=actor_user_id,
        name="手工结营二班",
    )
    actor = db.get(UserAccount, actor_user_id)
    assert actor is not None
    second_student, _ = _create_account(db, full_name="手工结营第二名学员")
    enrollments = [
        _add_active_enrollment(
            db,
            cohort=cohort,
            teaching_class=first_class,
            student_person_id=actor.person_id,
            actor_user_id=actor_user_id,
        ),
        _add_active_enrollment(
            db,
            cohort=cohort,
            teaching_class=second_class,
            student_person_id=second_student.id,
            actor_user_id=actor_user_id,
        ),
    ]
    db.commit()

    statements: list[str] = []
    bind = db.get_bind()

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select") and " for update" in normalized:
            statements.append(normalized)

    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        completed = service.transition(
            cohort.id,
            target="completed",
            trigger="manual",
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="manual-complete-enrollments",
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    assert completed.status == "completed"
    db.expire_all()
    stored = [db.get(Enrollment, enrollment.id) for enrollment in enrollments]
    assert all(enrollment is not None for enrollment in stored)
    assert [enrollment.status for enrollment in stored if enrollment is not None] == [
        "completed",
        "completed",
    ]
    assert [
        enrollment.completed_at for enrollment in stored if enrollment is not None
    ] == [NOW, NOW]
    completion_events = list(
        db.scalars(
            select(EnrollmentEvent)
            .where(
                EnrollmentEvent.enrollment_id.in_(
                    [enrollment.id for enrollment in enrollments]
                ),
                EnrollmentEvent.event_type == "completed",
            )
            .order_by(EnrollmentEvent.enrollment_id)
        )
    )
    assert len(completion_events) == 2
    assert all(
        (item.old_status, item.new_status, item.actor_user_id, item.request_id)
        == (
            "active",
            "completed",
            actor_user_id,
            "manual-complete-enrollments",
        )
        for item in completion_events
    )

    cohort_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from cohorts " in statement
    )
    class_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from teaching_classes " in statement
    )
    enrollment_lock = next(
        index
        for index, statement in enumerate(statements)
        if " from enrollments " in statement
    )
    assert cohort_lock < class_lock < enrollment_lock
    assert "order by teaching_classes.id" in statements[class_lock]
    assert "order by enrollments.id" in statements[enrollment_lock]


def test_every_enabled_class_requires_an_eligible_current_primary_coach(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "主教练门槛营期")
    first_class = _add_class(
        db,
        cohort_id=cohort.id,
        actor_user_id=actor_user_id,
        name="一班",
    )
    second_class = _add_class(
        db,
        cohort_id=cohort.id,
        actor_user_id=actor_user_id,
        name="二班",
    )
    coach, account = _create_account(
        db,
        full_name="合格主教练",
        staff_type="coach",
        coach_role=True,
    )
    _assign_primary(
        db,
        class_id=first_class.id,
        coach_person_id=coach.id,
        actor_user_id=actor_user_id,
    )
    db.commit()

    with pytest.raises(PrimaryCoachRequired) as error:
        service.transition(
            cohort.id,
            target="open",
            trigger="manual",
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="missing-second-primary",
        )
    assert error.value.code == "primary_coach_required"

    _assign_primary(
        db,
        class_id=second_class.id,
        coach_person_id=coach.id,
        actor_user_id=actor_user_id,
    )
    account.status = "disabled"
    db.commit()
    with pytest.raises(PrimaryCoachRequired):
        service.transition(
            cohort.id,
            target="open",
            trigger="manual",
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="disabled-primary",
        )

    account.status = "active"
    db.flush()
    opened = service.transition(
        cohort.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="all-primary-ready",
    )
    assert opened.status == "open"


def test_update_matrix_locks_schedule_by_status_and_archived_is_read_only(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "更新矩阵营期")
    draft_end = cohort.ends_at + timedelta(days=1)
    service.update_cohort(
        cohort.id,
        CohortUpdate(ends_at=draft_end, auto_start_enabled=True),
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="draft-schedule-update",
    )
    service.transition(
        cohort.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="matrix-open",
    )
    service.transition(
        cohort.id,
        target="active",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="matrix-active",
    )
    active_end = draft_end + timedelta(days=1)
    updated = service.update_cohort(
        cohort.id,
        CohortUpdate(
            name="更新矩阵营期修正",
            ends_at=active_end,
            default_check_out_at=active_end,
            auto_complete_enabled=True,
        ),
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="active-allowed-update",
    )
    assert updated.ends_at == active_end

    with pytest.raises(CohortScheduleLocked) as active_error:
        service.update_cohort(
            cohort.id,
            CohortUpdate(starts_at=cohort.starts_at + timedelta(hours=1)),
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="active-locked-update",
        )
    assert active_error.value.code == "cohort_schedule_locked"

    service.transition(
        cohort.id,
        target="completed",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="matrix-completed",
    )
    renamed = service.update_cohort(
        cohort.id,
        CohortUpdate(name="结营后名称修正"),
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="completed-name-update",
    )
    assert renamed.name == "结营后名称修正"
    with pytest.raises(CohortScheduleLocked):
        service.update_cohort(
            cohort.id,
            CohortUpdate(ends_at=active_end + timedelta(days=1)),
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="completed-schedule-update",
        )

    service.transition(
        cohort.id,
        target="archived",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="matrix-archived",
    )
    with pytest.raises(CohortScheduleLocked):
        service.update_cohort(
            cohort.id,
            CohortUpdate(name="归档后不能改名"),
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id="archived-name-update",
        )


def test_update_enabling_due_automation_reconciles_the_locked_target_once(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(
        service,
        actor_user_id,
        "更新触发校准营期",
        starts_at=NOW - timedelta(days=2),
        ends_at=NOW + timedelta(days=2),
    )
    service.transition(
        cohort.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW - timedelta(days=3),
        request_id="reconcile-update-open",
    )

    updated = service.update_cohort(
        cohort.id,
        CohortUpdate(auto_start_enabled=True),
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="reconcile-on-update",
    )

    assert updated.status == "active"
    events = list(
        db.scalars(
            select(CohortStatusEvent)
            .where(CohortStatusEvent.cohort_id == cohort.id)
            .order_by(CohortStatusEvent.occurred_at, CohortStatusEvent.id)
        )
    )
    assert [event.trigger for event in events] == ["manual", "request_reconciliation"]
    assert events[-1].actor_user_id is None


def test_direct_reconciliation_is_adjacent_idempotent_and_audited(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(
        service,
        actor_user_id,
        "连续到期营期",
        starts_at=NOW - timedelta(days=3),
        ends_at=NOW - timedelta(days=1),
        auto_start_enabled=True,
        auto_complete_enabled=True,
    )
    service.transition(
        cohort.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW - timedelta(days=4),
        request_id="adjacent-open",
    )

    assert service.reconcile_due_cohort(
        cohort.id,
        now=NOW,
        trigger="request_reconciliation",
        request_id="adjacent-first",
    ) is True
    db.refresh(cohort)
    assert cohort.status == "active"
    assert service.reconcile_due_cohort(
        cohort.id,
        now=NOW + timedelta(seconds=1),
        trigger="request_reconciliation",
        request_id="adjacent-second",
    ) is True
    db.refresh(cohort)
    assert cohort.status == "completed"
    assert service.reconcile_due_cohort(
        cohort.id,
        now=NOW + timedelta(seconds=2),
        trigger="request_reconciliation",
        request_id="adjacent-third",
    ) is False

    automatic_events = list(
        db.scalars(
            select(CohortStatusEvent)
            .where(
                CohortStatusEvent.cohort_id == cohort.id,
                CohortStatusEvent.trigger == "request_reconciliation",
            )
            .order_by(CohortStatusEvent.occurred_at, CohortStatusEvent.id)
        )
    )
    assert [(event.old_status, event.new_status) for event in automatic_events] == [
        ("open", "active"),
        ("active", "completed"),
    ]
    assert all(event.actor_user_id is None for event in automatic_events)
    audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.request_id.in_({"adjacent-first", "adjacent-second"})
            )
        )
    )
    assert len(audits) == 2 and all(event.actor_user_id is None for event in audits)


def test_direct_reconciliation_is_not_starved_by_a_fifty_row_backlog(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohorts = [
        _create_cohort(
            service,
            actor_user_id,
            f"积压营期 {index:02d}",
            starts_at=NOW - timedelta(days=60 - index),
            ends_at=NOW + timedelta(days=30),
            auto_start_enabled=True,
        )
        for index in range(52)
    ]
    for index, cohort in enumerate(cohorts):
        service.transition(
            cohort.id,
            target="open",
            trigger="manual",
            actor_user_id=actor_user_id,
            occurred_at=NOW - timedelta(days=70),
            request_id=f"backlog-open-{index}",
        )

    assert service.reconcile_due_batch_for_request(
        now=NOW,
        request_id="backlog-batch",
        limit=50,
    ) == 50
    target = cohorts[-1]
    db.refresh(target)
    assert target.status == "open"

    assert service.reconcile_due_cohort(
        target.id,
        now=NOW,
        trigger="request_reconciliation",
        request_id="backlog-direct-target",
    ) is True
    db.refresh(target)
    assert target.status == "active"


def test_visibility_filters_before_count_and_pagination(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    zero_class = _create_cohort(service, actor_user_id, "无班级全局可见")
    allowed = _create_cohort(service, actor_user_id, "范围内营期")
    denied = _create_cohort(service, actor_user_id, "范围外营期")
    allowed_class = _add_class(
        db,
        cohort_id=allowed.id,
        actor_user_id=actor_user_id,
        name="范围内班级",
    )
    denied_class = _add_class(
        db,
        cohort_id=denied.id,
        actor_user_id=actor_user_id,
        name="范围外班级",
    )

    scoped, scoped_total = service.list_cohorts(
        name=None,
        status=None,
        date_from=None,
        date_to=None,
        visibility=TeachingVisibility(
            global_access=False,
            class_ids=frozenset({allowed_class.id}),
        ),
        page=1,
        page_size=1,
    )
    global_filtered, global_total = service.list_cohorts(
        name=None,
        status=None,
        date_from=None,
        date_to=None,
        visibility=TeachingVisibility(
            global_access=True,
            excluded_class_ids=frozenset({denied_class.id}),
        ),
        page=1,
        page_size=20,
    )

    assert scoped_total == 1 and [item.id for item in scoped] == [allowed.id]
    assert global_total == 2
    assert {item.id for item in global_filtered} == {zero_class.id, allowed.id}


def test_standard_class_rejects_professional_team(
    db: Session,
    actor_user_id: UUID,
) -> None:
    cohort_service = CohortService(db)
    catalog = TeachingCatalogService(db)
    cohort = _create_cohort(cohort_service, actor_user_id, "普通班队伍约束营期")
    team = catalog.create_team(
        ProfessionalTeamCreate(project_id=PROJECT_ID, name="普通班不应关联战队"),
        actor_user_id=actor_user_id,
    )

    with pytest.raises(StandardClassTeamForbidden) as error:
        cohort_service.create_class(
            TeachingClassCreate(
                cohort_id=cohort.id,
                project_id=PROJECT_ID,
                name="违规普通班",
                class_type="standard",
                professional_team_id=team.id,
                capacity=20,
                default_tuition_cents=100_000,
            ),
            actor_user_id=actor_user_id,
        )

    assert error.value.code == "standard_class_team_forbidden"
    assert db.scalar(
        select(func.count(TeachingClass.id)).where(TeachingClass.cohort_id == cohort.id)
    ) == 0


def test_elite_class_requires_active_same_project_team(
    db: Session,
    actor_user_id: UUID,
) -> None:
    cohort_service = CohortService(db)
    catalog = TeachingCatalogService(db)
    cohort = _create_cohort(cohort_service, actor_user_id, "菁英班队伍约束营期")
    other_project = catalog.create_project(
        ProjectCreate(name="菁英班其他项目"),
        actor_user_id=actor_user_id,
    )
    valid_team = catalog.create_team(
        ProfessionalTeamCreate(project_id=PROJECT_ID, name="有效菁英战队"),
        actor_user_id=actor_user_id,
    )
    inactive_team = catalog.create_team(
        ProfessionalTeamCreate(project_id=PROJECT_ID, name="停用菁英战队"),
        actor_user_id=actor_user_id,
    )
    catalog.update_team(
        inactive_team.id,
        ProfessionalTeamUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )
    cross_project_team = catalog.create_team(
        ProfessionalTeamCreate(project_id=other_project.id, name="跨项目菁英战队"),
        actor_user_id=actor_user_id,
    )

    base = {
        "cohort_id": cohort.id,
        "project_id": PROJECT_ID,
        "class_type": "elite",
        "capacity": 20,
        "default_tuition_cents": 100_000,
    }
    with pytest.raises(EliteTeamRequired) as missing_error:
        cohort_service.create_class(
            TeachingClassCreate(name="缺少队伍菁英班", **base),
            actor_user_id=actor_user_id,
        )
    assert missing_error.value.code == "elite_team_required"
    with pytest.raises(InactiveProfessionalTeam) as inactive_error:
        cohort_service.create_class(
            TeachingClassCreate(
                name="停用队伍菁英班",
                professional_team_id=inactive_team.id,
                **base,
            ),
            actor_user_id=actor_user_id,
        )
    assert inactive_error.value.code == "inactive_professional_team"
    with pytest.raises(ProjectTeamMismatch) as mismatch_error:
        cohort_service.create_class(
            TeachingClassCreate(
                name="跨项目菁英班",
                professional_team_id=cross_project_team.id,
                **base,
            ),
            actor_user_id=actor_user_id,
        )
    assert mismatch_error.value.code == "project_team_mismatch"

    created = cohort_service.create_class(
        TeachingClassCreate(
            name="有效菁英班",
            professional_team_id=valid_team.id,
            **base,
        ),
        actor_user_id=actor_user_id,
    )
    assert created.professional_team_id == valid_team.id


@pytest.mark.parametrize("capacity", [0, -1, 10_001])
def test_class_capacity_must_be_positive_and_bounded(capacity: int) -> None:
    with pytest.raises(ValidationError):
        TeachingClassCreate(
            cohort_id=uuid4(),
            project_id=uuid4(),
            name="容量校验班",
            class_type="standard",
            capacity=capacity,
            default_tuition_cents=0,
        )


@pytest.mark.parametrize("tuition", [-1, 1.5, "1.5"])
def test_class_default_tuition_rejects_negative_or_non_integer_values(tuition) -> None:
    with pytest.raises(ValidationError):
        TeachingClassCreate(
            cohort_id=uuid4(),
            project_id=uuid4(),
            name="学费校验班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=tuition,
        )


def test_class_default_tuition_persists_zero_and_upper_bound_exactly(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "学费边界营期")

    zero = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="零学费班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_user_id,
    )
    upper = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="学费上限班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=1_000_000_000,
        ),
        actor_user_id=actor_user_id,
    )

    assert zero.default_tuition_cents == 0
    assert upper.default_tuition_cents == 1_000_000_000


def test_class_name_is_unique_within_cohort_after_trimming(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    first_cohort = _create_cohort(service, actor_user_id, "班名唯一营期 A")
    second_cohort = _create_cohort(service, actor_user_id, "班名唯一营期 B")
    input = {
        "project_id": PROJECT_ID,
        "name": "  同名普通班  ",
        "class_type": "standard",
        "capacity": 20,
        "default_tuition_cents": 100_000,
    }
    created = service.create_class(
        TeachingClassCreate(cohort_id=first_cohort.id, **input),
        actor_user_id=actor_user_id,
    )
    db.refresh(created)
    updated_at = created.updated_at

    with pytest.raises(DuplicateClass) as error:
        service.create_class(
            TeachingClassCreate(
                cohort_id=first_cohort.id,
                **{**input, "name": "同名普通班"},
            ),
            actor_user_id=actor_user_id,
        )
    assert error.value.code == "duplicate_class"
    db.expire_all()
    stored = db.get(TeachingClass, created.id)
    assert stored is not None and stored.updated_at == updated_at

    other = service.create_class(
        TeachingClassCreate(
            cohort_id=second_cohort.id,
            **{**input, "name": "同名普通班"},
        ),
        actor_user_id=actor_user_id,
    )
    assert other.cohort_id == second_cohort.id


def test_inactive_project_rejects_new_class(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    catalog = TeachingCatalogService(db)
    cohort = _create_cohort(service, actor_user_id, "停用项目班级营期")
    project = catalog.create_project(
        ProjectCreate(name="停用班级项目"),
        actor_user_id=actor_user_id,
    )
    catalog.update_project(
        project.id,
        ProjectUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )

    with pytest.raises(InactiveProject) as error:
        service.create_class(
            TeachingClassCreate(
                cohort_id=cohort.id,
                project_id=project.id,
                name="不应创建的班级",
                class_type="standard",
                capacity=20,
                default_tuition_cents=0,
            ),
            actor_user_id=actor_user_id,
        )
    assert error.value.code == "inactive_project"


def test_active_enrollment_blocks_class_deactivation(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "有效报名班级停用保护")
    teaching_class = _add_class(
        db,
        cohort_id=cohort.id,
        actor_user_id=actor_user_id,
        name="有有效报名的班级",
    )
    actor = db.get(UserAccount, actor_user_id)
    assert actor is not None
    _add_active_enrollment(
        db,
        cohort=cohort,
        teaching_class=teaching_class,
        student_person_id=actor.person_id,
        actor_user_id=actor_user_id,
    )
    db.commit()

    with pytest.raises(TeachingServiceError) as error:
        service.update_class(
            teaching_class.id,
            TeachingClassUpdate(is_active=False),
            actor_user_id=actor_user_id,
        )

    assert error.value.code == "class_has_active_enrollments"
    db.expire_all()
    stored = db.get(TeachingClass, teaching_class.id)
    assert stored is not None and stored.is_active is True


def test_archived_cohort_rejects_all_class_changes(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "归档班级保护营期")
    service.transition(
        cohort.id,
        target="open",
        trigger="manual",
        actor_user_id=actor_user_id,
        occurred_at=NOW,
        request_id="archive-class-open",
    )
    existing = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="归档前班级",
            class_type="standard",
            capacity=20,
            default_tuition_cents=100_000,
        ),
        actor_user_id=actor_user_id,
    )
    for target in ("active", "completed", "archived"):
        service.transition(
            cohort.id,
            target=target,
            trigger="manual",
            actor_user_id=actor_user_id,
            occurred_at=NOW,
            request_id=f"archive-class-{target}",
        )
    db.refresh(existing)
    original = (existing.name, existing.is_active, existing.updated_at)

    with pytest.raises(CohortArchived):
        service.create_class(
            TeachingClassCreate(
                cohort_id=cohort.id,
                project_id=PROJECT_ID,
                name="归档后新班级",
                class_type="standard",
                capacity=20,
                default_tuition_cents=0,
            ),
            actor_user_id=actor_user_id,
        )
    with pytest.raises(CohortArchived):
        service.update_class(
            existing.id,
            TeachingClassUpdate(name="归档后改名"),
            actor_user_id=actor_user_id,
        )
    with pytest.raises(CohortArchived):
        service.update_class(
            existing.id,
            TeachingClassUpdate(is_active=False),
            actor_user_id=actor_user_id,
        )

    db.expire_all()
    stored = db.get(TeachingClass, existing.id)
    assert stored is not None
    assert (stored.name, stored.is_active, stored.updated_at) == original


def test_class_lists_filter_before_stable_pagination(
    db: Session,
    actor_user_id: UUID,
) -> None:
    service = CohortService(db)
    cohort = _create_cohort(service, actor_user_id, "班级列表营期")
    second = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="B 班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_user_id,
    )
    first = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="A 班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_user_id,
    )
    inactive = service.create_class(
        TeachingClassCreate(
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name="最先但停用班",
            class_type="standard",
            capacity=20,
            default_tuition_cents=0,
        ),
        actor_user_id=actor_user_id,
    )
    service.update_class(
        inactive.id,
        TeachingClassUpdate(is_active=False),
        actor_user_id=actor_user_id,
    )

    page_one, total = service.list_classes(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=None,
        is_active=None,
        visibility=TeachingVisibility.global_scope(),
        page=1,
        page_size=2,
    )
    page_two, second_total = service.list_classes(
        cohort_id=cohort.id,
        project_id=PROJECT_ID,
        name=None,
        is_active=None,
        visibility=TeachingVisibility.global_scope(),
        page=2,
        page_size=2,
    )
    exact, exact_total = service.list_classes(
        cohort_id=cohort.id,
        project_id=None,
        name="  B 班  ",
        is_active=True,
        visibility=TeachingVisibility.global_scope(),
        page=1,
        page_size=1,
    )

    assert total == second_total == 3
    assert [item.id for item in page_one] == [first.id, second.id]
    assert [item.id for item in page_two] == [inactive.id]
    assert exact_total == 1 and [item.id for item in exact] == [second.id]
