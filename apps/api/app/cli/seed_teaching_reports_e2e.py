from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_ACCOUNT_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_OTHER_GUARDIAN_ACCOUNT_ID,
    E2E_OTHER_GUARDIAN_PERSON_ID,
    E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
    E2ESeedRefused,
    _require_e2e_environment,
    _seed_account,
    _seed_person,
    _upsert_seed_phone_identifier,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.evaluations.report_models import StudentReport
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import GuardianRelationship, Person, StaffProfile, StudentProfile
from app.identity.normalization import normalize_email
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

AUTHORIZED_ASSISTANT_PERSON_ID = UUID("e5000000-0000-4000-8000-000000000001")
UNAUTHORIZED_ASSISTANT_PERSON_ID = UUID("e5000000-0000-4000-8000-000000000002")
AUTHORIZED_ASSISTANT_ACCOUNT_ID = UUID("e5010000-0000-4000-8000-000000000001")
UNAUTHORIZED_ASSISTANT_ACCOUNT_ID = UUID("e5010000-0000-4000-8000-000000000002")
TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID = UUID(
    "e5020000-0000-4000-8000-000000000001"
)

TEACHING_REPORT_COHORT_IDS = (
    UUID("e5100000-0000-4000-8000-000000000001"),
    UUID("e5100000-0000-4000-8000-000000000002"),
    UUID("e5100000-0000-4000-8000-000000000003"),
)
TEACHING_REPORT_CLASS_IDS = (
    UUID("e5110000-0000-4000-8000-000000000001"),
    UUID("e5110000-0000-4000-8000-000000000002"),
    UUID("e5110000-0000-4000-8000-000000000003"),
)
TEACHING_REPORT_ADMISSION_CASE_IDS = (
    UUID("e5120000-0000-4000-8000-000000000001"),
    UUID("e5120000-0000-4000-8000-000000000002"),
    UUID("e5120000-0000-4000-8000-000000000003"),
)
TEACHING_REPORT_ENROLLMENT_IDS = (
    UUID("e5130000-0000-4000-8000-000000000001"),
    UUID("e5130000-0000-4000-8000-000000000002"),
    UUID("e5130000-0000-4000-8000-000000000003"),
)
TEACHING_REPORT_COACH_ASSIGNMENT_IDS = (
    UUID("e5140000-0000-4000-8000-000000000001"),
    UUID("e5140000-0000-4000-8000-000000000002"),
    UUID("e5140000-0000-4000-8000-000000000003"),
    UUID("e5140000-0000-4000-8000-000000000004"),
    UUID("e5140000-0000-4000-8000-000000000005"),
)
TEACHING_REPORT_PERMISSION_OVERRIDE_IDS = (
    UUID("e5150000-0000-4000-8000-000000000001"),
    UUID("e5150000-0000-4000-8000-000000000002"),
)

AUTHORIZED_ASSISTANT_PHONE = E2E_CREDENTIALS[
    "teaching_authorized_assistant"
].identifier
AUTHORIZED_ASSISTANT_PASSWORD = E2E_CREDENTIALS[
    "teaching_authorized_assistant"
].password
UNAUTHORIZED_ASSISTANT_PHONE = E2E_CREDENTIALS[
    "teaching_unauthorized_assistant"
].identifier
UNAUTHORIZED_ASSISTANT_PASSWORD = E2E_CREDENTIALS[
    "teaching_unauthorized_assistant"
].password

_LOL_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_COHORT_NAMES = (
    "E2E教学评语21日营",
    "E2E教学评语10日营",
    "E2E教学评语跨UTC日界营",
)
_CLASS_NAMES = (
    "E2E教学评语21日班",
    "E2E教学评语10日班",
    "E2E教学评语UTC日界班",
)
_CREDENTIAL_REFS = (
    "ADMIN",
    "PRIMARY_COACH",
    "AUTHORIZED_ASSISTANT",
    "UNAUTHORIZED_ASSISTANT",
    "REVIEWER",
    "GUARDIAN_ONE",
    "GUARDIAN_TWO",
    "STUDENT",
)


@dataclass(frozen=True)
class TeachingReportsE2ESeedResult:
    cohort_ids: tuple[UUID, UUID, UUID]
    class_ids: tuple[UUID, UUID, UUID]
    enrollment_ids: tuple[UUID, UUID, UUID]
    authorized_assistant_account_id: UUID
    unauthorized_assistant_account_id: UUID
    reviewer_account_id: UUID
    student_person_id: UUID
    guardian_person_ids: tuple[UUID, UUID]


@dataclass(frozen=True)
class _CohortFixture:
    id: UUID
    class_id: UUID
    admission_case_id: UUID
    enrollment_id: UUID
    name: str
    class_name: str
    starts_at: datetime
    ends_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _refuse_missing_fixture() -> None:
    raise E2ESeedRefused("Teaching report E2E seed requires canonical E2E fixtures")


def _refuse_collision() -> None:
    raise E2ESeedRefused("Teaching report E2E fixture is not canonical")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


def _require_base_fixtures(db: Session) -> None:
    account_people = {
        E2E_ADMIN_ACCOUNT_ID: None,
        E2E_COACH_ACCOUNT_ID: E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_ACCOUNT_ID: E2E_LIFE_TEACHER_PERSON_ID,
        E2E_GUARDIAN_ACCOUNT_ID: E2E_GUARDIAN_PERSON_ID,
        E2E_OTHER_GUARDIAN_ACCOUNT_ID: E2E_OTHER_GUARDIAN_PERSON_ID,
        E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID: E2E_SECOND_BOUND_STUDENT_ID,
    }
    accounts = {
        row.id: row
        for row in db.scalars(
            select(UserAccount).where(UserAccount.id.in_(account_people))
        )
    }
    if set(accounts) != set(account_people):
        _refuse_missing_fixture()
    if any(
        expected_person_id is not None
        and accounts[account_id].person_id != expected_person_id
        for account_id, expected_person_id in account_people.items()
    ):
        _refuse_missing_fixture()

    expected_people = {
        E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_PERSON_ID,
        E2E_GUARDIAN_PERSON_ID,
        E2E_OTHER_GUARDIAN_PERSON_ID,
        E2E_SECOND_BOUND_STUDENT_ID,
    }
    if set(db.scalars(select(Person.id).where(Person.id.in_(expected_people)))) != expected_people:
        _refuse_missing_fixture()
    if db.get(StaffProfile, E2E_COACH_PERSON_ID) is None:
        _refuse_missing_fixture()
    if db.get(StaffProfile, E2E_LIFE_TEACHER_PERSON_ID) is None:
        _refuse_missing_fixture()
    if db.get(StudentProfile, E2E_SECOND_BOUND_STUDENT_ID) is None:
        _refuse_missing_fixture()

    first_relationship = db.get(
        GuardianRelationship,
        E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    if (
        first_relationship is None
        or first_relationship.guardian_person_id != E2E_GUARDIAN_PERSON_ID
        or first_relationship.student_person_id != E2E_SECOND_BOUND_STUDENT_ID
    ):
        _refuse_missing_fixture()

    if db.get(GameProject, _LOL_PROJECT_ID) is None:
        _refuse_missing_fixture()
    required_ids = {
        PERMISSIONS["student_reports.write"].id,
        PERMISSIONS["student_reports.review"].id,
    }
    if set(db.scalars(select(Permission.id).where(Permission.id.in_(required_ids)))) != required_ids:
        _refuse_missing_fixture()
    if db.get(UserRole, (E2E_COACH_ACCOUNT_ID, ROLES["coach"].id)) is None:
        _refuse_missing_fixture()


def _cohort_fixtures(occurred_at: datetime) -> tuple[_CohortFixture, ...]:
    local_date = occurred_at.astimezone(_SHANGHAI).date()
    common_end = datetime.combine(local_date, time(18, 0), tzinfo=_SHANGHAI)
    utc_boundary_date = local_date - timedelta(days=1)
    starts = (
        datetime.combine(
            local_date - timedelta(days=20),
            time(10, 0),
            tzinfo=_SHANGHAI,
        ),
        datetime.combine(
            local_date - timedelta(days=9),
            time(10, 0),
            tzinfo=_SHANGHAI,
        ),
        datetime.combine(utc_boundary_date, time(0, 30), tzinfo=_SHANGHAI),
    )
    ends = (
        common_end,
        common_end,
        datetime.combine(utc_boundary_date, time(23, 30), tzinfo=_SHANGHAI),
    )
    return tuple(
        _CohortFixture(
            id=cohort_id,
            class_id=class_id,
            admission_case_id=case_id,
            enrollment_id=enrollment_id,
            name=name,
            class_name=class_name,
            starts_at=starts[index],
            ends_at=ends[index],
        )
        for index, (
            cohort_id,
            class_id,
            case_id,
            enrollment_id,
            name,
            class_name,
        ) in enumerate(
            zip(
                TEACHING_REPORT_COHORT_IDS,
                TEACHING_REPORT_CLASS_IDS,
                TEACHING_REPORT_ADMISSION_CASE_IDS,
                TEACHING_REPORT_ENROLLMENT_IDS,
                _COHORT_NAMES,
                _CLASS_NAMES,
                strict=True,
            )
        )
    )


def _expected_assignment_fixtures(
    cohorts: tuple[_CohortFixture, ...],
) -> tuple[tuple[UUID, UUID, UUID, str], ...]:
    return (
        (
            TEACHING_REPORT_COACH_ASSIGNMENT_IDS[0],
            cohorts[0].class_id,
            E2E_COACH_PERSON_ID,
            "primary",
        ),
        (
            TEACHING_REPORT_COACH_ASSIGNMENT_IDS[1],
            cohorts[0].class_id,
            AUTHORIZED_ASSISTANT_PERSON_ID,
            "assistant",
        ),
        (
            TEACHING_REPORT_COACH_ASSIGNMENT_IDS[2],
            cohorts[0].class_id,
            UNAUTHORIZED_ASSISTANT_PERSON_ID,
            "assistant",
        ),
        (
            TEACHING_REPORT_COACH_ASSIGNMENT_IDS[3],
            cohorts[1].class_id,
            E2E_COACH_PERSON_ID,
            "primary",
        ),
        (
            TEACHING_REPORT_COACH_ASSIGNMENT_IDS[4],
            cohorts[2].class_id,
            E2E_COACH_PERSON_ID,
            "primary",
        ),
    )


def _preflight_owned_rows(
    db: Session,
    *,
    lookup: LookupDigest,
    cohorts: tuple[_CohortFixture, ...],
) -> None:
    people = (
        (
            AUTHORIZED_ASSISTANT_PERSON_ID,
            "E2E授权助教",
            AUTHORIZED_ASSISTANT_PHONE,
            "19920201",
            131,
        ),
        (
            UNAUTHORIZED_ASSISTANT_PERSON_ID,
            "E2E未授权助教",
            UNAUTHORIZED_ASSISTANT_PHONE,
            "19920301",
            132,
        ),
    )
    for person_id, full_name, phone, identity_birth, identity_sequence in people:
        existing = db.get(Person, person_id)
        identity_body = f"110105{identity_birth}{identity_sequence:03d}"
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
        checksum = sum(int(digit) * weight for digit, weight in zip(identity_body, weights))
        identity_number = identity_body + checks[checksum % 11]
        if existing is not None and (
            existing.full_name != full_name
            or existing.phone_digest != lookup.digest(phone)
            or existing.identity_digest != lookup.digest(identity_number)
        ):
            _refuse_collision()
        for digest_field, digest in (
            (Person.phone_digest, lookup.digest(phone)),
            (Person.identity_digest, lookup.digest(identity_number)),
        ):
            collision = db.scalar(
                select(Person.id).where(
                    digest_field == digest,
                    Person.id != person_id,
                )
            )
            if collision is not None:
                _refuse_collision()

    for account_id, person_id, phone in (
        (
            AUTHORIZED_ASSISTANT_ACCOUNT_ID,
            AUTHORIZED_ASSISTANT_PERSON_ID,
            AUTHORIZED_ASSISTANT_PHONE,
        ),
        (
            UNAUTHORIZED_ASSISTANT_ACCOUNT_ID,
            UNAUTHORIZED_ASSISTANT_PERSON_ID,
            UNAUTHORIZED_ASSISTANT_PHONE,
        ),
    ):
        account = db.get(UserAccount, account_id)
        normalized_phone = normalize_login_identifier(phone)
        stored_identifier_digest = db.scalar(
            select(AccountLoginIdentifier.identifier_digest).where(
                AccountLoginIdentifier.account_id == account_id,
                AccountLoginIdentifier.kind == "phone",
            )
        )
        if account is not None and (
            account.person_id != person_id
            or stored_identifier_digest
            != login_identifier_digest(lookup, normalized_phone)
        ):
            _refuse_collision()

    for fixture in cohorts:
        cohort = db.get(Cohort, fixture.id)
        if cohort is not None and cohort.name != fixture.name:
            _refuse_collision()
        teaching_class = db.get(TeachingClass, fixture.class_id)
        if teaching_class is not None and (
            teaching_class.cohort_id != fixture.id
            or teaching_class.project_id != _LOL_PROJECT_ID
            or teaching_class.name != fixture.class_name
        ):
            _refuse_collision()
        admission_case = db.get(AdmissionCase, fixture.admission_case_id)
        if admission_case is not None and (
            admission_case.student_person_id != E2E_SECOND_BOUND_STUDENT_ID
        ):
            _refuse_collision()
        enrollment = db.get(Enrollment, fixture.enrollment_id)
        if enrollment is not None and (
            enrollment.admission_case_id != fixture.admission_case_id
            or enrollment.student_person_id != E2E_SECOND_BOUND_STUDENT_ID
            or enrollment.cohort_id != fixture.id
            or enrollment.class_id != fixture.class_id
        ):
            _refuse_collision()

    for assignment_id, class_id, person_id, assignment_type in (
        _expected_assignment_fixtures(cohorts)
    ):
        assignment = db.get(CoachAssignment, assignment_id)
        if assignment is not None and (
            assignment.class_id != class_id
            or assignment.coach_person_id != person_id
            or assignment.assignment_type != assignment_type
        ):
            _refuse_collision()

    relationship = db.get(
        GuardianRelationship,
        TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    if relationship is not None and (
        relationship.guardian_person_id != E2E_OTHER_GUARDIAN_PERSON_ID
        or relationship.student_person_id != E2E_SECOND_BOUND_STUDENT_ID
    ):
        _refuse_collision()

    expected_overrides = (
        (
            TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[0],
            AUTHORIZED_ASSISTANT_ACCOUNT_ID,
            PERMISSIONS["student_reports.write"].id,
        ),
        (
            TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[1],
            E2E_LIFE_TEACHER_ACCOUNT_ID,
            PERMISSIONS["student_reports.review"].id,
        ),
    )
    for override_id, user_id, permission_id in expected_overrides:
        override = db.get(UserPermissionOverride, override_id)
        expected_scope_kind = (
            "assigned_class"
            if override_id == TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[0]
            else "project"
        )
        expected_scope_id = (
            TEACHING_REPORT_CLASS_IDS[0]
            if override_id == TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[0]
            else _LOL_PROJECT_ID
        )
        if override is not None and (
            override.user_id != user_id
            or override.permission_id != permission_id
            or override.scope_kind != expected_scope_kind
            or override.scope_id != expected_scope_id
        ):
            _refuse_collision()

    if db.scalar(select(StudentReport.id).limit(1)) is not None:
        raise E2ESeedRefused(
            "Teaching report E2E seed requires a fresh canonical fixture"
        )


def _clear_stale_report_idempotency(db: Session) -> None:
    db.execute(
        delete(EvaluationIdempotencyKey).where(
            EvaluationIdempotencyKey.scope.like("teaching_report.%")
        )
    )


def _restore_base_access(db: Session) -> None:
    for person_id in (
        E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_PERSON_ID,
        E2E_GUARDIAN_PERSON_ID,
        E2E_OTHER_GUARDIAN_PERSON_ID,
        E2E_SECOND_BOUND_STUDENT_ID,
    ):
        person = db.get(Person, person_id)
        assert person is not None
        person.deleted_at = None
    for account_id in (
        E2E_COACH_ACCOUNT_ID,
        E2E_LIFE_TEACHER_ACCOUNT_ID,
        E2E_GUARDIAN_ACCOUNT_ID,
        E2E_OTHER_GUARDIAN_ACCOUNT_ID,
        E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID,
    ):
        account = db.get(UserAccount, account_id)
        assert account is not None
        account.status = "active"
        account.failed_login_count = 0
        account.locked_until = None
    student = db.get(StudentProfile, E2E_SECOND_BOUND_STUDENT_ID)
    assert student is not None
    student.status = "active"
    relationship = db.get(
        GuardianRelationship,
        E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    assert relationship is not None
    relationship.is_primary = True
    relationship.valid_from = date(2026, 1, 1)
    relationship.valid_until = None
    relationship.ended_at = None
    relationship.ended_by_user_id = None
    relationship.end_reason = None


def _upsert_assistant_identity(
    db: Session,
    *,
    person_id: UUID,
    account_id: UUID,
    full_name: str,
    phone: str,
    email: str,
    password: str,
    identity_birth: str,
    identity_sequence: int,
    birth_date: date,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> None:
    person = db.get(Person, person_id)
    if person is None:
        person = _seed_person(
            person_id=person_id,
            full_name=full_name,
            identity_birth=identity_birth,
            identity_sequence=identity_sequence,
            sex="female",
            date_of_birth=birth_date,
            phone=phone,
            email=email,
            cipher=cipher,
            lookup=lookup,
        )
        db.add(person)
    else:
        person.full_name = full_name
        normalized_email = normalize_email(email)
        person.email = None
        person.email_ciphertext = cipher.encrypt(normalized_email, field_name="email")
        person.email_digest = lookup.digest_domain("person-email", normalized_email)
        person.sex = "female"
        person.date_of_birth = birth_date
        person.deleted_at = None
    db.flush()

    staff = db.get(StaffProfile, person_id)
    if staff is None:
        db.add(StaffProfile(person_id=person_id, staff_type="coach"))
    else:
        staff.staff_type = "coach"

    account = db.get(UserAccount, account_id)
    if account is None:
        account = _seed_account(
            account_id=account_id,
            person_id=person_id,
            phone=phone,
            email=email,
            password=password,
            lookup=lookup,
            passwords=passwords,
        )
        db.add(account)
    else:
        account.login_phone_digest = None
        account.email_normalized = None
        account.status = "active"
        account.force_password_change = False
        account.failed_login_count = 0
        account.locked_until = None
        if not passwords.verify(password, account.password_hash):
            account.password_hash = passwords.hash(password)
    db.flush()
    _upsert_seed_phone_identifier(
        db,
        account_id=account_id,
        phone=phone,
        lookup=lookup,
    )
    db.flush()

    coach_role_id = ROLES["coach"].id
    if db.get(UserRole, (account_id, coach_role_id)) is None:
        db.add(UserRole(user_id=account_id, role_id=coach_role_id))


def _upsert_guardian_relationship(db: Session, *, valid_from: date) -> None:
    relationship = db.get(
        GuardianRelationship,
        TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    if relationship is None:
        relationship = GuardianRelationship(
            id=TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
            guardian_person_id=E2E_OTHER_GUARDIAN_PERSON_ID,
            student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
            relationship="mother",
            is_primary=False,
            valid_from=valid_from,
        )
        db.add(relationship)
    else:
        relationship.relationship = "mother"
        relationship.is_primary = False
        relationship.valid_from = valid_from
        relationship.valid_until = None
        relationship.ended_at = None
        relationship.ended_by_user_id = None
        relationship.end_reason = None


def _upsert_cohorts_and_classes(
    db: Session,
    *,
    fixtures: tuple[_CohortFixture, ...],
    occurred_at: datetime,
) -> None:
    for fixture in fixtures:
        status = "completed" if occurred_at >= fixture.ends_at else "active"
        cohort = db.get(Cohort, fixture.id)
        if cohort is None:
            cohort = Cohort(
                id=fixture.id,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(cohort)
        cohort.name = fixture.name
        cohort.starts_at = fixture.starts_at
        cohort.ends_at = fixture.ends_at
        cohort.default_check_in_at = fixture.starts_at
        cohort.default_check_out_at = fixture.ends_at
        cohort.status = status
        cohort.auto_start_enabled = False
        cohort.auto_complete_enabled = False
        cohort.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()

    for fixture in fixtures:
        teaching_class = db.get(TeachingClass, fixture.class_id)
        if teaching_class is None:
            teaching_class = TeachingClass(
                id=fixture.class_id,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(teaching_class)
        teaching_class.cohort_id = fixture.id
        teaching_class.project_id = _LOL_PROJECT_ID
        teaching_class.name = fixture.class_name
        teaching_class.class_type = "standard"
        teaching_class.professional_team_id = None
        teaching_class.capacity = 20
        teaching_class.default_tuition_cents = 0
        teaching_class.is_active = True
        teaching_class.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()


def _upsert_enrollments(
    db: Session,
    *,
    fixtures: tuple[_CohortFixture, ...],
    occurred_at: datetime,
) -> None:
    cases: dict[UUID, AdmissionCase] = {}
    for fixture in fixtures:
        admission_case = db.get(AdmissionCase, fixture.admission_case_id)
        if admission_case is None:
            admission_case = AdmissionCase(
                id=fixture.admission_case_id,
                student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
                status="ready",
                agreed_tuition_cents=0,
                current_enrollment_id=None,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(admission_case)
        else:
            admission_case.student_person_id = E2E_SECOND_BOUND_STUDENT_ID
            admission_case.status = "ready"
            admission_case.agreed_tuition_cents = 0
            admission_case.tuition_override_reason = None
            admission_case.current_enrollment_id = None
            admission_case.closed_reason = None
            admission_case.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
        cases[fixture.admission_case_id] = admission_case
    db.flush()

    for fixture in fixtures:
        completed = occurred_at >= fixture.ends_at
        enrollment = db.get(Enrollment, fixture.enrollment_id)
        if enrollment is None:
            enrollment = Enrollment(
                id=fixture.enrollment_id,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(enrollment)
        enrollment.admission_case_id = fixture.admission_case_id
        enrollment.student_person_id = E2E_SECOND_BOUND_STUDENT_ID
        enrollment.cohort_id = fixture.id
        enrollment.class_id = fixture.class_id
        enrollment.agreed_tuition_cents = 0
        enrollment.status = "completed" if completed else "active"
        enrollment.capacity_override_reason = None
        enrollment.confirmed_at = fixture.starts_at - timedelta(days=1)
        enrollment.cancelled_at = None
        enrollment.completed_at = fixture.ends_at if completed else None
        enrollment.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()

    for fixture in fixtures:
        admission_case = cases[fixture.admission_case_id]
        admission_case.status = "enrolled"
        admission_case.current_enrollment_id = fixture.enrollment_id


def _upsert_assignments(
    db: Session,
    *,
    fixtures: tuple[_CohortFixture, ...],
) -> None:
    by_class = {fixture.class_id: fixture for fixture in fixtures}
    for assignment_id, class_id, person_id, assignment_type in (
        _expected_assignment_fixtures(fixtures)
    ):
        fixture = by_class[class_id]
        assignment = db.get(CoachAssignment, assignment_id)
        if assignment is None:
            assignment = CoachAssignment(
                id=assignment_id,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(assignment)
        assignment.class_id = class_id
        assignment.coach_person_id = person_id
        assignment.assignment_type = assignment_type
        assignment.valid_from = fixture.starts_at
        assignment.valid_until = fixture.ends_at + timedelta(days=8)
        assignment.ended_at = None
        assignment.ended_by_user_id = None
        assignment.end_reason = None
        assignment.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()


def _upsert_permission_overrides(
    db: Session,
    *,
    fixture: _CohortFixture,
) -> None:
    override_fixtures = (
        (
            TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[0],
            AUTHORIZED_ASSISTANT_ACCOUNT_ID,
            PERMISSIONS["student_reports.write"].id,
            "assigned_class",
            fixture.class_id,
            "E2E authorized assistant teaching report fixture",
        ),
        (
            TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[1],
            E2E_LIFE_TEACHER_ACCOUNT_ID,
            PERMISSIONS["student_reports.review"].id,
            "project",
            _LOL_PROJECT_ID,
            "E2E teaching report reviewer fixture",
        ),
    )
    for (
        override_id,
        user_id,
        permission_id,
        scope_kind,
        scope_id,
        reason,
    ) in override_fixtures:
        override = db.get(UserPermissionOverride, override_id)
        if override is None:
            override = UserPermissionOverride(id=override_id)
            db.add(override)
        override.user_id = user_id
        override.permission_id = permission_id
        override.effect = "allow"
        override.scope_kind = scope_kind
        override.scope_id = scope_id
        override.valid_from = fixture.starts_at
        override.valid_until = fixture.ends_at + timedelta(days=8)
        override.created_by_user_id = E2E_ADMIN_ACCOUNT_ID
        override.grant_reason = reason


def seed_teaching_reports_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
    occurred_at: datetime,
) -> TeachingReportsE2ESeedResult:
    _require_e2e_environment(settings)
    _require_aware(occurred_at)
    _require_base_fixtures(db)
    fixtures = _cohort_fixtures(occurred_at)
    _preflight_owned_rows(db, lookup=lookup, cohorts=fixtures)

    with write_transaction(db):
        _clear_stale_report_idempotency(db)
        _restore_base_access(db)
        _upsert_assistant_identity(
            db,
            person_id=AUTHORIZED_ASSISTANT_PERSON_ID,
            account_id=AUTHORIZED_ASSISTANT_ACCOUNT_ID,
            full_name="E2E授权助教",
            phone=AUTHORIZED_ASSISTANT_PHONE,
            email="e2e-teaching-authorized@example.com",
            password=AUTHORIZED_ASSISTANT_PASSWORD,
            identity_birth="19920201",
            identity_sequence=131,
            birth_date=date(1992, 2, 1),
            cipher=cipher,
            lookup=lookup,
            passwords=passwords,
        )
        _upsert_assistant_identity(
            db,
            person_id=UNAUTHORIZED_ASSISTANT_PERSON_ID,
            account_id=UNAUTHORIZED_ASSISTANT_ACCOUNT_ID,
            full_name="E2E未授权助教",
            phone=UNAUTHORIZED_ASSISTANT_PHONE,
            email="e2e-teaching-unauthorized@example.com",
            password=UNAUTHORIZED_ASSISTANT_PASSWORD,
            identity_birth="19920301",
            identity_sequence=132,
            birth_date=date(1992, 3, 1),
            cipher=cipher,
            lookup=lookup,
            passwords=passwords,
        )
        _upsert_guardian_relationship(
            db,
            valid_from=fixtures[0].starts_at.astimezone(_SHANGHAI).date(),
        )
        _upsert_cohorts_and_classes(db, fixtures=fixtures, occurred_at=occurred_at)
        _upsert_enrollments(db, fixtures=fixtures, occurred_at=occurred_at)
        _upsert_assignments(db, fixtures=fixtures)
        _upsert_permission_overrides(db, fixture=fixtures[0])
        db.flush()

    return TeachingReportsE2ESeedResult(
        cohort_ids=TEACHING_REPORT_COHORT_IDS,
        class_ids=TEACHING_REPORT_CLASS_IDS,
        enrollment_ids=TEACHING_REPORT_ENROLLMENT_IDS,
        authorized_assistant_account_id=AUTHORIZED_ASSISTANT_ACCOUNT_ID,
        unauthorized_assistant_account_id=UNAUTHORIZED_ASSISTANT_ACCOUNT_ID,
        reviewer_account_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
        student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
        guardian_person_ids=(
            E2E_GUARDIAN_PERSON_ID,
            E2E_OTHER_GUARDIAN_PERSON_ID,
        ),
    )


def _public_payload(result: TeachingReportsE2ESeedResult) -> dict[str, Any]:
    return {
        "cohort_ids": [str(value) for value in result.cohort_ids],
        "class_ids": [str(value) for value in result.class_ids],
        "enrollment_ids": [str(value) for value in result.enrollment_ids],
        "account_ids": {
            "authorized_assistant": str(result.authorized_assistant_account_id),
            "unauthorized_assistant": str(result.unauthorized_assistant_account_id),
            "reviewer": str(result.reviewer_account_id),
            "student": str(E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID),
            "guardian_one": str(E2E_GUARDIAN_ACCOUNT_ID),
            "guardian_two": str(E2E_OTHER_GUARDIAN_ACCOUNT_ID),
            "primary_coach": str(E2E_COACH_ACCOUNT_ID),
            "admin": str(E2E_ADMIN_ACCOUNT_ID),
        },
        "person_ids": {
            "authorized_assistant": str(AUTHORIZED_ASSISTANT_PERSON_ID),
            "unauthorized_assistant": str(UNAUTHORIZED_ASSISTANT_PERSON_ID),
            "reviewer": str(E2E_LIFE_TEACHER_PERSON_ID),
            "student": str(result.student_person_id),
            "guardian_one": str(result.guardian_person_ids[0]),
            "guardian_two": str(result.guardian_person_ids[1]),
            "primary_coach": str(E2E_COACH_PERSON_ID),
        },
        "relationship_ids": [
            str(E2E_SECOND_GUARDIAN_RELATIONSHIP_ID),
            str(TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID),
        ],
        "assignment_ids": [
            str(value) for value in TEACHING_REPORT_COACH_ASSIGNMENT_IDS
        ],
        "permission_override_ids": [
            str(value) for value in TEACHING_REPORT_PERMISSION_OVERRIDE_IDS
        ],
        "credential_refs": list(_CREDENTIAL_REFS),
    }


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        result = seed_teaching_reports_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(
                _secret_bytes(settings, "field_encryption_master_key")
            ),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
            occurred_at=_now(),
        )
    print(json.dumps(_public_payload(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
