from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_ADMIN_PERSON_ID,
    E2E_BOUND_STUDENT_ID,
    E2E_COHORT_ID,
    E2E_GUARDIAN_PERSON_ID,
    E2E_OTHER_GUARDIAN_PERSON_ID,
    E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    E2E_READY_MALE_STUDENT_PERSON_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    E2E_VISITOR_PERSON_ID,
    E2ESeedRefused,
    _require_e2e_environment,
    _seed_account,
    _seed_person,
    _upsert_seed_phone_identifier,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingStay,
    HousingTransfer,
)
from app.housing.reporting_models import HousingReportBatch
from app.housing.seed_inventory import FLOOR_SEEDS, INITIAL_ROOMS
from app.identity.models import GuardianRelationship, Person, StaffProfile
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

_SHANGHAI = ZoneInfo("Asia/Shanghai")

REPORT_BUILDING_PHONE = E2E_CREDENTIALS["housing_report_building"].identifier
REPORT_BUILDING_PASSWORD = E2E_CREDENTIALS["housing_report_building"].password
REPORT_FLOOR_PHONE = E2E_CREDENTIALS["housing_report_floor"].identifier
REPORT_FLOOR_PASSWORD = E2E_CREDENTIALS["housing_report_floor"].password
REPORT_NO_PERMISSION_PHONE = E2E_CREDENTIALS["housing_report_none"].identifier
REPORT_NO_PERMISSION_PASSWORD = E2E_CREDENTIALS["housing_report_none"].password

REPORT_BUILDING_PERSON_ID = UUID("e3100000-0000-4000-8000-000000000001")
REPORT_FLOOR_PERSON_ID = UUID("e3100000-0000-4000-8000-000000000002")
REPORT_NO_PERMISSION_PERSON_ID = UUID("e3100000-0000-4000-8000-000000000003")
REPORT_TRANSFER_PERSON_ID = UUID("e2000000-0000-4000-8000-000000000010")
REPORT_FIXTURE_PERSON_IDS = (
    REPORT_BUILDING_PERSON_ID,
    REPORT_FLOOR_PERSON_ID,
    REPORT_NO_PERMISSION_PERSON_ID,
)

REPORT_BUILDING_ACCOUNT_ID = UUID("e3110000-0000-4000-8000-000000000001")
REPORT_FLOOR_ACCOUNT_ID = UUID("e3110000-0000-4000-8000-000000000002")
REPORT_NO_PERMISSION_ACCOUNT_ID = UUID("e3110000-0000-4000-8000-000000000003")
REPORT_FIXTURE_ACCOUNT_IDS = (
    REPORT_BUILDING_ACCOUNT_ID,
    REPORT_FLOOR_ACCOUNT_ID,
    REPORT_NO_PERMISSION_ACCOUNT_ID,
)
_REPORT_MINOR_ACCOUNT_ID = UUID("e3110000-0000-4000-8000-000000000004")
_REPORT_TRANSFER_ACCOUNT_ID = UUID("e3110000-0000-4000-8000-000000000005")
_REPORT_RESIDENT_ACCOUNT_IDS = (
    _REPORT_MINOR_ACCOUNT_ID,
    _REPORT_TRANSFER_ACCOUNT_ID,
)
_REPORT_SEED_ACCOUNT_IDS = (*REPORT_FIXTURE_ACCOUNT_IDS, *_REPORT_RESIDENT_ACCOUNT_IDS)

REPORT_FIXTURE_STAY_IDS = tuple(
    UUID(f"e3120000-0000-4000-8000-{index:012d}") for index in range(1, 7)
)
REPORT_TRANSFER_ID = UUID("e3130000-0000-4000-8000-000000000001")
REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID = UUID(
    "e3140000-0000-4000-8000-000000000001"
)
REPORT_PERMISSION_ID = PERMISSIONS["housing.reports.export_sensitive"].id

_ROOM_NUMBERS = ("4-201", "4-202", "4-203", "4-301", "A-201", "A-202")
_ROOM_SEEDS = {room.room_number: room for room in INITIAL_ROOMS}
REPORT_FIXTURE_ROOM_IDS = tuple(_ROOM_SEEDS[number].id for number in _ROOM_NUMBERS)
_FLOOR_SEEDS = {
    (floor.building_code, floor.number): floor for floor in FLOOR_SEEDS
}
REPORT_BUILDING_SCOPE_ID = _FLOOR_SEEDS[("4", 2)].building_id
REPORT_ALLOWED_FLOOR_ID = _FLOOR_SEEDS[("4", 2)].id
REPORT_DENIED_FLOOR_ID = _FLOOR_SEEDS[("4", 3)].id
REPORT_OTHER_BUILDING_ID = _FLOOR_SEEDS[("A", 2)].building_id
REPORT_OTHER_FLOOR_ID = _FLOOR_SEEDS[("A", 2)].id

_OVERRIDE_IDS = (
    UUID("e3150000-0000-4000-8000-000000000001"),
    UUID("e3150000-0000-4000-8000-000000000002"),
    UUID("e3150000-0000-4000-8000-000000000003"),
)
_MINOR_REPORT_PHONE = "13900000024"
_TRANSFER_REPORT_PHONE = "13800000007"


@dataclass(frozen=True)
class HousingReportingE2ESeedResult:
    stays: int
    accounts: int
    rooms: int


@dataclass(frozen=True)
class _RoomFixture:
    room: HousingRoom
    floor_name: str
    building_name: str


def _refuse_missing_fixture() -> None:
    raise E2ESeedRefused("Housing reporting seed requires canonical E2E fixtures")


def _require_base_fixtures(db: Session) -> dict[str, _RoomFixture]:
    required_people = {
        E2E_ADMIN_PERSON_ID,
        E2E_BOUND_STUDENT_ID,
        E2E_GUARDIAN_PERSON_ID,
        E2E_OTHER_GUARDIAN_PERSON_ID,
        E2E_READY_MALE_STUDENT_PERSON_ID,
        E2E_SELF_STUDENT_PERSON_ID,
        E2E_VISITOR_PERSON_ID,
        REPORT_TRANSFER_PERSON_ID,
    }
    if set(
        db.scalars(
            select(Person.id).where(
                Person.id.in_(required_people),
                Person.deleted_at.is_(None),
            )
        )
    ) != required_people:
        _refuse_missing_fixture()
    admin = db.get(UserAccount, E2E_ADMIN_ACCOUNT_ID)
    primary_guardian = db.get(
        GuardianRelationship,
        E2E_PRIMARY_GUARDIAN_RELATIONSHIP_ID,
    )
    if (
        admin is None
        or admin.person_id != E2E_ADMIN_PERSON_ID
        or admin.status != "active"
        or primary_guardian is None
        or primary_guardian.guardian_person_id != E2E_GUARDIAN_PERSON_ID
        or primary_guardian.student_person_id != E2E_BOUND_STUDENT_ID
        or primary_guardian.ended_at is not None
    ):
        _refuse_missing_fixture()
    rows = db.execute(
        select(HousingRoom, HousingFloor.name, HousingBuilding.name)
        .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
        .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
        .where(
            HousingRoom.id.in_(REPORT_FIXTURE_ROOM_IDS),
            HousingRoom.service_status == "available",
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
    return rooms


def _clear_fixture_rows(db: Session) -> None:
    if (db.scalar(select(func.count(HousingReportBatch.id))) or 0) != 0:
        raise E2ESeedRefused(
            "Housing reporting seed requires a fresh base E2E dataset without report batches"
        )
    db.execute(delete(HousingTransfer).where(HousingTransfer.id == REPORT_TRANSFER_ID))
    db.execute(delete(HousingStay).where(HousingStay.id.in_(REPORT_FIXTURE_STAY_IDS)))
    db.execute(
        delete(GuardianRelationship).where(
            GuardianRelationship.id == REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID
        )
    )
    db.execute(
        delete(UserPermissionOverride).where(
            UserPermissionOverride.user_id.in_(REPORT_FIXTURE_ACCOUNT_IDS)
        )
    )
    db.execute(delete(UserAccount).where(UserAccount.id.in_(_REPORT_SEED_ACCOUNT_IDS)))
    db.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(REPORT_FIXTURE_PERSON_IDS)))
    db.execute(delete(Person).where(Person.id.in_(REPORT_FIXTURE_PERSON_IDS)))


def _stay(
    *,
    stay_id: UUID,
    person_id: UUID,
    room: _RoomFixture,
    purpose: str,
    check_in: datetime,
    planned_check_out: datetime | None,
    status: str,
    actual_check_out: datetime | None = None,
    visit_reason: str | None = None,
    cohort_id: UUID | None = None,
    checkout_requested_actual_at: datetime | None = None,
    checkout_requested_at: datetime | None = None,
) -> HousingStay:
    checked_out = status == "checked_out"
    checkout_pending = status == "checkout_pending"
    return HousingStay(
        id=stay_id,
        person_id=person_id,
        room_id=room.room.id,
        stay_purpose=purpose,
        duration_mode="long_term" if planned_check_out is None else "fixed_term",
        cohort_id=cohort_id,
        visit_reason=visit_reason,
        planned_check_in_at=check_in,
        planned_check_out_at=planned_check_out,
        actual_check_in_at=check_in,
        actual_check_out_at=actual_check_out,
        status=status,
        room_name_snapshot=room.room.room_number,
        floor_name_snapshot=room.floor_name,
        building_name_snapshot=room.building_name,
        notes="E2E实名报送固定入住夹具",
        cancellation_reason=None,
        checkout_requested_actual_at=(
            checkout_requested_actual_at if checkout_pending else None
        ),
        checkout_requested_at=checkout_requested_at if checkout_pending else None,
        checkout_requested_by_user_id=(
            E2E_ADMIN_ACCOUNT_ID if checkout_pending else None
        ),
        created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        checked_in_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        checked_out_by_user_id=E2E_ADMIN_ACCOUNT_ID if checked_out else None,
        cancelled_by_user_id=None,
        version=1,
    )


def seed_housing_reporting_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> HousingReportingE2ESeedResult:
    _require_e2e_environment(settings)
    with write_transaction(db):
        rooms = _require_base_fixtures(db)
        _clear_fixture_rows(db)

        minor = db.get(Person, E2E_BOUND_STUDENT_ID)
        assert minor is not None
        minor.phone_ciphertext = cipher.encrypt(_MINOR_REPORT_PHONE, field_name="phone")
        minor.phone_digest = lookup.digest(_MINOR_REPORT_PHONE)

        db.add_all(
            [
                _seed_person(
                    person_id=REPORT_BUILDING_PERSON_ID,
                    full_name="E2E实名楼栋专员",
                    identity_birth="19850102",
                    identity_sequence=204,
                    sex="female",
                    date_of_birth=date(1985, 1, 2),
                    phone=REPORT_BUILDING_PHONE,
                    email="e2e-report-building@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=REPORT_FLOOR_PERSON_ID,
                    full_name="E2E实名楼层专员",
                    identity_birth="19860102",
                    identity_sequence=205,
                    sex="male",
                    date_of_birth=date(1986, 1, 2),
                    phone=REPORT_FLOOR_PHONE,
                    email="e2e-report-floor@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
                _seed_person(
                    person_id=REPORT_NO_PERMISSION_PERSON_ID,
                    full_name="E2E实名无权账号",
                    identity_birth="19870102",
                    identity_sequence=206,
                    sex="female",
                    date_of_birth=date(1987, 1, 2),
                    phone=REPORT_NO_PERMISSION_PHONE,
                    email="e2e-report-none@example.com",
                    cipher=cipher,
                    lookup=lookup,
                ),
            ]
        )
        db.flush()
        db.add_all(
            [
                StaffProfile(
                    person_id=REPORT_BUILDING_PERSON_ID,
                    staff_type="customer_service",
                ),
                StaffProfile(
                    person_id=REPORT_FLOOR_PERSON_ID,
                    staff_type="coach",
                ),
                StaffProfile(
                    person_id=REPORT_NO_PERMISSION_PERSON_ID,
                    staff_type="life_teacher",
                ),
            ]
        )
        db.add_all(
            [
                _seed_account(
                    account_id=REPORT_BUILDING_ACCOUNT_ID,
                    person_id=REPORT_BUILDING_PERSON_ID,
                    phone=REPORT_BUILDING_PHONE,
                    email="e2e-report-building@example.com",
                    password=REPORT_BUILDING_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=REPORT_FLOOR_ACCOUNT_ID,
                    person_id=REPORT_FLOOR_PERSON_ID,
                    phone=REPORT_FLOOR_PHONE,
                    email="e2e-report-floor@example.com",
                    password=REPORT_FLOOR_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=REPORT_NO_PERMISSION_ACCOUNT_ID,
                    person_id=REPORT_NO_PERMISSION_PERSON_ID,
                    phone=REPORT_NO_PERMISSION_PHONE,
                    email="e2e-report-none@example.com",
                    password=REPORT_NO_PERMISSION_PASSWORD,
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=_REPORT_MINOR_ACCOUNT_ID,
                    person_id=E2E_BOUND_STUDENT_ID,
                    phone=_MINOR_REPORT_PHONE,
                    email="e2e-report-minor@example.com",
                    password="E2EReportM26Aa",
                    lookup=lookup,
                    passwords=passwords,
                ),
                _seed_account(
                    account_id=_REPORT_TRANSFER_ACCOUNT_ID,
                    person_id=REPORT_TRANSFER_PERSON_ID,
                    phone=_TRANSFER_REPORT_PHONE,
                    email="e2e-report-transfer@example.com",
                    password="E2EReportT26Aa",
                    lookup=lookup,
                    passwords=passwords,
                ),
            ]
        )
        db.flush()
        for account_id, phone in (
            (REPORT_BUILDING_ACCOUNT_ID, REPORT_BUILDING_PHONE),
            (REPORT_FLOOR_ACCOUNT_ID, REPORT_FLOOR_PHONE),
            (REPORT_NO_PERMISSION_ACCOUNT_ID, REPORT_NO_PERMISSION_PHONE),
            (_REPORT_MINOR_ACCOUNT_ID, _MINOR_REPORT_PHONE),
            (_REPORT_TRANSFER_ACCOUNT_ID, _TRANSFER_REPORT_PHONE),
        ):
            _upsert_seed_phone_identifier(
                db,
                account_id=account_id,
                phone=phone,
                lookup=lookup,
            )
        db.flush()
        db.add_all(
            [
                UserPermissionOverride(
                    id=_OVERRIDE_IDS[0],
                    user_id=REPORT_BUILDING_ACCOUNT_ID,
                    permission_id=REPORT_PERMISSION_ID,
                    effect="allow",
                    scope_kind="building",
                    scope_id=REPORT_BUILDING_SCOPE_ID,
                    valid_from=None,
                    valid_until=None,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                    grant_reason="E2E实名报送楼栋范围",
                ),
                UserPermissionOverride(
                    id=_OVERRIDE_IDS[1],
                    user_id=REPORT_FLOOR_ACCOUNT_ID,
                    permission_id=REPORT_PERMISSION_ID,
                    effect="allow",
                    scope_kind="floor",
                    scope_id=REPORT_ALLOWED_FLOOR_ID,
                    valid_from=None,
                    valid_until=None,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                    grant_reason="E2E实名报送楼层范围",
                ),
                UserPermissionOverride(
                    id=_OVERRIDE_IDS[2],
                    user_id=REPORT_FLOOR_ACCOUNT_ID,
                    permission_id=REPORT_PERMISSION_ID,
                    effect="deny",
                    scope_kind="floor",
                    scope_id=REPORT_DENIED_FLOOR_ID,
                    valid_from=None,
                    valid_until=None,
                    created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                    grant_reason="E2E实名报送显式拒绝",
                ),
                GuardianRelationship(
                    id=REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID,
                    guardian_person_id=E2E_OTHER_GUARDIAN_PERSON_ID,
                    student_person_id=E2E_BOUND_STUDENT_ID,
                    relationship="mother",
                    is_primary=False,
                    valid_from=date(2026, 1, 1),
                    valid_until=None,
                ),
            ]
        )

        for room in rooms.values():
            if room.room.verification_status != "verified":
                room.room.verification_status = "verified"
                room.room.updated_by_user_id = E2E_ADMIN_ACCOUNT_ID
                room.room.version += 1

        transfer_start = datetime(2026, 8, 1, 9, 0, tzinfo=_SHANGHAI)
        transfer_at = datetime(2026, 8, 5, 10, 0, tzinfo=_SHANGHAI)
        transfer_end = datetime(2026, 8, 10, 18, 0, tzinfo=_SHANGHAI)
        db.add_all(
            [
                _stay(
                    stay_id=REPORT_FIXTURE_STAY_IDS[0],
                    person_id=E2E_SELF_STUDENT_PERSON_ID,
                    room=rooms["4-201"],
                    purpose="permanent",
                    check_in=datetime(2026, 8, 2, 9, 0, tzinfo=_SHANGHAI),
                    planned_check_out=None,
                    status="checked_in",
                ),
                _stay(
                    stay_id=REPORT_FIXTURE_STAY_IDS[1],
                    person_id=E2E_BOUND_STUDENT_ID,
                    room=rooms["4-202"],
                    purpose="camp",
                    check_in=datetime(2026, 8, 3, 9, 0, tzinfo=_SHANGHAI),
                    planned_check_out=datetime(
                        2026, 8, 20, 18, 0, tzinfo=_SHANGHAI
                    ),
                    status="checked_in",
                    cohort_id=E2E_COHORT_ID,
                ),
                _stay(
                    stay_id=REPORT_FIXTURE_STAY_IDS[2],
                    person_id=REPORT_TRANSFER_PERSON_ID,
                    room=rooms["4-301"],
                    purpose="permanent",
                    check_in=transfer_start,
                    planned_check_out=transfer_at,
                    actual_check_out=transfer_at,
                    status="checked_out",
                ),
                _stay(
                    stay_id=REPORT_FIXTURE_STAY_IDS[3],
                    person_id=REPORT_TRANSFER_PERSON_ID,
                    room=rooms["A-201"],
                    purpose="permanent",
                    check_in=transfer_at,
                    planned_check_out=transfer_end,
                    actual_check_out=transfer_end,
                    status="checked_out",
                ),
                _stay(
                    stay_id=REPORT_FIXTURE_STAY_IDS[4],
                    person_id=REPORT_FLOOR_PERSON_ID,
                    room=rooms["4-203"],
                    purpose="permanent",
                    check_in=datetime(2026, 7, 1, 9, 0, tzinfo=_SHANGHAI),
                    planned_check_out=None,
                    status="checkout_pending",
                    checkout_requested_actual_at=datetime(
                        2026, 7, 31, 18, 0, tzinfo=_SHANGHAI
                    ),
                    checkout_requested_at=datetime(
                        2026, 7, 30, 12, 0, tzinfo=_SHANGHAI
                    ),
                ),
                _stay(
                    stay_id=REPORT_FIXTURE_STAY_IDS[5],
                    person_id=E2E_VISITOR_PERSON_ID,
                    room=rooms["A-202"],
                    purpose="temporary",
                    check_in=datetime(2026, 8, 6, 9, 0, tzinfo=_SHANGHAI),
                    planned_check_out=datetime(
                        2026, 8, 8, 18, 0, tzinfo=_SHANGHAI
                    ),
                    status="checked_in",
                    visit_reason="探望学员临时入住",
                ),
            ]
        )
        db.flush()
        db.add(
            HousingTransfer(
                id=REPORT_TRANSFER_ID,
                person_id=REPORT_TRANSFER_PERSON_ID,
                from_stay_id=REPORT_FIXTURE_STAY_IDS[2],
                to_stay_id=REPORT_FIXTURE_STAY_IDS[3],
                transferred_at=transfer_at,
                reason="E2E实名报送已完成换房",
                transferred_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            )
        )
        db.flush()
        result = HousingReportingE2ESeedResult(
            stays=(
                db.scalar(
                    select(func.count(HousingStay.id)).where(
                        HousingStay.id.in_(REPORT_FIXTURE_STAY_IDS)
                    )
                )
                or 0
            ),
            accounts=(
                db.scalar(
                    select(func.count(UserAccount.id)).where(
                        UserAccount.id.in_(REPORT_FIXTURE_ACCOUNT_IDS)
                    )
                )
                or 0
            ),
            rooms=(
                db.scalar(
                    select(func.count(HousingRoom.id)).where(
                        HousingRoom.id.in_(REPORT_FIXTURE_ROOM_IDS),
                        HousingRoom.verification_status == "verified",
                    )
                )
                or 0
            ),
        )
    return result


def _decode_secret(value: str) -> bytes:
    return base64.b64decode(value, altchars=b"-_", validate=True)


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    cipher = EnvelopeCipher(
        _decode_secret(settings.field_encryption_master_key.get_secret_value())
    )
    lookup = LookupDigest(_decode_secret(settings.field_lookup_key.get_secret_value()))
    with SessionLocal() as db:
        result = seed_housing_reporting_e2e(
            db,
            settings=settings,
            cipher=cipher,
            lookup=lookup,
            passwords=PasswordService(),
        )
    print(
        "Housing reporting E2E fixtures rebuilt: "
        f"stays={result.stays}, accounts={result.accounts}, rooms={result.rooms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
