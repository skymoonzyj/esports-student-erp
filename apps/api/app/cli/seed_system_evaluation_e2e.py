from __future__ import annotations

import shutil
import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_BOUND_STUDENT_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_CUSTOM_STAFF_ROLE_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_ACCOUNT_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    E2ESeedRefused,
    _require_e2e_environment,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.identity.models import GuardianRelationship, Person, StaffProfile, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.teaching.models import Cohort, GameProject, TeachingClass

AUTOMATIC_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")
MANUAL_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000003")

SYSTEM_EVALUATION_COHORT_ID = UUID("e4000000-0000-4000-8000-000000000001")
AUTOMATIC_CLASS_ID = UUID("e4010000-0000-4000-8000-000000000001")
MANUAL_CLASS_ID = UUID("e4010000-0000-4000-8000-000000000002")
SYSTEM_EVALUATION_ADMISSION_CASE_IDS = (
    UUID("e4020000-0000-4000-8000-000000000001"),
    UUID("e4020000-0000-4000-8000-000000000002"),
)
AUTOMATIC_ENROLLMENT_ID = UUID("e4030000-0000-4000-8000-000000000001")
MANUAL_ENROLLMENT_ID = UUID("e4030000-0000-4000-8000-000000000002")
AUTOMATIC_ANALYSIS_PROFILE_ID = UUID("e4040000-0000-4000-8000-000000000001")
MANUAL_ANALYSIS_PROFILE_ID = UUID("e4040000-0000-4000-8000-000000000002")
SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS = (
    UUID("e4050000-0000-4000-8000-000000000001"),
    UUID("e4050000-0000-4000-8000-000000000002"),
    UUID("e4050000-0000-4000-8000-000000000003"),
    UUID("e4050000-0000-4000-8000-000000000004"),
)
SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS = (
    UUID("e4060000-0000-4000-8000-000000000001"),
    UUID("e4060000-0000-4000-8000-000000000002"),
)

E2E_FIXTURE_DIRECTORY = ".system-evaluation-e2e"

_FIXTURE_START = datetime(2026, 1, 1, tzinfo=UTC)
_FIXTURE_END = datetime(2031, 1, 1, tzinfo=UTC)
_COHORT_END = datetime(2030, 12, 31, 23, 0, tzinfo=UTC)
_CONFIRMED_AT = datetime(2026, 1, 2, tzinfo=UTC)
_REQUIRED_FIELDS = ["kills", "deaths", "assists"]
_MEDIA_SLOTS = [
    {"key": "match_result", "label": "合成比赛截图", "required": True}
]
_STAFF_PERMISSION_KEYS = {
    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[0]: (
        "evaluation_media.upload",
        "evaluation_media.original.read",
        "evaluation_jobs.match",
    ),
    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[1]: (
        "evaluation_media.original.read",
        "evaluation_jobs.review",
    ),
    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2]: (
        "evaluation_media.upload",
        "evaluation_media.original.read",
        "evaluation_jobs.match",
        "evaluation_jobs.manual_input",
        "evaluation_jobs.review",
    ),
    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[3]: (
        "evaluation_media.original.read",
        "evaluation_jobs.review",
    ),
}


@dataclass(frozen=True)
class SystemEvaluationE2ESeedResult:
    automatic_enrollment_id: UUID
    manual_enrollment_id: UUID
    automatic_png_path: Path
    manual_png_path: Path


@dataclass(frozen=True)
class _AnalysisProfileFixture:
    id: UUID
    project_id: UUID
    mode: str
    adapter_id: str | None
    adapter_version: str | None
    scoring_rule_version: str | None
    confidence_thresholds: dict[str, float]
    auto_publish_warning_levels: list[str]


def _refuse_missing_fixture() -> None:
    raise E2ESeedRefused(
        "System evaluation E2E seed requires canonical E2E fixtures"
    )


def _require_base_fixtures(db: Session) -> None:
    expected_accounts = {
        E2E_ADMIN_ACCOUNT_ID,
        E2E_GUARDIAN_ACCOUNT_ID,
        E2E_SELF_STUDENT_ACCOUNT_ID,
        E2E_COACH_ACCOUNT_ID,
        E2E_LIFE_TEACHER_ACCOUNT_ID,
    }
    expected_people = {
        E2E_GUARDIAN_PERSON_ID,
        E2E_BOUND_STUDENT_ID,
        E2E_SELF_STUDENT_PERSON_ID,
        E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_PERSON_ID,
    }
    expected_account_people = {
        E2E_GUARDIAN_ACCOUNT_ID: E2E_GUARDIAN_PERSON_ID,
        E2E_SELF_STUDENT_ACCOUNT_ID: E2E_SELF_STUDENT_PERSON_ID,
        E2E_COACH_ACCOUNT_ID: E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_ACCOUNT_ID: E2E_LIFE_TEACHER_PERSON_ID,
    }
    accounts = {
        account.id: account
        for account in db.scalars(
            select(UserAccount).where(UserAccount.id.in_(expected_accounts))
        )
    }
    if set(accounts) != expected_accounts or any(
        accounts[account_id].person_id != person_id
        for account_id, person_id in expected_account_people.items()
    ):
        _refuse_missing_fixture()
    if (
        set(
            db.scalars(select(Person.id).where(Person.id.in_(expected_people)))
        )
        != expected_people
    ):
        _refuse_missing_fixture()
    if (
        set(
            db.scalars(
                select(StudentProfile.person_id).where(
                    StudentProfile.person_id.in_(
                        (E2E_BOUND_STUDENT_ID, E2E_SELF_STUDENT_PERSON_ID)
                    )
                )
            )
        )
        != {E2E_BOUND_STUDENT_ID, E2E_SELF_STUDENT_PERSON_ID}
    ):
        _refuse_missing_fixture()
    if (
        set(
            db.scalars(
                select(StaffProfile.person_id).where(
                    StaffProfile.person_id.in_(
                        (E2E_COACH_PERSON_ID, E2E_LIFE_TEACHER_PERSON_ID)
                    )
                )
            )
        )
        != {E2E_COACH_PERSON_ID, E2E_LIFE_TEACHER_PERSON_ID}
    ):
        _refuse_missing_fixture()

    relationship = db.get(
        GuardianRelationship,
        E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    )
    if (
        relationship is None
        or relationship.guardian_person_id != E2E_GUARDIAN_PERSON_ID
        or relationship.student_person_id != E2E_BOUND_STUDENT_ID
    ):
        _refuse_missing_fixture()
    if (
        set(
            db.scalars(
                select(GameProject.id).where(
                    GameProject.id.in_((AUTOMATIC_PROJECT_ID, MANUAL_PROJECT_ID))
                )
            )
        )
        != {AUTOMATIC_PROJECT_ID, MANUAL_PROJECT_ID}
        or db.get(EvaluationStaffRole, E2E_CUSTOM_STAFF_ROLE_ID) is None
    ):
        _refuse_missing_fixture()

    permission_ids = {
        PERMISSIONS[key].id
        for keys in _STAFF_PERMISSION_KEYS.values()
        for key in keys
    } | {PERMISSIONS["evaluation_media.upload"].id}
    if (
        set(
            db.scalars(
                select(Permission.id).where(Permission.id.in_(permission_ids))
            )
        )
        != permission_ids
    ):
        _refuse_missing_fixture()


def _restore_canonical_people(db: Session) -> None:
    for person_id in (
        E2E_GUARDIAN_PERSON_ID,
        E2E_BOUND_STUDENT_ID,
        E2E_SELF_STUDENT_PERSON_ID,
        E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_PERSON_ID,
    ):
        person = db.get(Person, person_id)
        assert person is not None
        person.deleted_at = None

    for account_id in (
        E2E_GUARDIAN_ACCOUNT_ID,
        E2E_SELF_STUDENT_ACCOUNT_ID,
        E2E_COACH_ACCOUNT_ID,
        E2E_LIFE_TEACHER_ACCOUNT_ID,
    ):
        account = db.get(UserAccount, account_id)
        assert account is not None
        account.status = "active"
        account.failed_login_count = 0
        account.locked_until = None

    for student_id in (E2E_BOUND_STUDENT_ID, E2E_SELF_STUDENT_PERSON_ID):
        profile = db.get(StudentProfile, student_id)
        assert profile is not None
        profile.status = "active"

    relationship = db.get(
        GuardianRelationship,
        E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    )
    assert relationship is not None
    relationship.is_primary = True
    relationship.valid_from = date(2026, 1, 1)
    relationship.valid_until = None
    relationship.ended_at = None
    relationship.ended_by_user_id = None
    relationship.end_reason = None


def _upsert_cohort_and_classes(db: Session) -> None:
    cohort = db.get(Cohort, SYSTEM_EVALUATION_COHORT_ID)
    if cohort is None:
        cohort = Cohort(
            id=SYSTEM_EVALUATION_COHORT_ID,
            name="E2E系统评价训练营",
            starts_at=_FIXTURE_START,
            ends_at=_COHORT_END,
            default_check_in_at=_FIXTURE_START,
            default_check_out_at=_COHORT_END,
            status="active",
            auto_start_enabled=False,
            auto_complete_enabled=False,
            created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        )
        db.add(cohort)
    else:
        cohort.name = "E2E系统评价训练营"
        cohort.starts_at = _FIXTURE_START
        cohort.ends_at = _COHORT_END
        cohort.default_check_in_at = _FIXTURE_START
        cohort.default_check_out_at = _COHORT_END
        cohort.status = "active"
        cohort.auto_start_enabled = False
        cohort.auto_complete_enabled = False
        cohort.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()

    class_fixtures = (
        (AUTOMATIC_CLASS_ID, AUTOMATIC_PROJECT_ID, "E2E自动评价班"),
        (MANUAL_CLASS_ID, MANUAL_PROJECT_ID, "E2E人工评价班"),
    )
    for class_id, project_id, name in class_fixtures:
        teaching_class = db.get(TeachingClass, class_id)
        if teaching_class is None:
            teaching_class = TeachingClass(
                id=class_id,
                cohort_id=SYSTEM_EVALUATION_COHORT_ID,
                project_id=project_id,
                name=name,
                class_type="standard",
                professional_team_id=None,
                capacity=20,
                default_tuition_cents=0,
                is_active=True,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(teaching_class)
        else:
            teaching_class.cohort_id = SYSTEM_EVALUATION_COHORT_ID
            teaching_class.project_id = project_id
            teaching_class.name = name
            teaching_class.class_type = "standard"
            teaching_class.professional_team_id = None
            teaching_class.capacity = 20
            teaching_class.default_tuition_cents = 0
            teaching_class.is_active = True
            teaching_class.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()


def _upsert_enrollments(db: Session) -> None:
    fixtures = (
        (
            SYSTEM_EVALUATION_ADMISSION_CASE_IDS[0],
            AUTOMATIC_ENROLLMENT_ID,
            E2E_BOUND_STUDENT_ID,
            AUTOMATIC_CLASS_ID,
        ),
        (
            SYSTEM_EVALUATION_ADMISSION_CASE_IDS[1],
            MANUAL_ENROLLMENT_ID,
            E2E_SELF_STUDENT_PERSON_ID,
            MANUAL_CLASS_ID,
        ),
    )
    cases: dict[UUID, AdmissionCase] = {}
    for case_id, _enrollment_id, student_id, _class_id in fixtures:
        admission_case = db.get(AdmissionCase, case_id)
        if admission_case is None:
            admission_case = AdmissionCase(
                id=case_id,
                student_person_id=student_id,
                status="ready",
                agreed_tuition_cents=0,
                current_enrollment_id=None,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(admission_case)
        else:
            admission_case.student_person_id = student_id
            admission_case.status = "ready"
            admission_case.agreed_tuition_cents = 0
            admission_case.tuition_override_reason = None
            admission_case.current_enrollment_id = None
            admission_case.closed_reason = None
            admission_case.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
        cases[case_id] = admission_case
    db.flush()

    for case_id, enrollment_id, student_id, class_id in fixtures:
        enrollment = db.get(Enrollment, enrollment_id)
        if enrollment is None:
            enrollment = Enrollment(
                id=enrollment_id,
                admission_case_id=case_id,
                student_person_id=student_id,
                cohort_id=SYSTEM_EVALUATION_COHORT_ID,
                class_id=class_id,
                agreed_tuition_cents=0,
                status="active",
                confirmed_at=_CONFIRMED_AT,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(enrollment)
        else:
            enrollment.admission_case_id = case_id
            enrollment.student_person_id = student_id
            enrollment.cohort_id = SYSTEM_EVALUATION_COHORT_ID
            enrollment.class_id = class_id
            enrollment.agreed_tuition_cents = 0
            enrollment.status = "active"
            enrollment.capacity_override_reason = None
            enrollment.confirmed_at = _CONFIRMED_AT
            enrollment.cancelled_at = None
            enrollment.completed_at = None
            enrollment.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()

    for case_id, enrollment_id, _student_id, _class_id in fixtures:
        admission_case = cases[case_id]
        admission_case.status = "enrolled"
        admission_case.current_enrollment_id = enrollment_id


def _upsert_analysis_profiles(db: Session) -> None:
    fixtures = (
        _AnalysisProfileFixture(
            id=AUTOMATIC_ANALYSIS_PROFILE_ID,
            project_id=AUTOMATIC_PROJECT_ID,
            mode="automatic",
            adapter_id="synthetic",
            adapter_version="1.0.0",
            scoring_rule_version="1",
            confidence_thresholds={
                "kills": 1.0,
                "deaths": 1.0,
                "assists": 1.0,
            },
            auto_publish_warning_levels=["blocking"],
        ),
        _AnalysisProfileFixture(
            id=MANUAL_ANALYSIS_PROFILE_ID,
            project_id=MANUAL_PROJECT_ID,
            mode="manual_only",
            adapter_id=None,
            adapter_version=None,
            scoring_rule_version=None,
            confidence_thresholds={},
            auto_publish_warning_levels=[],
        ),
    )
    for fixture in fixtures:
        profile = db.get(ProjectAnalysisProfile, fixture.id)
        competing = db.scalar(
            select(ProjectAnalysisProfile).where(
                ProjectAnalysisProfile.project_id == fixture.project_id,
                ProjectAnalysisProfile.version == 2,
            )
        )
        if competing is not None and competing.id != fixture.id:
            raise E2ESeedRefused(
                "System evaluation E2E analysis profile is not canonical"
            )
        expected = {
            "project_id": fixture.project_id,
            "version": 2,
            "mode": fixture.mode,
            "adapter_id": fixture.adapter_id,
            "adapter_version": fixture.adapter_version,
            "scoring_rule_version": fixture.scoring_rule_version,
            "required_fields": _REQUIRED_FIELDS,
            "media_slots": _MEDIA_SLOTS,
            "result_schema_version": "1",
            "confidence_thresholds": fixture.confidence_thresholds,
            "auto_publish_warning_levels": fixture.auto_publish_warning_levels,
            "publish_mode": "review_required",
            "effective_from": _FIXTURE_START,
            "configured_by_user_id": E2E_ADMIN_ACCOUNT_ID,
            "change_reason": "E2E system evaluation fixture",
        }
        if profile is not None:
            if any(getattr(profile, key) != value for key, value in expected.items()):
                raise E2ESeedRefused(
                    "System evaluation E2E analysis profile is not canonical"
                )
            continue
        db.add(
            ProjectAnalysisProfile(
                id=fixture.id,
                project_id=fixture.project_id,
                version=2,
                mode=fixture.mode,
                adapter_id=fixture.adapter_id,
                adapter_version=fixture.adapter_version,
                scoring_rule_version=fixture.scoring_rule_version,
                required_fields=list(_REQUIRED_FIELDS),
                media_slots=[dict(slot) for slot in _MEDIA_SLOTS],
                result_schema_version="1",
                confidence_thresholds=dict(fixture.confidence_thresholds),
                auto_publish_warning_levels=list(
                    fixture.auto_publish_warning_levels
                ),
                publish_mode="review_required",
                effective_from=_FIXTURE_START,
                configured_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                change_reason="E2E system evaluation fixture",
            )
        )
    db.flush()


def _upsert_staff_assignments(db: Session) -> None:
    fixtures = (
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[0],
            E2E_COACH_PERSON_ID,
            AUTOMATIC_PROJECT_ID,
        ),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[1],
            E2E_LIFE_TEACHER_PERSON_ID,
            AUTOMATIC_PROJECT_ID,
        ),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2],
            E2E_COACH_PERSON_ID,
            MANUAL_PROJECT_ID,
        ),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[3],
            E2E_LIFE_TEACHER_PERSON_ID,
            MANUAL_PROJECT_ID,
        ),
    )
    for assignment_id, staff_person_id, project_id in fixtures:
        assignment = db.get(CohortStaffAssignment, assignment_id)
        if assignment is None:
            assignment = CohortStaffAssignment(
                id=assignment_id,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
            db.add(assignment)
        assignment.cohort_id = SYSTEM_EVALUATION_COHORT_ID
        assignment.role_id = E2E_CUSTOM_STAFF_ROLE_ID
        assignment.staff_person_id = staff_person_id
        assignment.project_id = project_id
        assignment.class_id = None
        assignment.role_name_snapshot = "E2E营务协同"
        assignment.role_category_snapshot = "operations"
        assignment.valid_from = _FIXTURE_START
        assignment.valid_until = _FIXTURE_END
        assignment.ended_at = None
        assignment.ended_by_user_id = None
        assignment.end_reason = None
        assignment.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
    db.flush()

    desired = {
        (assignment_id, PERMISSIONS[permission_key].id)
        for assignment_id, permission_keys in _STAFF_PERMISSION_KEYS.items()
        for permission_key in permission_keys
    }
    existing = list(
        db.scalars(
            select(CohortStaffAssignmentPermission).where(
                CohortStaffAssignmentPermission.assignment_id.in_(
                    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS
                )
            )
        )
    )
    for permission in existing:
        if (permission.assignment_id, permission.permission_id) not in desired:
            db.delete(permission)
    for assignment_id, permission_id in desired:
        if (
            db.get(
                CohortStaffAssignmentPermission,
                (assignment_id, permission_id),
            )
            is None
        ):
            db.add(
                CohortStaffAssignmentPermission(
                    assignment_id=assignment_id,
                    permission_id=permission_id,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                )
            )


def _upsert_permission_overrides(db: Session) -> None:
    permission_id = PERMISSIONS["evaluation_media.upload"].id
    fixtures = (
        (
            SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS[0],
            E2E_GUARDIAN_ACCOUNT_ID,
            "family",
            "E2E family upload fixture",
        ),
        (
            SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS[1],
            E2E_SELF_STUDENT_ACCOUNT_ID,
            "self",
            "E2E self upload fixture",
        ),
    )
    for override_id, user_id, scope_kind, reason in fixtures:
        override = db.get(UserPermissionOverride, override_id)
        if override is None:
            override = UserPermissionOverride(id=override_id)
            db.add(override)
        override.user_id = user_id
        override.permission_id = permission_id
        override.effect = "allow"
        override.scope_kind = scope_kind
        override.scope_id = None
        override.valid_from = _FIXTURE_START
        override.valid_until = _FIXTURE_END
        override.created_by_user_id = E2E_ADMIN_ACCOUNT_ID
        override.grant_reason = reason


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def _synthetic_png(*, color: tuple[int, int, int]) -> bytes:
    width = 32
    height = 24
    scanline = b"\x00" + bytes(color) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", header),
            _png_chunk(b"IDAT", zlib.compress(scanline * height, level=9)),
            _png_chunk(b"IEND", b""),
        )
    )


def _replace_owned_fixture(
    root: Path,
    *,
    filename: str,
    payload: bytes,
) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fixture_root = root / E2E_FIXTURE_DIRECTORY
    if fixture_root.is_symlink() or (fixture_root.exists() and not fixture_root.is_dir()):
        fixture_root.unlink()
    elif fixture_root.exists():
        shutil.rmtree(fixture_root)
    fixture_root.mkdir(mode=0o700)
    path = fixture_root / filename
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def seed_system_evaluation_e2e(
    db: Session,
    *,
    settings: Settings,
) -> SystemEvaluationE2ESeedResult:
    _require_e2e_environment(settings)
    _require_base_fixtures(db)
    media_root, quarantine_root = settings.evaluation_media_paths()
    automatic_png_path = _replace_owned_fixture(
        media_root,
        filename="automatic.png",
        payload=_synthetic_png(color=(28, 94, 166)),
    )
    manual_png_path = _replace_owned_fixture(
        quarantine_root,
        filename="manual.png",
        payload=_synthetic_png(color=(36, 136, 92)),
    )
    with write_transaction(db):
        _restore_canonical_people(db)
        _upsert_cohort_and_classes(db)
        _upsert_enrollments(db)
        _upsert_analysis_profiles(db)
        _upsert_staff_assignments(db)
        _upsert_permission_overrides(db)
        db.flush()
    return SystemEvaluationE2ESeedResult(
        automatic_enrollment_id=AUTOMATIC_ENROLLMENT_ID,
        manual_enrollment_id=MANUAL_ENROLLMENT_ID,
        automatic_png_path=automatic_png_path,
        manual_png_path=manual_png_path,
    )


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        result = seed_system_evaluation_e2e(db, settings=settings)
    print(
        "System evaluation E2E fixtures rebuilt: "
        f"automatic={result.automatic_enrollment_id}, "
        f"manual={result.manual_enrollment_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
