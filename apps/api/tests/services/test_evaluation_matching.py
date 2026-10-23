from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.evaluations.game_id_digest import student_game_id_digest
from app.evaluations.game_models import StudentAuxiliaryPhone, StudentGameAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from app.security.encryption import LookupDigest
from app.teaching.models import Cohort, TeachingClass
from app.teaching.seed import INITIAL_PROJECTS

MATCH_NOW = datetime(2026, 8, 20, 8, tzinfo=UTC)
MATCH_LOOKUP_KEY = bytes(reversed(range(32)))
MATCH_GAME_ID = "PriorityPlayer"
MATCH_PHONE = "13800138000"
MATCH_AUXILIARY_ONLY_PHONE = "13900139000"


@dataclass(frozen=True)
class MatchScenario:
    admin: UserAccount
    guardian: UserAccount
    project_id: UUID
    students: dict[str, Person]
    enrollments: dict[str, Enrollment]


def _person(name: str, *, phone_digest: str | None = None) -> Person:
    marker = uuid4().hex
    return Person(
        full_name=name,
        identity_ciphertext=f"matching:{marker}",
        identity_digest=(marker * 2)[:64],
        phone_ciphertext=(f"matching-phone:{marker}" if phone_digest else None),
        phone_digest=phone_digest,
        email=None,
        sex="female",
        date_of_birth=date(2000, 1, 2),
    )


def _account(person: Person, key: str) -> UserAccount:
    marker = uuid4().hex
    return UserAccount(
        person_id=person.id,
        login_phone_digest=(marker * 2)[:64],
        email_normalized=f"matching-{key}-{marker[:8]}@example.test",
        password_hash="matching-test-password-hash",
        status="active",
        force_password_change=False,
    )


def _formal_enrollment(
    db: Session,
    *,
    student: Person,
    admin: UserAccount,
    cohort: Cohort,
    teaching_class: TeachingClass,
) -> Enrollment:
    admission_case = AdmissionCase(
        student_person_id=student.id,
        status="ready",
        agreed_tuition_cents=0,
        created_by_user_id=admin.id,
    )
    db.add(admission_case)
    db.flush()
    enrollment = Enrollment(
        admission_case_id=admission_case.id,
        student_person_id=student.id,
        cohort_id=cohort.id,
        class_id=teaching_class.id,
        agreed_tuition_cents=0,
        status="active",
        confirmed_at=MATCH_NOW - timedelta(days=30),
        created_by_user_id=admin.id,
    )
    db.add(enrollment)
    db.flush()
    admission_case.status = "enrolled"
    admission_case.current_enrollment_id = enrollment.id
    return enrollment


@pytest.fixture
def match_scenario(db: Session) -> MatchScenario:
    lookup = LookupDigest(MATCH_LOOKUP_KEY)
    admin_person = _person("匹配管理员")
    guardian_person = _person("匹配监护人")
    game_student = _person("游戏 ID 学员")
    phone_student = _person(
        "主手机号学员",
        phone_digest=lookup.digest(MATCH_PHONE),
    )
    auxiliary_student = _person("辅助手机号学员")
    db.add_all(
        [
            admin_person,
            guardian_person,
            game_student,
            phone_student,
            auxiliary_student,
        ]
    )
    db.flush()
    db.add_all(
        [
            StudentProfile(
                person_id=game_student.id,
                student_number=f"MATCH-GAME-{uuid4().hex[:8]}",
                status="active",
            ),
            StudentProfile(
                person_id=phone_student.id,
                student_number=f"MATCH-PHONE-{uuid4().hex[:8]}",
                status="active",
            ),
            StudentProfile(
                person_id=auxiliary_student.id,
                student_number=f"MATCH-AUX-{uuid4().hex[:8]}",
                status="active",
            ),
        ]
    )
    admin = _account(admin_person, "admin")
    guardian = _account(guardian_person, "guardian")
    db.add_all([admin, guardian])
    db.flush()
    db.add_all(
        [
            UserRole(user_id=admin.id, role_id=ROLES["super_admin"].id),
            UserRole(user_id=guardian.id, role_id=ROLES["guardian"].id),
        ]
    )

    cohort = Cohort(
        name=f"匹配营期-{uuid4().hex[:8]}",
        starts_at=MATCH_NOW - timedelta(days=5),
        ends_at=MATCH_NOW + timedelta(days=16),
        default_check_in_at=MATCH_NOW - timedelta(days=5),
        default_check_out_at=MATCH_NOW + timedelta(days=16),
        status="active",
        created_by_user_id=admin.id,
    )
    db.add(cohort)
    db.flush()
    teaching_class = TeachingClass(
        cohort_id=cohort.id,
        project_id=INITIAL_PROJECTS[0].id,
        name=f"匹配班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        is_active=True,
        created_by_user_id=admin.id,
    )
    db.add(teaching_class)
    db.flush()
    enrollments = {
        "game": _formal_enrollment(
            db,
            student=game_student,
            admin=admin,
            cohort=cohort,
            teaching_class=teaching_class,
        ),
        "phone": _formal_enrollment(
            db,
            student=phone_student,
            admin=admin,
            cohort=cohort,
            teaching_class=teaching_class,
        ),
        "auxiliary": _formal_enrollment(
            db,
            student=auxiliary_student,
            admin=admin,
            cohort=cohort,
            teaching_class=teaching_class,
        ),
    }
    upload_permission = db.scalar(
        select(Permission).where(Permission.key == "evaluation_media.upload")
    )
    assert upload_permission is not None
    db.add_all(
        [
            GuardianRelationship(
                guardian_person_id=guardian_person.id,
                student_person_id=game_student.id,
                relationship="mother",
                is_primary=True,
                valid_from=(MATCH_NOW - timedelta(days=30)).date(),
            ),
            UserPermissionOverride(
                user_id=guardian.id,
                permission_id=upload_permission.id,
                effect="allow",
                scope_kind="family",
                scope_id=None,
                valid_from=MATCH_NOW - timedelta(days=30),
                valid_until=MATCH_NOW + timedelta(days=30),
                created_by_user_id=admin.id,
                grant_reason="system evaluation matching authorization test",
            ),
        ]
    )
    db.add(
        StudentGameAccount(
            student_person_id=game_student.id,
            project_id=teaching_class.project_id,
            identifier_ciphertext="matching-game-id-ciphertext",
            identifier_digest=student_game_id_digest(lookup, MATCH_GAME_ID),
            valid_from=MATCH_NOW - timedelta(days=1),
            created_by_user_id=admin.id,
        )
    )
    db.add(
        StudentAuxiliaryPhone(
            student_person_id=auxiliary_student.id,
            phone_ciphertext="matching-auxiliary-phone-ciphertext",
            phone_digest=lookup.digest(MATCH_PHONE),
            valid_from=MATCH_NOW - timedelta(days=1),
            created_by_user_id=admin.id,
        )
    )
    db.add(
        StudentAuxiliaryPhone(
            student_person_id=auxiliary_student.id,
            phone_ciphertext="matching-auxiliary-only-phone-ciphertext",
            phone_digest=lookup.digest(MATCH_AUXILIARY_ONLY_PHONE),
            valid_from=MATCH_NOW - timedelta(days=1),
            created_by_user_id=admin.id,
        )
    )
    db.flush()
    return MatchScenario(
        admin=admin,
        guardian=guardian,
        project_id=teaching_class.project_id,
        students={
            "game": game_student,
            "phone": phone_student,
            "auxiliary": auxiliary_student,
        },
        enrollments=enrollments,
    )


def test_game_id_match_wins_before_primary_phone(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    target = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    ).match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=MATCH_GAME_ID,
        phone=MATCH_PHONE,
    )

    assert target is not None
    assert target.student_person_id == scenario.students["game"].id
    assert target.enrollment_id == scenario.enrollments["game"].id


def test_primary_phone_match_wins_before_auxiliary_phone(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    target = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    ).match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id="NoMatchingPlayer",
        phone=MATCH_PHONE,
    )

    assert target is not None
    assert target.student_person_id == scenario.students["phone"].id
    assert target.enrollment_id == scenario.enrollments["phone"].id


def test_auxiliary_phone_is_used_only_after_stronger_signals_miss(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    target = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    ).match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id="NoMatchingPlayer",
        phone=MATCH_AUXILIARY_ONLY_PHONE,
    )

    assert target is not None
    assert target.student_person_id == scenario.students["auxiliary"].id
    assert target.enrollment_id == scenario.enrollments["auxiliary"].id


def test_unauthorized_candidates_do_not_make_an_authorized_phone_ambiguous(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    scenario.students["game"].phone_digest = LookupDigest(MATCH_LOOKUP_KEY).digest(
        MATCH_PHONE
    )
    db.flush()

    target = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    ).match_upload_target(
        account_id=scenario.guardian.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=None,
        phone=MATCH_PHONE,
    )

    assert target is not None
    assert target.student_person_id == scenario.students["game"].id
    assert target.authorization_source == "family"


def test_higher_priority_ambiguity_stops_before_a_unique_phone_match(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    second_cohort = Cohort(
        name=f"第二匹配营期-{uuid4().hex[:8]}",
        starts_at=MATCH_NOW - timedelta(days=4),
        ends_at=MATCH_NOW + timedelta(days=17),
        default_check_in_at=MATCH_NOW - timedelta(days=4),
        default_check_out_at=MATCH_NOW + timedelta(days=17),
        status="active",
        created_by_user_id=scenario.admin.id,
    )
    db.add(second_cohort)
    db.flush()
    second_class = TeachingClass(
        cohort_id=second_cohort.id,
        project_id=scenario.project_id,
        name=f"第二匹配班-{uuid4().hex[:8]}",
        class_type="standard",
        capacity=20,
        default_tuition_cents=0,
        is_active=True,
        created_by_user_id=scenario.admin.id,
    )
    db.add(second_class)
    db.flush()
    _formal_enrollment(
        db,
        student=scenario.students["game"],
        admin=scenario.admin,
        cohort=second_cohort,
        teaching_class=second_class,
    )
    db.flush()

    match = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    ).match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=MATCH_GAME_ID,
        phone=MATCH_PHONE,
    )

    assert match is None


def test_zero_and_multiple_authorized_matches_share_the_same_pending_result(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    service = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    )
    zero_match = service.match_upload_target(
        account_id=scenario.guardian.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=None,
        phone=MATCH_PHONE,
    )

    scenario.students["game"].phone_digest = LookupDigest(MATCH_LOOKUP_KEY).digest(
        MATCH_PHONE
    )
    db.flush()
    multiple_match = service.match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=None,
        phone=MATCH_PHONE,
    )

    assert zero_match is multiple_match is None


def test_unbound_game_id_and_disabled_auxiliary_phone_are_ignored_at_boundary(
    db: Session,
    match_scenario: MatchScenario,
) -> None:
    from app.evaluations.matching_service import EvaluationMatchingService

    scenario = match_scenario
    game_account = db.scalar(
        select(StudentGameAccount).where(
            StudentGameAccount.student_person_id == scenario.students["game"].id,
            StudentGameAccount.project_id == scenario.project_id,
        )
    )
    assert game_account is not None
    game_account.unbound_at = MATCH_NOW
    game_account.unbound_by_user_id = scenario.admin.id
    game_account.unbind_reason = "matching boundary test"
    auxiliary_phone = db.scalar(
        select(StudentAuxiliaryPhone).where(
            StudentAuxiliaryPhone.student_person_id
            == scenario.students["auxiliary"].id,
            StudentAuxiliaryPhone.phone_digest
            == LookupDigest(MATCH_LOOKUP_KEY).digest(MATCH_AUXILIARY_ONLY_PHONE),
        )
    )
    assert auxiliary_phone is not None
    auxiliary_phone.disabled_at = MATCH_NOW
    auxiliary_phone.disabled_by_user_id = scenario.admin.id
    auxiliary_phone.disable_reason = "matching boundary test"
    db.flush()

    service = EvaluationMatchingService(
        db,
        lookup=LookupDigest(MATCH_LOOKUP_KEY),
    )
    phone_fallback = service.match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=MATCH_GAME_ID,
        phone=MATCH_PHONE,
    )
    disabled_auxiliary = service.match_upload_target(
        account_id=scenario.admin.id,
        project_id=scenario.project_id,
        occurred_at=MATCH_NOW,
        game_id=None,
        phone=MATCH_AUXILIARY_ONLY_PHONE,
    )

    assert phone_fallback is not None
    assert phone_fallback.student_person_id == scenario.students["phone"].id
    assert disabled_auxiliary is None
