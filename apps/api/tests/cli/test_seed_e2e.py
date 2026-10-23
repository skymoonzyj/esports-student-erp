from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import (
    AdmissionCasePayment,
    Enrollment,
    EnrollmentEvent,
    Payment,
    PaymentRefund,
    ServiceAccount,
    ServiceAccountCredential,
)
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.cli.seed_e2e import (
    COACH_PHONE,
    E2E_BOUND_STUDENT_ID,
    E2E_COHORT_ID,
    E2E_ELITE_CLASS_ID,
    E2E_FULL_ADMISSION_CASE_ID,
    E2E_FULL_CLASS_ID,
    E2E_FULL_ENROLLMENT_ID,
    E2E_OTHER_GUARDIAN_PERSON_ID,
    E2E_PAYMENT_CREDENTIAL_PUBLIC_ID,
    E2E_PAYMENT_CREDENTIAL_SECRET,
    E2E_READY_MALE_GUARDIAN_RELATIONSHIP_ID,
    E2E_READY_MALE_STUDENT_PERSON_ID,
    E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    E2E_SERVICE_ACCOUNT_ID,
    E2E_STANDARD_CLASS_ID,
    E2E_VISITOR_PERSON_ID,
    GUARDIAN_PHONE,
    READY_MALE_STUDENT_PASSWORD,
    SECOND_STUDENT_PASSWORD,
    VISITOR_PASSWORD,
    E2ESeedRefused,
    _require_e2e_environment,
    seed_e2e,
)
from app.core.config import Settings
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentGameAccount
from app.evaluations.staff_models import CohortStaffAssignment, EvaluationStaffRole
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.identity.models import (
    GuardianProfile,
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.normalization import normalize_cn_id
from app.identity.service import IdentityService
from app.rbac.models import Role, UserPermissionOverride, UserRole
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")


def settings(*, app_env: str = "test", database: str = "erp_e2e") -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
    )


def _account_id(db: Session, phone: str) -> UUID:
    normalized = normalize_login_identifier(phone)
    account_id = db.scalar(
        select(AccountLoginIdentifier.account_id).where(
            AccountLoginIdentifier.kind == "phone",
            AccountLoginIdentifier.identifier_digest
            == login_identifier_digest(
                LookupDigest(bytes(range(32))),
                normalized,
            ),
        )
    )
    assert account_id is not None
    return account_id


@pytest.mark.parametrize(
    "unsafe_settings",
    [
        settings(app_env="development"),
        settings(app_env="production"),
        settings(database="production"),
        settings(database="foo_test?dbname=production"),
    ],
)
def test_e2e_seed_refuses_non_e2e_environments(
    db: Session,
    unsafe_settings: Settings,
) -> None:
    with pytest.raises(E2ESeedRefused):
        seed_e2e(
            db,
            settings=unsafe_settings,
            cipher=EnvelopeCipher(bytes(range(32))),
            lookup=LookupDigest(bytes(range(32))),
            passwords=PasswordService(),
        )


@pytest.mark.parametrize("database", ["erp_e2e", "erp_test", "housing_test"])
def test_e2e_seed_guard_accepts_supported_database_names(database: str) -> None:
    _require_e2e_environment(settings(database=database))


def test_e2e_seed_guard_refuses_database_selector_override_without_leaking() -> None:
    with pytest.raises(E2ESeedRefused) as exc_info:
        _require_e2e_environment(
            settings(database="foo_test?dbname=production-sensitive-token")
        )

    assert "production-sensitive-token" not in str(exc_info.value)


def test_evaluation_entry_is_seeded_by_role_only(db: Session) -> None:
    seed_e2e(
        db,
        settings=settings(),
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
    )
    rbac = RbacService(db)

    assert rbac.decision_context(_account_id(db, "19990000001")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert rbac.decision_context(_account_id(db, "19990000003")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert rbac.decision_context(_account_id(db, "19990000005")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert rbac.decision_context(_account_id(db, "19990000016")).decide(
        "evaluation_center.access", {"global"}
    ).allowed
    assert not rbac.decision_context(_account_id(db, "19990000004")).decide(
        "evaluation_center.access", {"global"}
    ).allowed


def test_e2e_seed_rebuilds_fixed_accounts_and_guarded_student(db: Session) -> None:
    kwargs = {
        "settings": settings(),
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }

    first = seed_e2e(db, **kwargs)
    second = seed_e2e(db, **kwargs)

    assert first == second
    assert second.admin_account_id == UUID(
        "e1100000-0000-4000-8000-000000000001"
    )
    assert second.customer_service_account_id == UUID(
        "e1100000-0000-4000-8000-000000000002"
    )
    assert second.guardian_person_id == UUID(
        "e1000000-0000-4000-8000-000000000003"
    )
    assert second.student_person_id == UUID(
        "e1000000-0000-4000-8000-000000000004"
    )
    assert second.coach_person_id == UUID(
        "e1000000-0000-4000-8000-000000000006"
    )
    assert second.coach_account_id == UUID(
        "e1100000-0000-4000-8000-000000000006"
    )
    assert db.scalar(select(func.count(UserAccount.id))) == 13
    assert db.scalar(select(func.count(Person.id))) == 16
    accounts = {
        name: account
        for name, account in db.execute(
            select(Person.full_name, UserAccount)
            .join(UserAccount, UserAccount.person_id == Person.id)
        )
    }
    roles_by_name: dict[str, set[str]] = defaultdict(set)
    for full_name, role_key in db.execute(
        select(Person.full_name, Role.key)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .join(UserRole, UserRole.user_id == UserAccount.id)
            .join(Role, Role.id == UserRole.role_id)
    ):
        roles_by_name[full_name].add(role_key)
    identifier_kinds_by_name: dict[str, set[str]] = defaultdict(set)
    for full_name, identifier_kind in db.execute(
        select(Person.full_name, AccountLoginIdentifier.kind)
        .join(UserAccount, UserAccount.person_id == Person.id)
        .join(
            AccountLoginIdentifier,
            AccountLoginIdentifier.account_id == UserAccount.id,
        )
    ):
        identifier_kinds_by_name[full_name].add(identifier_kind)
    assert set(identifier_kinds_by_name) == set(accounts)
    assert all(
        identifier_kinds == {"email", "phone"}
        for identifier_kinds in identifier_kinds_by_name.values()
    )
    admin = accounts["E2E平台管理员"]
    customer_service = accounts["E2E平台客服"]
    coach_account = accounts["E2E教学教练"]
    guardian_account = accounts["E2E监护人"]
    student_account = accounts["E2E自助学员"]
    female_student_account = accounts["E2E监护人第二学员"]
    ready_male_student_account = accounts["E2E监护就绪男学员"]
    life_teacher_account = accounts["E2E生活老师"]
    visitor_account = accounts["E2E临时访客"]
    assert roles_by_name["E2E平台管理员"] == {"super_admin"}
    assert roles_by_name["E2E平台客服"] == {"customer_service"}
    assert roles_by_name["E2E教学教练"] == {"coach", "guardian"}
    assert roles_by_name["E2E监护人"] == {"guardian"}
    assert roles_by_name["E2E自助学员"] == {"student"}
    for viewport in (1024, 768, 360):
        assert roles_by_name[f"E2E招生学员{viewport}"] == {"student"}
    assert roles_by_name["E2E监护人第二学员"] == {"student"}
    assert roles_by_name["E2E监护就绪男学员"] == {"student"}
    assert roles_by_name["E2E另一付款家长"] == {"guardian"}
    assert roles_by_name["E2E生活老师"] == {"life_teacher"}
    assert roles_by_name["E2E临时访客"] == set()
    guardian_profile_ids = set(db.scalars(select(GuardianProfile.person_id)))
    assert {
        second.guardian_person_id,
        second.coach_person_id,
        E2E_OTHER_GUARDIAN_PERSON_ID,
    } <= guardian_profile_ids
    assert guardian_account.status == "active"
    assert PasswordService().verify("TstA8c6976e5b7", admin.password_hash)
    assert PasswordService().verify(
        "TstA9944d65447",
        customer_service.password_hash,
    )
    assert PasswordService().verify(
        "TstAe0f167bc87",
        coach_account.password_hash,
    )
    assert PasswordService().verify(
        "TstA264c8c3817",
        student_account.password_hash,
    )
    assert PasswordService().verify(
        SECOND_STUDENT_PASSWORD,
        female_student_account.password_hash,
    )
    assert PasswordService().verify(
        READY_MALE_STUDENT_PASSWORD,
        ready_male_student_account.password_hash,
    )
    assert PasswordService().verify(VISITOR_PASSWORD, visitor_account.password_hash)
    assert PasswordService().verify(
        "TstA1453b1c227",
        life_teacher_account.password_hash,
    )
    assert admin.force_password_change is False
    assert customer_service.force_password_change is False
    assert coach_account.force_password_change is False

    coach_person = db.scalar(select(Person).where(Person.full_name == "E2E教学教练"))
    assert coach_person is not None
    assert coach_person.phone_digest == LookupDigest(bytes(range(32))).digest(
        "19990000003"
    )
    coach_profile = db.get(StaffProfile, coach_person.id)
    assert coach_profile is not None and coach_profile.staff_type == "coach"
    assert second.coach_person_id == coach_person.id
    assert second.coach_account_id == coach_account.id

    projects = list(db.scalars(select(GameProject).order_by(GameProject.sort_order)))
    assert [(project.id, project.name, project.sort_order) for project in projects] == [
        (seed.id, seed.name, seed.sort_order) for seed in INITIAL_PROJECTS
    ]

    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id == second.student_person_id
        )
    )
    assert relationship is not None
    guardian_name = db.scalar(
        select(Person.full_name).where(Person.id == relationship.guardian_person_id)
    )
    student_name = db.scalar(
        select(Person.full_name).where(Person.id == relationship.student_person_id)
    )
    assert guardian_name == "E2E监护人"
    assert student_name == "E2E已绑定学员"
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
    )
    bound_student_id = db.scalar(
        select(Person.id).where(Person.full_name == "E2E已绑定学员")
    )
    pending_student_id = db.scalar(
        select(Person.id).where(Person.full_name == "E2E待绑定学员")
    )
    assert bound_student_id is not None
    assert pending_student_id is not None
    bound_student = db.get(StudentProfile, bound_student_id)
    assert bound_student is not None and bound_student.status == "active"
    assert (
        identity.readiness(bound_student_id, reference_date=date(2026, 7, 27)).guardian_status
        == "ready"
    )
    assert (
        identity.readiness(pending_student_id, reference_date=date(2026, 7, 27)).guardian_status
        == "pending"
    )
    assert (
        identity.readiness(
            E2E_READY_MALE_STUDENT_PERSON_ID,
            reference_date=date(2026, 7, 27),
        ).guardian_status
        == "ready"
    )
    ready_male_student = db.get(StudentProfile, E2E_READY_MALE_STUDENT_PERSON_ID)
    assert ready_male_student is not None and ready_male_student.status == "active"
    female_student = db.get(StudentProfile, E2E_SECOND_BOUND_STUDENT_ID)
    assert female_student is not None and female_student.status == "active"
    assert db.get(UserAccount, E2E_SECOND_BOUND_STUDENT_ACCOUNT_ID) is not None
    visitor = db.get(VisitorProfile, E2E_VISITOR_PERSON_ID)
    assert visitor is not None
    assert visitor.linked_student_id == E2E_SELF_STUDENT_PERSON_ID
    assert visitor.visit_reason == "探望学员临时住宿"
    assert db.scalar(
        select(UserAccount.id).where(UserAccount.person_id == pending_student_id)
    ) is None
    assert isinstance(second.admin_account_id, UUID)
    assert isinstance(second.coach_account_id, UUID)

    adult_students = {
        UUID("e2000000-0000-4000-8000-000000000010"): (
            "E2E招生学员1440",
            "E2E-ADMISSIONS-1440",
        ),
        UUID("e2000000-0000-4000-8000-000000000020"): (
            "E2E招生学员1024",
            "E2E-ADMISSIONS-1024",
        ),
        UUID("e2000000-0000-4000-8000-000000000030"): (
            "E2E招生学员768",
            "E2E-ADMISSIONS-768",
        ),
        UUID("e2000000-0000-4000-8000-000000000040"): (
            "E2E招生学员360",
            "E2E-ADMISSIONS-360",
        ),
    }
    for person_id, (name, student_number) in adult_students.items():
        adult = db.get(Person, person_id)
        assert adult is not None and adult.full_name == name
        adult_profile = db.get(StudentProfile, person_id)
        assert adult_profile is not None
        assert adult_profile.status == "active"
        assert adult_profile.student_number == student_number
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.guardian_person_id == second.guardian_person_id
        )
    ) == 7
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.ended_at.is_(None)
        )
    ) == 8
    relationships_by_student = {
        relationship.student_person_id: relationship.id
        for relationship in db.scalars(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == second.guardian_person_id,
                GuardianRelationship.ended_at.is_(None),
            )
        )
    }
    assert relationships_by_student == {
        E2E_BOUND_STUDENT_ID: UUID("e1200000-0000-4000-8000-000000000001"),
        E2E_READY_MALE_STUDENT_PERSON_ID: E2E_READY_MALE_GUARDIAN_RELATIONSHIP_ID,
        E2E_SECOND_BOUND_STUDENT_ID: UUID(
            "e2000000-0000-4000-8000-000000000012"
        ),
        UUID("e2000000-0000-4000-8000-000000000010"): UUID(
            "e2400000-0000-4000-8000-000000000001"
        ),
        UUID("e2000000-0000-4000-8000-000000000020"): UUID(
            "e2400000-0000-4000-8000-000000000002"
        ),
        UUID("e2000000-0000-4000-8000-000000000030"): UUID(
            "e2400000-0000-4000-8000-000000000003"
        ),
        UUID("e2000000-0000-4000-8000-000000000040"): UUID(
            "e2400000-0000-4000-8000-000000000004"
        ),
    }

    seeded_cohort = db.get(Cohort, E2E_COHORT_ID)
    assert seeded_cohort is not None and seeded_cohort.status == "open"
    classes = {
        teaching_class.id: teaching_class
        for teaching_class in db.scalars(
            select(TeachingClass).where(TeachingClass.cohort_id == E2E_COHORT_ID)
        )
    }
    assert set(classes) == {
        E2E_STANDARD_CLASS_ID,
        E2E_ELITE_CLASS_ID,
        E2E_FULL_CLASS_ID,
    }
    assert classes[E2E_STANDARD_CLASS_ID].capacity == 20
    assert classes[E2E_ELITE_CLASS_ID].capacity == 10
    assert classes[E2E_FULL_CLASS_ID].capacity == 1
    assert db.scalar(
        select(func.count(Enrollment.id)).where(
            Enrollment.class_id == E2E_FULL_CLASS_ID,
            Enrollment.status == "active",
        )
    ) == 1
    later_cohort = db.get(
        Cohort,
        UUID("e2100000-0000-4000-8000-000000000002"),
    )
    assert later_cohort is not None
    assert later_cohort.starts_at > seeded_cohort.starts_at
    assignments = {
        assignment.id: (
            assignment.class_id,
            assignment.assignment_type,
            assignment.valid_from,
            assignment.valid_until,
        )
        for assignment in db.scalars(
            select(CoachAssignment).where(
                CoachAssignment.coach_person_id == second.coach_person_id,
                CoachAssignment.ended_at.is_(None),
            )
        )
    }
    assert assignments == {
        UUID("e2130000-0000-4000-8000-000000000001"): (
            E2E_STANDARD_CLASS_ID,
            "primary",
            datetime.fromisoformat("2026-01-01T00:00:00+08:00"),
            datetime.fromisoformat("2031-01-01T00:00:00+08:00"),
        ),
        UUID("e2130000-0000-4000-8000-000000000002"): (
            E2E_ELITE_CLASS_ID,
            "assistant",
            datetime.fromisoformat("2026-01-01T00:00:00+08:00"),
            datetime.fromisoformat("2031-01-01T00:00:00+08:00"),
        ),
        UUID("e2130000-0000-4000-8000-000000000003"): (
            UUID("e2120000-0000-4000-8000-000000000004"),
            "primary",
            datetime.fromisoformat("2026-01-01T00:00:00+08:00"),
            datetime.fromisoformat("2031-01-01T00:00:00+08:00"),
        ),
    }
    assignment_count = db.scalar(
        select(func.count(CoachAssignment.id)).where(
            CoachAssignment.coach_person_id == second.coach_person_id,
            CoachAssignment.ended_at.is_(None),
        )
    )
    assert assignment_count is not None and assignment_count >= 3

    portal_payments = {
        payment.id: (
            payment.payer_account_id,
            payment.amount_cents,
            payment.paid_at,
        )
        for payment in db.scalars(
            select(Payment).where(
                Payment.id.in_(
                    [
                        UUID("e2520000-0000-4000-8000-000000000001"),
                        UUID("e2520000-0000-4000-8000-000000000002"),
                        UUID("e2520000-0000-4000-8000-000000000003"),
                    ]
                )
            )
        )
    }
    assert portal_payments == {
        UUID("e2520000-0000-4000-8000-000000000001"): (
            UUID("e1100000-0000-4000-8000-000000000003"),
            100_000,
            datetime.fromisoformat("2030-07-01T09:00:00+08:00"),
        ),
        UUID("e2520000-0000-4000-8000-000000000002"): (
            UUID("e1100000-0000-4000-8000-000000000007"),
            80_000,
            datetime.fromisoformat("2030-07-02T09:00:00+08:00"),
        ),
        UUID("e2520000-0000-4000-8000-000000000003"): (
            UUID("e1100000-0000-4000-8000-000000000008"),
            99_999,
            datetime.fromisoformat("2030-07-03T09:00:00+08:00"),
        ),
    }
    refund = db.get(
        PaymentRefund,
        UUID("e2530000-0000-4000-8000-000000000001"),
    )
    assert refund is not None
    assert refund.payment_id == UUID("e2520000-0000-4000-8000-000000000001")
    assert refund.amount_cents == 20_000
    assert refund.refund_type == "manual"

    service_account = db.get(ServiceAccount, E2E_SERVICE_ACCOUNT_ID)
    credential = db.scalar(
        select(ServiceAccountCredential).where(
            ServiceAccountCredential.service_account_id == E2E_SERVICE_ACCOUNT_ID
        )
    )
    assert service_account is not None and service_account.source_system == "e2e-payment"
    assert credential is not None
    assert credential.public_id == E2E_PAYMENT_CREDENTIAL_PUBLIC_ID
    assert (
        EnvelopeCipher(bytes(range(32))).decrypt(
            credential.secret_ciphertext,
            field_name="service_account_credentials.secret",
        )
        == E2E_PAYMENT_CREDENTIAL_SECRET
    )


def test_e2e_seed_rebuilds_housing_catalog_after_people_truncate(
    db: Session,
) -> None:
    kwargs = {
        "settings": settings(),
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }

    def assert_canonical_housing_counts() -> None:
        assert db.scalar(select(func.count(HousingBuilding.id))) == 4
        assert db.scalar(select(func.count(HousingFloor.id))) == 11
        assert db.scalar(select(func.count(HousingRoom.id))) == 188
        assert (
            db.scalar(
                select(func.count(HousingRoom.id)).where(
                    HousingRoom.verification_status == "pending_verification"
                )
            )
            == 171
        )
        assert db.scalar(select(func.sum(HousingRoom.capacity))) == 223

    seed_e2e(db, **kwargs)
    assert_canonical_housing_counts()
    room = db.scalar(
        select(HousingRoom).where(HousingRoom.room_number == "4-409")
    )
    assert room is not None
    room.notes = "E2E destructive reset marker"
    db.commit()

    seed_e2e(db, **kwargs)
    assert_canonical_housing_counts()
    rebuilt_room = db.scalar(
        select(HousingRoom).where(HousingRoom.room_number == "4-409")
    )
    assert rebuilt_room is not None
    assert rebuilt_room.notes is None


def test_e2e_seeded_identities_match_stored_birth_dates_and_sexes(db: Session) -> None:
    cipher = EnvelopeCipher(bytes(range(32)))
    seed_e2e(
        db,
        settings=settings(),
        cipher=cipher,
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
    )

    for person in db.scalars(select(Person).order_by(Person.id)):
        identity_number = cipher.decrypt(
            person.identity_ciphertext,
            field_name="identity_number",
        )
        normalized = normalize_cn_id(
            identity_number,
            expected_birth_date=person.date_of_birth,
        )
        assert normalized.sex == person.sex


def test_e2e_seed_never_reintroduces_plaintext_person_email(db: Session) -> None:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    seed_e2e(
        db,
        settings=settings(),
        cipher=cipher,
        lookup=lookup,
        passwords=PasswordService(),
    )

    people = list(db.scalars(select(Person).order_by(Person.id)))
    accounts = list(db.scalars(select(UserAccount).order_by(UserAccount.id)))
    assert people
    assert all(person.email is None for person in people)
    assert accounts
    assert all(account.email_normalized is None for account in accounts)
    assert any(person.email_ciphertext is not None for person in people)
    for person in people:
        assert (person.email_ciphertext is None) is (person.email_digest is None)
        if person.email_ciphertext is None:
            continue
        normalized_email = cipher.decrypt(person.email_ciphertext, field_name="email")
        assert person.email_digest == lookup.digest_domain(
            "person-email",
            normalized_email,
        )


def test_e2e_full_class_occupant_has_payment_link_and_confirmation_event(
    db: Session,
) -> None:
    seed_e2e(
        db,
        settings=settings(),
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
    )

    payment = db.scalar(
        select(Payment)
        .join(AdmissionCasePayment, AdmissionCasePayment.payment_id == Payment.id)
        .where(AdmissionCasePayment.admission_case_id == E2E_FULL_ADMISSION_CASE_ID)
    )
    assert payment is not None and payment.amount_cents == 1_280_000
    event = db.scalar(
        select(EnrollmentEvent).where(
            EnrollmentEvent.enrollment_id == E2E_FULL_ENROLLMENT_ID,
            EnrollmentEvent.event_type == "confirmed",
        )
    )
    assert event is not None
    assert event.new_class_id == E2E_FULL_CLASS_ID
    assert event.enrollment_count_snapshot == 1


def test_e2e_seed_rebuilds_evaluation_foundation_fixtures(db: Session) -> None:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    kwargs = {
        "settings": settings(),
        "cipher": cipher,
        "lookup": lookup,
        "passwords": PasswordService(),
    }

    seed_e2e(db, **kwargs)
    first_profile_ids = {
        row.project_id: row.id
        for row in db.scalars(select(ProjectAnalysisProfile))
    }
    seed_e2e(db, **kwargs)
    second_profile_ids = {
        row.project_id: row.id
        for row in db.scalars(select(ProjectAnalysisProfile))
    }

    assert first_profile_ids == second_profile_ids
    profiles = list(db.scalars(select(ProjectAnalysisProfile)))
    assert len(profiles) == len(INITIAL_PROJECTS)
    assert all(
        profile.version == 1
        and profile.mode == "manual_only"
        and profile.publish_mode == "review_required"
        and profile.adapter_id is None
        and profile.adapter_version is None
        and profile.scoring_rule_version is None
        and profile.required_fields == []
        and profile.confidence_thresholds == {}
        and profile.auto_publish_warning_levels == []
        and profile.effective_from == datetime(2026, 1, 1, tzinfo=UTC)
        for profile in profiles
    )

    student_profile = db.get(StudentProfile, E2E_SELF_STUDENT_PERSON_ID)
    assert student_profile is not None
    assert student_profile.status == "active"
    assert student_profile.game_account_limit == 3
    game_accounts = list(
        db.scalars(
            select(StudentGameAccount)
            .where(
                StudentGameAccount.student_person_id == E2E_SELF_STUDENT_PERSON_ID
            )
            .order_by(StudentGameAccount.valid_from, StudentGameAccount.id)
        )
    )
    assert [account.id for account in game_accounts] == [
        UUID("e2700000-0000-4000-8000-000000000001"),
        UUID("e2700000-0000-4000-8000-000000000002"),
        UUID("e2700000-0000-4000-8000-000000000003"),
    ]
    assert sum(account.unbound_at is None for account in game_accounts) == 2
    assert sum(account.unbound_at is not None for account in game_accounts) == 1
    assert {
        cipher.decrypt(
            account.identifier_ciphertext,
            field_name="student_game_id",
        )
        for account in game_accounts
    } == {"E2E-SELF-ACTIVE-LOL", "E2E-SELF-ACTIVE-PEL", "E2E-SELF-HISTORY"}
    assert {account.identifier_digest for account in game_accounts} == {
        student_game_id_digest(lookup, game_id)
        for game_id in (
            "E2E-SELF-ACTIVE-LOL",
            "E2E-SELF-ACTIVE-PEL",
            "E2E-SELF-HISTORY",
        )
    }

    life_teacher = db.get(
        Person,
        UUID("e1000000-0000-4000-8000-000000000009"),
    )
    assert life_teacher is not None and life_teacher.full_name == "E2E生活老师"
    life_profile = db.get(StaffProfile, life_teacher.id)
    assert life_profile is not None and life_profile.staff_type == "life_teacher"
    life_account = db.scalar(
        select(UserAccount).where(UserAccount.person_id == life_teacher.id)
    )
    assert life_account is not None and life_account.status == "active"
    life_role_keys = set(
        db.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == life_account.id)
        )
    )
    assert life_role_keys == {"life_teacher"}

    custom_role = db.get(
        EvaluationStaffRole,
        UUID("e2800000-0000-4000-8000-000000000001"),
    )
    assert custom_role is not None
    assert (
        custom_role.key,
        custom_role.name,
        custom_role.category,
        custom_role.is_builtin,
        custom_role.is_active,
    ) == ("e2e_camp_care", "E2E营务协同", "operations", False, True)

    assignments = list(
        db.scalars(
            select(CohortStaffAssignment)
            .where(CohortStaffAssignment.staff_person_id == life_teacher.id)
            .order_by(CohortStaffAssignment.id)
        )
    )
    assert len(assignments) == 4
    assert {
        (
            "project" if assignment.project_id is not None else "class",
            "ended" if assignment.ended_at is not None else "current",
        )
        for assignment in assignments
    } == {
        ("project", "current"),
        ("project", "ended"),
        ("class", "current"),
        ("class", "ended"),
    }

    guardian_id = _account_id(db, GUARDIAN_PHONE)
    coach_id = _account_id(db, COACH_PHONE)
    assert not RbacService(db).decision_context(
        guardian_id,
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
    ).decide("student_game_accounts.manage", {"family"}).allowed
    assert RbacService(db).decision_context(
        coach_id,
        occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
    ).decide("student_game_accounts.manage", {"family"}).allowed
    assert not RbacService(db).decision_context(
        coach_id,
        occurred_at=datetime(2032, 1, 1, tzinfo=UTC),
    ).decide("student_game_accounts.manage", {"family"}).allowed
    finite_override = db.get(
        UserPermissionOverride,
        UUID("e2900000-0000-4000-8000-000000000001"),
    )
    assert finite_override is not None
    assert finite_override.scope_kind == "family"
    assert finite_override.valid_from is not None
    assert finite_override.valid_until is not None
