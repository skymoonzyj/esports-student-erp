from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_ADMIN_PERSON_ID,
    E2E_BOUND_STUDENT_ID,
    E2E_COACH_ACCOUNT_ID,
    E2E_COACH_PERSON_ID,
    E2E_GUARDIAN_ACCOUNT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    E2E_SECOND_BOUND_STUDENT_ID,
    E2E_SECOND_GUARDIAN_RELATIONSHIP_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    PORTAL_RELATIONSHIPS_BY_VIEWPORT,
    E2ESeedRefused,
    _require_e2e_environment,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingStay,
)
from app.identity.models import GuardianRelationship, Person, StudentProfile

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
FUTURE_ROOM_BY_VIEWPORT = {
    1440: "4-202",
    1024: "4-203",
    768: "4-205",
    360: "4-206",
}

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CURRENT_CHECK_IN = datetime(2026, 8, 1, 8, 0, tzinfo=_SHANGHAI)
_FUTURE_MONTH_BY_VIEWPORT = {1440: 8, 1024: 9, 768: 10, 360: 11}
_FUTURE_STUDENT_BY_VIEWPORT = {
    1440: UUID("e2000000-0000-4000-8000-000000000010"),
    1024: UUID("e2000000-0000-4000-8000-000000000020"),
    768: UUID("e2000000-0000-4000-8000-000000000030"),
    360: UUID("e2000000-0000-4000-8000-000000000040"),
}
_ALL_STAY_IDS = (
    *CURRENT_STAY_IDS,
    *(stay_id for pair in FUTURE_STAY_IDS_BY_VIEWPORT.values() for stay_id in pair),
)
_ROOM_NUMBERS = (
    "4-201",
    "4-202",
    "4-203",
    "4-205",
    "4-206",
    "4-210",
)


@dataclass(frozen=True)
class HousingPortalE2ESeedResult:
    current_stays: int
    planned_stays: int
    room_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class _RoomFixture:
    room: HousingRoom
    floor_name: str
    building_name: str


def _refuse_missing_fixture() -> None:
    raise E2ESeedRefused("Housing portal E2E seed requires canonical E2E fixtures")


def _require_people(db: Session) -> None:
    expected_people = {
        E2E_ADMIN_PERSON_ID: ("E2E平台管理员", "female"),
        E2E_GUARDIAN_PERSON_ID: ("E2E监护人", "male"),
        E2E_BOUND_STUDENT_ID: ("E2E已绑定学员", "female"),
        E2E_SECOND_BOUND_STUDENT_ID: ("E2E监护人第二学员", "female"),
        E2E_COACH_PERSON_ID: ("E2E教学教练", "male"),
        E2E_SELF_STUDENT_PERSON_ID: ("E2E自助学员", "male"),
        **{
            student_id: (f"E2E招生学员{viewport}", "male")
            for viewport, student_id in _FUTURE_STUDENT_BY_VIEWPORT.items()
        },
    }
    people = {
        person.id: person
        for person in db.scalars(
            select(Person).where(
                Person.id.in_(expected_people),
                Person.deleted_at.is_(None),
            )
        )
    }
    if set(people) != set(expected_people):
        _refuse_missing_fixture()
    if any(
        (people[person_id].full_name, people[person_id].sex) != expected
        for person_id, expected in expected_people.items()
    ):
        _refuse_missing_fixture()

    student_ids = {
        E2E_BOUND_STUDENT_ID,
        E2E_SECOND_BOUND_STUDENT_ID,
        E2E_SELF_STUDENT_PERSON_ID,
        *_FUTURE_STUDENT_BY_VIEWPORT.values(),
    }
    if (
        set(
            db.scalars(
                select(StudentProfile.person_id).where(StudentProfile.person_id.in_(student_ids))
            )
        )
        != student_ids
    ):
        _refuse_missing_fixture()


def _require_accounts(db: Session) -> None:
    expected_accounts = {
        E2E_ADMIN_ACCOUNT_ID: E2E_ADMIN_PERSON_ID,
        E2E_GUARDIAN_ACCOUNT_ID: E2E_GUARDIAN_PERSON_ID,
        E2E_COACH_ACCOUNT_ID: E2E_COACH_PERSON_ID,
        E2E_SELF_STUDENT_ACCOUNT_ID: E2E_SELF_STUDENT_PERSON_ID,
    }
    accounts = {
        account.id: account
        for account in db.scalars(select(UserAccount).where(UserAccount.id.in_(expected_accounts)))
    }
    if set(accounts) != set(expected_accounts):
        _refuse_missing_fixture()
    if any(
        account.person_id != expected_accounts[account_id] or account.status != "active"
        for account_id, account in accounts.items()
    ):
        _refuse_missing_fixture()


def _require_guardian_relationships(db: Session) -> None:
    expected_relationships = {
        E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID: E2E_BOUND_STUDENT_ID,
        E2E_SECOND_GUARDIAN_RELATIONSHIP_ID: E2E_SECOND_BOUND_STUDENT_ID,
        **{
            PORTAL_RELATIONSHIPS_BY_VIEWPORT[viewport]: student_id
            for viewport, student_id in _FUTURE_STUDENT_BY_VIEWPORT.items()
        },
    }
    relationships = {
        relationship.id: relationship
        for relationship in db.scalars(
            select(GuardianRelationship).where(GuardianRelationship.id.in_(expected_relationships))
        )
    }
    if set(relationships) != set(expected_relationships):
        _refuse_missing_fixture()
    if any(
        relationship.guardian_person_id != E2E_GUARDIAN_PERSON_ID
        or relationship.student_person_id != expected_relationships[relationship_id]
        or relationship.ended_at is not None
        for relationship_id, relationship in relationships.items()
    ):
        _refuse_missing_fixture()


def _require_rooms(db: Session) -> dict[str, _RoomFixture]:
    rows = db.execute(
        select(HousingRoom, HousingFloor.name, HousingBuilding.name)
        .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
        .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
        .where(
            HousingRoom.room_number.in_(_ROOM_NUMBERS),
            HousingFloor.is_active.is_(True),
            HousingBuilding.is_active.is_(True),
        )
    ).all()
    rooms = {
        room.room_number: _RoomFixture(
            room=room,
            floor_name=floor_name,
            building_name=building_name,
        )
        for room, floor_name, building_name in rows
    }
    if set(rooms) != set(_ROOM_NUMBERS):
        _refuse_missing_fixture()
    expected_capacity = {
        room_number: ("single", 1) if room_number == "4-210" else ("double", 2)
        for room_number in _ROOM_NUMBERS
    }
    if any(
        (fixture.room.room_type, fixture.room.capacity) != expected_capacity[room_number]
        or fixture.room.service_status != "available"
        or fixture.floor_name != "二楼"
        or fixture.building_name != "4幢"
        for room_number, fixture in rooms.items()
    ):
        _refuse_missing_fixture()
    return rooms


def _stay(
    *,
    stay_id: UUID,
    person_id: UUID,
    room_fixture: _RoomFixture,
    planned_check_in_at: datetime,
    planned_check_out_at: datetime | None,
    checked_in: bool,
) -> HousingStay:
    return HousingStay(
        id=stay_id,
        person_id=person_id,
        room_id=room_fixture.room.id,
        stay_purpose="permanent",
        duration_mode="long_term" if planned_check_out_at is None else "fixed_term",
        cohort_id=None,
        visit_reason=None,
        planned_check_in_at=planned_check_in_at,
        planned_check_out_at=planned_check_out_at,
        actual_check_in_at=planned_check_in_at if checked_in else None,
        actual_check_out_at=None,
        status="checked_in" if checked_in else "planned",
        room_name_snapshot=room_fixture.room.room_number,
        floor_name_snapshot=room_fixture.floor_name,
        building_name_snapshot=room_fixture.building_name,
        notes="E2E个人住宿门户私有夹具备注",
        cancellation_reason=None,
        checkout_requested_actual_at=None,
        checkout_requested_at=None,
        checkout_requested_by_user_id=None,
        created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        checked_in_by_user_id=E2E_ADMIN_ACCOUNT_ID if checked_in else None,
        checked_out_by_user_id=None,
        cancelled_by_user_id=None,
        version=1,
    )


def seed_housing_portal_e2e(
    db: Session,
    *,
    settings: Settings,
) -> HousingPortalE2ESeedResult:
    _require_e2e_environment(settings)
    with write_transaction(db):
        _require_people(db)
        _require_accounts(db)
        _require_guardian_relationships(db)
        rooms = _require_rooms(db)

        db.execute(delete(HousingStay).where(HousingStay.id.in_(_ALL_STAY_IDS)))

        for fixture in rooms.values():
            if fixture.room.verification_status != "verified":
                fixture.room.verification_status = "verified"
                fixture.room.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
                fixture.room.version += 1

        db.add_all(
            [
                _stay(
                    stay_id=CURRENT_STAY_IDS[0],
                    person_id=E2E_BOUND_STUDENT_ID,
                    room_fixture=rooms["4-201"],
                    planned_check_in_at=_CURRENT_CHECK_IN,
                    planned_check_out_at=None,
                    checked_in=True,
                ),
                _stay(
                    stay_id=CURRENT_STAY_IDS[1],
                    person_id=E2E_SECOND_BOUND_STUDENT_ID,
                    room_fixture=rooms["4-201"],
                    planned_check_in_at=_CURRENT_CHECK_IN,
                    planned_check_out_at=None,
                    checked_in=True,
                ),
                _stay(
                    stay_id=CURRENT_STAY_IDS[2],
                    person_id=E2E_SELF_STUDENT_PERSON_ID,
                    room_fixture=rooms["4-210"],
                    planned_check_in_at=_CURRENT_CHECK_IN,
                    planned_check_out_at=None,
                    checked_in=True,
                ),
                *(
                    stay
                    for viewport, (
                        student_stay_id,
                        coach_stay_id,
                    ) in FUTURE_STAY_IDS_BY_VIEWPORT.items()
                    for stay in (
                        _stay(
                            stay_id=student_stay_id,
                            person_id=_FUTURE_STUDENT_BY_VIEWPORT[viewport],
                            room_fixture=rooms[FUTURE_ROOM_BY_VIEWPORT[viewport]],
                            planned_check_in_at=datetime(
                                2030,
                                _FUTURE_MONTH_BY_VIEWPORT[viewport],
                                1,
                                9,
                                0,
                                tzinfo=_SHANGHAI,
                            ),
                            planned_check_out_at=datetime(
                                2030,
                                _FUTURE_MONTH_BY_VIEWPORT[viewport],
                                8,
                                18,
                                0,
                                tzinfo=_SHANGHAI,
                            ),
                            checked_in=False,
                        ),
                        _stay(
                            stay_id=coach_stay_id,
                            person_id=E2E_COACH_PERSON_ID,
                            room_fixture=rooms[FUTURE_ROOM_BY_VIEWPORT[viewport]],
                            planned_check_in_at=datetime(
                                2030,
                                _FUTURE_MONTH_BY_VIEWPORT[viewport],
                                1,
                                9,
                                0,
                                tzinfo=_SHANGHAI,
                            ),
                            planned_check_out_at=datetime(
                                2030,
                                _FUTURE_MONTH_BY_VIEWPORT[viewport],
                                8,
                                18,
                                0,
                                tzinfo=_SHANGHAI,
                            ),
                            checked_in=False,
                        ),
                    )
                ),
            ]
        )
        db.flush()

        current_stays = (
            db.scalar(
                select(func.count(HousingStay.id)).where(
                    HousingStay.id.in_(CURRENT_STAY_IDS),
                    HousingStay.status == "checked_in",
                )
            )
            or 0
        )
        planned_ids = _ALL_STAY_IDS[len(CURRENT_STAY_IDS) :]
        planned_stays = (
            db.scalar(
                select(func.count(HousingStay.id)).where(
                    HousingStay.id.in_(planned_ids),
                    HousingStay.status == "planned",
                )
            )
            or 0
        )
        result = HousingPortalE2ESeedResult(
            current_stays=current_stays,
            planned_stays=planned_stays,
            room_ids=tuple(rooms[number].room.id for number in _ROOM_NUMBERS),
        )
    return result


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        result = seed_housing_portal_e2e(db, settings=settings)
    print(
        "Housing portal E2E fixtures rebuilt: "
        f"current={result.current_stays}, "
        f"planned={result.planned_stays}, "
        f"rooms={len(result.room_ids)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
