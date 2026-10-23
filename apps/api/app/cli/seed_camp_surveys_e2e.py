from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment, EnrollmentEvent
from app.auth.models import UserAccount
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_OTHER_GUARDIAN_PERSON_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2ESeedRefused,
    _require_e2e_environment,
    _seed_account,
    _seed_person,
    _upsert_seed_phone_identifier,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.evaluations.staff_models import CohortStaffAssignment, EvaluationStaffRole
from app.evaluations.survey_models import CampSurveyCycle, CampSurveySnapshotVersion
from app.evaluations.survey_snapshot_service import CampSurveySnapshotService
from app.evaluations.survey_window import survey_window_for_end
from app.food_preferences.models import FoodCuisineOption
from app.food_preferences.normalization import normalize_food_key
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
CAMP_SURVEY_COHORT_ID = UUID("e6100000-0000-4000-8000-000000000001")
CAMP_SURVEY_CLASS_ID = UUID("e6110000-0000-4000-8000-000000000001")
CAMP_SURVEY_CASE_ID = UUID("e6120000-0000-4000-8000-000000000001")
CAMP_SURVEY_ENROLLMENT_ID = UUID("e6130000-0000-4000-8000-000000000001")
CAMP_SURVEY_CYCLE_ID = UUID("e6140000-0000-4000-8000-000000000001")
CAMP_SURVEY_SNAPSHOT_ID = UUID("e6150000-0000-4000-8000-000000000001")
CAMP_SURVEY_SECOND_GUARDIAN_RELATIONSHIP_ID = UUID("e6160000-0000-4000-8000-000000000001")
CAMP_SURVEY_NO_ACCESS_PERSON_ID = UUID("e6170000-0000-4000-8000-000000000001")
CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID = UUID("e6180000-0000-4000-8000-000000000001")
CAMP_SURVEY_NO_ACCESS_PHONE = E2E_CREDENTIALS["camp_survey_no_access"].identifier
CAMP_SURVEY_NO_ACCESS_PASSWORD = E2E_CREDENTIALS["camp_survey_no_access"].password
CAMP_SURVEY_PERMISSION_OVERRIDE_IDS = (
    UUID("e6190000-0000-4000-8000-000000000001"),
    UUID("e6190000-0000-4000-8000-000000000002"),
)
CAMP_SURVEY_ASSIGNMENT_IDS = (
    UUID("e61a0000-0000-4000-8000-000000000001"),
    UUID("e61a0000-0000-4000-8000-000000000002"),
    UUID("e61a0000-0000-4000-8000-000000000003"),
    UUID("e61a0000-0000-4000-8000-000000000004"),
)
CAMP_SURVEY_CUSTOM_ROLE_ID = UUID("e2800000-0000-4000-8000-000000000001")
CAMP_SURVEY_CUISINE_ID = UUID("e61b0000-0000-4000-8000-000000000001")


@dataclass(frozen=True)
class CampSurveysE2ESeedResult:
    cycle_id: UUID
    cohort_id: UUID
    enrollment_id: UUID
    student_person_id: UUID
    guardian_person_ids: tuple[UUID, UUID]
    admin_account_id: UUID
    no_access_account_id: UUID


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")


def _upsert_relationship(
    db: Session,
    *,
    relationship_id: UUID,
    guardian_id: UUID,
    student_id: UUID,
    primary: bool,
    valid_from: date,
) -> None:
    row = db.get(GuardianRelationship, relationship_id)
    if row is None:
        row = GuardianRelationship(id=relationship_id)
        db.add(row)
    row.guardian_person_id = guardian_id
    row.student_person_id = student_id
    row.relationship = "mother"
    row.is_primary = primary
    row.valid_from = valid_from
    row.valid_until = None
    row.ended_at = None
    row.ended_by_user_id = None
    row.end_reason = None


def _upsert_role(db: Session, *, admin_id: UUID) -> EvaluationStaffRole:
    row = db.get(EvaluationStaffRole, CAMP_SURVEY_CUSTOM_ROLE_ID)
    if row is None:
        row = db.scalar(
            select(EvaluationStaffRole).where(EvaluationStaffRole.key == "e2e_camp_care")
        )
    if row is None:
        row = EvaluationStaffRole(id=CAMP_SURVEY_CUSTOM_ROLE_ID)
        db.add(row)
        row.key = "e2e_camp_care"
        row.name = "营期生活照护"
        row.category = "life_service"
        row.is_builtin = False
        row.is_active = True
        row.created_by_user_id = admin_id
    return row


def _upsert_food_catalog(db: Session) -> None:
    """Provide one ordinary cuisine alongside the migration's protected option."""
    row = db.get(FoodCuisineOption, CAMP_SURVEY_CUISINE_ID)
    if row is None:
        row = FoodCuisineOption(
            id=CAMP_SURVEY_CUISINE_ID,
            name="川菜",
            normalized_name=normalize_food_key("川菜"),
            sort_order=10,
            is_active=True,
            is_other=False,
            created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            updated_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        )
        db.add(row)
    else:
        row.name = "川菜"
        row.normalized_name = normalize_food_key("川菜")
        row.sort_order = 10
        row.is_active = True
        row.is_other = False
        row.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()


def _upsert_no_access(
    db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest, passwords: PasswordService
) -> None:
    person = db.get(Person, CAMP_SURVEY_NO_ACCESS_PERSON_ID)
    if person is None:
        person = _seed_person(
            person_id=CAMP_SURVEY_NO_ACCESS_PERSON_ID,
            full_name="E2E无营期评价权限账号",
            identity_birth="19950101",
            identity_sequence=199,
            sex="male",
            date_of_birth=date(1995, 1, 1),
            phone=CAMP_SURVEY_NO_ACCESS_PHONE,
            email="e2e-camp-survey-no-access@example.com",
            cipher=cipher,
            lookup=lookup,
        )
        db.add(person)
        db.flush()
    account = db.get(UserAccount, CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID)
    if account is None:
        account = _seed_account(
            account_id=CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID,
            person_id=person.id,
            phone=CAMP_SURVEY_NO_ACCESS_PHONE,
            email="e2e-camp-survey-no-access@example.com",
            password=CAMP_SURVEY_NO_ACCESS_PASSWORD,
            lookup=lookup,
            passwords=passwords,
        )
        db.add(account)
    else:
        account.person_id = person.id
        account.login_phone_digest = None
        account.email_normalized = None
        account.status = "active"
        account.force_password_change = False
        account.failed_login_count = 0
        account.locked_until = None
        if not passwords.verify(CAMP_SURVEY_NO_ACCESS_PASSWORD, account.password_hash):
            account.password_hash = passwords.hash(CAMP_SURVEY_NO_ACCESS_PASSWORD)
    db.flush()
    _upsert_seed_phone_identifier(
        db,
        account_id=account.id,
        phone=CAMP_SURVEY_NO_ACCESS_PHONE,
        lookup=lookup,
    )
    db.flush()
    if db.get(UserRole, (account.id, ROLES["staff"].id)) is None:
        db.add(UserRole(user_id=account.id, role_id=ROLES["staff"].id))


def seed_camp_surveys_e2e(
    db: Session,
    *,
    settings: Settings,
    occurred_at: datetime,
    second_guardian_relationship_id: UUID = CAMP_SURVEY_SECOND_GUARDIAN_RELATIONSHIP_ID,
    cipher: EnvelopeCipher | None = None,
    lookup: LookupDigest | None = None,
    passwords: PasswordService | None = None,
) -> CampSurveysE2ESeedResult:
    _require_e2e_environment(settings)
    _aware(occurred_at)
    cipher = cipher or EnvelopeCipher(bytes(range(32)))
    lookup = lookup or LookupDigest(bytes(range(32)))
    passwords = passwords or PasswordService()
    base = db.get(UserAccount, E2E_ADMIN_ACCOUNT_ID)
    if base is None or db.get(Person, E2E_SECOND_BOUND_STUDENT_ID) is None:
        raise E2ESeedRefused("Camp survey E2E seed requires canonical E2E fixtures")
    local = occurred_at.astimezone(_SHANGHAI)
    starts = local - timedelta(days=20)
    completed = local - timedelta(days=1)
    survey_due_at = survey_window_for_end(completed).closes_at
    with write_transaction(db):
        _upsert_food_catalog(db)
        _upsert_no_access(db, cipher=cipher, lookup=lookup, passwords=passwords)
        _upsert_relationship(
            db,
            relationship_id=second_guardian_relationship_id,
            guardian_id=E2E_OTHER_GUARDIAN_PERSON_ID,
            student_id=E2E_SECOND_BOUND_STUDENT_ID,
            primary=False,
            valid_from=starts.date(),
        )
        cohort = db.get(Cohort, CAMP_SURVEY_COHORT_ID)
        if cohort is None:
            cohort = Cohort(
                id=CAMP_SURVEY_COHORT_ID,
                name="E2E营期评价营",
                starts_at=starts,
                ends_at=completed,
                default_check_in_at=starts,
                default_check_out_at=completed,
                status="completed",
                auto_start_enabled=False,
                auto_complete_enabled=False,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(cohort)
            db.flush()
        project = db.get(GameProject, _PROJECT_ID)
        if project is None:
            raise E2ESeedRefused("canonical E2E project missing")
        teaching_class = db.get(TeachingClass, CAMP_SURVEY_CLASS_ID)
        if teaching_class is None:
            teaching_class = TeachingClass(
                id=CAMP_SURVEY_CLASS_ID,
                cohort_id=cohort.id,
                project_id=_PROJECT_ID,
                name="E2E营期评价班",
                class_type="standard",
                capacity=30,
                default_tuition_cents=0,
                is_active=True,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(teaching_class)
            db.flush()
        case = db.get(AdmissionCase, CAMP_SURVEY_CASE_ID)
        if case is None:
            case = AdmissionCase(
                id=CAMP_SURVEY_CASE_ID,
                student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
                status="ready",
                agreed_tuition_cents=0,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(case)
            db.flush()
        enrollment = db.get(Enrollment, CAMP_SURVEY_ENROLLMENT_ID)
        if enrollment is None:
            enrollment = Enrollment(
                id=CAMP_SURVEY_ENROLLMENT_ID,
                admission_case_id=case.id,
                student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
                cohort_id=cohort.id,
                class_id=teaching_class.id,
                agreed_tuition_cents=0,
                status="completed",
                confirmed_at=starts,
                completed_at=completed,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(enrollment)
            db.flush()
            case.current_enrollment_id = enrollment.id
            case.status = "enrolled"
            db.add(
                EnrollmentEvent(
                    id=UUID("e61c0000-0000-4000-8000-000000000001"),
                    enrollment_id=enrollment.id,
                    event_type="confirmed",
                    new_class_id=teaching_class.id,
                    old_class_id=None,
                    old_status=None,
                    new_status="active",
                    reason="e2e",
                    enrollment_count_snapshot=1,
                    actor_user_id=E2E_ADMIN_ACCOUNT_ID,
                    request_id="camp-surveys-e2e-confirmed",
                    occurred_at=starts,
                )
            )
            db.add(
                EnrollmentEvent(
                    id=UUID("e61c0000-0000-4000-8000-000000000002"),
                    enrollment_id=enrollment.id,
                    event_type="completed",
                    new_class_id=None,
                    old_class_id=teaching_class.id,
                    old_status="active",
                    new_status="completed",
                    reason="e2e",
                    enrollment_count_snapshot=1,
                    actor_user_id=E2E_ADMIN_ACCOUNT_ID,
                    request_id="camp-surveys-e2e-completed",
                    occurred_at=completed,
                )
            )
        else:
            enrollment.status = "completed"
            enrollment.confirmed_at = starts
            enrollment.completed_at = completed
        db.flush()
        assignments = [
            (
                CAMP_SURVEY_ASSIGNMENT_IDS[0],
                E2E_COACH_PERSON_ID,
                "primary",
                "主教练",
                "teaching",
                starts,
                completed,
            ),
            (
                CAMP_SURVEY_ASSIGNMENT_IDS[1],
                E2E_LIFE_TEACHER_PERSON_ID,
                "assistant",
                "助教",
                "teaching",
                starts,
                starts + timedelta(days=5),
            ),
        ]
        for aid, person_id, typ, _name, _cat, vf, vu in assignments:
            coach_assignment = db.get(CoachAssignment, aid)
            if coach_assignment is None:
                coach_assignment = CoachAssignment(id=aid)
                db.add(coach_assignment)
            coach_assignment.class_id = teaching_class.id
            coach_assignment.coach_person_id = person_id
            coach_assignment.assignment_type = typ
            coach_assignment.valid_from = vf
            coach_assignment.valid_until = vu
            coach_assignment.ended_at = None
            coach_assignment.created_by_user_id = E2E_ADMIN_ACCOUNT_ID
        role = _upsert_role(db, admin_id=E2E_ADMIN_ACCOUNT_ID)
        db.flush()
        for aid, person_id, vf, vu in (
            (CAMP_SURVEY_ASSIGNMENT_IDS[2], E2E_LIFE_TEACHER_PERSON_ID, starts, completed),
            (
                CAMP_SURVEY_ASSIGNMENT_IDS[3],
                E2E_COACH_PERSON_ID,
                starts,
                starts + timedelta(days=6),
            ),
        ):
            staff_assignment = db.get(CohortStaffAssignment, aid)
            if staff_assignment is None:
                staff_assignment = CohortStaffAssignment(id=aid)
                db.add(staff_assignment)
            staff_assignment.cohort_id = cohort.id
            staff_assignment.role_id = (
                role.id
                if person_id == E2E_COACH_PERSON_ID
                else UUID("41000000-0000-4000-8000-000000000001")
            )
            staff_assignment.staff_person_id = person_id
            staff_assignment.project_id = None
            staff_assignment.class_id = teaching_class.id
            staff_assignment.role_name_snapshot = (
                "营期生活照护" if person_id == E2E_COACH_PERSON_ID else "生活老师"
            )
            staff_assignment.role_category_snapshot = "life_service"
            staff_assignment.valid_from = vf
            staff_assignment.valid_until = vu
            staff_assignment.ended_at = None
            staff_assignment.ended_by_user_id = None
            staff_assignment.end_reason = None
            staff_assignment.created_by_user_id = E2E_ADMIN_ACCOUNT_ID
        db.flush()
        existing_cycle = db.scalar(
            select(CampSurveyCycle).where(
                CampSurveyCycle.enrollment_id == enrollment.id,
            )
        )
        if existing_cycle is not None and existing_cycle.id != CAMP_SURVEY_CYCLE_ID:
            raise E2ESeedRefused("canonical camp survey cycle ID collision")
        cycle = db.get(CampSurveyCycle, CAMP_SURVEY_CYCLE_ID)
        if cycle is None:
            cycle = CampSurveyCycle(
                id=CAMP_SURVEY_CYCLE_ID,
                enrollment_id=enrollment.id,
                student_person_id=enrollment.student_person_id,
                cohort_id=enrollment.cohort_id,
                class_id=enrollment.class_id,
                opens_at=completed,
                due_at=survey_due_at,
                current_snapshot_version=1,
            )
            db.add(cycle)
        else:
            cycle.enrollment_id = enrollment.id
            cycle.student_person_id = enrollment.student_person_id
            cycle.cohort_id = enrollment.cohort_id
            cycle.class_id = enrollment.class_id
            cycle.opens_at = completed
            cycle.due_at = survey_due_at
            cycle.current_snapshot_version = 1
        db.flush()
        snapshot = db.scalar(
            select(CampSurveySnapshotVersion).where(
                CampSurveySnapshotVersion.cycle_id == cycle.id,
                CampSurveySnapshotVersion.version == 1,
            )
        )
        if snapshot is None:
            snapshot = CampSurveySnapshotVersion(
                id=CAMP_SURVEY_SNAPSHOT_ID,
                cycle_id=cycle.id,
                version=1,
                change_reason="initial roster",
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(snapshot)
            db.flush()
            db.add_all(
                CampSurveySnapshotService(db)._build_snapshot_rows(
                    enrollment,
                    cohort,
                    snapshot.id,
                )
            )
            db.flush()
        roster = CampSurveySnapshotService(db).ensure_cycle_in_transaction(
            enrollment.id,
            actor_user_id=E2E_ADMIN_ACCOUNT_ID,
        )
        permission_ids = (
            PERMISSIONS["camp_surveys.identity.read"].id,
            PERMISSIONS["camp_surveys.print"].id,
        )
        for oid, pid in zip(CAMP_SURVEY_PERMISSION_OVERRIDE_IDS, permission_ids, strict=True):
            permission_override = db.get(UserPermissionOverride, oid)
            if permission_override is None:
                permission_override = UserPermissionOverride(id=oid)
                db.add(permission_override)
            permission_override.user_id = E2E_ADMIN_ACCOUNT_ID
            permission_override.permission_id = pid
            permission_override.effect = "allow"
            permission_override.scope_kind = "global"
            permission_override.scope_id = None
            permission_override.valid_from = starts
            permission_override.valid_until = survey_due_at
            permission_override.created_by_user_id = E2E_ADMIN_ACCOUNT_ID
            permission_override.grant_reason = (
                "E2E camp survey real-name permission fixture"
            )
        db.flush()
    return CampSurveysE2ESeedResult(
        roster.cycle.id,
        CAMP_SURVEY_COHORT_ID,
        CAMP_SURVEY_ENROLLMENT_ID,
        E2E_SECOND_BOUND_STUDENT_ID,
        (E2E_GUARDIAN_PERSON_ID, E2E_OTHER_GUARDIAN_PERSON_ID),
        E2E_ADMIN_ACCOUNT_ID,
        CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID,
    )


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    return base64.b64decode(
        getattr(settings, field_name).get_secret_value(), altchars=b"-_", validate=True
    )


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        result = seed_camp_surveys_e2e(
            db,
            settings=settings,
            occurred_at=datetime.now(UTC),
            cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
    print(
        json.dumps(
            {
                "cycle_id": str(result.cycle_id),
                "cohort_id": str(result.cohort_id),
                "enrollment_id": str(result.enrollment_id),
                "student_person_id": str(result.student_person_id),
                "guardian_person_ids": [str(x) for x in result.guardian_person_ids],
                "admin_account_id": str(result.admin_account_id),
                "no_access_account_id": str(result.no_access_account_id),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
