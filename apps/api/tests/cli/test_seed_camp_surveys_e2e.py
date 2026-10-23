from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.seed_camp_surveys_e2e import (
    CAMP_SURVEY_COHORT_ID,
    CAMP_SURVEY_ENROLLMENT_ID,
    CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID,
    CAMP_SURVEY_PERMISSION_OVERRIDE_IDS,
    CAMP_SURVEY_SECOND_GUARDIAN_RELATIONSHIP_ID,
    CampSurveysE2ESeedResult,
    seed_camp_surveys_e2e,
)
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_LIFE_TEACHER_PERSON_ID,
    E2E_OTHER_GUARDIAN_PERSON_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2ESeedRefused,
    seed_e2e,
)
from app.core.config import Settings
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffSnapshot,
    CampSurveySubmission,
)
from app.evaluations.survey_window import survey_window_for_end
from app.identity.models import GuardianRelationship
from app.rbac.models import UserPermissionOverride
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
FIXED_NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)
SHANGHAI = ZoneInfo("Asia/Shanghai")
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SEED_SCRIPT = REPOSITORY_ROOT / "scripts/seed-camp-surveys-e2e.py"


def settings(
    *,
    app_env: str = "test",
    database: str = "camp_surveys_e2e_test",
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
    )


def seed_kwargs(value: Settings) -> dict[str, object]:
    return {
        "settings": value,
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }


def _seed_base(db: Session, value: Settings) -> None:
    seed_e2e(db, **seed_kwargs(value))


def test_camp_survey_seed_is_deterministic_and_builds_complete_roster(
    db: Session,
) -> None:
    value = settings()
    _seed_base(db, value)

    first = seed_camp_surveys_e2e(
        db,
        settings=value,
        occurred_at=FIXED_NOW,
    )
    second = seed_camp_surveys_e2e(
        db,
        settings=value,
        occurred_at=FIXED_NOW,
    )

    assert first == second == CampSurveysE2ESeedResult(
        cycle_id=first.cycle_id,
        cohort_id=CAMP_SURVEY_COHORT_ID,
        enrollment_id=CAMP_SURVEY_ENROLLMENT_ID,
        student_person_id=E2E_SECOND_BOUND_STUDENT_ID,
        guardian_person_ids=(
            E2E_GUARDIAN_PERSON_ID,
            E2E_OTHER_GUARDIAN_PERSON_ID,
        ),
        admin_account_id=E2E_ADMIN_ACCOUNT_ID,
        no_access_account_id=CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID,
    )
    assert all(
        account.email_normalized is None
        for account in db.scalars(select(UserAccount).order_by(UserAccount.id))
    )

    cycle = db.get(CampSurveyCycle, first.cycle_id)
    assert cycle is not None
    assert cycle.opens_at <= FIXED_NOW < cycle.due_at
    assert cycle.due_at == survey_window_for_end(cycle.opens_at).closes_at
    assert cycle.opens_at.astimezone(SHANGHAI).date() == date(2026, 1, 31)
    assert cycle.due_at.astimezone(SHANGHAI).date() == date(2026, 2, 28)
    snapshot = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == first.cycle_id,
            CampSurveySnapshotVersion.version == 1,
        )
    )
    assert snapshot is not None
    rows = list(
        db.scalars(
            select(CampSurveyStaffSnapshot).where(
                CampSurveyStaffSnapshot.snapshot_version_id == snapshot.id
            )
        )
    )
    assert {row.staff_person_id for row in rows} >= {
        E2E_COACH_PERSON_ID,
        E2E_LIFE_TEACHER_PERSON_ID,
    }
    role_keys = {
        role["role_key"]
        for row in rows
        for role in row.roles_snapshot
    }
    assert {"primary_coach", "assistant_coach", "life_teacher", "e2e_camp_care"} <= role_keys
    assert any(
        interval.get("until") and interval.get("source") == "cohort_staff_assignment"
        for row in rows
        for interval in row.service_intervals
    )
    assert db.scalar(select(CampSurveySubmission.id).limit(1)) is None

    relationships = {
        row.guardian_person_id
        for row in db.scalars(
            select(GuardianRelationship).where(
                GuardianRelationship.student_person_id == E2E_SECOND_BOUND_STUDENT_ID,
                GuardianRelationship.ended_at.is_(None),
            )
        )
    }
    assert relationships == {
        E2E_GUARDIAN_PERSON_ID,
        E2E_OTHER_GUARDIAN_PERSON_ID,
    }
    second_relationship = db.get(
        GuardianRelationship,
        CAMP_SURVEY_SECOND_GUARDIAN_RELATIONSHIP_ID,
    )
    assert second_relationship is not None

    overrides = {
        row.id: row
        for row in db.scalars(
            select(UserPermissionOverride).where(
                UserPermissionOverride.id.in_(CAMP_SURVEY_PERMISSION_OVERRIDE_IDS)
            )
        )
    }
    assert set(overrides) == set(CAMP_SURVEY_PERMISSION_OVERRIDE_IDS)
    assert {row.permission_id for row in overrides.values()} == {
        UUID("20000000-0000-4000-8000-000000000034"),
        UUID("20000000-0000-4000-8000-000000000035"),
    }
    assert {row.valid_until for row in overrides.values()} == {cycle.due_at}
    assert not list(
        db.scalars(
            select(UserPermissionOverride).where(
                UserPermissionOverride.user_id == CAMP_SURVEY_NO_ACCESS_ACCOUNT_ID,
                UserPermissionOverride.permission_id.in_(
                    tuple(row.permission_id for row in overrides.values())
                ),
            )
        )
    )


@pytest.mark.parametrize(
    ("app_env", "database"),
    [
        pytest.param("development", "camp_surveys_e2e_test", id="app-env"),
        pytest.param("test", "production", id="database-name"),
    ],
)
def test_camp_survey_seed_refuses_non_test_database_before_writes(
    db: Session,
    app_env: str,
    database: str,
) -> None:
    value = settings(app_env=app_env, database=database)

    with pytest.raises(E2ESeedRefused):
        seed_camp_surveys_e2e(db, settings=value, occurred_at=FIXED_NOW)

    assert db.scalar(select(CampSurveyCycle.id).limit(1)) is None


def test_camp_survey_seed_script_is_a_thin_module_wrapper() -> None:
    source = SEED_SCRIPT.read_text(encoding="utf-8")
    assert "app.cli.seed_camp_surveys_e2e" in source
    assert "main" in source
    assert "seed_e2e" not in source
