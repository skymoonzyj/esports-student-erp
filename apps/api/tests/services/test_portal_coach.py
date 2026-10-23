from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile, StudentProfile
from app.portal.coach_service import PortalCoachService
from app.portal.errors import CoachCohortNotFound, PortalPermissionDenied
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    ProfessionalTeam,
    TeachingClass,
)

PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class PortalCoachScenario:
    db: Session
    service: PortalCoachService
    now: datetime
    coach: Person
    coach_account: UserAccount
    nearest_start: datetime
    later_start: datetime
    primary_class: TeachingClass
    assistant_class: TeachingClass
    later_class: TeachingClass
    later_cohort: Cohort
    unassigned_cohort: Cohort

    def deny(self, permission: str, scope_kind: str = "global") -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.coach_account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind=scope_kind,
                scope_id=None,
            )
        )
        self.db.flush()

    def deny_class(self, permission: str, class_id: UUID) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.coach_account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="assigned_class",
                scope_id=class_id,
            )
        )
        self.db.flush()


def _create_person(
    db: Session,
    *,
    name: str,
    birth_date: date,
    marker: str,
) -> Person:
    person = Person(
        full_name=name,
        identity_ciphertext=f"portal-coach-cipher-{marker}",
        identity_digest=f"portal-coach-id-{marker}",
        sex="female",
        date_of_birth=birth_date,
    )
    db.add(person)
    db.flush()
    return person


def _create_student(
    db: Session,
    *,
    name: str,
    number: str,
    birth_date: date,
    marker: str,
) -> Person:
    person = _create_person(
        db,
        name=name,
        birth_date=birth_date,
        marker=marker,
    )
    db.add(
        StudentProfile(
            person_id=person.id,
            student_number=number,
            status="active",
        )
    )
    db.flush()
    return person


def _create_enrollment(
    db: Session,
    *,
    student: Person,
    cohort: Cohort,
    teaching_class: TeachingClass,
    actor: UserAccount,
    confirmed_at: datetime,
    status: str = "active",
) -> Enrollment:
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=100_000,
        created_by_user_id=actor.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=100_000,
        status=status,
        confirmed_at=confirmed_at,
        cancelled_at=confirmed_at + timedelta(days=1)
        if status == "cancelled"
        else None,
        completed_at=None,
        created_by_user_id=actor.id,
    )
    db.add(enrollment)
    db.flush()
    return enrollment


@pytest.fixture
def portal_coach_scenario(db: Session) -> PortalCoachScenario:
    marker = uuid4().hex[:12]
    now = datetime(2026, 7, 29, 8, tzinfo=SHANGHAI)
    nearest_start = datetime(2030, 8, 1, 9, tzinfo=SHANGHAI)
    later_start = datetime(2030, 8, 15, 9, tzinfo=SHANGHAI)

    coach = _create_person(
        db,
        name="门户名单教练",
        birth_date=date(1988, 1, 2),
        marker=f"{marker}-coach",
    )
    db.add(StaffProfile(person_id=coach.id, staff_type="coach"))
    coach_account = UserAccount(
        person_id=coach.id,
        login_phone_digest=f"portal-coach-phone-{marker}",
        email_normalized=f"portal-coach-{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(coach_account)
    db.flush()
    db.add(UserRole(user_id=coach_account.id, role_id=ROLES["coach"].id))

    team = ProfessionalTeam(
        project_id=PROJECT_ID,
        name=f"门户测试职业队-{marker}",
        notes=None,
        is_active=True,
        created_by_user_id=coach_account.id,
    )
    db.add(team)
    db.flush()

    cohort_specs = (
        (UUID("81000000-0000-4000-8000-000000000001"), "最近营期", nearest_start, "open"),
        (UUID("81000000-0000-4000-8000-000000000002"), "稍后营期", later_start, "upcoming"),
        (
            UUID("81000000-0000-4000-8000-000000000003"),
            "未分配营期",
            nearest_start + timedelta(days=2),
            "open",
        ),
        (
            UUID("81000000-0000-4000-8000-000000000004"),
            "已开营营期",
            now - timedelta(days=10),
            "active",
        ),
    )
    cohorts: dict[str, Cohort] = {}
    for cohort_id, name, starts_at, status in cohort_specs:
        cohort = Cohort(
            id=cohort_id,
            name=f"{name}-{marker}",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=10),
            default_check_in_at=starts_at,
            default_check_out_at=starts_at + timedelta(days=10),
            status=status,
            created_by_user_id=coach_account.id,
        )
        db.add(cohort)
        cohorts[name] = cohort
    db.flush()

    class_specs = (
        (
            UUID("82000000-0000-4000-8000-000000000001"),
            "最近普通班",
            cohorts["最近营期"],
            "standard",
            None,
        ),
        (
            UUID("82000000-0000-4000-8000-000000000002"),
            "最近菁英班",
            cohorts["最近营期"],
            "elite",
            team.id,
        ),
        (
            UUID("82000000-0000-4000-8000-000000000003"),
            "稍后普通班",
            cohorts["稍后营期"],
            "standard",
            None,
        ),
        (
            UUID("82000000-0000-4000-8000-000000000004"),
            "未分配班",
            cohorts["未分配营期"],
            "standard",
            None,
        ),
        (
            UUID("82000000-0000-4000-8000-000000000005"),
            "已开营班",
            cohorts["已开营营期"],
            "standard",
            None,
        ),
    )
    classes: dict[str, TeachingClass] = {}
    for class_id, name, cohort, class_type, team_id in class_specs:
        teaching_class = TeachingClass(
            id=class_id,
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name=f"{name}-{marker}",
            class_type=class_type,
            professional_team_id=team_id,
            capacity=20,
            default_tuition_cents=100_000,
            is_active=True,
            created_by_user_id=coach_account.id,
        )
        db.add(teaching_class)
        classes[name] = teaching_class
    db.flush()

    assignment_specs = (
        (classes["最近普通班"], "primary"),
        (classes["最近菁英班"], "assistant"),
        (classes["稍后普通班"], "primary"),
        (classes["已开营班"], "primary"),
    )
    for teaching_class, assignment_type in assignment_specs:
        db.add(
            CoachAssignment(
                class_id=teaching_class.id,
                coach_person_id=coach.id,
                assignment_type=assignment_type,
                valid_from=now - timedelta(days=30),
                valid_until=datetime(2031, 1, 1, tzinfo=SHANGHAI),
                created_by_user_id=coach_account.id,
            )
        )
    db.flush()

    enrollment_specs = (
        ("最近一班学员", "PORTAL-C-001", date(2012, 1, 2), cohorts["最近营期"], classes["最近普通班"], "active"),
        ("最近二班学员", "PORTAL-C-002", date(1998, 1, 2), cohorts["最近营期"], classes["最近菁英班"], "active"),
        ("稍后营期学员", "PORTAL-C-003", date(2000, 1, 2), cohorts["稍后营期"], classes["稍后普通班"], "active"),
        ("未分配班学员", "PORTAL-C-004", date(2001, 1, 2), cohorts["未分配营期"], classes["未分配班"], "active"),
        ("已取消学员", "PORTAL-C-005", date(2002, 1, 2), cohorts["最近营期"], classes["最近普通班"], "cancelled"),
    )
    for index, (name, number, birth, cohort, teaching_class, status) in enumerate(
        enrollment_specs,
        start=1,
    ):
        student = _create_student(
            db,
            name=name,
            number=f"{number}-{marker[:4]}",
            birth_date=birth,
            marker=f"{marker}-student-{index}",
        )
        _create_enrollment(
            db,
            student=student,
            cohort=cohort,
            teaching_class=teaching_class,
            actor=coach_account,
            confirmed_at=now,
            status=status,
        )
    db.flush()

    return PortalCoachScenario(
        db=db,
        service=PortalCoachService(db),
        now=now,
        coach=coach,
        coach_account=coach_account,
        nearest_start=nearest_start,
        later_start=later_start,
        primary_class=classes["最近普通班"],
        assistant_class=classes["最近菁英班"],
        later_class=classes["稍后普通班"],
        later_cohort=cohorts["稍后营期"],
        unassigned_cohort=cohorts["未分配营期"],
    )


def test_coach_defaults_to_nearest_authorized_future_start(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )

    assert result.selected_starts_at == portal_coach_scenario.nearest_start
    assert {item.class_id for item in result.classes} == {
        portal_coach_scenario.primary_class.id,
        portal_coach_scenario.assistant_class.id,
    }
    assert {item.student_name for item in result.items} == {
        "最近一班学员",
        "最近二班学员",
    }
    assert all(item.enrollment_status == "active" for item in result.items)
    assert {item.student_count for item in result.classes} == {1}


def test_coach_can_select_a_later_authorized_start(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=portal_coach_scenario.later_cohort.id,
        page=1,
        page_size=20,
    )

    assert result.selected_starts_at == portal_coach_scenario.later_start
    assert [item.student_name for item in result.items] == ["稍后营期学员"]


def test_coach_anchor_masks_unassigned_cohort(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    with pytest.raises(CoachCohortNotFound):
        portal_coach_scenario.service.list_next(
            user_id=portal_coach_scenario.coach_account.id,
            actor_person_id=portal_coach_scenario.coach.id,
            now=portal_coach_scenario.now,
            anchor_cohort_id=portal_coach_scenario.unassigned_cohort.id,
            page=1,
            page_size=20,
        )


def test_generic_coach_deny_returns_permission_error(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    portal_coach_scenario.deny("coach_roster.read")

    with pytest.raises(PortalPermissionDenied):
        portal_coach_scenario.service.list_next(
            user_id=portal_coach_scenario.coach_account.id,
            actor_person_id=portal_coach_scenario.coach.id,
            now=portal_coach_scenario.now,
            anchor_cohort_id=None,
            page=1,
            page_size=20,
        )


def test_roster_paging_is_stable_and_class_counts_ignore_current_page(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    first = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=1,
    )
    second = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=2,
        page_size=1,
    )

    assert first.total == second.total == 2
    assert first.items[0].enrollment_id != second.items[0].enrollment_id
    assert [(item.class_id, item.student_count) for item in first.classes] == [
        (item.class_id, 1) for item in first.classes
    ]


def test_class_specific_deny_removes_only_that_roster(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    portal_coach_scenario.deny_class(
        "coach_roster.read",
        portal_coach_scenario.primary_class.id,
    )

    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )

    assert [item.class_id for item in result.classes] == [
        portal_coach_scenario.assistant_class.id
    ]
    assert [item.student_name for item in result.items] == ["最近二班学员"]


def test_ended_assignment_disappears_on_the_next_request(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    before = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )
    assignment = portal_coach_scenario.db.scalar(
        select(CoachAssignment).where(
            CoachAssignment.class_id == portal_coach_scenario.primary_class.id,
            CoachAssignment.coach_person_id == portal_coach_scenario.coach.id,
            CoachAssignment.ended_at.is_(None),
        )
    )
    assert assignment is not None
    assignment.ended_at = portal_coach_scenario.now
    assignment.ended_by_user_id = portal_coach_scenario.coach_account.id
    assignment.end_reason = "门户测试撤销"
    portal_coach_scenario.db.flush()

    after = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )

    assert {item.class_id for item in before.classes} == {
        portal_coach_scenario.primary_class.id,
        portal_coach_scenario.assistant_class.id,
    }
    assert [item.class_id for item in after.classes] == [
        portal_coach_scenario.assistant_class.id
    ]
    assert [item.student_name for item in after.items] == ["最近二班学员"]


def test_assignment_at_valid_until_is_no_longer_current(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    assignment = portal_coach_scenario.db.scalar(
        select(CoachAssignment).where(
            CoachAssignment.class_id == portal_coach_scenario.primary_class.id,
            CoachAssignment.coach_person_id == portal_coach_scenario.coach.id,
        )
    )
    assert assignment is not None
    assignment.valid_until = portal_coach_scenario.now
    portal_coach_scenario.db.flush()

    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )

    assert [item.class_id for item in result.classes] == [
        portal_coach_scenario.assistant_class.id
    ]


def test_losing_coach_eligibility_removes_all_assignments(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    staff_profile = portal_coach_scenario.db.get(
        StaffProfile,
        portal_coach_scenario.coach.id,
    )
    assert staff_profile is not None
    staff_profile.staff_type = "life_teacher"
    portal_coach_scenario.db.flush()

    result = portal_coach_scenario.service.list_next(
        user_id=portal_coach_scenario.coach_account.id,
        actor_person_id=portal_coach_scenario.coach.id,
        now=portal_coach_scenario.now,
        anchor_cohort_id=None,
        page=1,
        page_size=20,
    )

    assert result.selected_starts_at is None
    assert result.options == []
    assert result.classes == []
    assert result.items == []
    assert result.total == 0


def test_coach_roster_projection_uses_bounded_query_count(
    portal_coach_scenario: PortalCoachScenario,
) -> None:
    bind = portal_coach_scenario.db.get_bind()
    selects = 0

    def record_select(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(bind, "before_cursor_execute", record_select)
    try:
        portal_coach_scenario.service.list_next(
            user_id=portal_coach_scenario.coach_account.id,
            actor_person_id=portal_coach_scenario.coach.id,
            now=portal_coach_scenario.now,
            anchor_cohort_id=None,
            page=1,
            page_size=20,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_select)

    assert selects <= 10
