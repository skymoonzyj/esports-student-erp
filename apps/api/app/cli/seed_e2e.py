from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.admissions.models import (
    AdmissionCase,
    AdmissionCasePayment,
    Enrollment,
    EnrollmentEvent,
    Payment,
    PaymentRefund,
    ServiceAccount,
    ServiceAccountCredential,
)
from app.auth.models import UserAccount
from app.cli.e2e_database_safety import E2EDatabaseRefused, require_e2e_database_url
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentGameAccount
from app.evaluations.staff_models import CohortStaffAssignment, EvaluationStaffRole
from app.housing.seed import HousingSeedResult, seed_initial_housing_inventory
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    GameProject,
    ProfessionalTeam,
    TeachingClass,
)
from app.teaching.seed import INITIAL_PROJECTS

ADMIN_PHONE = "19990000001"
ADMIN_PASSWORD = "E2EAdm1n26"
CUSTOMER_SERVICE_PHONE = "19990000004"
CUSTOMER_SERVICE_PASSWORD = "E2ECust1m26"
COACH_PHONE = "19990000003"
COACH_PASSWORD = "E2ECoach1x26"
GUARDIAN_PHONE = "19990000005"
GUARDIAN_PASSWORD = "E2EGuard1x26"
STUDENT_PHONE = "19990000016"
STUDENT_PASSWORD = "E2EStud1nt26"
OTHER_GUARDIAN_PHONE = "19990000010"
OTHER_GUARDIAN_PASSWORD = "E2EOther1x26"
LIFE_TEACHER_PHONE = "19990000009"
LIFE_TEACHER_PASSWORD = "E2ELife1x26"
READY_MALE_STUDENT_PHONE = "19990000011"
READY_MALE_STUDENT_PASSWORD = "E2EReady1x26"
VISITOR_PHONE = "19990000028"
VISITOR_PASSWORD = "E2EVisit1x26"
SECOND_STUDENT_PHONE = "19990000015"
SECOND_STUDENT_PASSWORD = "E2ESecond1x26"

E2E_ADMIN_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000001")
E2E_CUSTOMER_SERVICE_PERSON_ID = UUID(
    "e1000000-0000-4000-8000-000000000002"
)
E2E_GUARDIAN_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000003")
E2E_BOUND_STUDENT_ID = UUID("e1000000-0000-4000-8000-000000000004")
E2E_PENDING_STUDENT_ID = UUID("e1000000-0000-4000-8000-000000000005")
E2E_COACH_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000006")
E2E_SELF_STUDENT_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000007")
E2E_OTHER_GUARDIAN_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000008")
E2E_LIFE_TEACHER_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000009")
E2E_READY_MALE_STUDENT_PERSON_ID = UUID(
    "e1000000-0000-4000-8000-000000000010"
)
E2E_VISITOR_PERSON_ID = UUID("e1000000-0000-4000-8000-000000000011")
E2E_ADMIN_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000001")
E2E_CUSTOMER_SERVICE_ACCOUNT_ID = UUID(
    "e1100000-0000-4000-8000-000000000002"
)
E2E_GUARDIAN_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000003")
E2E_COACH_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000006")
E2E_SELF_STUDENT_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000007")
E2E_OTHER_GUARDIAN_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000008")
E2E_LIFE_TEACHER_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000009")
E2E_READY_MALE_STUDENT_ACCOUNT_ID = UUID(
    "e1100000-0000-4000-8000-000000000010"
)
E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID = UUID(
    "e1100000-0000-4000-8000-000000000011"
)
E2E_VISITOR_ACCOUNT_ID = UUID("e1100000-0000-4000-8000-000000000012")
E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID = UUID(
    "e1200000-0000-4000-8000-000000000001"
)
E2E_READY_MALE_GUARDIAN_RELATIONSHIP_ID = UUID(
    "e1200000-0000-4000-8000-000000000010"
)

E2E_SECOND_BOUND_STUDENT_ID = UUID("e2000000-0000-4000-8000-000000000011")
E2E_SECOND_GUARDIAN_RELATIONSHIP_ID = UUID(
    "e2000000-0000-4000-8000-000000000012"
)
E2E_COHORT_ID = UUID("e2100000-0000-4000-8000-000000000001")
E2E_LATER_COHORT_ID = UUID("e2100000-0000-4000-8000-000000000002")
E2E_PROFESSIONAL_TEAM_ID = UUID("e2110000-0000-4000-8000-000000000001")
E2E_STANDARD_CLASS_ID = UUID("e2120000-0000-4000-8000-000000000001")
E2E_ELITE_CLASS_ID = UUID("e2120000-0000-4000-8000-000000000002")
E2E_FULL_CLASS_ID = UUID("e2120000-0000-4000-8000-000000000003")
E2E_LATER_CLASS_ID = UUID("e2120000-0000-4000-8000-000000000004")
E2E_STANDARD_ASSIGNMENT_ID = UUID("e2130000-0000-4000-8000-000000000001")
E2E_ELITE_ASSIGNMENT_ID = UUID("e2130000-0000-4000-8000-000000000002")
E2E_LATER_ASSIGNMENT_ID = UUID("e2130000-0000-4000-8000-000000000003")
E2E_COACH_FAMILY_RELATIONSHIP_ID = UUID(
    "e2400000-0000-4000-8000-000000000005"
)
E2E_GUARDIAN_ADMISSION_CASE_ID = UUID("e2500000-0000-4000-8000-000000000001")
E2E_ADULT_ADMISSION_CASE_ID = UUID("e2500000-0000-4000-8000-000000000002")
E2E_SELF_ADMISSION_CASE_ID = UUID("e2500000-0000-4000-8000-000000000003")
E2E_GUARDIAN_ENROLLMENT_ID = UUID("e2510000-0000-4000-8000-000000000001")
E2E_ADULT_ENROLLMENT_ID = UUID("e2510000-0000-4000-8000-000000000002")
E2E_SELF_ENROLLMENT_ID = UUID("e2510000-0000-4000-8000-000000000003")
E2E_GUARDIAN_PAYMENT_ID = UUID("e2520000-0000-4000-8000-000000000001")
E2E_STUDENT_PAYMENT_ID = UUID("e2520000-0000-4000-8000-000000000002")
E2E_OTHER_GUARDIAN_PAYMENT_ID = UUID("e2520000-0000-4000-8000-000000000003")
E2E_GUARDIAN_REFUND_ID = UUID("e2530000-0000-4000-8000-000000000001")
E2E_FULL_ADMISSION_CASE_ID = UUID("e2200000-0000-4000-8000-000000000001")
E2E_FULL_ENROLLMENT_ID = UUID("e2210000-0000-4000-8000-000000000001")
E2E_FULL_PAYMENT_ID = UUID("e2220000-0000-4000-8000-000000000001")
E2E_FULL_ENROLLMENT_EVENT_ID = UUID("e2230000-0000-4000-8000-000000000001")
E2E_SERVICE_ACCOUNT_ID = UUID("e2300000-0000-4000-8000-000000000001")
E2E_PAYMENT_CREDENTIAL_ID = UUID("e2310000-0000-4000-8000-000000000001")
E2E_PAYMENT_CREDENTIAL_PUBLIC_ID = "pay_e2e_seeded_credential_0001"
E2E_PAYMENT_CREDENTIAL_SECRET = "e2e-payment-secret-2026-fixed"
E2E_GAME_ACCOUNT_IDS = (
    UUID("e2700000-0000-4000-8000-000000000001"),
    UUID("e2700000-0000-4000-8000-000000000002"),
    UUID("e2700000-0000-4000-8000-000000000003"),
)
E2E_CUSTOM_STAFF_ROLE_ID = UUID("e2800000-0000-4000-8000-000000000001")
E2E_STAFF_ASSIGNMENT_IDS = (
    UUID("e2810000-0000-4000-8000-000000000001"),
    UUID("e2810000-0000-4000-8000-000000000002"),
    UUID("e2810000-0000-4000-8000-000000000003"),
    UUID("e2810000-0000-4000-8000-000000000004"),
)
E2E_FAMILY_PERMISSION_OVERRIDE_ID = UUID(
    "e2900000-0000-4000-8000-000000000001"
)
E2E_ANALYSIS_PROFILE_IDS = {
    UUID("30000000-0000-4000-8000-000000000001"): UUID(
        "e2600000-0000-4000-8000-000000000001"
    ),
    UUID("30000000-0000-4000-8000-000000000002"): UUID(
        "e2600000-0000-4000-8000-000000000002"
    ),
    UUID("30000000-0000-4000-8000-000000000003"): UUID(
        "e2600000-0000-4000-8000-000000000003"
    ),
    UUID("30000000-0000-4000-8000-000000000004"): UUID(
        "e2600000-0000-4000-8000-000000000004"
    ),
}

E2E_GUARDIAN_PAYMENT_CENTS = 100_000
E2E_STUDENT_PAYMENT_CENTS = 80_000
E2E_OTHER_GUARDIAN_PAYMENT_CENTS = 99_999

E2E_COHORT_NAME = "E2E招生暑期营"
E2E_STANDARD_CLASS_NAME = "E2E英雄联盟普通班"
E2E_ELITE_CLASS_NAME = "E2E英雄联盟菁英班"
E2E_FULL_CLASS_NAME = "E2E英雄联盟满班"

_LOL_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
_PEL_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000002")
_DELTA_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000003")
_LIFE_TEACHER_STAFF_ROLE_ID = UUID("41000000-0000-4000-8000-000000000001")
_SHANGHAI = ZoneInfo("Asia/Shanghai")

PORTAL_RELATIONSHIPS_BY_VIEWPORT = {
    1440: UUID("e2400000-0000-4000-8000-000000000001"),
    1024: UUID("e2400000-0000-4000-8000-000000000002"),
    768: UUID("e2400000-0000-4000-8000-000000000003"),
    360: UUID("e2400000-0000-4000-8000-000000000004"),
}


@dataclass(frozen=True)
class _E2EAdultStudentFixture:
    id: UUID
    viewport: int
    phone: str
    identity_sequence: int


_E2E_ADULT_STUDENTS = (
    _E2EAdultStudentFixture(
        id=UUID("e2000000-0000-4000-8000-000000000010"),
        viewport=1440,
        phone="13800000007",
        identity_sequence=115,
    ),
    _E2EAdultStudentFixture(
        id=UUID("e2000000-0000-4000-8000-000000000020"),
        viewport=1024,
        phone="13800000009",
        identity_sequence=117,
    ),
    _E2EAdultStudentFixture(
        id=UUID("e2000000-0000-4000-8000-000000000030"),
        viewport=768,
        phone="13800000010",
        identity_sequence=119,
    ),
    _E2EAdultStudentFixture(
        id=UUID("e2000000-0000-4000-8000-000000000040"),
        viewport=360,
        phone="13800000011",
        identity_sequence=121,
    ),
)


class E2ESeedRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class E2ESeedResult:
    admin_account_id: UUID
    customer_service_account_id: UUID
    coach_person_id: UUID
    coach_account_id: UUID
    guardian_person_id: UUID
    student_person_id: UUID


def _make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def _require_e2e_environment(settings: Settings) -> None:
    if settings.app_env != "test":
        raise E2ESeedRefused(
            "E2E seed requires APP_ENV=test and an isolated test database"
        )
    try:
        require_e2e_database_url(settings.database_url)
    except E2EDatabaseRefused as exc:
        raise E2ESeedRefused(
            "E2E seed requires APP_ENV=test and an isolated test database"
        ) from exc


def _seed_person(
    *,
    person_id: UUID,
    full_name: str,
    identity_birth: str,
    identity_sequence: int,
    sex: str,
    date_of_birth: date,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    phone: str | None = None,
    email: str | None = None,
) -> Person:
    identity_number = _make_cn_id(identity_birth, identity_sequence)
    return Person(
        id=person_id,
        full_name=full_name,
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="identity_number",
        ),
        identity_digest=lookup.digest(identity_number),
        phone_ciphertext=(
            cipher.encrypt(phone, field_name="phone") if phone is not None else None
        ),
        phone_digest=lookup.digest(phone) if phone is not None else None,
        email=email,
        sex=sex,
        date_of_birth=date_of_birth,
    )


def _seed_account(
    *,
    account_id: UUID,
    person_id: UUID,
    phone: str,
    email: str,
    password: str,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> UserAccount:
    return UserAccount(
        id=account_id,
        person_id=person_id,
        login_phone_digest=lookup.digest(phone),
        email_normalized=email,
        password_hash=passwords.hash(password),
        status="active",
        force_password_change=False,
        failed_login_count=0,
        locked_until=None,
    )


def _seed_admissions_fixtures(
    db: Session,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
    admin_user_id: UUID,
    guardian_person_id: UUID,
) -> None:
    second_minor_identity = _make_cn_id("20120204", 116)
    adult_people: list[Person] = []
    adult_profiles: list[StudentProfile] = []
    for fixture in _E2E_ADULT_STUDENTS:
        identity_number = _make_cn_id("20000102", fixture.identity_sequence)
        adult_people.append(
            Person(
                id=fixture.id,
                full_name=f"E2E招生学员{fixture.viewport}",
                identity_ciphertext=cipher.encrypt(
                    identity_number,
                    field_name="identity_number",
                ),
                identity_digest=lookup.digest(identity_number),
                phone_ciphertext=cipher.encrypt(fixture.phone, field_name="phone"),
                phone_digest=lookup.digest(fixture.phone),
                email=f"e2e-admissions-{fixture.viewport}@example.com",
                sex="male",
                date_of_birth=date(2000, 1, 2),
            )
        )
        adult_profiles.append(
            StudentProfile(
                person_id=fixture.id,
                student_number=f"E2E-ADMISSIONS-{fixture.viewport}",
                status="active",
            )
        )
    second_minor = Person(
        id=E2E_SECOND_BOUND_STUDENT_ID,
        full_name="E2E监护人第二学员",
        identity_ciphertext=cipher.encrypt(
            second_minor_identity,
            field_name="identity_number",
        ),
        identity_digest=lookup.digest(second_minor_identity),
        phone_ciphertext=cipher.encrypt(SECOND_STUDENT_PHONE, field_name="phone"),
        phone_digest=lookup.digest(SECOND_STUDENT_PHONE),
        email="e2e-second-guarded-student@example.com",
        sex="female",
        date_of_birth=date(2012, 2, 4),
    )
    db.add_all([*adult_people, second_minor])
    db.flush()

    db.add_all(
        [
            *adult_profiles,
            StudentProfile(
                person_id=E2E_SECOND_BOUND_STUDENT_ID,
                student_number="E2E-GUARDED-002",
                status="active",
            ),
            GuardianRelationship(
                id=E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
                guardian_person_id=guardian_person_id,
                student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
                relationship="father",
                is_primary=True,
                valid_from=date(2026, 1, 1),
                valid_until=None,
            ),
            *[
                GuardianRelationship(
                    id=PORTAL_RELATIONSHIPS_BY_VIEWPORT[fixture.viewport],
                    guardian_person_id=guardian_person_id,
                    student_person_id=fixture.id,
                    relationship="father",
                    is_primary=False,
                    valid_from=date(2026, 1, 1),
                    valid_until=None,
                )
                for fixture in _E2E_ADULT_STUDENTS
            ],
            GuardianRelationship(
                id=E2E_COACH_FAMILY_RELATIONSHIP_ID,
                guardian_person_id=E2E_COACH_PERSON_ID,
                student_person_id=E2E_SELF_STUDENT_PERSON_ID,
                relationship="father",
                is_primary=True,
                valid_from=date(2026, 1, 1),
                valid_until=None,
            ),
        ]
    )

    professional_team = ProfessionalTeam(
        id=E2E_PROFESSIONAL_TEAM_ID,
        project_id=_LOL_PROJECT_ID,
        name="E2E英雄联盟职业一队",
        notes="招生报名 E2E 固定队伍",
        is_active=True,
        created_by_user_id=admin_user_id,
    )
    cohort = Cohort(
        id=E2E_COHORT_ID,
        name=E2E_COHORT_NAME,
        starts_at=datetime(2030, 8, 1, 9, 0, tzinfo=_SHANGHAI),
        ends_at=datetime(2030, 8, 10, 18, 0, tzinfo=_SHANGHAI),
        default_check_in_at=datetime(2030, 8, 1, 8, 0, tzinfo=_SHANGHAI),
        default_check_out_at=datetime(2030, 8, 10, 19, 0, tzinfo=_SHANGHAI),
        status="open",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin_user_id,
    )
    later_cohort = Cohort(
        id=E2E_LATER_COHORT_ID,
        name="E2E招生未来训练营",
        starts_at=datetime(2030, 8, 15, 9, 0, tzinfo=_SHANGHAI),
        ends_at=datetime(2030, 8, 25, 18, 0, tzinfo=_SHANGHAI),
        default_check_in_at=datetime(2030, 8, 15, 8, 0, tzinfo=_SHANGHAI),
        default_check_out_at=datetime(2030, 8, 25, 19, 0, tzinfo=_SHANGHAI),
        status="open",
        auto_start_enabled=False,
        auto_complete_enabled=False,
        created_by_user_id=admin_user_id,
    )
    db.add_all([professional_team, cohort, later_cohort])
    db.flush()

    db.add_all(
        [
            TeachingClass(
                id=E2E_STANDARD_CLASS_ID,
                cohort_id=E2E_COHORT_ID,
                project_id=_LOL_PROJECT_ID,
                name=E2E_STANDARD_CLASS_NAME,
                class_type="standard",
                professional_team_id=None,
                capacity=20,
                default_tuition_cents=1_280_000,
                is_active=True,
                created_by_user_id=admin_user_id,
            ),
            TeachingClass(
                id=E2E_ELITE_CLASS_ID,
                cohort_id=E2E_COHORT_ID,
                project_id=_LOL_PROJECT_ID,
                name=E2E_ELITE_CLASS_NAME,
                class_type="elite",
                professional_team_id=E2E_PROFESSIONAL_TEAM_ID,
                capacity=10,
                default_tuition_cents=3_680_000,
                is_active=True,
                created_by_user_id=admin_user_id,
            ),
            TeachingClass(
                id=E2E_FULL_CLASS_ID,
                cohort_id=E2E_COHORT_ID,
                project_id=_LOL_PROJECT_ID,
                name=E2E_FULL_CLASS_NAME,
                class_type="standard",
                professional_team_id=None,
                capacity=1,
                default_tuition_cents=1_280_000,
                is_active=True,
                created_by_user_id=admin_user_id,
            ),
            TeachingClass(
                id=E2E_LATER_CLASS_ID,
                cohort_id=E2E_LATER_COHORT_ID,
                project_id=_LOL_PROJECT_ID,
                name="E2E未来英雄联盟普通班",
                class_type="standard",
                professional_team_id=None,
                capacity=20,
                default_tuition_cents=1_280_000,
                is_active=True,
                created_by_user_id=admin_user_id,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            _seed_account(
                account_id=E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID,
                person_id=E2E_SECOND_BOUND_STUDENT_ID,
                phone=SECOND_STUDENT_PHONE,
                email="e2e-second-guarded-student@example.com",
                password=SECOND_STUDENT_PASSWORD,
                lookup=lookup,
                passwords=passwords,
            ),
            UserRole(
                user_id=E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID,
                role_id=ROLES["student"].id,
            ),
        ]
    )
    db.flush()

    assignment_valid_from = datetime(2026, 1, 1, 0, 0, tzinfo=_SHANGHAI)
    assignment_valid_until = datetime(2031, 1, 1, 0, 0, tzinfo=_SHANGHAI)
    db.add_all(
        [
            CoachAssignment(
                id=E2E_STANDARD_ASSIGNMENT_ID,
                class_id=E2E_STANDARD_CLASS_ID,
                coach_person_id=E2E_COACH_PERSON_ID,
                assignment_type="primary",
                valid_from=assignment_valid_from,
                valid_until=assignment_valid_until,
                created_by_user_id=admin_user_id,
            ),
            CoachAssignment(
                id=E2E_ELITE_ASSIGNMENT_ID,
                class_id=E2E_ELITE_CLASS_ID,
                coach_person_id=E2E_COACH_PERSON_ID,
                assignment_type="assistant",
                valid_from=assignment_valid_from,
                valid_until=assignment_valid_until,
                created_by_user_id=admin_user_id,
            ),
            CoachAssignment(
                id=E2E_LATER_ASSIGNMENT_ID,
                class_id=E2E_LATER_CLASS_ID,
                coach_person_id=E2E_COACH_PERSON_ID,
                assignment_type="primary",
                valid_from=assignment_valid_from,
                valid_until=assignment_valid_until,
                created_by_user_id=admin_user_id,
            ),
        ]
    )
    db.flush()

    full_case = AdmissionCase(
        id=E2E_FULL_ADMISSION_CASE_ID,
        student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
        status="ready",
        agreed_tuition_cents=1_280_000,
        created_by_user_id=admin_user_id,
    )
    full_payment = Payment(
        id=E2E_FULL_PAYMENT_ID,
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=None,
        payer_person_id=E2E_SECOND_BOUND_STUDENT_ID,
        amount_cents=1_280_000,
        currency="CNY",
        paid_at=datetime(2030, 7, 20, 10, 0, tzinfo=_SHANGHAI),
        manual_note="E2E满班占位付款",
        created_by_user_id=admin_user_id,
    )
    db.add_all([full_case, full_payment])
    db.flush()
    db.add(
        AdmissionCasePayment(
            admission_case_id=E2E_FULL_ADMISSION_CASE_ID,
            payment_id=E2E_FULL_PAYMENT_ID,
            attached_by_user_id=admin_user_id,
        )
    )
    db.flush()
    full_enrollment = Enrollment(
        id=E2E_FULL_ENROLLMENT_ID,
        admission_case_id=E2E_FULL_ADMISSION_CASE_ID,
        student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
        cohort_id=E2E_COHORT_ID,
        class_id=E2E_FULL_CLASS_ID,
        agreed_tuition_cents=1_280_000,
        status="active",
        confirmed_at=datetime(2030, 7, 28, 10, 0, tzinfo=_SHANGHAI),
        created_by_user_id=admin_user_id,
    )
    db.add(full_enrollment)
    db.flush()
    full_case.status = "enrolled"
    full_case.current_enrollment_id = E2E_FULL_ENROLLMENT_ID
    db.add(
        EnrollmentEvent(
            id=E2E_FULL_ENROLLMENT_EVENT_ID,
            enrollment_id=E2E_FULL_ENROLLMENT_ID,
            event_type="confirmed",
            old_class_id=None,
            new_class_id=E2E_FULL_CLASS_ID,
            old_status=None,
            new_status="active",
            reason=None,
            enrollment_count_snapshot=1,
            actor_user_id=admin_user_id,
            request_id="e2e-full-class-confirmed",
            occurred_at=datetime(2030, 7, 28, 10, 0, tzinfo=_SHANGHAI),
        )
    )
    db.flush()

    guardian_case = AdmissionCase(
        id=E2E_GUARDIAN_ADMISSION_CASE_ID,
        student_person_id=E2E_BOUND_STUDENT_ID,
        status="ready",
        agreed_tuition_cents=E2E_GUARDIAN_PAYMENT_CENTS,
        created_by_user_id=admin_user_id,
    )
    adult_case = AdmissionCase(
        id=E2E_ADULT_ADMISSION_CASE_ID,
        student_person_id=_E2E_ADULT_STUDENTS[0].id,
        status="ready",
        agreed_tuition_cents=3_680_000,
        created_by_user_id=admin_user_id,
    )
    self_case = AdmissionCase(
        id=E2E_SELF_ADMISSION_CASE_ID,
        student_person_id=E2E_SELF_STUDENT_PERSON_ID,
        status="ready",
        agreed_tuition_cents=E2E_STUDENT_PAYMENT_CENTS,
        created_by_user_id=admin_user_id,
    )
    db.add_all([guardian_case, adult_case, self_case])
    db.flush()

    guardian_enrollment = Enrollment(
        id=E2E_GUARDIAN_ENROLLMENT_ID,
        admission_case_id=E2E_GUARDIAN_ADMISSION_CASE_ID,
        student_person_id=E2E_BOUND_STUDENT_ID,
        cohort_id=E2E_COHORT_ID,
        class_id=E2E_STANDARD_CLASS_ID,
        agreed_tuition_cents=E2E_GUARDIAN_PAYMENT_CENTS,
        status="active",
        confirmed_at=datetime(2030, 7, 25, 9, 0, tzinfo=_SHANGHAI),
        created_by_user_id=admin_user_id,
    )
    adult_enrollment = Enrollment(
        id=E2E_ADULT_ENROLLMENT_ID,
        admission_case_id=E2E_ADULT_ADMISSION_CASE_ID,
        student_person_id=_E2E_ADULT_STUDENTS[0].id,
        cohort_id=E2E_COHORT_ID,
        class_id=E2E_ELITE_CLASS_ID,
        agreed_tuition_cents=3_680_000,
        status="active",
        confirmed_at=datetime(2030, 7, 26, 9, 0, tzinfo=_SHANGHAI),
        created_by_user_id=admin_user_id,
    )
    self_enrollment = Enrollment(
        id=E2E_SELF_ENROLLMENT_ID,
        admission_case_id=E2E_SELF_ADMISSION_CASE_ID,
        student_person_id=E2E_SELF_STUDENT_PERSON_ID,
        cohort_id=E2E_LATER_COHORT_ID,
        class_id=E2E_LATER_CLASS_ID,
        agreed_tuition_cents=E2E_STUDENT_PAYMENT_CENTS,
        status="active",
        confirmed_at=datetime(2030, 7, 27, 9, 0, tzinfo=_SHANGHAI),
        created_by_user_id=admin_user_id,
    )
    db.add_all([guardian_enrollment, adult_enrollment, self_enrollment])
    db.flush()
    for admission_case, enrollment in (
        (guardian_case, guardian_enrollment),
        (adult_case, adult_enrollment),
        (self_case, self_enrollment),
    ):
        admission_case.status = "enrolled"
        admission_case.current_enrollment_id = enrollment.id

    guardian_payment = Payment(
        id=E2E_GUARDIAN_PAYMENT_ID,
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=E2E_GUARDIAN_ACCOUNT_ID,
        payer_person_id=E2E_GUARDIAN_PERSON_ID,
        amount_cents=E2E_GUARDIAN_PAYMENT_CENTS,
        currency="CNY",
        paid_at=datetime(2030, 7, 1, 9, 0, tzinfo=_SHANGHAI),
        manual_note="E2E监护人门户付款",
        created_by_user_id=admin_user_id,
    )
    student_payment = Payment(
        id=E2E_STUDENT_PAYMENT_ID,
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=E2E_SELF_STUDENT_ACCOUNT_ID,
        payer_person_id=E2E_SELF_STUDENT_PERSON_ID,
        amount_cents=E2E_STUDENT_PAYMENT_CENTS,
        currency="CNY",
        paid_at=datetime(2030, 7, 2, 9, 0, tzinfo=_SHANGHAI),
        manual_note="E2E学员本人门户付款",
        created_by_user_id=admin_user_id,
    )
    other_guardian_payment = Payment(
        id=E2E_OTHER_GUARDIAN_PAYMENT_ID,
        source_kind="manual",
        source_system=None,
        external_order_id=None,
        payer_account_id=E2E_OTHER_GUARDIAN_ACCOUNT_ID,
        payer_person_id=E2E_OTHER_GUARDIAN_PERSON_ID,
        amount_cents=E2E_OTHER_GUARDIAN_PAYMENT_CENTS,
        currency="CNY",
        paid_at=datetime(2030, 7, 3, 9, 0, tzinfo=_SHANGHAI),
        manual_note="E2E另一付款家长隔离付款",
        created_by_user_id=admin_user_id,
    )
    db.add_all([guardian_payment, student_payment, other_guardian_payment])
    db.flush()
    db.add_all(
        [
            AdmissionCasePayment(
                admission_case_id=E2E_GUARDIAN_ADMISSION_CASE_ID,
                payment_id=E2E_GUARDIAN_PAYMENT_ID,
                attached_by_user_id=admin_user_id,
            ),
            AdmissionCasePayment(
                admission_case_id=E2E_SELF_ADMISSION_CASE_ID,
                payment_id=E2E_STUDENT_PAYMENT_ID,
                attached_by_user_id=admin_user_id,
            ),
            AdmissionCasePayment(
                admission_case_id=E2E_GUARDIAN_ADMISSION_CASE_ID,
                payment_id=E2E_OTHER_GUARDIAN_PAYMENT_ID,
                attached_by_user_id=admin_user_id,
            ),
            PaymentRefund(
                id=E2E_GUARDIAN_REFUND_ID,
                payment_id=E2E_GUARDIAN_PAYMENT_ID,
                withdrawal_id=None,
                amount_cents=20_000,
                refunded_at=datetime(2030, 7, 10, 9, 0, tzinfo=_SHANGHAI),
                refund_type="manual",
                reason="E2E门户退款明细",
                created_by_user_id=admin_user_id,
            ),
        ]
    )
    db.flush()
    db.execute(
        text("SET CONSTRAINTS fk_admission_cases_current_enrollment IMMEDIATE")
    )
    db.execute(
        text("SET CONSTRAINTS fk_admission_cases_current_enrollment DEFERRED")
    )

    db.add(
        ServiceAccount(
            id=E2E_SERVICE_ACCOUNT_ID,
            name="E2E固定付款接口",
            source_system="e2e-payment",
            is_active=True,
            created_by_user_id=admin_user_id,
        )
    )
    db.flush()
    db.add(
        ServiceAccountCredential(
            id=E2E_PAYMENT_CREDENTIAL_ID,
            service_account_id=E2E_SERVICE_ACCOUNT_ID,
            public_id=E2E_PAYMENT_CREDENTIAL_PUBLIC_ID,
            name="E2E固定凭证",
            secret_ciphertext=cipher.encrypt(
                E2E_PAYMENT_CREDENTIAL_SECRET,
                field_name="service_account_credentials.secret",
            ),
            is_active=True,
            created_by_user_id=admin_user_id,
        )
    )


def _seed_e2e_projects(db: Session) -> None:
    effective_from = datetime(2026, 1, 1, tzinfo=UTC)
    for project in INITIAL_PROJECTS:
        db.add(
            GameProject(
                id=project.id,
                name=project.name,
                sort_order=project.sort_order,
                is_active=True,
                created_by_user_id=None,
            )
        )
    db.flush()
    db.add_all(
        [
            ProjectAnalysisProfile(
                id=E2E_ANALYSIS_PROFILE_IDS[project.id],
                project_id=project.id,
                version=1,
                mode="manual_only",
                adapter_id=None,
                adapter_version=None,
                scoring_rule_version=None,
                required_fields=[],
                confidence_thresholds={},
                auto_publish_warning_levels=[],
                publish_mode="review_required",
                effective_from=effective_from,
                configured_by_user_id=None,
                change_reason="migration backfill",
            )
            for project in INITIAL_PROJECTS
        ]
    )
    db.flush()


def _seed_evaluation_foundation_fixtures(
    db: Session,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    admin_user_id: UUID,
) -> None:
    db.add_all(
        [
            EvaluationStaffRole(
                id=_LIFE_TEACHER_STAFF_ROLE_ID,
                key="life_teacher",
                name="生活老师",
                category="life_service",
                is_builtin=True,
                is_active=True,
                created_by_user_id=None,
            ),
            EvaluationStaffRole(
                id=E2E_CUSTOM_STAFF_ROLE_ID,
                key="e2e_camp_care",
                name="E2E营务协同",
                category="operations",
                is_builtin=False,
                is_active=True,
                created_by_user_id=admin_user_id,
            ),
        ]
    )
    db.flush()

    game_valid_from = datetime(2026, 1, 1, tzinfo=UTC)
    game_rows = (
        (
            E2E_GAME_ACCOUNT_IDS[0],
            _LOL_PROJECT_ID,
            "E2E-SELF-ACTIVE-LOL",
            None,
        ),
        (
            E2E_GAME_ACCOUNT_IDS[1],
            _PEL_PROJECT_ID,
            "E2E-SELF-ACTIVE-PEL",
            None,
        ),
        (
            E2E_GAME_ACCOUNT_IDS[2],
            _DELTA_PROJECT_ID,
            "E2E-SELF-HISTORY",
            datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )
    for account_id, project_id, game_id, unbound_at in game_rows:
        db.add(
            StudentGameAccount(
                id=account_id,
                student_person_id=E2E_SELF_STUDENT_PERSON_ID,
                project_id=project_id,
                identifier_ciphertext=cipher.encrypt(
                    game_id,
                    field_name="student_game_id",
                ),
                identifier_digest=student_game_id_digest(lookup, game_id),
                valid_from=game_valid_from,
                unbound_at=unbound_at,
                unbound_by_user_id=(
                    E2E_SELF_STUDENT_ACCOUNT_ID if unbound_at is not None else None
                ),
                unbind_reason=(
                    "E2E历史解绑记录" if unbound_at is not None else None
                ),
                created_by_user_id=E2E_SELF_STUDENT_ACCOUNT_ID,
            )
        )

    current_from = datetime(2026, 1, 1, tzinfo=UTC)
    current_until = datetime(2031, 1, 1, tzinfo=UTC)
    ended_from = datetime(2025, 1, 1, tzinfo=UTC)
    ended_at = datetime(2025, 6, 1, tzinfo=UTC)
    db.add_all(
        [
            CohortStaffAssignment(
                id=E2E_STAFF_ASSIGNMENT_IDS[0],
                cohort_id=E2E_COHORT_ID,
                role_id=_LIFE_TEACHER_STAFF_ROLE_ID,
                staff_person_id=E2E_LIFE_TEACHER_PERSON_ID,
                project_id=_LOL_PROJECT_ID,
                class_id=None,
                role_name_snapshot="生活老师",
                role_category_snapshot="life_service",
                valid_from=current_from,
                valid_until=current_until,
                ended_at=None,
                ended_by_user_id=None,
                end_reason=None,
                created_by_user_id=admin_user_id,
                updated_by_user_id=None,
            ),
            CohortStaffAssignment(
                id=E2E_STAFF_ASSIGNMENT_IDS[1],
                cohort_id=E2E_COHORT_ID,
                role_id=E2E_CUSTOM_STAFF_ROLE_ID,
                staff_person_id=E2E_LIFE_TEACHER_PERSON_ID,
                project_id=_LOL_PROJECT_ID,
                class_id=None,
                role_name_snapshot="E2E营务协同",
                role_category_snapshot="operations",
                valid_from=ended_from,
                valid_until=None,
                ended_at=ended_at,
                ended_by_user_id=admin_user_id,
                end_reason="E2E项目服务历史",
                created_by_user_id=admin_user_id,
                updated_by_user_id=admin_user_id,
            ),
            CohortStaffAssignment(
                id=E2E_STAFF_ASSIGNMENT_IDS[2],
                cohort_id=E2E_COHORT_ID,
                role_id=E2E_CUSTOM_STAFF_ROLE_ID,
                staff_person_id=E2E_LIFE_TEACHER_PERSON_ID,
                project_id=None,
                class_id=E2E_STANDARD_CLASS_ID,
                role_name_snapshot="E2E营务协同",
                role_category_snapshot="operations",
                valid_from=current_from,
                valid_until=current_until,
                ended_at=None,
                ended_by_user_id=None,
                end_reason=None,
                created_by_user_id=admin_user_id,
                updated_by_user_id=None,
            ),
            CohortStaffAssignment(
                id=E2E_STAFF_ASSIGNMENT_IDS[3],
                cohort_id=E2E_COHORT_ID,
                role_id=_LIFE_TEACHER_STAFF_ROLE_ID,
                staff_person_id=E2E_LIFE_TEACHER_PERSON_ID,
                project_id=None,
                class_id=E2E_ELITE_CLASS_ID,
                role_name_snapshot="生活老师",
                role_category_snapshot="life_service",
                valid_from=ended_from,
                valid_until=None,
                ended_at=ended_at,
                ended_by_user_id=admin_user_id,
                end_reason="E2E班级服务历史",
                created_by_user_id=admin_user_id,
                updated_by_user_id=admin_user_id,
            ),
            UserPermissionOverride(
                id=E2E_FAMILY_PERMISSION_OVERRIDE_ID,
                user_id=E2E_COACH_ACCOUNT_ID,
                permission_id=PERMISSIONS["student_game_accounts.manage"].id,
                effect="allow",
                scope_kind="family",
                scope_id=None,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_until=datetime(2031, 1, 1, tzinfo=UTC),
                created_by_user_id=admin_user_id,
                grant_reason="E2E有限期家庭权限夹具",
            ),
        ]
    )


def seed_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> E2ESeedResult:
    _require_e2e_environment(settings)
    with write_transaction(db):
        # Browser projects mutate payments and enrollments, so each E2E run starts
        # from an empty isolated dataset. Every fixture below uses a fixed UUID,
        # making repeated rebuilds stable without carrying state between runs.
        db.execute(
            text(
                "TRUNCATE TABLE people, audit_events, auth_rate_limits "
                ", evaluation_media_blobs "
                "RESTART IDENTITY CASCADE"
            )
        )
        db.expunge_all()
        housing_result: HousingSeedResult = seed_initial_housing_inventory(db)
        if housing_result != HousingSeedResult(
            buildings=4,
            floors=11,
            rooms=188,
            import_batches=1,
        ):
            raise E2ESeedRefused(
                "E2E seed requires the canonical housing catalog "
                "(4 buildings, 11 floors, 188 rooms, 1 import batch)"
            )
        _seed_e2e_projects(db)
        db.add_all(
            [
                _seed_person(
                    person_id=E2E_ADMIN_PERSON_ID,
                    full_name="E2E平台管理员",
                    identity_birth="19800102",
                    identity_sequence=102,
                    sex="female",
                    date_of_birth=date(1980, 1, 2),
                    phone=ADMIN_PHONE,
                    email="e2e-admin@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_CUSTOMER_SERVICE_PERSON_ID,
                    full_name="E2E平台客服",
                    identity_birth="19820102",
                    identity_sequence=104,
                    sex="female",
                    date_of_birth=date(1982, 1, 2),
                    phone=CUSTOMER_SERVICE_PHONE,
                    email="e2e-customer-service@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_GUARDIAN_PERSON_ID,
                    full_name="E2E监护人",
                    identity_birth="19840102",
                    identity_sequence=103,
                    sex="male",
                    date_of_birth=date(1984, 1, 2),
                    phone=GUARDIAN_PHONE,
                    email="e2e-guardian@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_BOUND_STUDENT_ID,
                    full_name="E2E已绑定学员",
                    identity_birth="20120102",
                    identity_sequence=106,
                    sex="female",
                    date_of_birth=date(2012, 1, 2),
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_PENDING_STUDENT_ID,
                    full_name="E2E待绑定学员",
                    identity_birth="20120203",
                    identity_sequence=107,
                    sex="male",
                    date_of_birth=date(2012, 2, 3),
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_COACH_PERSON_ID,
                    full_name="E2E教学教练",
                    identity_birth="19900102",
                    identity_sequence=109,
                    sex="male",
                    date_of_birth=date(1990, 1, 2),
                    phone=COACH_PHONE,
                    email="e2e-coach@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_SELF_STUDENT_PERSON_ID,
                    full_name="E2E自助学员",
                    identity_birth="20000102",
                    identity_sequence=123,
                    sex="male",
                    date_of_birth=date(2000, 1, 2),
                    phone=STUDENT_PHONE,
                    email="e2e-self-student@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_OTHER_GUARDIAN_PERSON_ID,
                    full_name="E2E另一付款家长",
                    identity_birth="19860102",
                    identity_sequence=124,
                    sex="female",
                    date_of_birth=date(1986, 1, 2),
                    phone=OTHER_GUARDIAN_PHONE,
                    email="e2e-other-guardian@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_LIFE_TEACHER_PERSON_ID,
                    full_name="E2E生活老师",
                    identity_birth="19920102",
                    identity_sequence=126,
                    sex="female",
                    date_of_birth=date(1992, 1, 2),
                    phone=LIFE_TEACHER_PHONE,
                    email="e2e-life-teacher@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_READY_MALE_STUDENT_PERSON_ID,
                    full_name="E2E监护就绪男学员",
                    identity_birth="20120304",
                    identity_sequence=127,
                    sex="male",
                    date_of_birth=date(2012, 3, 4),
                    phone=READY_MALE_STUDENT_PHONE,
                    email="e2e-ready-male-student@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=E2E_VISITOR_PERSON_ID,
                    full_name="E2E临时访客",
                    identity_birth="19880305",
                    identity_sequence=128,
                    sex="female",
                    date_of_birth=date(1988, 3, 5),
                    phone=VISITOR_PHONE,
                    email="e2e-visitor@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffProfile(
                    person_id=E2E_ADMIN_PERSON_ID,
                    staff_type="administration",
                ),
                StaffProfile(
                    person_id=E2E_CUSTOMER_SERVICE_PERSON_ID,
                    staff_type="customer_service",
                ),
                StaffProfile(
                    person_id=E2E_COACH_PERSON_ID,
                    staff_type="coach",
                ),
                StaffProfile(
                    person_id=E2E_LIFE_TEACHER_PERSON_ID,
                    staff_type="life_teacher",
                ),
                StudentProfile(
                    person_id=E2E_BOUND_STUDENT_ID,
                    student_number="E2E-S-001",
                    status="draft",
                ),
                StudentProfile(
                    person_id=E2E_PENDING_STUDENT_ID,
                    student_number="E2E-S-002",
                    status="draft",
                ),
                StudentProfile(
                    person_id=E2E_SELF_STUDENT_PERSON_ID,
                    student_number="E2E-SELF-001",
                    status="active",
                ),
                StudentProfile(
                    person_id=E2E_READY_MALE_STUDENT_PERSON_ID,
                    student_number="E2E-HOUSING-MALE-001",
                    status="active",
                ),
                VisitorProfile(
                    person_id=E2E_VISITOR_PERSON_ID,
                    linked_student_id=E2E_SELF_STUDENT_PERSON_ID,
                    visit_reason="探望学员临时住宿",
                ),
                GuardianRelationship(
                    id=E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
                    guardian_person_id=E2E_GUARDIAN_PERSON_ID,
                    student_person_id=E2E_BOUND_STUDENT_ID,
                    relationship="father",
                    is_primary=True,
                    valid_from=date(2026, 1, 1),
                ),
                GuardianRelationship(
                    id=E2E_READY_MALE_GUARDIAN_RELATIONSHIP_ID,
                    guardian_person_id=E2E_GUARDIAN_PERSON_ID,
                    student_person_id=E2E_READY_MALE_STUDENT_PERSON_ID,
                    relationship="father",
                    is_primary=True,
                    valid_from=date(2026, 1, 1),
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                _seed_account(
                    account_id=E2E_ADMIN_ACCOUNT_ID,
                    person_id=E2E_ADMIN_PERSON_ID,
                    phone=ADMIN_PHONE,
                    email="e2e-admin@example.com",
                    password=ADMIN_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
                    person_id=E2E_CUSTOMER_SERVICE_PERSON_ID,
                    phone=CUSTOMER_SERVICE_PHONE,
                    email="e2e-customer-service@example.com",
                    password=CUSTOMER_SERVICE_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_GUARDIAN_ACCOUNT_ID,
                    person_id=E2E_GUARDIAN_PERSON_ID,
                    phone=GUARDIAN_PHONE,
                    email="e2e-guardian@example.com",
                    password=GUARDIAN_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_COACH_ACCOUNT_ID,
                    person_id=E2E_COACH_PERSON_ID,
                    phone=COACH_PHONE,
                    email="e2e-coach@example.com",
                    password=COACH_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_SELF_STUDENT_ACCOUNT_ID,
                    person_id=E2E_SELF_STUDENT_PERSON_ID,
                    phone=STUDENT_PHONE,
                    email="e2e-self-student@example.com",
                    password=STUDENT_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_OTHER_GUARDIAN_ACCOUNT_ID,
                    person_id=E2E_OTHER_GUARDIAN_PERSON_ID,
                    phone=OTHER_GUARDIAN_PHONE,
                    email="e2e-other-guardian@example.com",
                    password=OTHER_GUARDIAN_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
                    person_id=E2E_LIFE_TEACHER_PERSON_ID,
                    phone=LIFE_TEACHER_PHONE,
                    email="e2e-life-teacher@example.com",
                    password=LIFE_TEACHER_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_READY_MALE_STUDENT_ACCOUNT_ID,
                    person_id=E2E_READY_MALE_STUDENT_PERSON_ID,
                    phone=READY_MALE_STUDENT_PHONE,
                    email="e2e-ready-male-student@example.com",
                    password=READY_MALE_STUDENT_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=E2E_VISITOR_ACCOUNT_ID,
                    person_id=E2E_VISITOR_PERSON_ID,
                    phone=VISITOR_PHONE,
                    email="e2e-visitor@example.com",
                    password=VISITOR_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                UserRole(
                    user_id=E2E_ADMIN_ACCOUNT_ID,
                    role_id=ROLES["admin"].id,
                ),
                UserRole(
                    user_id=E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
                    role_id=ROLES["customer_service"].id,
                ),
                UserRole(
                    user_id=E2E_COACH_ACCOUNT_ID,
                    role_id=ROLES["coach"].id,
                ),
                UserRole(
                    user_id=E2E_COACH_ACCOUNT_ID,
                    role_id=ROLES["guardian"].id,
                ),
                UserRole(
                    user_id=E2E_GUARDIAN_ACCOUNT_ID,
                    role_id=ROLES["guardian"].id,
                ),
                UserRole(
                    user_id=E2E_SELF_STUDENT_ACCOUNT_ID,
                    role_id=ROLES["student"].id,
                ),
                UserRole(
                    user_id=E2E_OTHER_GUARDIAN_ACCOUNT_ID,
                    role_id=ROLES["guardian"].id,
                ),
                UserRole(
                    user_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
                    role_id=ROLES["life_teacher"].id,
                ),
                UserRole(
                    user_id=E2E_READY_MALE_STUDENT_ACCOUNT_ID,
                    role_id=ROLES["student"].id,
                ),
            ]
        )
        db.flush()
        _seed_admissions_fixtures(
            db,
            cipher=cipher,
            lookup=lookup,
            passwords=passwords,
            admin_user_id=E2E_ADMIN_ACCOUNT_ID,
            guardian_person_id=E2E_GUARDIAN_PERSON_ID,
        )
        _seed_evaluation_foundation_fixtures(
            db,
            cipher=cipher,
            lookup=lookup,
            admin_user_id=E2E_ADMIN_ACCOUNT_ID,
        )
        result = E2ESeedResult(
            admin_account_id=E2E_ADMIN_ACCOUNT_ID,
            customer_service_account_id=E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
            coach_person_id=E2E_COACH_PERSON_ID,
            coach_account_id=E2E_COACH_ACCOUNT_ID,
            guardian_person_id=E2E_GUARDIAN_PERSON_ID,
            student_person_id=E2E_BOUND_STUDENT_ID,
        )
    return result


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    _require_e2e_environment(settings)
    with SessionLocal() as db:
        result = seed_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
    print(f"E2E fixtures rebuilt: admin={result.admin_account_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
