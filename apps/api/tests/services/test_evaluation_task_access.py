from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.evaluations.access import EvaluationDecision, EvaluationResource
from app.evaluations.errors import EvaluationResourceNotFound
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.evaluations.task_access import (
    EvaluationTaskAccessService,
    EvaluationTaskResource,
    upload_window_for_cohort,
)
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
)
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS

SHANGHAI = ZoneInfo("Asia/Shanghai")
ACCESS_COHORT_START = datetime(2026, 8, 10, 8, tzinfo=SHANGHAI).astimezone(UTC)
ACCESS_COHORT_END = datetime(2026, 8, 31, 18, tzinfo=SHANGHAI).astimezone(UTC)
ACCESS_NOW = datetime(2026, 8, 18, 12, tzinfo=SHANGHAI).astimezone(UTC)
LIFE_TEACHER_ROLE_ID = UUID("41000000-0000-4000-8000-000000000001")


def test_upload_window_uses_china_calendar_days_and_inclusive_endpoints() -> None:
    cohort_starts_at = datetime(2026, 8, 10, 16, 45, tzinfo=SHANGHAI)
    cohort_ends_at = datetime(2026, 8, 31, 9, 15, tzinfo=SHANGHAI)

    window = upload_window_for_cohort(
        starts_at=cohort_starts_at.astimezone(UTC),
        ends_at=cohort_ends_at.astimezone(UTC),
    )

    assert window.starts_at == datetime(2026, 8, 3, tzinfo=SHANGHAI)
    assert window.ends_at == datetime(
        2026,
        9,
        7,
        23,
        59,
        59,
        999999,
        tzinfo=SHANGHAI,
    )
    assert window.contains(window.starts_at)
    assert window.contains(window.ends_at)
    assert not window.contains(window.starts_at - timedelta(microseconds=1))
    assert not window.contains(window.ends_at + timedelta(microseconds=1))


@pytest.mark.parametrize(
    ("starts_at", "ends_at", "occurred_at"),
    [
        (
            datetime(2026, 8, 10),  # noqa: DTZ001 - intentional naive input
            datetime(2026, 8, 31, tzinfo=UTC),
            datetime(2026, 8, 10, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 10, tzinfo=UTC),
            datetime(2026, 8, 31),  # noqa: DTZ001 - intentional naive input
            datetime(2026, 8, 10, tzinfo=UTC),
        ),
        (
            datetime(2026, 8, 10, tzinfo=UTC),
            datetime(2026, 8, 31, tzinfo=UTC),
            datetime(2026, 8, 10),  # noqa: DTZ001 - intentional naive input
        ),
    ],
)
def test_upload_window_rejects_naive_datetimes(
    starts_at: datetime,
    ends_at: datetime,
    occurred_at: datetime,
) -> None:
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        with pytest.raises(ValueError, match="timezone-aware"):
            upload_window_for_cohort(starts_at=starts_at, ends_at=ends_at)
        return

    window = upload_window_for_cohort(starts_at=starts_at, ends_at=ends_at)
    with pytest.raises(ValueError, match="timezone-aware"):
        window.contains(occurred_at)


def test_task_resource_maps_every_authorization_dimension() -> None:
    resource = EvaluationTaskResource(
        student_person_id=uuid4(),
        enrollment_id=uuid4(),
        cohort_id=uuid4(),
        project_id=uuid4(),
        class_id=uuid4(),
    )

    assert resource.as_evaluation_resource() == EvaluationResource(
        student_person_id=resource.student_person_id,
        enrollment_id=resource.enrollment_id,
        cohort_id=resource.cohort_id,
        project_id=resource.project_id,
        class_id=resource.class_id,
    )


@dataclass
class RecordingAccess:
    decision: EvaluationDecision
    received: tuple[UUID, str, EvaluationResource, datetime] | None = None

    def decide(
        self,
        *,
        account_id: UUID,
        permission: str,
        resource: EvaluationResource,
        occurred_at: datetime,
    ) -> EvaluationDecision:
        self.received = (account_id, permission, resource, occurred_at)
        return self.decision


@pytest.mark.parametrize("allowed", [True, False])
def test_task_allowed_delegates_to_the_shared_evaluation_policy(allowed: bool) -> None:
    account_id = uuid4()
    occurred_at = datetime(2026, 8, 8, 9, tzinfo=UTC)
    resource = EvaluationTaskResource(
        student_person_id=uuid4(),
        enrollment_id=uuid4(),
        cohort_id=uuid4(),
        project_id=uuid4(),
        class_id=uuid4(),
    )
    access = RecordingAccess(EvaluationDecision(allowed, "test"))

    result = EvaluationTaskAccessService(access=access).task_allowed(
        account_id=account_id,
        permission="evaluation_media.upload",
        resource=resource,
        occurred_at=occurred_at,
    )

    assert result is allowed
    assert access.received == (
        account_id,
        "evaluation_media.upload",
        resource.as_evaluation_resource(),
        occurred_at,
    )


@dataclass(frozen=True)
class FormalUploadScenario:
    accounts: dict[str, UserAccount]
    people: dict[str, Person]
    cohort: Cohort
    teaching_class: TeachingClass
    admission_case: AdmissionCase
    enrollment: Enrollment


def _person(name: str) -> Person:
    token = uuid4().hex
    return Person(
        full_name=name,
        identity_ciphertext=f"task-access:{token}",
        identity_digest=(token * 2)[:64],
        phone_ciphertext=None,
        phone_digest=None,
        email=None,
        sex="female",
        date_of_birth=date(1990, 1, 2),
    )


def _account(person: Person, key: str) -> UserAccount:
    token = uuid4().hex
    return UserAccount(
        person_id=person.id,
        login_phone_digest=(token * 2)[:64],
        email_normalized=f"task-access-{key}-{token[:8]}@example.test",
        password_hash="task-access-test-password-hash",
        status="active",
        force_password_change=False,
    )


@pytest.fixture
def formal_upload_scenario(db: Session) -> FormalUploadScenario:
    people = {
        key: _person(name)
        for key, name in {
            "admin": "上传授权管理员",
            "student": "上传授权学员",
            "guardian": "上传授权监护人",
            "coach": "上传授权教练",
            "staff": "上传授权生活老师",
        }.items()
    }
    db.add_all(people.values())
    db.flush()
    db.add(
        StudentProfile(
            person_id=people["student"].id,
            student_number=f"UTA-{uuid4().hex[:12]}",
            status="active",
        )
    )
    db.add_all(
        [
            StaffProfile(person_id=people["admin"].id, staff_type="administration"),
            StaffProfile(person_id=people["coach"].id, staff_type="coach"),
            StaffProfile(person_id=people["staff"].id, staff_type="life_teacher"),
        ]
    )
    db.flush()

    accounts = {key: _account(person, key) for key, person in people.items()}
    db.add_all(accounts.values())
    db.flush()
    role_keys = {
        "admin": "admin",
        "student": "student",
        "guardian": "guardian",
        "coach": "coach",
        "staff": "staff",
    }
    db.add_all(
        UserRole(user_id=accounts[key].id, role_id=ROLES[role_key].id)
        for key, role_key in role_keys.items()
    )

    cohort = Cohort(
        name=f"上传授权营期-{uuid4().hex[:8]}",
        starts_at=ACCESS_COHORT_START,
        ends_at=ACCESS_COHORT_END,
        default_check_in_at=ACCESS_COHORT_START,
        default_check_out_at=ACCESS_COHORT_END,
        status="active",
        created_by_user_id=accounts["admin"].id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[0].id,
        name=f"上传授权班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        is_active=True,
        created_by_user_id=accounts["admin"].id,
    )
    db.add(teaching_class)
    db.flush()

    admission_case = AdmissionCase(
        student_person_id=people["student"].id,
        status="ready",
        agreed_tuition_cents=0,
        created_by_user_id=accounts["admin"].id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=people["student"].id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=0,
        status="active",
        confirmed_at=ACCESS_COHORT_START - timedelta(days=30),
        created_by_user_id=accounts["admin"].id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id

    grant_start = ACCESS_COHORT_START - timedelta(days=8)
    grant_end = ACCESS_COHORT_END + timedelta(days=8)
    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian"].id,
            student_person_id=people["student"].id,
            relationship="mother",
            is_primary=True,
            valid_from=grant_start.astimezone(SHANGHAI).date(),
            valid_until=grant_end.astimezone(SHANGHAI).date(),
        )
    )
    db.add(
        CoachAssignment(
            class_id=teaching_class.id,
            coach_person_id=people["coach"].id,
            assignment_type="primary",
            valid_from=grant_start,
            valid_until=grant_end,
            created_by_user_id=accounts["admin"].id,
        )
    )
    staff_assignment = CohortStaffAssignment(
        cohort_id=cohort.id,
        role_id=LIFE_TEACHER_ROLE_ID,
        staff_person_id=people["staff"].id,
        project_id=teaching_class.project_id,
        role_name_snapshot="生活老师",
        role_category_snapshot="life_service",
        valid_from=grant_start,
        valid_until=grant_end,
        created_by_user_id=accounts["admin"].id,
    )
    db.add(staff_assignment)
    db.flush()

    upload_permission = db.scalar(
        select(Permission).where(Permission.key == "evaluation_media.upload")
    )
    assert upload_permission is not None
    db.add(
        CohortStaffAssignmentPermission(
            assignment_id=staff_assignment.id,
            permission_id=upload_permission.id,
            created_by_user_id=accounts["admin"].id,
        )
    )
    for key, scope_kind, scope_id in (
        ("student", "self", None),
        ("guardian", "family", None),
        ("coach", "assigned_class", teaching_class.id),
    ):
        db.add(
            UserPermissionOverride(
                user_id=accounts[key].id,
                permission_id=upload_permission.id,
                effect="allow",
                scope_kind=scope_kind,
                scope_id=scope_id,
                valid_from=grant_start,
                valid_until=grant_end,
                created_by_user_id=accounts["admin"].id,
                grant_reason="system evaluation upload access test",
            )
        )
    db.flush()
    return FormalUploadScenario(
        accounts=accounts,
        people=people,
        cohort=cohort,
        teaching_class=teaching_class,
        admission_case=admission_case,
        enrollment=enrollment,
    )


@pytest.mark.parametrize(
    ("actor_key", "expected_source", "requires_reason"),
    [
        ("student", "self", False),
        ("guardian", "family", False),
        ("coach", "coach", False),
        ("staff", "staff", False),
        ("admin", "administrator", True),
    ],
)
def test_explicit_target_allows_only_current_formal_relationship_sources(
    db: Session,
    formal_upload_scenario: FormalUploadScenario,
    actor_key: str,
    expected_source: str,
    requires_reason: bool,
) -> None:
    scenario = formal_upload_scenario

    target = EvaluationTaskAccessService(db).require_upload_target(
        account_id=scenario.accounts[actor_key].id,
        enrollment_id=scenario.enrollment.id,
        project_id=scenario.teaching_class.project_id,
        occurred_at=ACCESS_NOW,
    )

    assert target.resource == EvaluationTaskResource(
        student_person_id=scenario.people["student"].id,
        enrollment_id=scenario.enrollment.id,
        cohort_id=scenario.cohort.id,
        project_id=scenario.teaching_class.project_id,
        class_id=scenario.teaching_class.id,
    )
    assert target.source == expected_source
    assert target.requires_business_reason is requires_reason
    assert target.upload_window == upload_window_for_cohort(
        starts_at=scenario.cohort.starts_at,
        ends_at=scenario.cohort.ends_at,
    )


def test_administrator_upload_requires_a_trimmed_business_reason(
    db: Session,
    formal_upload_scenario: FormalUploadScenario,
) -> None:
    scenario = formal_upload_scenario
    service = EvaluationTaskAccessService(db)
    target = service.require_upload_target(
        account_id=scenario.accounts["admin"].id,
        enrollment_id=scenario.enrollment.id,
        project_id=scenario.teaching_class.project_id,
        occurred_at=ACCESS_NOW,
    )

    with pytest.raises(ValueError, match="business reason"):
        service.require_delegated_upload_reason(target, None)
    with pytest.raises(ValueError, match="business reason"):
        service.require_delegated_upload_reason(target, "   ")
    assert (
        service.require_delegated_upload_reason(target, "  管理员协助上传  ")
        == "管理员协助上传"
    )


def test_completed_formal_enrollment_remains_eligible_in_post_cohort_window(
    db: Session,
    formal_upload_scenario: FormalUploadScenario,
) -> None:
    scenario = formal_upload_scenario
    scenario.enrollment.status = "completed"
    scenario.enrollment.completed_at = scenario.cohort.ends_at
    scenario.cohort.status = "completed"
    occurred_at = scenario.cohort.ends_at + timedelta(days=3)
    db.flush()

    target = EvaluationTaskAccessService(db).require_upload_target(
        account_id=scenario.accounts["student"].id,
        enrollment_id=scenario.enrollment.id,
        project_id=scenario.teaching_class.project_id,
        occurred_at=occurred_at,
    )

    assert target.resource.enrollment_id == scenario.enrollment.id


@pytest.mark.parametrize("failure", ["informal", "wrong_project", "inactive_class"])
def test_invalid_target_variants_are_the_same_private_not_found(
    db: Session,
    formal_upload_scenario: FormalUploadScenario,
    failure: str,
) -> None:
    scenario = formal_upload_scenario
    project_id = scenario.teaching_class.project_id
    if failure == "informal":
        scenario.admission_case.status = "ready"
        scenario.admission_case.current_enrollment_id = None
    elif failure == "wrong_project":
        project_id = INITIAL_PROJECTS[1].id
    else:
        scenario.teaching_class.is_active = False
    db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        EvaluationTaskAccessService(db).require_upload_target(
            account_id=scenario.accounts["student"].id,
            enrollment_id=scenario.enrollment.id,
            project_id=project_id,
            occurred_at=ACCESS_NOW,
        )


def test_family_relationship_end_revokes_target_immediately(
    db: Session,
    formal_upload_scenario: FormalUploadScenario,
) -> None:
    scenario = formal_upload_scenario
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id
            == scenario.people["guardian"].id,
            GuardianRelationship.student_person_id == scenario.people["student"].id,
        )
    )
    assert relationship is not None
    relationship.ended_at = ACCESS_NOW
    relationship.ended_by_user_id = scenario.accounts["admin"].id
    relationship.end_reason = "relationship ended for access test"
    db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        EvaluationTaskAccessService(db).require_upload_target(
            account_id=scenario.accounts["guardian"].id,
            enrollment_id=scenario.enrollment.id,
            project_id=scenario.teaching_class.project_id,
            occurred_at=ACCESS_NOW,
        )
