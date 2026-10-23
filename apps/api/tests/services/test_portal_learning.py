from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.portal.errors import FamilyStudentNotFound
from app.portal.learning_service import PortalLearningService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.models import Cohort, TeachingClass

PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
HISTORY_IDS = (
    UUID("93000000-0000-4000-8000-000000000001"),
    UUID("93000000-0000-4000-8000-000000000002"),
    UUID("93000000-0000-4000-8000-000000000003"),
    UUID("93000000-0000-4000-8000-000000000004"),
)


@dataclass
class PortalLearningScenario:
    db: Session
    service: PortalLearningService
    today: date
    now: datetime
    guardian: Person
    guardian_account: UserAccount
    minor_student: Person
    adult_student: Person
    student_account: UserAccount
    minor_relationship: GuardianRelationship

    @property
    def student_guardian_account(self) -> UserAccount:
        return self.student_account

    def end_minor_relationship(self) -> None:
        self.minor_relationship.ended_at = self.now
        self.minor_relationship.ended_by_user_id = self.guardian_account.id
        self.minor_relationship.end_reason = "门户测试结束关系"
        self.db.flush()

    def deny_person(
        self,
        account: UserAccount,
        permission: str,
        person_id: UUID,
    ) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="person",
                scope_id=person_id,
            )
        )
        self.db.flush()

    def deny(
        self,
        account: UserAccount,
        permission: str,
        scope_kind: str,
    ) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind=scope_kind,
                scope_id=None,
            )
        )
        self.db.flush()

    def bind_adult_as_guardian_of_minor(self) -> None:
        self.db.add(
            GuardianRelationship(
                guardian_person_id=self.adult_student.id,
                student_person_id=self.minor_student.id,
                relationship="mentor",
                is_primary=False,
                valid_from=date(2026, 1, 1),
            )
        )
        self.db.flush()


def _person(
    db: Session,
    *,
    name: str,
    birth_date: date,
    marker: str,
    student_number: str | None = None,
) -> Person:
    person = Person(
        full_name=name,
        identity_ciphertext=f"learning-cipher-{marker}",
        identity_digest=f"learning-id-{marker}",
        sex="female",
        date_of_birth=birth_date,
    )
    db.add(person)
    db.flush()
    if student_number is not None:
        db.add(
            StudentProfile(
                person_id=person.id,
                student_number=student_number,
                status="active",
            )
        )
        db.flush()
    return person


def _account(
    db: Session,
    *,
    person: Person,
    marker: str,
    roles: tuple[str, ...],
) -> UserAccount:
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"learning-phone-{marker}",
        email_normalized=f"learning-{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    for role in roles:
        db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
    db.flush()
    return account


def _enrollment(
    db: Session,
    *,
    enrollment_id: UUID,
    student: Person,
    cohort: Cohort,
    teaching_class: TeachingClass,
    actor: UserAccount,
    status: str,
    confirmed_at: datetime,
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
        id=enrollment_id,
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
        completed_at=confirmed_at + timedelta(days=20)
        if status == "completed"
        else None,
        created_by_user_id=actor.id,
    )
    db.add(enrollment)
    db.flush()
    return enrollment


@pytest.fixture
def portal_learning_scenario(db: Session) -> PortalLearningScenario:
    marker = uuid4().hex[:12]
    today = date(2026, 7, 29)
    now = datetime(2026, 7, 29, 8, tzinfo=UTC)
    guardian = _person(
        db,
        name="学习门户监护人",
        birth_date=date(1980, 1, 2),
        marker=f"{marker}-guardian",
    )
    guardian_account = _account(
        db,
        person=guardian,
        marker=f"{marker}-guardian",
        roles=("guardian",),
    )
    minor_student = _person(
        db,
        name="未成年在营学员",
        birth_date=date(2012, 1, 2),
        marker=f"{marker}-minor",
        student_number=f"LEARN-M-{marker[:5]}",
    )
    adult_student = _person(
        db,
        name="成年后仍绑定学员",
        birth_date=date(1995, 1, 2),
        marker=f"{marker}-adult",
        student_number=f"LEARN-A-{marker[:5]}",
    )
    student_account = _account(
        db,
        person=adult_student,
        marker=f"{marker}-adult",
        roles=("student", "guardian"),
    )
    minor_relationship = GuardianRelationship(
        guardian_person_id=guardian.id,
        student_person_id=minor_student.id,
        relationship="mother",
        is_primary=True,
        valid_from=date(2020, 1, 1),
    )
    adult_relationship = GuardianRelationship(
        guardian_person_id=guardian.id,
        student_person_id=adult_student.id,
        relationship="father",
        is_primary=True,
        valid_from=date(2010, 1, 1),
    )
    db.add_all([minor_relationship, adult_relationship])
    db.flush()

    cohort_specs = (
        (
            UUID("91000000-0000-4000-8000-000000000001"),
            "当前在营",
            datetime(2026, 7, 1, 1, tzinfo=UTC),
            "active",
        ),
        (
            UUID("91000000-0000-4000-8000-000000000002"),
            "最近待开营",
            datetime(2026, 8, 15, 1, tzinfo=UTC),
            "open",
        ),
        (
            UUID("91000000-0000-4000-8000-000000000003"),
            "已完成历史",
            datetime(2026, 1, 1, 1, tzinfo=UTC),
            "completed",
        ),
        (
            UUID("91000000-0000-4000-8000-000000000004"),
            "已取消历史",
            datetime(2025, 12, 1, 1, tzinfo=UTC),
            "completed",
        ),
    )
    cohorts: list[Cohort] = []
    classes: list[TeachingClass] = []
    for index, (cohort_id, name, starts_at, status) in enumerate(
        cohort_specs,
        start=1,
    ):
        cohort = Cohort(
            id=cohort_id,
            name=f"{name}-{marker}",
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=20),
            default_check_in_at=starts_at,
            default_check_out_at=starts_at + timedelta(days=20),
            status=status,
            created_by_user_id=guardian_account.id,
        )
        db.add(cohort)
        db.flush()
        teaching_class = TeachingClass(
            id=UUID(f"92000000-0000-4000-8000-{index:012d}"),
            cohort_id=cohort.id,
            project_id=PROJECT_ID,
            name=f"学习历史{index}班-{marker}",
            class_type="standard",
            professional_team_id=None,
            capacity=20,
            default_tuition_cents=100_000,
            is_active=True,
            created_by_user_id=guardian_account.id,
        )
        db.add(teaching_class)
        db.flush()
        cohorts.append(cohort)
        classes.append(teaching_class)

    history_statuses = ("active", "active", "completed", "cancelled")
    for enrollment_id, cohort, teaching_class, status in zip(
        HISTORY_IDS,
        cohorts,
        classes,
        history_statuses,
        strict=True,
    ):
        _enrollment(
            db,
            enrollment_id=enrollment_id,
            student=minor_student,
            cohort=cohort,
            teaching_class=teaching_class,
            actor=guardian_account,
            status=status,
            confirmed_at=cohort.starts_at - timedelta(days=5),
        )
    _enrollment(
        db,
        enrollment_id=UUID("93000000-0000-4000-8000-000000000010"),
        student=adult_student,
        cohort=cohorts[1],
        teaching_class=classes[1],
        actor=guardian_account,
        status="active",
        confirmed_at=cohorts[1].starts_at - timedelta(days=3),
    )
    db.flush()

    return PortalLearningScenario(
        db=db,
        service=PortalLearningService(db),
        today=today,
        now=now,
        guardian=guardian,
        guardian_account=guardian_account,
        minor_student=minor_student,
        adult_student=adult_student,
        student_account=student_account,
        minor_relationship=minor_relationship,
    )


def test_guardian_lists_minor_and_adult_active_relationships(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today,
    )

    assert [(item.student_name, item.access_source) for item in result.items] == [
        ("未成年在营学员", "family"),
        ("成年后仍绑定学员", "family"),
    ]


def test_student_lists_only_self(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.student_account.id,
        actor_person_id=portal_learning_scenario.adult_student.id,
        reference_date=portal_learning_scenario.today,
    )

    assert [item.student_person_id for item in result.items] == [
        portal_learning_scenario.adult_student.id
    ]
    assert result.items[0].access_source == "self"


def test_ended_relationship_masks_teaching_detail(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    portal_learning_scenario.end_minor_relationship()

    students = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today,
    )

    assert [item.student_person_id for item in students.items] == [
        portal_learning_scenario.adult_student.id
    ]

    with pytest.raises(FamilyStudentNotFound):
        portal_learning_scenario.service.get_teaching(
            user_id=portal_learning_scenario.guardian_account.id,
            actor_person_id=portal_learning_scenario.guardian.id,
            student_person_id=portal_learning_scenario.minor_student.id,
            reference_date=portal_learning_scenario.today,
            page=1,
            page_size=20,
        )


def test_relationship_remains_valid_through_end_date_then_expires(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    portal_learning_scenario.minor_relationship.valid_until = (
        portal_learning_scenario.today
    )
    portal_learning_scenario.db.flush()

    on_end_date = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today,
    )
    assert portal_learning_scenario.minor_student.id in {
        item.student_person_id for item in on_end_date.items
    }

    after_end_date = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today + timedelta(days=1),
    )
    assert [item.student_person_id for item in after_end_date.items] == [
        portal_learning_scenario.adult_student.id
    ]


def test_person_deny_removes_only_one_family_candidate(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    portal_learning_scenario.deny_person(
        portal_learning_scenario.guardian_account,
        "people.read",
        portal_learning_scenario.minor_student.id,
    )

    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        reference_date=portal_learning_scenario.today,
    )

    assert [item.student_person_id for item in result.items] == [
        portal_learning_scenario.adult_student.id
    ]


def test_family_deny_removes_family_candidates_but_keeps_self(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    portal_learning_scenario.bind_adult_as_guardian_of_minor()
    portal_learning_scenario.deny(
        portal_learning_scenario.student_guardian_account,
        "enrollments.read",
        "family",
    )

    result = portal_learning_scenario.service.list_students(
        user_id=portal_learning_scenario.student_guardian_account.id,
        actor_person_id=portal_learning_scenario.adult_student.id,
        reference_date=portal_learning_scenario.today,
    )

    assert [(item.student_person_id, item.access_source) for item in result.items] == [
        (portal_learning_scenario.adult_student.id, "self")
    ]


def test_teaching_history_uses_deterministic_business_order(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    result = portal_learning_scenario.service.get_teaching(
        user_id=portal_learning_scenario.guardian_account.id,
        actor_person_id=portal_learning_scenario.guardian.id,
        student_person_id=portal_learning_scenario.minor_student.id,
        reference_date=portal_learning_scenario.today,
        page=1,
        page_size=20,
    )

    assert [item.enrollment_id for item in result.items] == list(HISTORY_IDS)
    assert result.total == 4


def test_family_student_projection_uses_bounded_query_count(
    portal_learning_scenario: PortalLearningScenario,
) -> None:
    bind = portal_learning_scenario.db.get_bind()
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
        portal_learning_scenario.service.list_students(
            user_id=portal_learning_scenario.guardian_account.id,
            actor_person_id=portal_learning_scenario.guardian.id,
            reference_date=portal_learning_scenario.today,
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_select)

    assert selects <= 10
