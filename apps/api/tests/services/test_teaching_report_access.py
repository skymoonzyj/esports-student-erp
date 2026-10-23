from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.errors import EvaluationResourceNotFound
from app.evaluations.report_access import TeachingReportAccessService
from app.evaluations.report_models import StudentReport
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.identity.models import GuardianRelationship, Person, StaffProfile, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS
from tests.account_identifiers import add_test_primary_login_identifier

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)
COHORT_START = datetime(2026, 8, 1, 2, tzinfo=UTC)  # 2026-08-01 10:00+08
COHORT_END = datetime(2026, 8, 21, 10, tzinfo=UTC)  # 2026-08-21 18:00+08


def _person(name: str) -> Person:
    token = uuid4().hex
    return Person(
        full_name=name,
        identity_ciphertext=f"report-access:{token}",
        identity_digest=(token * 2)[:64],
        sex="female",
        date_of_birth=date(1990, 1, 2),
    )


def _account(person: Person, key: str) -> UserAccount:
    token = uuid4().hex
    return UserAccount(
        person_id=person.id,
        login_phone_digest=(token * 2)[:64],
        email_normalized=f"report-{key}-{token[:8]}@example.test",
        password_hash="report-access-test-password-hash",
        status="active",
        force_password_change=False,
    )


@dataclass(frozen=True)
class ReportAccessScenario:
    db: Session
    people: dict[str, Person]
    accounts: dict[str, UserAccount]
    cohort: Cohort
    teaching_class: TeachingClass
    enrollment: Enrollment
    primary_assignment: CoachAssignment
    assistant_assignment: CoachAssignment
    staff_assignment: CohortStaffAssignment

    @property
    def permission(self) -> Permission:
        permission = self.db.scalar(
            select(Permission).where(Permission.key == "student_reports.write")
        )
        assert permission is not None
        return permission

    def allow_account(self, key: str) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.accounts[key].id,
                permission_id=self.permission.id,
                effect="allow",
                scope_kind="assigned_class",
                scope_id=self.teaching_class.id,
                valid_from=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(days=30),
                created_by_user_id=self.accounts["admin"].id,
                grant_reason="教学评语测试授权",
            )
        )
        self.db.flush()

    def deny_account(self, key: str) -> None:
        self.db.add(
            UserPermissionOverride(
                user_id=self.accounts[key].id,
                permission_id=self.permission.id,
                effect="deny",
                scope_kind="enrollment",
                scope_id=self.enrollment.id,
                valid_from=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(days=30),
                created_by_user_id=self.accounts["admin"].id,
                grant_reason="教学评语测试拒绝",
            )
        )
        self.db.flush()

    def deny_evaluation_center(self, key: str) -> None:
        permission = self.db.scalar(
            select(Permission).where(Permission.key == "evaluation_center.access")
        )
        assert permission is not None
        self.db.add(
            UserPermissionOverride(
                user_id=self.accounts[key].id,
                permission_id=permission.id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
                created_by_user_id=self.accounts["admin"].id,
            )
        )
        self.db.flush()

    def attach_staff_permission(self) -> None:
        self.db.add(
            CohortStaffAssignmentPermission(
                assignment_id=self.staff_assignment.id,
                permission_id=self.permission.id,
                created_by_user_id=self.accounts["admin"].id,
            )
        )
        self.db.flush()


def create_report_access_scenario(
    db: Session,
    *,
    add_primary_identifiers: bool = True,
) -> ReportAccessScenario:
    people = {
        key: _person(name)
        for key, name in {
            "admin": "评语授权管理员",
            "student": "评语授权学员",
            "guardian": "评语授权监护人",
            "primary": "评语主教练",
            "assistant": "评语助教",
            "staff": "评语生活老师",
            "outsider": "评语无关员工",
        }.items()
    }
    db.add_all(people.values())
    db.flush()
    db.add(StudentProfile(person_id=people["student"].id, student_number=f"RPT-{uuid4().hex[:12]}", status="active"))
    for key, staff_type in {
        "admin": "administration",
        "primary": "coach",
        "assistant": "coach",
        "staff": "life_teacher",
        "outsider": "other",
    }.items():
        db.add(StaffProfile(person_id=people[key].id, staff_type=staff_type))
    db.flush()

    accounts = {key: _account(person, key) for key, person in people.items()}
    db.add_all(accounts.values())
    db.flush()
    if add_primary_identifiers:
        for account in accounts.values():
            add_test_primary_login_identifier(db, account)
    for key, role_key in {
        "admin": "admin",
        "student": "student",
        "guardian": "guardian",
        "primary": "coach",
        "assistant": "coach",
        "staff": "life_teacher",
        "outsider": "staff",
    }.items():
        db.add(UserRole(user_id=accounts[key].id, role_id=ROLES[role_key].id))

    cohort = Cohort(
        name=f"评语授权营期-{uuid4().hex[:8]}",
        starts_at=COHORT_START,
        ends_at=COHORT_END,
        default_check_in_at=COHORT_START,
        default_check_out_at=COHORT_END,
        status="active",
        created_by_user_id=accounts["admin"].id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[0].id,
        name=f"评语授权班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        created_by_user_id=accounts["admin"].id,
    )
    db.add(teaching_class)
    db.flush()
    admission_case = AdmissionCase(
        student_person_id=people["student"].id,
        status="ready",
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
        confirmed_at=COHORT_START - timedelta(days=1),
        created_by_user_id=accounts["admin"].id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    db.add(
        GuardianRelationship(
            guardian_person_id=people["guardian"].id,
            student_person_id=people["student"].id,
            relationship="母亲",
            is_primary=True,
            valid_from=date(2026, 8, 1),
        )
    )

    primary_assignment = CoachAssignment(
        class_id=teaching_class.id,
        coach_person_id=people["primary"].id,
        assignment_type="primary",
        valid_from=COHORT_START,
        valid_until=COHORT_END + timedelta(days=1),
        created_by_user_id=accounts["admin"].id,
    )
    assistant_assignment = CoachAssignment(
        class_id=teaching_class.id,
        coach_person_id=people["assistant"].id,
        assignment_type="assistant",
        valid_from=COHORT_START,
        valid_until=COHORT_END + timedelta(days=1),
        created_by_user_id=accounts["admin"].id,
    )
    staff_assignment = CohortStaffAssignment(
        cohort_id=cohort.id,
        role_id=UUID("41000000-0000-4000-8000-000000000001"),
        staff_person_id=people["staff"].id,
        project_id=teaching_class.project_id,
        role_name_snapshot="生活老师",
        role_category_snapshot="life_service",
        valid_from=COHORT_START,
        valid_until=COHORT_END + timedelta(days=1),
        created_by_user_id=accounts["admin"].id,
    )
    db.add_all([primary_assignment, assistant_assignment, staff_assignment])
    db.flush()
    return ReportAccessScenario(
        db=db,
        people=people,
        accounts=accounts,
        cohort=cohort,
        teaching_class=teaching_class,
        enrollment=enrollment,
        primary_assignment=primary_assignment,
        assistant_assignment=assistant_assignment,
        staff_assignment=staff_assignment,
    )


@pytest.fixture
def report_access_scenario(db: Session) -> ReportAccessScenario:
    return create_report_access_scenario(db)


def _slots(scenario: ReportAccessScenario, key: str, *, occurred_at: datetime = NOW):
    return TeachingReportAccessService(scenario.db).author_slots(
        account_id=scenario.accounts[key].id,
        enrollment_id=scenario.enrollment.id,
        occurred_at=occurred_at,
    )


def test_primary_coach_is_the_only_default_writer_and_periods_have_server_actions(
    report_access_scenario: ReportAccessScenario,
) -> None:
    slots = _slots(report_access_scenario, "primary")

    daily = [slot for slot in slots if slot.period.kind == "daily"]
    weekly = [slot for slot in slots if slot.period.kind == "weekly"]
    assert [slot.period.sequence for slot in daily] == list(range(1, 10))
    assert [slot.period.sequence for slot in weekly] == [1, 2]
    assert all(slot.author.role_key == "primary_coach" for slot in slots)
    assert all(slot.draft_allowed for slot in slots)
    assert weekly[0].submit_allowed is True
    assert weekly[1].submit_allowed is False

    for key in ("assistant", "staff", "outsider"):
        with pytest.raises(EvaluationResourceNotFound):
            _slots(report_access_scenario, key)


def test_assistant_requires_a_live_scoped_account_grant(
    report_access_scenario: ReportAccessScenario,
) -> None:
    report_access_scenario.allow_account("assistant")

    slots = _slots(report_access_scenario, "assistant")

    assert slots
    assert all(slot.author.role_key == "assistant_coach" for slot in slots)
    assert all(
        slot.author.coach_assignment_id
        == report_access_scenario.assistant_assignment.id
        for slot in slots
    )


def test_explicit_deny_overrides_the_primary_coach_default(
    report_access_scenario: ReportAccessScenario,
) -> None:
    report_access_scenario.deny_account("primary")

    with pytest.raises(EvaluationResourceNotFound):
        _slots(report_access_scenario, "primary")


def test_evaluation_center_revocation_also_blocks_the_primary_default(
    report_access_scenario: ReportAccessScenario,
) -> None:
    report_access_scenario.deny_evaluation_center("primary")

    with pytest.raises(EvaluationResourceNotFound):
        _slots(report_access_scenario, "primary")


def test_staff_assignment_permission_extends_only_overlapping_periods(
    report_access_scenario: ReportAccessScenario,
) -> None:
    report_access_scenario.staff_assignment.valid_until = datetime(2026, 8, 7, 16, tzinfo=UTC)
    report_access_scenario.attach_staff_permission()

    slots = _slots(
        report_access_scenario,
        "staff",
        occurred_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )

    assert [slot.period.sequence for slot in slots if slot.period.kind == "daily"] == list(
        range(1, 8)
    )
    assert [slot.period.sequence for slot in slots if slot.period.kind == "weekly"] == [1]
    assert all(slot.author.staff_assignment_id == report_access_scenario.staff_assignment.id for slot in slots)


@pytest.mark.parametrize("actor_key", ("primary", "staff"))
def test_future_assignment_cannot_write_until_service_begins(
    report_access_scenario: ReportAccessScenario,
    actor_key: str,
) -> None:
    starts_at = NOW + timedelta(hours=1)
    if actor_key == "primary":
        report_access_scenario.primary_assignment.valid_from = starts_at
    else:
        report_access_scenario.staff_assignment.valid_from = starts_at
        report_access_scenario.attach_staff_permission()
    report_access_scenario.db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        _slots(report_access_scenario, actor_key, occurred_at=NOW)

    slots = _slots(
        report_access_scenario,
        actor_key,
        occurred_at=starts_at,
    )
    assert slots


def test_removing_staff_permission_revokes_the_next_request(
    report_access_scenario: ReportAccessScenario,
) -> None:
    report_access_scenario.attach_staff_permission()
    assert _slots(report_access_scenario, "staff")
    report_access_scenario.db.query(CohortStaffAssignmentPermission).filter_by(
        assignment_id=report_access_scenario.staff_assignment.id,
        permission_id=report_access_scenario.permission.id,
    ).delete(synchronize_session=False)
    report_access_scenario.db.flush()

    with pytest.raises(EvaluationResourceNotFound):
        _slots(report_access_scenario, "staff")


@pytest.mark.parametrize("failure", ["disabled_account", "deleted_author", "inactive_student"])
def test_ineligible_or_disabled_resources_are_private_and_leave_no_writes(
    report_access_scenario: ReportAccessScenario,
    failure: str,
) -> None:
    if failure == "disabled_account":
        report_access_scenario.accounts["primary"].status = "disabled"
    elif failure == "deleted_author":
        report_access_scenario.people["primary"].deleted_at = NOW
    else:
        profile = report_access_scenario.db.get(
            StudentProfile,
            report_access_scenario.people["student"].id,
        )
        assert profile is not None
        profile.status = "inactive"
    report_access_scenario.db.flush()
    before = (
        report_access_scenario.db.scalar(select(func.count(StudentReport.id))),
        report_access_scenario.db.scalar(select(func.count(AuditEvent.id))),
    )

    with pytest.raises(EvaluationResourceNotFound):
        _slots(report_access_scenario, "primary")

    assert (
        report_access_scenario.db.scalar(select(func.count(StudentReport.id))),
        report_access_scenario.db.scalar(select(func.count(AuditEvent.id))),
    ) == before


def test_missing_and_wrong_enrollment_have_the_same_private_failure(
    report_access_scenario: ReportAccessScenario,
) -> None:
    service = TeachingReportAccessService(report_access_scenario.db)

    for enrollment_id in (uuid4(), report_access_scenario.enrollment.admission_case_id):
        with pytest.raises(EvaluationResourceNotFound, match="evaluation resource not found"):
            service.author_slots(
                account_id=report_access_scenario.accounts["primary"].id,
                enrollment_id=enrollment_id,
                occurred_at=NOW,
            )
