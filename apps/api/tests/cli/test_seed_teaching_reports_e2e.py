from __future__ import annotations

import json
import runpy
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.cli.seed_teaching_reports_e2e as seed_module
from app.admissions.models import AdmissionCase, Enrollment
from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    ADMIN_PASSWORD,
    COACH_PASSWORD,
    E2E_ADMIN_ACCOUNT_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_ACCOUNT_ID,
    E2E_OTHER_GUARDIAN_PERSON_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
    GUARDIAN_PASSWORD,
    LIFE_TEACHER_PASSWORD,
    OTHER_GUARDIAN_PASSWORD,
    SECOND_STUDENT_PASSWORD,
    E2ESeedRefused,
    seed_e2e,
)
from app.cli.seed_teaching_reports_e2e import (
    AUTHORIZED_ASSISTANT_ACCOUNT_ID,
    AUTHORIZED_ASSISTANT_PASSWORD,
    AUTHORIZED_ASSISTANT_PERSON_ID,
    TEACHING_REPORT_ADMISSION_CASE_IDS,
    TEACHING_REPORT_CLASS_IDS,
    TEACHING_REPORT_COACH_ASSIGNMENT_IDS,
    TEACHING_REPORT_COHORT_IDS,
    TEACHING_REPORT_ENROLLMENT_IDS,
    TEACHING_REPORT_PERMISSION_OVERRIDE_IDS,
    TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    UNAUTHORIZED_ASSISTANT_ACCOUNT_ID,
    UNAUTHORIZED_ASSISTANT_PASSWORD,
    UNAUTHORIZED_ASSISTANT_PERSON_ID,
    TeachingReportsE2ESeedResult,
    seed_teaching_reports_e2e,
)
from app.core.config import Settings
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.errors import EvaluationResourceNotFound
from app.evaluations.report_access import TeachingReportAccessService
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import GuardianRelationship, Person, StaffProfile
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import CoachAssignment, Cohort, TeachingClass

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
FIXED_NOW = datetime(2026, 8, 10, 4, 0, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SEED_SCRIPT = REPOSITORY_ROOT / "scripts/seed-teaching-reports-e2e.py"


def settings(
    *,
    app_env: str = "test",
    database: str = "teaching_reports_e2e_test",
) -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-v1-token-2026-change-me",
        evaluation_tool_web_service_token=(
            "evaluation-local-web-token-2026-change-me"
        ),
        evaluation_tool_worker_service_token=(
            "evaluation-local-worker-token-2026-change-me"
        ),
    )


def seed_kwargs(value: Settings) -> dict[str, object]:
    return {
        "settings": value,
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }


def _owned_created_at(db: Session) -> dict[tuple[str, UUID], datetime]:
    rows: dict[tuple[str, UUID], datetime] = {}
    fixtures = (
        ("person", Person, (AUTHORIZED_ASSISTANT_PERSON_ID, UNAUTHORIZED_ASSISTANT_PERSON_ID)),
        ("account", UserAccount, (AUTHORIZED_ASSISTANT_ACCOUNT_ID, UNAUTHORIZED_ASSISTANT_ACCOUNT_ID)),
        ("cohort", Cohort, TEACHING_REPORT_COHORT_IDS),
        ("class", TeachingClass, TEACHING_REPORT_CLASS_IDS),
        ("case", AdmissionCase, TEACHING_REPORT_ADMISSION_CASE_IDS),
        ("enrollment", Enrollment, TEACHING_REPORT_ENROLLMENT_IDS),
        ("assignment", CoachAssignment, TEACHING_REPORT_COACH_ASSIGNMENT_IDS),
        (
            "relationship",
            GuardianRelationship,
            (TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,),
        ),
        (
            "override",
            UserPermissionOverride,
            TEACHING_REPORT_PERMISSION_OVERRIDE_IDS,
        ),
    )
    for label, model, identifiers in fixtures:
        for row in db.scalars(select(model).where(model.id.in_(identifiers))):
            rows[(label, row.id)] = row.created_at
    return rows


def _seed_base(db: Session, value: Settings) -> None:
    seed_e2e(db, **seed_kwargs(value))


def test_teaching_report_seed_is_deterministic_and_preserves_unrelated_rows(
    db: Session,
) -> None:
    value = settings()
    _seed_base(db, value)
    unrelated = db.get(Cohort, UUID("e2100000-0000-4000-8000-000000000001"))
    assert unrelated is not None
    unrelated_snapshot = (unrelated.name, unrelated.created_at)

    first = seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )
    first_created_at = _owned_created_at(db)

    assert first == TeachingReportsE2ESeedResult(
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
    assert len(first_created_at) == 24

    second = seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )

    assert second == first
    assert _owned_created_at(db) == first_created_at
    assert all(
        account.email_normalized is None
        for account in db.scalars(select(UserAccount).order_by(UserAccount.id))
    )
    db.refresh(unrelated)
    assert (unrelated.name, unrelated.created_at) == unrelated_snapshot


def test_teaching_report_seed_creates_exact_period_and_role_fixtures(
    db: Session,
) -> None:
    value = settings()
    _seed_base(db, value)
    result = seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )

    cohorts = [db.get(Cohort, cohort_id) for cohort_id in result.cohort_ids]
    assert all(cohort is not None for cohort in cohorts)
    cohort_21, cohort_10, cohort_utc = cohorts
    assert cohort_21 is not None
    assert cohort_10 is not None
    assert cohort_utc is not None
    assert cohort_21.starts_at.astimezone(SHANGHAI) == datetime(
        2026, 7, 21, 10, 0, tzinfo=SHANGHAI
    )
    assert cohort_21.ends_at.astimezone(SHANGHAI) == datetime(
        2026, 8, 10, 18, 0, tzinfo=SHANGHAI
    )
    assert (cohort_21.ends_at.astimezone(SHANGHAI).date() - cohort_21.starts_at.astimezone(SHANGHAI).date()).days + 1 == 21
    assert cohort_10.starts_at.astimezone(SHANGHAI) == datetime(
        2026, 8, 1, 10, 0, tzinfo=SHANGHAI
    )
    assert cohort_10.ends_at.astimezone(SHANGHAI) == datetime(
        2026, 8, 10, 18, 0, tzinfo=SHANGHAI
    )
    assert (cohort_10.ends_at.astimezone(SHANGHAI).date() - cohort_10.starts_at.astimezone(SHANGHAI).date()).days + 1 == 10
    assert cohort_utc.starts_at == datetime(2026, 8, 8, 16, 30, tzinfo=UTC)
    assert cohort_utc.ends_at == datetime(2026, 8, 9, 15, 30, tzinfo=UTC)
    assert cohort_utc.starts_at.astimezone(SHANGHAI).date() == date(2026, 8, 9)
    assert cohort_utc.ends_at.astimezone(SHANGHAI).date() == date(2026, 8, 9)
    assert cohort_utc.starts_at.astimezone(UTC).date() != cohort_utc.ends_at.astimezone(
        UTC
    ).date()

    enrollments = [
        db.get(Enrollment, enrollment_id) for enrollment_id in result.enrollment_ids
    ]
    assert all(enrollment is not None for enrollment in enrollments)
    assert {enrollment.student_person_id for enrollment in enrollments if enrollment} == {
        E2E_SECOND_BOUND_STUDENT_ID
    }
    assert {enrollment.class_id for enrollment in enrollments if enrollment} == set(
        TEACHING_REPORT_CLASS_IDS
    )

    assignments = list(
        db.scalars(
            select(CoachAssignment).where(
                CoachAssignment.id.in_(TEACHING_REPORT_COACH_ASSIGNMENT_IDS)
            )
        )
    )
    assert len(assignments) == 5
    assert sum(item.assignment_type == "primary" for item in assignments) == 3
    assert sum(item.assignment_type == "assistant" for item in assignments) == 2
    assert {
        item.coach_person_id
        for item in assignments
        if item.assignment_type == "assistant"
    } == {AUTHORIZED_ASSISTANT_PERSON_ID, UNAUTHORIZED_ASSISTANT_PERSON_ID}

    authorized = db.get(UserAccount, AUTHORIZED_ASSISTANT_ACCOUNT_ID)
    unauthorized = db.get(UserAccount, UNAUTHORIZED_ASSISTANT_ACCOUNT_ID)
    assert authorized is not None and authorized.status == "active"
    assert unauthorized is not None and unauthorized.status == "active"
    assert db.get(StaffProfile, AUTHORIZED_ASSISTANT_PERSON_ID).staff_type == "coach"
    assert db.get(StaffProfile, UNAUTHORIZED_ASSISTANT_PERSON_ID).staff_type == "coach"
    coach_role_id = ROLES["coach"].id
    assert db.get(UserRole, (AUTHORIZED_ASSISTANT_ACCOUNT_ID, coach_role_id)) is not None
    assert db.get(UserRole, (UNAUTHORIZED_ASSISTANT_ACCOUNT_ID, coach_role_id)) is not None

    overrides = {
        row.id: row
        for row in db.scalars(
            select(UserPermissionOverride).where(
                UserPermissionOverride.id.in_(TEACHING_REPORT_PERMISSION_OVERRIDE_IDS)
            )
        )
    }
    assert len(overrides) == 2
    write_override = overrides[TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[0]]
    review_override = overrides[TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[1]]
    assert (
        write_override.user_id,
        write_override.permission_id,
        write_override.effect,
        write_override.scope_kind,
        write_override.scope_id,
    ) == (
        AUTHORIZED_ASSISTANT_ACCOUNT_ID,
        PERMISSIONS["student_reports.write"].id,
        "allow",
        "assigned_class",
        TEACHING_REPORT_CLASS_IDS[0],
    )
    assert (
        review_override.user_id,
        review_override.permission_id,
        review_override.effect,
        review_override.scope_kind,
        review_override.scope_id,
    ) == (
        E2E_LIFE_TEACHER_ACCOUNT_ID,
        PERMISSIONS["student_reports.review"].id,
        "allow",
        "project",
        UUID("30000000-0000-4000-8000-000000000001"),
    )
    unauthorized_write = db.scalar(
        select(func.count(UserPermissionOverride.id)).where(
            UserPermissionOverride.user_id == UNAUTHORIZED_ASSISTANT_ACCOUNT_ID,
            UserPermissionOverride.permission_id
            == PERMISSIONS["student_reports.write"].id,
        )
    )
    assert unauthorized_write == 0

    first_relationship = db.get(
        GuardianRelationship,
        E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    second_relationship = db.get(
        GuardianRelationship,
        TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    assert first_relationship is not None
    assert second_relationship is not None
    assert first_relationship.guardian_person_id == E2E_GUARDIAN_PERSON_ID
    assert second_relationship.guardian_person_id == E2E_OTHER_GUARDIAN_PERSON_ID
    assert first_relationship.student_person_id == E2E_SECOND_BOUND_STUDENT_ID
    assert second_relationship.student_person_id == E2E_SECOND_BOUND_STUDENT_ID
    assert first_relationship.ended_at is None
    assert second_relationship.ended_at is None

    for model in (
        StudentReport,
        StudentReportDraft,
        StudentReportVersion,
        StudentReportReview,
        StudentReportWindowGrant,
    ):
        assert db.scalar(select(func.count()).select_from(model)) == 0


def test_teaching_report_seed_supports_real_author_and_reviewer_selectors(
    db: Session,
) -> None:
    value = settings()
    _seed_base(db, value)
    seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )
    report_access = TeachingReportAccessService(db)

    assert [
        len(
            report_access.author_slots(
                account_id=E2E_COACH_ACCOUNT_ID,
                enrollment_id=enrollment_id,
                occurred_at=FIXED_NOW,
            )
        )
        for enrollment_id in TEACHING_REPORT_ENROLLMENT_IDS
    ] == [24, 12, 2]
    assert len(
        report_access.author_slots(
            account_id=AUTHORIZED_ASSISTANT_ACCOUNT_ID,
            enrollment_id=TEACHING_REPORT_ENROLLMENT_IDS[0],
            occurred_at=FIXED_NOW,
        )
    ) == 24
    with pytest.raises(EvaluationResourceNotFound):
        report_access.author_slots(
            account_id=AUTHORIZED_ASSISTANT_ACCOUNT_ID,
            enrollment_id=TEACHING_REPORT_ENROLLMENT_IDS[1],
            occurred_at=FIXED_NOW,
        )
    with pytest.raises(EvaluationResourceNotFound):
        report_access.author_slots(
            account_id=UNAUTHORIZED_ASSISTANT_ACCOUNT_ID,
            enrollment_id=TEACHING_REPORT_ENROLLMENT_IDS[0],
            occurred_at=FIXED_NOW,
        )

    evaluation_access = EvaluationAccessService(db)
    for cohort_id, class_id, enrollment_id in zip(
        TEACHING_REPORT_COHORT_IDS,
        TEACHING_REPORT_CLASS_IDS,
        TEACHING_REPORT_ENROLLMENT_IDS,
        strict=True,
    ):
        decision = evaluation_access.decide(
            account_id=E2E_LIFE_TEACHER_ACCOUNT_ID,
            permission="student_reports.review",
            resource=EvaluationResource(
                student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
                enrollment_id=enrollment_id,
                cohort_id=cohort_id,
                project_id=UUID("30000000-0000-4000-8000-000000000001"),
                class_id=class_id,
            ),
            occurred_at=FIXED_NOW,
        )
        assert decision.allowed is True


def test_teaching_report_seed_clears_only_stale_report_idempotency(
    db: Session,
) -> None:
    value = settings()
    _seed_base(db, value)
    stale_report_key = EvaluationIdempotencyKey(
        id=UUID("e5160000-0000-4000-8000-000000000001"),
        scope="teaching_report.submit",
        actor_key="stale-teaching-report-e2e",
        key_digest="a" * 64,
        payload_digest="b" * 64,
        resource_id=UUID("e5170000-0000-4000-8000-000000000001"),
        created_at=FIXED_NOW,
        expires_at=FIXED_NOW + timedelta(days=1),
    )
    unrelated_key = EvaluationIdempotencyKey(
        id=UUID("e5160000-0000-4000-8000-000000000002"),
        scope="system_evaluation.task.create",
        actor_key="unrelated-e2e",
        key_digest="c" * 64,
        payload_digest="d" * 64,
        resource_id=UUID("e5170000-0000-4000-8000-000000000002"),
        created_at=FIXED_NOW,
        expires_at=FIXED_NOW + timedelta(days=1),
    )
    db.add_all([stale_report_key, unrelated_key])
    db.commit()
    stale_report_key_id = stale_report_key.id
    unrelated_key_id = unrelated_key.id

    seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )

    assert db.get(EvaluationIdempotencyKey, stale_report_key_id) is None
    assert db.get(EvaluationIdempotencyKey, unrelated_key_id) is not None


@pytest.mark.parametrize(
    ("app_env", "database"),
    [
        pytest.param("development", "teaching_reports_e2e_test", id="app-env"),
        pytest.param("test", "production", id="database-name"),
    ],
)
def test_teaching_report_seed_refuses_non_test_database_before_writes(
    db: Session,
    app_env: str,
    database: str,
) -> None:
    value = settings(app_env=app_env, database=database)

    with pytest.raises(E2ESeedRefused):
        seed_teaching_reports_e2e(
            db,
            settings=value,
            cipher=seed_kwargs(value)["cipher"],
            lookup=seed_kwargs(value)["lookup"],
            passwords=seed_kwargs(value)["passwords"],
            occurred_at=FIXED_NOW,
        )

    assert db.get(Cohort, TEACHING_REPORT_COHORT_IDS[0]) is None
    assert db.get(Person, AUTHORIZED_ASSISTANT_PERSON_ID) is None


def test_teaching_report_seed_requires_canonical_base_fixtures(db: Session) -> None:
    value = settings()

    with pytest.raises(E2ESeedRefused, match="canonical E2E fixtures"):
        seed_teaching_reports_e2e(
            db,
            settings=value,
            cipher=seed_kwargs(value)["cipher"],
            lookup=seed_kwargs(value)["lookup"],
            passwords=seed_kwargs(value)["passwords"],
            occurred_at=FIXED_NOW,
        )

    assert db.get(Cohort, TEACHING_REPORT_COHORT_IDS[0]) is None
    assert db.get(Person, AUTHORIZED_ASSISTANT_PERSON_ID) is None


def test_teaching_report_seed_restores_owned_mutable_access(db: Session) -> None:
    value = settings()
    _seed_base(db, value)
    seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )
    assistant = db.get(UserAccount, AUTHORIZED_ASSISTANT_ACCOUNT_ID)
    assignment = db.get(CoachAssignment, TEACHING_REPORT_COACH_ASSIGNMENT_IDS[1])
    relationship = db.get(
        GuardianRelationship,
        TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    override = db.get(
        UserPermissionOverride,
        TEACHING_REPORT_PERMISSION_OVERRIDE_IDS[0],
    )
    assert assistant is not None
    assert assignment is not None
    assert relationship is not None
    assert override is not None
    assistant.status = "disabled"
    assignment.ended_at = FIXED_NOW
    assignment.ended_by_user_id = E2E_ADMIN_ACCOUNT_ID
    assignment.end_reason = "E2E revocation exercise"
    relationship.ended_at = FIXED_NOW
    relationship.ended_by_user_id = E2E_ADMIN_ACCOUNT_ID
    relationship.end_reason = "E2E revocation exercise"
    override.effect = "deny"
    db.flush()

    seed_teaching_reports_e2e(
        db,
        settings=value,
        cipher=seed_kwargs(value)["cipher"],
        lookup=seed_kwargs(value)["lookup"],
        passwords=seed_kwargs(value)["passwords"],
        occurred_at=FIXED_NOW,
    )

    assert assistant.status == "active"
    assert assignment.ended_at is None
    assert assignment.ended_by_user_id is None
    assert assignment.end_reason is None
    assert relationship.ended_at is None
    assert relationship.ended_by_user_id is None
    assert relationship.end_reason is None
    assert override.effect == "allow"


def test_teaching_report_seed_refuses_owned_id_collision_without_partial_writes(
    db: Session,
) -> None:
    value = settings()
    _seed_base(db, value)
    db.add(
        Cohort(
            id=TEACHING_REPORT_COHORT_IDS[0],
            name="not the teaching report fixture",
            starts_at=datetime(2026, 1, 1, tzinfo=UTC),
            ends_at=datetime(2026, 1, 2, tzinfo=UTC),
            default_check_in_at=datetime(2026, 1, 1, tzinfo=UTC),
            default_check_out_at=datetime(2026, 1, 2, tzinfo=UTC),
            status="completed",
            auto_start_enabled=False,
            auto_complete_enabled=False,
            created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        )
    )
    db.commit()

    with pytest.raises(E2ESeedRefused, match="not canonical"):
        seed_teaching_reports_e2e(
            db,
            settings=value,
            cipher=seed_kwargs(value)["cipher"],
            lookup=seed_kwargs(value)["lookup"],
            passwords=seed_kwargs(value)["passwords"],
            occurred_at=FIXED_NOW,
        )

    assert db.get(Person, AUTHORIZED_ASSISTANT_PERSON_ID) is None
    assert db.get(TeachingClass, TEACHING_REPORT_CLASS_IDS[0]) is None


def test_teaching_report_seed_cli_projection_contains_no_plaintext_credentials(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    value = settings()
    _seed_base(db, value)
    monkeypatch.setattr(seed_module, "get_settings", lambda: value)
    monkeypatch.setattr(seed_module, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(seed_module, "_now", lambda: FIXED_NOW)

    assert seed_module.main() == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert set(payload) == {
        "cohort_ids",
        "class_ids",
        "enrollment_ids",
        "account_ids",
        "person_ids",
        "relationship_ids",
        "assignment_ids",
        "permission_override_ids",
        "credential_refs",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    for forbidden in (
        ADMIN_PASSWORD,
        COACH_PASSWORD,
        AUTHORIZED_ASSISTANT_PASSWORD,
        UNAUTHORIZED_ASSISTANT_PASSWORD,
        LIFE_TEACHER_PASSWORD,
        GUARDIAN_PASSWORD,
        OTHER_GUARDIAN_PASSWORD,
        SECOND_STUDENT_PASSWORD,
        "138000000",
        "139000000",
        "E2E教学评语",
        "E2E授权助教",
        "E2E未授权助教",
    ):
        assert forbidden not in serialized
        assert forbidden not in captured.err
    assert all(
        reference.isupper()
        for reference in payload["credential_refs"]
    )


def test_teaching_report_seed_rejects_naive_clock(db: Session) -> None:
    value = settings()
    _seed_base(db, value)

    with pytest.raises(ValueError, match="timezone-aware"):
        seed_teaching_reports_e2e(
            db,
            settings=value,
            cipher=seed_kwargs(value)["cipher"],
            lookup=seed_kwargs(value)["lookup"],
            passwords=seed_kwargs(value)["passwords"],
            occurred_at=datetime(2026, 8, 10, 4, 0),  # noqa: DTZ001
        )

    assert db.get(Cohort, TEACHING_REPORT_COHORT_IDS[0]) is None


def test_teaching_report_seed_uses_frozen_permission_ids(db: Session) -> None:
    value = settings()
    _seed_base(db, value)
    assert db.get(Permission, PERMISSIONS["student_reports.write"].id) is not None
    assert db.get(Permission, PERMISSIONS["student_reports.review"].id) is not None
    assert PERMISSIONS["student_reports.write"].id == UUID(
        "20000000-0000-4000-8000-000000000032"
    )
    assert PERMISSIONS["student_reports.review"].id == UUID(
        "20000000-0000-4000-8000-000000000033"
    )


def test_teaching_report_seed_plan_script_is_a_thin_importable_entrypoint() -> None:
    namespace = runpy.run_path(str(SEED_SCRIPT), run_name="seed_wrapper_test")

    assert namespace["main"] is seed_module.main
    source = SEED_SCRIPT.read_text(encoding="utf-8")
    assert "StudentReport" not in source
    assert AUTHORIZED_ASSISTANT_PASSWORD not in source
    assert UNAUTHORIZED_ASSISTANT_PASSWORD not in source
