from __future__ import annotations

from datetime import datetime
from typing import TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_BOUND_STUDENT_ID,
    E2E_COACH_PERSON_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    E2ESeedRefused,
    seed_e2e,
)
from app.core.config import Settings
from app.housing.models import HousingRoom, HousingStay
from app.identity.models import Person
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
SHANGHAI = ZoneInfo("Asia/Shanghai")

CURRENT_STAY_IDS = (
    UUID("e2700000-0000-4000-8000-000000000001"),
    UUID("e2700000-0000-4000-8000-000000000002"),
    UUID("e2700000-0000-4000-8000-000000000003"),
)
FUTURE_STAY_IDS_BY_VIEWPORT = {
    1440: (
        UUID("e2710000-0000-4000-8000-000000000001"),
        UUID("e2720000-0000-4000-8000-000000000001"),
    ),
    1024: (
        UUID("e2710000-0000-4000-8000-000000000002"),
        UUID("e2720000-0000-4000-8000-000000000002"),
    ),
    768: (
        UUID("e2710000-0000-4000-8000-000000000003"),
        UUID("e2720000-0000-4000-8000-000000000003"),
    ),
    360: (
        UUID("e2710000-0000-4000-8000-000000000004"),
        UUID("e2720000-0000-4000-8000-000000000004"),
    ),
}
FUTURE_STUDENT_BY_VIEWPORT = {
    1440: UUID("e2000000-0000-4000-8000-000000000010"),
    1024: UUID("e2000000-0000-4000-8000-000000000020"),
    768: UUID("e2000000-0000-4000-8000-000000000030"),
    360: UUID("e2000000-0000-4000-8000-000000000040"),
}
FUTURE_ROOM_BY_VIEWPORT = {
    1440: "4-202",
    1024: "4-203",
    768: "4-205",
    360: "4-206",
}
FUTURE_MONTH_BY_VIEWPORT = {1440: 8, 1024: 9, 768: 10, 360: 11}


class SeedKwargs(TypedDict):
    settings: Settings
    cipher: EnvelopeCipher
    lookup: LookupDigest
    passwords: PasswordService


def settings(
    *,
    app_env: str = "test",
    database: str = "housing_personal_portal_test",
) -> Settings:
    return Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token=("evaluation-local-service-token-2026-change-me"),
    )


def seed_kwargs() -> SeedKwargs:
    return {
        "settings": settings(),
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(range(32))),
        "passwords": PasswordService(),
    }


def test_housing_portal_scenario_is_opt_in_and_idempotent(db: Session) -> None:
    from app.cli.seed_housing_portal_e2e import seed_housing_portal_e2e

    seed_e2e(db, **seed_kwargs())
    assert db.scalar(select(func.count(HousingStay.id))) == 0

    first = seed_housing_portal_e2e(db, settings=settings())
    second = seed_housing_portal_e2e(db, settings=settings())

    assert first == second
    assert first.current_stays == 3
    assert first.planned_stays == 8
    assert db.scalar(select(func.count(HousingStay.id))) == 11
    assert db.scalar(
        select(func.count(HousingRoom.id)).where(
            HousingRoom.id.in_(first.room_ids),
            HousingRoom.verification_status == "verified",
        )
    ) == len(first.room_ids)

    stays = {stay.id: stay for stay in db.scalars(select(HousingStay).order_by(HousingStay.id))}
    assert set(stays) == {
        *CURRENT_STAY_IDS,
        *(stay_id for pair in FUTURE_STAY_IDS_BY_VIEWPORT.values() for stay_id in pair),
    }

    rooms = {
        room.id: room
        for room in db.scalars(select(HousingRoom).where(HousingRoom.id.in_(first.room_ids)))
    }
    room_ids_by_number = {room.room_number: room.id for room in rooms.values()}
    assert set(room_ids_by_number) == {
        "4-201",
        "4-202",
        "4-203",
        "4-205",
        "4-206",
        "4-210",
    }

    current_expectations = {
        CURRENT_STAY_IDS[0]: (E2E_BOUND_STUDENT_ID, "4-201", "female"),
        CURRENT_STAY_IDS[1]: (
            E2E_SECOND_BOUND_STUDENT_ID,
            "4-201",
            "female",
        ),
        CURRENT_STAY_IDS[2]: (E2E_SELF_STUDENT_PERSON_ID, "4-210", "male"),
    }
    for stay_id, (person_id, room_number, sex) in current_expectations.items():
        stay = stays[stay_id]
        person = db.get(Person, person_id)
        assert person is not None and person.sex == sex
        assert stay.person_id == person_id
        assert stay.room_id == room_ids_by_number[room_number]
        assert stay.room_name_snapshot == room_number
        assert stay.status == "checked_in"
        assert stay.duration_mode == "long_term"
        assert stay.actual_check_in_at == datetime(2026, 8, 1, 8, 0, tzinfo=SHANGHAI)
        assert stay.planned_check_out_at is None
        assert stay.checked_in_by_user_id == E2E_ADMIN_ACCOUNT_ID

    for viewport, (student_stay_id, coach_stay_id) in FUTURE_STAY_IDS_BY_VIEWPORT.items():
        student_stay = stays[student_stay_id]
        coach_stay = stays[coach_stay_id]
        room_number = FUTURE_ROOM_BY_VIEWPORT[viewport]
        month = FUTURE_MONTH_BY_VIEWPORT[viewport]
        expected_check_in = datetime(2030, month, 1, 9, 0, tzinfo=SHANGHAI)
        expected_check_out = datetime(2030, month, 8, 18, 0, tzinfo=SHANGHAI)

        assert student_stay.person_id == FUTURE_STUDENT_BY_VIEWPORT[viewport]
        assert coach_stay.person_id == E2E_COACH_PERSON_ID
        for stay in (student_stay, coach_stay):
            assert stay.room_id == room_ids_by_number[room_number]
            assert stay.room_name_snapshot == room_number
            assert stay.status == "planned"
            assert stay.duration_mode == "fixed_term"
            assert stay.planned_check_in_at == expected_check_in
            assert stay.planned_check_out_at == expected_check_out
            assert stay.actual_check_in_at is None
            assert stay.checked_in_by_user_id is None


@pytest.mark.parametrize(
    "unsafe_settings",
    [
        settings(app_env="development"),
        settings(database="production"),
    ],
)
def test_housing_portal_scenario_refuses_unsafe_environment(
    db: Session,
    unsafe_settings: Settings,
) -> None:
    from app.cli.seed_housing_portal_e2e import seed_housing_portal_e2e

    with pytest.raises(E2ESeedRefused):
        seed_housing_portal_e2e(db, settings=unsafe_settings)


def test_housing_portal_scenario_refuses_missing_canonical_fixtures(
    db: Session,
) -> None:
    from app.cli.seed_housing_portal_e2e import seed_housing_portal_e2e

    seed_e2e(db, **seed_kwargs())
    db.execute(text("TRUNCATE TABLE people RESTART IDENTITY CASCADE"))

    with pytest.raises(E2ESeedRefused, match="canonical E2E fixtures"):
        seed_housing_portal_e2e(db, settings=settings())

    assert db.scalar(select(func.count(HousingStay.id))) == 0
    assert (
        db.scalar(
            select(func.count(UserAccount.id)).where(
                UserAccount.id.in_(
                    (
                        E2E_ADMIN_ACCOUNT_ID,
                        E2E_GUARDIAN_ACCOUNT_ID,
                        E2E_SELF_STUDENT_ACCOUNT_ID,
                    )
                )
            )
        )
        == 3
    )
