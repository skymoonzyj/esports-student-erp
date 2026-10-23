from datetime import datetime
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session

from app.cli import seed_e2e as seed
from app.core.config import Settings
from app.housing.models import HousingRoom
from app.housing.seed_inventory import INITIAL_ROOMS


def test_history_void_fixture_preserves_transfer_endpoints_and_current_stays() -> None:
    factory = getattr(seed, "history_void_fixture_rows", None)
    assert callable(factory), "history-void fixture factory is not implemented"
    rooms = {
        row.room_number: HousingRoom(id=row.id, room_number=row.room_number)
        for row in INITIAL_ROOMS
        if row.room_number in {"4-201", "4-202"}
    }
    stays, transfers = factory(
        rooms["4-201"], rooms["4-202"], floor_name="二层", building_name="四号楼"
    )
    assert len(stays) == 4
    assert len(transfers) == 2
    assert [row.id for row in stays] == [
        UUID("e2900000-0000-4000-8000-000000000001"),
        UUID("e2900000-0000-4000-8000-000000000002"),
        UUID("e2900000-0000-4000-8000-000000000003"),
        UUID("e2900000-0000-4000-8000-000000000004"),
    ]
    by_id = {row.id: row for row in stays}
    expected_people = {seed.E2E_SELF_STUDENT_PERSON_ID, seed.E2E_COACH_PERSON_ID}
    assert {row.person_id for row in stays} == expected_people
    for transfer in transfers:
        source, target = by_id[transfer.from_stay_id], by_id[transfer.to_stay_id]
        assert source.person_id == target.person_id == transfer.person_id
        assert source.room_id == rooms["4-201"].id
        assert target.room_id == rooms["4-202"].id
        assert source.status == "checked_out"
        assert target.status == "checked_in"
        assert source.actual_check_out_at == target.actual_check_in_at
        assert transfer.transferred_at == datetime(
            2026, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        assert source.actual_check_in_at < source.actual_check_out_at
        assert target.actual_check_out_at is None
        assert source.voided_at is None
        assert target.voided_at is None
        assert source.checked_out_by_user_id == seed.E2E_ADMIN_ACCOUNT_ID
        assert source.version == target.version == 1


@pytest.mark.parametrize(
    ("app_env", "database"), [("development", "housing_test"), ("test", "production")]
)
def test_history_void_fixture_refuses_unsafe_environment_before_db_access(
    app_env: str, database: str
) -> None:
    settings = Settings(
        app_env=app_env,
        database_url=f"postgresql+psycopg://erp:erp@db:5432/{database}",
        session_cookie_secure=False,
    )
    with pytest.raises(seed.E2ESeedRefused):
        seed._seed_history_void_fixtures(cast(Session, None), settings)


def test_history_void_fixture_roles_revoke_superadmin_without_losing_room_read() -> None:
    factory = getattr(seed, "history_void_fixture_roles", None)
    assert callable(factory), "history-void role factory is not implemented"
    roles = factory()
    assert {(role.user_id, role.role_id) for role in roles} == {
        (
            seed.E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
            UUID("10000000-0000-4000-8000-000000000009"),
        ),
        (
            seed.E2E_CUSTOMER_SERVICE_ACCOUNT_ID,
            UUID("10000000-0000-4000-8000-000000000001"),
        ),
        (
            seed.E2E_LIFE_TEACHER_ACCOUNT_ID,
            UUID("10000000-0000-4000-8000-000000000001"),
        ),
    }
