from __future__ import annotations

import hashlib
import stat
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.cli.seed_system_evaluation_e2e as seed_module
from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_BOUND_STUDENT_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_ACCOUNT_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    E2ESeedRefused,
    seed_e2e,
)
from app.cli.seed_system_evaluation_e2e import (
    AUTOMATIC_ANALYSIS_PROFILE_ID,
    AUTOMATIC_CLASS_ID,
    AUTOMATIC_ENROLLMENT_ID,
    AUTOMATIC_PROJECT_ID,
    E2E_FIXTURE_DIRECTORY,
    MANUAL_ANALYSIS_PROFILE_ID,
    MANUAL_CLASS_ID,
    MANUAL_ENROLLMENT_ID,
    MANUAL_PROJECT_ID,
    SYSTEM_EVALUATION_ADMISSION_CASE_IDS,
    SYSTEM_EVALUATION_COHORT_ID,
    SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS,
    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS,
    SystemEvaluationE2ESeedResult,
    seed_system_evaluation_e2e,
)
from app.core.config import Settings
from app.evaluations.analysis_models import ProjectAnalysisProfile
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.processing_models import (
    EvaluationAdapterCertification,
    SystemEvaluationAttempt,
)
from app.evaluations.review_models import SystemEvaluationResultVersion
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.evaluations.task_models import SystemEvaluationTask
from app.evaluations.upload_service import EvaluationUploadService
from app.identity.models import GuardianRelationship, StaffProfile, StudentProfile
from app.rbac.models import Permission, UserPermissionOverride
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import Cohort, TeachingClass

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def settings(
    tmp_path: Path,
    *,
    app_env: str = "test",
    database: str = "system_evaluation_e2e_test",
) -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-v1-token-2026-change-me",
        evaluation_tool_web_service_token="evaluation-local-web-token-2026-change-me",
        evaluation_tool_worker_service_token=(
            "evaluation-local-worker-token-2026-change-me"
        ),
        evaluation_media_root=tmp_path / "erp-media",
        evaluation_media_quarantine_root=tmp_path / "erp-quarantine",
    )


def seed_kwargs(value: Settings) -> dict[str, object]:
    return {
        "settings": value,
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }


def _business_created_at(db: Session) -> dict[tuple[str, UUID], datetime]:
    rows: dict[tuple[str, UUID], datetime] = {}
    fixtures = (
        ("case", AdmissionCase, SYSTEM_EVALUATION_ADMISSION_CASE_IDS),
        (
            "enrollment",
            Enrollment,
            (AUTOMATIC_ENROLLMENT_ID, MANUAL_ENROLLMENT_ID),
        ),
        (
            "assignment",
            CohortStaffAssignment,
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS,
        ),
        (
            "profile",
            ProjectAnalysisProfile,
            (AUTOMATIC_ANALYSIS_PROFILE_ID, MANUAL_ANALYSIS_PROFILE_ID),
        ),
        (
            "override",
            UserPermissionOverride,
            SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS,
        ),
    )
    for label, model, identifiers in fixtures:
        for row in db.scalars(select(model).where(model.id.in_(identifiers))):
            rows[(label, row.id)] = row.created_at
    return rows


def _assert_synthetic_png(path: Path) -> None:
    payload = path.read_bytes()
    assert payload.startswith(_PNG_SIGNATURE)
    assert all(
        marker not in payload
        for marker in (
            "E2E监护人".encode(),
            "E2E已绑定学员".encode(),
            b"19990000005",
            str(E2E_BOUND_STUDENT_ID).encode(),
        )
    )
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (32, 24)
        assert image.info == {}


def test_system_evaluation_seed_is_deterministic_and_storage_scoped(
    db: Session,
    tmp_path: Path,
) -> None:
    test_settings = settings(tmp_path)
    seed_e2e(db, **seed_kwargs(test_settings))
    media_root, quarantine_root = test_settings.evaluation_media_paths()
    media_root.mkdir(mode=0o700, parents=True)
    quarantine_root.mkdir(mode=0o700, parents=True)
    media_sentinel = media_root / "unrelated-media.bin"
    quarantine_sentinel = quarantine_root / "unrelated-quarantine.bin"
    media_sentinel.write_bytes(b"preserve-media")
    quarantine_sentinel.write_bytes(b"preserve-quarantine")

    first = seed_system_evaluation_e2e(db, settings=test_settings)
    first_created_at = _business_created_at(db)
    assert len(first_created_at) == 12
    assert first == SystemEvaluationE2ESeedResult(
        automatic_enrollment_id=AUTOMATIC_ENROLLMENT_ID,
        manual_enrollment_id=MANUAL_ENROLLMENT_ID,
        automatic_png_path=media_root / E2E_FIXTURE_DIRECTORY / "automatic.png",
        manual_png_path=quarantine_root / E2E_FIXTURE_DIRECTORY / "manual.png",
    )

    first.automatic_png_path.write_bytes(b"stale automatic fixture")
    first.manual_png_path.write_bytes(b"stale manual fixture")
    (first.automatic_png_path.parent / "stale-media.png").write_bytes(b"stale")
    (first.manual_png_path.parent / "stale-quarantine.png").write_bytes(b"stale")

    second = seed_system_evaluation_e2e(db, settings=test_settings)

    assert second == first
    assert _business_created_at(db) == first_created_at
    assert media_sentinel.read_bytes() == b"preserve-media"
    assert quarantine_sentinel.read_bytes() == b"preserve-quarantine"
    assert set(first.automatic_png_path.parent.iterdir()) == {
        first.automatic_png_path
    }
    assert set(first.manual_png_path.parent.iterdir()) == {first.manual_png_path}
    _assert_synthetic_png(first.automatic_png_path)
    _assert_synthetic_png(first.manual_png_path)
    assert stat.S_IMODE(first.automatic_png_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.manual_png_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(first.automatic_png_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.manual_png_path.stat().st_mode) == 0o600
    assert hashlib.sha256(first.automatic_png_path.read_bytes()).digest() != hashlib.sha256(
        first.manual_png_path.read_bytes()
    ).digest()
    for model in (
        SystemEvaluationTask,
        SystemEvaluationAttempt,
        SystemEvaluationResultVersion,
        EvaluationAdapterCertification,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0

    students = {
        row.person_id: row.status
        for row in db.scalars(
            select(StudentProfile).where(
                StudentProfile.person_id.in_(
                    (E2E_BOUND_STUDENT_ID, E2E_SELF_STUDENT_PERSON_ID)
                )
            )
        )
    }
    assert students == {
        E2E_BOUND_STUDENT_ID: "active",
        E2E_SELF_STUDENT_PERSON_ID: "active",
    }
    guardian = db.get(
        GuardianRelationship,
        E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    )
    assert guardian is not None
    assert guardian.guardian_person_id == E2E_GUARDIAN_PERSON_ID
    assert guardian.student_person_id == E2E_BOUND_STUDENT_ID
    assert guardian.valid_until is None
    assert guardian.ended_at is None

    staff_profiles = set(
        db.scalars(
            select(StaffProfile.person_id).where(
                StaffProfile.person_id.in_(
                    (E2E_COACH_PERSON_ID, E2E_LIFE_TEACHER_PERSON_ID)
                )
            )
        )
    )
    assert staff_profiles == {E2E_COACH_PERSON_ID, E2E_LIFE_TEACHER_PERSON_ID}
    staff_accounts = {
        row.id: (row.person_id, row.status)
        for row in db.scalars(
            select(UserAccount).where(
                UserAccount.id.in_(
                    (E2E_COACH_ACCOUNT_ID, E2E_LIFE_TEACHER_ACCOUNT_ID)
                )
            )
        )
    }
    assert staff_accounts == {
        E2E_COACH_ACCOUNT_ID: (E2E_COACH_PERSON_ID, "active"),
        E2E_LIFE_TEACHER_ACCOUNT_ID: (E2E_LIFE_TEACHER_PERSON_ID, "active"),
    }

    cohort = db.get(Cohort, SYSTEM_EVALUATION_COHORT_ID)
    assert cohort is not None and cohort.status == "active"
    classes = {
        row.id: row
        for row in db.scalars(
            select(TeachingClass).where(
                TeachingClass.id.in_((AUTOMATIC_CLASS_ID, MANUAL_CLASS_ID))
            )
        )
    }
    assert classes[AUTOMATIC_CLASS_ID].project_id == AUTOMATIC_PROJECT_ID
    assert classes[MANUAL_CLASS_ID].project_id == MANUAL_PROJECT_ID
    assert MANUAL_PROJECT_ID == UUID("30000000-0000-4000-8000-000000000003")

    enrollments = {
        row.id: row
        for row in db.scalars(
            select(Enrollment).where(
                Enrollment.id.in_((AUTOMATIC_ENROLLMENT_ID, MANUAL_ENROLLMENT_ID))
            )
        )
    }
    assert set(enrollments) == {AUTOMATIC_ENROLLMENT_ID, MANUAL_ENROLLMENT_ID}
    assert (
        enrollments[AUTOMATIC_ENROLLMENT_ID].student_person_id,
        enrollments[AUTOMATIC_ENROLLMENT_ID].class_id,
        enrollments[AUTOMATIC_ENROLLMENT_ID].status,
    ) == (E2E_BOUND_STUDENT_ID, AUTOMATIC_CLASS_ID, "active")
    assert (
        enrollments[MANUAL_ENROLLMENT_ID].student_person_id,
        enrollments[MANUAL_ENROLLMENT_ID].class_id,
        enrollments[MANUAL_ENROLLMENT_ID].status,
    ) == (E2E_SELF_STUDENT_PERSON_ID, MANUAL_CLASS_ID, "active")
    cases = {
        row.id: row
        for row in db.scalars(
            select(AdmissionCase).where(
                AdmissionCase.id.in_(SYSTEM_EVALUATION_ADMISSION_CASE_IDS)
            )
        )
    }
    assert {
        (row.student_person_id, row.status, row.current_enrollment_id)
        for row in cases.values()
    } == {
        (E2E_BOUND_STUDENT_ID, "enrolled", AUTOMATIC_ENROLLMENT_ID),
        (E2E_SELF_STUDENT_PERSON_ID, "enrolled", MANUAL_ENROLLMENT_ID),
    }

    profiles = {
        row.id: row
        for row in db.scalars(
            select(ProjectAnalysisProfile).where(
                ProjectAnalysisProfile.id.in_(
                    (AUTOMATIC_ANALYSIS_PROFILE_ID, MANUAL_ANALYSIS_PROFILE_ID)
                )
            )
        )
    }
    automatic = profiles[AUTOMATIC_ANALYSIS_PROFILE_ID]
    assert (
        automatic.project_id,
        automatic.version,
        automatic.mode,
        automatic.adapter_id,
        automatic.adapter_version,
        automatic.scoring_rule_version,
        automatic.required_fields,
        automatic.confidence_thresholds,
        automatic.publish_mode,
    ) == (
        AUTOMATIC_PROJECT_ID,
        2,
        "automatic",
        "synthetic",
        "1.0.0",
        "1",
        ["kills", "deaths", "assists"],
        {"kills": 1.0, "deaths": 1.0, "assists": 1.0},
        "review_required",
    )
    manual = profiles[MANUAL_ANALYSIS_PROFILE_ID]
    assert (
        manual.project_id,
        manual.version,
        manual.mode,
        manual.adapter_id,
        manual.confidence_thresholds,
        manual.publish_mode,
    ) == (
        MANUAL_PROJECT_ID,
        2,
        "manual_only",
        None,
        {},
        "review_required",
    )
    assert automatic.media_slots == manual.media_slots == [
        {"key": "match_result", "label": "合成比赛截图", "required": True}
    ]

    assignments = {
        row.id: row
        for row in db.scalars(
            select(CohortStaffAssignment).where(
                CohortStaffAssignment.id.in_(SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS)
            )
        )
    }
    assert len(assignments) == 4
    assert {
        (row.staff_person_id, row.project_id, row.cohort_id)
        for row in assignments.values()
    } == {
        (E2E_COACH_PERSON_ID, AUTOMATIC_PROJECT_ID, SYSTEM_EVALUATION_COHORT_ID),
        (
            E2E_LIFE_TEACHER_PERSON_ID,
            AUTOMATIC_PROJECT_ID,
            SYSTEM_EVALUATION_COHORT_ID,
        ),
        (E2E_COACH_PERSON_ID, MANUAL_PROJECT_ID, SYSTEM_EVALUATION_COHORT_ID),
        (
            E2E_LIFE_TEACHER_PERSON_ID,
            MANUAL_PROJECT_ID,
            SYSTEM_EVALUATION_COHORT_ID,
        ),
    }
    assignment_permissions = {
        (row.assignment_id, permission.key)
        for row, permission in db.execute(
            select(CohortStaffAssignmentPermission, Permission)
            .join(
                Permission,
                Permission.id == CohortStaffAssignmentPermission.permission_id,
            )
            .where(
                CohortStaffAssignmentPermission.assignment_id.in_(
                    SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS
                )
            )
        )
    }
    assert assignment_permissions == {
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[0], "evaluation_media.upload"),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[0],
            "evaluation_media.original.read",
        ),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[0], "evaluation_jobs.match"),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[1], "evaluation_jobs.review"),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[1],
            "evaluation_media.original.read",
        ),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2], "evaluation_media.upload"),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2],
            "evaluation_media.original.read",
        ),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2], "evaluation_jobs.match"),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2], "evaluation_jobs.manual_input"),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[2], "evaluation_jobs.review"),
        (SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[3], "evaluation_jobs.review"),
        (
            SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[3],
            "evaluation_media.original.read",
        ),
    }

    overrides = {
        (row.user_id, permission.key, row.scope_kind, row.scope_id)
        for row, permission in db.execute(
            select(UserPermissionOverride, Permission)
            .join(Permission, Permission.id == UserPermissionOverride.permission_id)
            .where(
                UserPermissionOverride.id.in_(
                    SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS
                )
            )
        )
    }
    assert overrides == {
        (E2E_GUARDIAN_ACCOUNT_ID, "evaluation_media.upload", "family", None),
        (E2E_SELF_STUDENT_ACCOUNT_ID, "evaluation_media.upload", "self", None),
    }


def test_system_evaluation_seed_supports_real_family_upload_session(
    db: Session,
    tmp_path: Path,
) -> None:
    value = settings(tmp_path)
    value.evaluation_media_store_id = uuid4()
    seed_e2e(db, **seed_kwargs(value))
    seed_system_evaluation_e2e(db, settings=value)
    media_root, quarantine_root = value.evaluation_media_paths()
    store = PrivateMediaStore(
        root=media_root,
        quarantine_root=quarantine_root,
        max_bytes=value.evaluation_media_max_bytes,
        max_pixels=value.evaluation_media_max_pixels,
        preview_max_edge=value.evaluation_media_preview_max_edge,
        expected_identity=value.evaluation_media_store_id,
    )
    occurred_at = db.scalar(select(func.now()))
    assert isinstance(occurred_at, datetime)
    try:
        store.initialize_store_identity()
        upload = EvaluationUploadService(db, store=store).create_session(
            project_id=AUTOMATIC_PROJECT_ID,
            created_by_user_id=E2E_GUARDIAN_ACCOUNT_ID,
            occurred_at=occurred_at,
            student_person_id=E2E_BOUND_STUDENT_ID,
            enrollment_id=AUTOMATIC_ENROLLMENT_ID,
        )
    finally:
        store.close()

    assert upload.project_id == AUTOMATIC_PROJECT_ID
    assert upload.status == "open"
    assert upload.expires_at > occurred_at


@pytest.mark.parametrize(
    ("app_env", "database"),
    [
        pytest.param("development", "system_evaluation_e2e_test", id="app-env"),
        pytest.param("test", "production", id="database-name"),
    ],
)
def test_system_evaluation_seed_refuses_non_test_database(
    db: Session,
    tmp_path: Path,
    app_env: str,
    database: str,
) -> None:
    value = settings(tmp_path, app_env=app_env, database=database)

    with pytest.raises(E2ESeedRefused):
        seed_system_evaluation_e2e(db, settings=value)

    assert (
        db.scalar(
            select(func.count(Cohort.id)).where(
                Cohort.id == SYSTEM_EVALUATION_COHORT_ID
            )
        )
        == 0
    )
    assert not value.evaluation_media_paths()[0].exists()
    assert not value.evaluation_media_paths()[1].exists()


def test_system_evaluation_seed_requires_canonical_e2e_fixtures(
    db: Session,
    tmp_path: Path,
) -> None:
    value = settings(tmp_path)

    with pytest.raises(E2ESeedRefused, match="canonical E2E fixtures"):
        seed_system_evaluation_e2e(db, settings=value)

    assert db.get(Cohort, SYSTEM_EVALUATION_COHORT_ID) is None
    assert not value.evaluation_media_paths()[0].exists()
    assert not value.evaluation_media_paths()[1].exists()


def test_system_evaluation_seed_restores_mutable_access_fixtures(
    db: Session,
    tmp_path: Path,
) -> None:
    value = settings(tmp_path)
    seed_e2e(db, **seed_kwargs(value))
    seed_system_evaluation_e2e(db, settings=value)
    changed_at = datetime(2026, 6, 1, tzinfo=UTC)

    guardian = db.get(GuardianRelationship, E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID)
    coach = db.get(UserAccount, E2E_COACH_ACCOUNT_ID)
    assignment = db.get(
        CohortStaffAssignment,
        SYSTEM_EVALUATION_STAFF_ASSIGNMENT_IDS[0],
    )
    override = db.get(
        UserPermissionOverride,
        SYSTEM_EVALUATION_PERMISSION_OVERRIDE_IDS[0],
    )
    assert guardian is not None
    assert coach is not None
    assert assignment is not None
    assert override is not None
    guardian.ended_at = changed_at
    coach.status = "disabled"
    assignment.ended_at = changed_at
    assignment.ended_by_user_id = E2E_ADMIN_ACCOUNT_ID
    assignment.end_reason = "E2E access revocation exercise"
    override.effect = "deny"
    db.flush()

    seed_system_evaluation_e2e(db, settings=value)

    assert guardian.ended_at is None
    assert coach.status == "active"
    assert assignment.ended_at is None
    assert assignment.ended_by_user_id is None
    assert assignment.end_reason is None
    assert override.effect == "allow"


def test_system_evaluation_seed_refuses_to_rewrite_immutable_profile_history(
    db: Session,
    tmp_path: Path,
) -> None:
    value = settings(tmp_path)
    seed_e2e(db, **seed_kwargs(value))
    profile = ProjectAnalysisProfile(
        id=AUTOMATIC_ANALYSIS_PROFILE_ID,
        project_id=AUTOMATIC_PROJECT_ID,
        version=2,
        mode="manual_only",
        adapter_id=None,
        adapter_version=None,
        scoring_rule_version=None,
        required_fields=["tampered"],
        media_slots=[],
        result_schema_version="1",
        confidence_thresholds={},
        auto_publish_warning_levels=[],
        publish_mode="review_required",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        configured_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        change_reason="tampered E2E fixture",
    )
    db.add(profile)
    db.commit()

    with pytest.raises(E2ESeedRefused, match="analysis profile"):
        seed_system_evaluation_e2e(db, settings=value)

    db.expire_all()
    profile = db.get(ProjectAnalysisProfile, AUTOMATIC_ANALYSIS_PROFILE_ID)
    assert profile is not None
    assert profile.required_fields == ["tampered"]


def test_system_evaluation_seed_preflights_owned_media_before_business_writes(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = settings(tmp_path)
    seed_e2e(db, **seed_kwargs(value))
    original_replace = seed_module._replace_owned_fixture
    calls = 0

    def replace_then_fail(
        root: Path,
        *,
        filename: str,
        payload: bytes,
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic fixture write failure")
        return original_replace(root, filename=filename, payload=payload)

    monkeypatch.setattr(seed_module, "_replace_owned_fixture", replace_then_fail)

    with pytest.raises(OSError, match="synthetic fixture write failure"):
        seed_system_evaluation_e2e(db, settings=value)

    assert db.get(Cohort, SYSTEM_EVALUATION_COHORT_ID) is None
    assert db.get(Enrollment, AUTOMATIC_ENROLLMENT_ID) is None
