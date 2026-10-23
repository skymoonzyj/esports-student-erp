from __future__ import annotations

from datetime import datetime
from typing import cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_ACCOUNT_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
)
from app.cli.seed_talent_e2e import (
    TALENT_E2E_GUARDIAN_RELATIONSHIP_ID,
    TALENT_E2E_LEGACY_SESSION_ID,
    TalentE2EEvidenceError,
    TalentE2ESeedRefused,
    TalentE2ESeedResult,
    seed_talent_e2e,
    validate_talent_e2e_audit_events,
    validate_talent_e2e_evidence_payload,
)
from app.core.config import Settings
from app.identity.models import GuardianRelationship, StaffProfile
from app.rbac.models import Role, UserPermissionOverride, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.talent_tests.models import (
    TalentTestReport,
    TalentTestResult,
    TalentTestSession,
)
from app.teaching.models import CoachAssignment, Cohort

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")


def settings(
    *,
    app_env: str = "test",
    database: str = "erp_e2e",
) -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
        evaluation_tool_web_service_token="evaluation-local-web-token-2026-change-me",
        evaluation_tool_worker_service_token=(
            "evaluation-local-worker-token-2026-change-me"
        ),
    )


def run_seed(
    db: Session,
    value: Settings,
    *,
    include_legacy_v1: bool = False,
) -> TalentE2ESeedResult:
    return seed_talent_e2e(
        db,
        settings=value,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))),
        passwords=PasswordService(),
        include_legacy_v1=include_legacy_v1,
    )


@pytest.mark.parametrize(
    "unsafe_settings",
    [
        settings(app_env="production"),
        settings(database="production"),
        settings(database="erp_e2e?dbname=production"),
    ],
)
def test_talent_seed_refuses_production_before_touching_database(
    unsafe_settings: Settings,
) -> None:
    db = cast(Session, Mock(spec=Session))

    with pytest.raises(TalentE2ESeedRefused) as exc_info:
        run_seed(db, unsafe_settings)

    assert "production" not in str(exc_info.value)
    assert "isolated" in str(exc_info.value)
    assert cast(Mock, db).mock_calls == []


def _role_keys(db: Session, account_id: UUID) -> set[str]:
    return set(
        db.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == account_id)
        )
    )


def test_talent_seed_is_idempotent_and_builds_real_relationships(db: Session) -> None:
    value = settings()

    first = run_seed(db, value)
    first_relationship_created_at = db.scalar(
        select(GuardianRelationship.created_at).where(
            GuardianRelationship.id == TALENT_E2E_GUARDIAN_RELATIONSHIP_ID
        )
    )
    second = run_seed(db, value)

    assert first == second == TalentE2ESeedResult(
        student_account_id=E2E_SELF_STUDENT_ACCOUNT_ID,
        student_person_id=E2E_SELF_STUDENT_PERSON_ID,
        guardian_account_id=E2E_GUARDIAN_ACCOUNT_ID,
        guardian_person_id=E2E_GUARDIAN_PERSON_ID,
        assigned_coach_account_id=E2E_COACH_ACCOUNT_ID,
        assigned_coach_person_id=E2E_COACH_PERSON_ID,
        unrelated_coach_account_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
        unrelated_coach_person_id=E2E_LIFE_TEACHER_PERSON_ID,
        ordinary_admin_account_id=E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
        super_admin_account_id=E2E_ADMIN_ACCOUNT_ID,
    )
    relationship = db.get(
        GuardianRelationship,
        TALENT_E2E_GUARDIAN_RELATIONSHIP_ID,
    )
    assert relationship is not None
    assert relationship.guardian_person_id == E2E_GUARDIAN_PERSON_ID
    assert relationship.student_person_id == E2E_SELF_STUDENT_PERSON_ID
    assert relationship.valid_until is None
    assert relationship.ended_at is None
    assert relationship.created_at == first_relationship_created_at

    assert _role_keys(db, E2E_SELF_STUDENT_ACCOUNT_ID) == {"student"}
    assert _role_keys(db, E2E_GUARDIAN_ACCOUNT_ID) == {"guardian"}
    assert _role_keys(db, E2E_COACH_ACCOUNT_ID) == {"coach"}
    assert _role_keys(db, E2E_LIFE_TEACHER_ACCOUNT_ID) == {"coach"}
    assert _role_keys(db, E2E_CUSTOMER_SERVICE_ACCOUNT_ID) == {"admin"}
    assert _role_keys(db, E2E_ADMIN_ACCOUNT_ID) == {"super_admin"}

    assigned_coach_profile = db.get(StaffProfile, E2E_COACH_PERSON_ID)
    unrelated_coach_profile = db.get(StaffProfile, E2E_LIFE_TEACHER_PERSON_ID)
    ordinary_admin_account = db.get(UserAccount, E2E_CUSTOMER_SERVICE_ACCOUNT_ID)
    assert assigned_coach_profile is not None
    assert unrelated_coach_profile is not None
    assert ordinary_admin_account is not None
    ordinary_admin_profile = db.get(StaffProfile, ordinary_admin_account.person_id)
    assert ordinary_admin_profile is not None
    assert assigned_coach_profile.staff_type == "coach"
    assert unrelated_coach_profile.staff_type == "coach"
    assert ordinary_admin_profile.staff_type == "administration"
    assert db.scalar(
        select(func.count())
        .select_from(CoachAssignment)
        .where(CoachAssignment.coach_person_id == E2E_LIFE_TEACHER_PERSON_ID)
    ) == 0
    assigned = db.scalar(
        select(CoachAssignment)
        .where(CoachAssignment.coach_person_id == E2E_COACH_PERSON_ID)
        .order_by(CoachAssignment.id)
    )
    assert assigned is not None
    assert assigned.valid_from < datetime.now(assigned.valid_from.tzinfo)
    assert assigned.valid_until is not None
    assert assigned.valid_until > datetime.now(assigned.valid_until.tzinfo)
    cohort = db.get(Cohort, UUID("e2100000-0000-4000-8000-000000000002"))
    assert cohort is not None
    assert cohort.status == "active"
    assert cohort.starts_at < datetime.now(cohort.starts_at.tzinfo) < cohort.ends_at

    assert db.scalar(
        select(func.count())
        .select_from(UserPermissionOverride)
        .where(
            UserPermissionOverride.user_id.in_(
                (E2E_CUSTOMER_SERVICE_ACCOUNT_ID, E2E_LIFE_TEACHER_ACCOUNT_ID)
            )
        )
    ) == 0
    for model in (TalentTestSession, TalentTestResult, TalentTestReport):
        assert db.scalar(select(func.count()).select_from(model)) == 0


def test_talent_seed_can_build_one_deterministic_legacy_v1_round(db: Session) -> None:
    first = run_seed(db, settings(), include_legacy_v1=True)
    second = run_seed(db, settings(), include_legacy_v1=True)

    assert first == second
    sessions = list(db.scalars(select(TalentTestSession)))
    assert [session.id for session in sessions] == [TALENT_E2E_LEGACY_SESSION_ID]
    legacy = sessions[0]
    assert legacy.person_id == E2E_SELF_STUDENT_PERSON_ID
    assert legacy.created_by_user_id == E2E_SELF_STUDENT_ACCOUNT_ID
    assert legacy.status == "in_progress"
    assert legacy.catalog_version == "talent-training-v1"
    assert legacy.scoring_version == "talent-score-v1"
    assert legacy.report_version == "talent-report-v1"
    assert legacy.rules_snapshot["test_ids"] == [
        "reactiontime",
        "aim",
        "sequence",
        "number-memory",
        "verbal-memory",
        "chimp",
        "memory",
    ]
    assert legacy.results == []
    assert legacy.report is None


def test_talent_evidence_payload_accepts_only_safe_dynamic_storage_shapes() -> None:
    raw = {
        "selected_level": 4,
        "outcomes": ["correct"] * 5,
        "measured_display_ms": [36] * 5,
        "font_size_px": 56,
    }
    device = {
        "input_kind": "mouse",
        "viewport_width": 1440,
        "viewport_height": 900,
        "geometry": {"width": 800, "height": 420, "target_diameter": None},
    }

    validate_talent_e2e_evidence_payload(raw, raw, device)

    with pytest.raises(TalentE2EEvidenceError, match="dynamic_raw_keyset"):
        validate_talent_e2e_evidence_payload(
            {**raw, "answers": ["00000"]},
            raw,
            device,
        )
    with pytest.raises(TalentE2EEvidenceError, match="dynamic_normalized_raw_keyset"):
        validate_talent_e2e_evidence_payload(
            raw,
            {**raw, "digits": ["00000"]},
            device,
        )
    with pytest.raises(TalentE2EEvidenceError, match="dynamic_device_shape"):
        validate_talent_e2e_evidence_payload(raw, raw, {**device, "answer": "00000"})


def _audit_metadata(*, completed: bool = False) -> dict[str, object]:
    metadata: dict[str, object] = {
        "session_id": "00000000-0000-4000-8000-000000000001",
        "person_id": "00000000-0000-4000-8000-000000000002",
        "catalog_version": "talent-training-v2",
        "scoring_version": "talent-score-v1",
        "operation_key": "00000000-0000-4000-8000-000000000003",
        "result_count": 8 if completed else 0,
    }
    if completed:
        metadata["report_id"] = "00000000-0000-4000-8000-000000000004"
    return metadata


def test_talent_evidence_requires_exact_expected_audit_events() -> None:
    validate_talent_e2e_audit_events(
        "dynamic",
        [
            ("talent_tests.started", _audit_metadata()),
            ("talent_tests.abandoned", _audit_metadata()),
        ],
    )
    validate_talent_e2e_audit_events(
        "full",
        [
            ("talent_tests.started", _audit_metadata()),
            ("talent_tests.completed", _audit_metadata(completed=True)),
        ],
    )

    with pytest.raises(TalentE2EEvidenceError, match="audit_event_counts"):
        validate_talent_e2e_audit_events("dynamic", [])


@pytest.mark.parametrize("leaked_key", ["score", "result_payload", "outcomes"])
def test_talent_evidence_rejects_every_extra_audit_metadata_key(
    leaked_key: str,
) -> None:
    leaked = {**_audit_metadata(), leaked_key: ["private"]}

    with pytest.raises(TalentE2EEvidenceError, match="audit_metadata_keyset"):
        validate_talent_e2e_audit_events(
            "dynamic",
            [
                ("talent_tests.started", leaked),
                ("talent_tests.abandoned", _audit_metadata()),
            ],
        )
