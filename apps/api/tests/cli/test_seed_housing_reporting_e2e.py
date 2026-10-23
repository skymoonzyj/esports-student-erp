from __future__ import annotations

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.seed_e2e import E2ESeedRefused, seed_e2e
from app.cli.seed_housing_reporting_e2e import (
    REPORT_BUILDING_ACCOUNT_ID,
    REPORT_FIXTURE_ACCOUNT_IDS,
    REPORT_FIXTURE_ROOM_IDS,
    REPORT_FIXTURE_STAY_IDS,
    REPORT_FLOOR_ACCOUNT_ID,
    REPORT_NO_PERMISSION_ACCOUNT_ID,
    REPORT_PERMISSION_ID,
    REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
    REPORT_TRANSFER_ID,
    HousingReportingE2ESeedResult,
    seed_housing_reporting_e2e,
)
from app.core.config import Settings
from app.housing.models import HousingRoom, HousingStay, HousingTransfer
from app.housing.reporting_models import HousingReportBatch
from app.housing.reporting_projection import (
    HousingReportProjectionBuilder,
    ProjectionRequest,
)
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import UserPermissionOverride
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
SHANGHAI = ZoneInfo("Asia/Shanghai")


def settings(
    *,
    app_env: str = "test",
    database: str = "housing_reporting_test",
) -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
    )


def seed_kwargs() -> dict[str, object]:
    return {
        "settings": settings(),
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }


def test_reporting_seed_is_opt_in_idempotent_and_creates_no_batch(
    db: Session,
) -> None:
    kwargs = seed_kwargs()
    seed_e2e(db, **kwargs)
    assert db.scalar(select(func.count(HousingStay.id))) == 0

    first = seed_housing_reporting_e2e(db, **kwargs)
    second = seed_housing_reporting_e2e(db, **kwargs)

    assert first == second == HousingReportingE2ESeedResult(
        stays=6,
        accounts=3,
        rooms=6,
    )
    assert set(db.scalars(select(HousingStay.id))) == set(REPORT_FIXTURE_STAY_IDS)
    assert db.scalar(select(func.count(HousingReportBatch.id))) == 0
    assert db.scalar(
        select(func.count(HousingTransfer.id)).where(
            HousingTransfer.id == REPORT_TRANSFER_ID
        )
    ) == 1
    assert set(
        db.scalars(
            select(UserAccount.id).where(
                UserAccount.id.in_(REPORT_FIXTURE_ACCOUNT_IDS),
                UserAccount.status == "active",
            )
        )
    ) == set(REPORT_FIXTURE_ACCOUNT_IDS)
    assert db.scalar(
        select(func.count(HousingRoom.id)).where(
            HousingRoom.id.in_(REPORT_FIXTURE_ROOM_IDS),
            HousingRoom.verification_status == "verified",
        )
    ) == len(REPORT_FIXTURE_ROOM_IDS)
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.id == REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID
        )
    ) == 1

    overrides = {
        (row.user_id, row.effect, row.scope_kind, row.scope_id)
        for row in db.scalars(
            select(UserPermissionOverride).where(
                UserPermissionOverride.user_id.in_(REPORT_FIXTURE_ACCOUNT_IDS),
                UserPermissionOverride.permission_id == REPORT_PERMISSION_ID,
            )
        )
    }
    assert len(overrides) == 3
    assert any(
        user_id == REPORT_BUILDING_ACCOUNT_ID
        and effect == "allow"
        and scope_kind == "building"
        and scope_id is not None
        for user_id, effect, scope_kind, scope_id in overrides
    )
    assert {
        (effect, scope_kind)
        for user_id, effect, scope_kind, _scope_id in overrides
        if user_id == REPORT_FLOOR_ACCOUNT_ID
    } == {("allow", "floor"), ("deny", "floor")}
    assert all(user_id != REPORT_NO_PERMISSION_ACCOUNT_ID for user_id, *_ in overrides)

    cipher = kwargs["cipher"]
    assert isinstance(cipher, EnvelopeCipher)
    floor_ids = tuple(
        db.scalars(
            select(HousingRoom.floor_id)
            .where(HousingRoom.id.in_(REPORT_FIXTURE_ROOM_IDS))
            .distinct()
        )
    )
    projection = HousingReportProjectionBuilder(db, cipher).build(
        ProjectionRequest(
            generated_at=datetime(2026, 8, 22, 20, 0, tzinfo=SHANGHAI),
            range_start=datetime(2026, 8, 1, 0, 0, tzinfo=SHANGHAI),
            range_end=datetime(2026, 8, 22, 0, 0, tzinfo=SHANGHAI),
            floor_ids=floor_ids,
            cohort_ids=(),
            person_types=(),
        )
    )
    assert projection.issues == ()
    assert projection.projection is not None
    assert {row.stay_id for row in projection.projection.residents} == set(
        REPORT_FIXTURE_STAY_IDS
    )
    assert projection.projection.guardian_row_count == 2


@pytest.mark.parametrize(
    "unsafe_settings",
    [
        settings(app_env="development"),
        settings(database="production"),
    ],
)
def test_reporting_seed_refuses_unsafe_environment(
    db: Session,
    unsafe_settings: Settings,
) -> None:
    kwargs = seed_kwargs()
    kwargs["settings"] = unsafe_settings

    with pytest.raises(E2ESeedRefused):
        seed_housing_reporting_e2e(db, **kwargs)

    assert db.scalar(select(func.count(HousingStay.id))) == 0


def test_reporting_seed_refuses_missing_canonical_room_without_partial_writes(
    db: Session,
) -> None:
    kwargs = seed_kwargs()
    seed_e2e(db, **kwargs)
    db.execute(delete(HousingRoom).where(HousingRoom.id == REPORT_FIXTURE_ROOM_IDS[0]))

    with pytest.raises(E2ESeedRefused, match="canonical E2E fixtures"):
        seed_housing_reporting_e2e(db, **kwargs)

    assert db.scalar(select(func.count(HousingStay.id))) == 0
    assert db.scalar(
        select(func.count(UserAccount.id)).where(
            UserAccount.id.in_(REPORT_FIXTURE_ACCOUNT_IDS)
        )
    ) == 0
    assert db.scalar(
        select(func.count(Person.id)).where(
            Person.id.in_(
                (
                    UUID("e3100000-0000-4000-8000-000000000001"),
                    UUID("e3100000-0000-4000-8000-000000000002"),
                    UUID("e3100000-0000-4000-8000-000000000003"),
                )
            )
        )
    ) == 0
