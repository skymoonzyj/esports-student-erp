"""Synthetic room-care browser fixtures, restricted to the task-owned local DB."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, make_url, select
from sqlalchemy.orm import Session

from app.cli import seed_e2e as base
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomPhoto,
    HousingStay,
)
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

E2ESeedRefused = base.E2ESeedRefused
CARE_SCENARIOS = {
    "single": "4-201",
    "double": "4-202",
    "target": "4-203",
    "maintenance": "4-204",
    "pending_verification": "4-205",
    "long_term": "4-206",
}
CARE_ACCOUNT_ROLES = {
    base.E2E_ADMIN_ACCOUNT_ID: "admin",
    base.E2E_CUSTOMER_SERVICE_ACCOUNT_ID: "administration",
    base.E2E_LIFE_TEACHER_ACCOUNT_ID: "super_admin",
    base.E2E_COACH_ACCOUNT_ID: "coach",
    base.E2E_READY_MALE_STUDENT_ACCOUNT_ID: "administration",
}


def require_care_database(settings: Settings) -> None:
    base._require_e2e_environment(settings)
    # The ledger is controller-created, never a caller-selected override. Unknown
    # databases are refused before SessionLocal or destructive base seeding.
    try:
        configured = os.environ.get("E2E_HOUSING_CARE_RUNTIME_LEDGER", "")
        if not configured or not Path(configured).is_absolute():
            raise ValueError
        ledger = Path(configured)
        if ledger.is_symlink() or not ledger.is_file():
            raise ValueError
        owned = json.loads(ledger.read_text())
        url = make_url(settings.database_url)
        if (
            owned["test_database_url"] != settings.database_url
            or owned["task_label"] != "housing-care-20261023"
            or re.fullmatch(r"[0-9a-f]{64}", owned["container_id"]) is None
            or re.fullmatch(r"[0-9a-f]{64}", owned["volume"]) is None
            or url.host != "127.0.0.1"
            or url.username != "erp_e2e"
            or url.database != "erp_housing_care_test"
            or url.port == 55433
            or url.query
        ):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise E2ESeedRefused(
            "Housing-care seed requires the exact task-owned local database"
        ) from exc


def seed_housing_care(db: Session, settings: Settings) -> None:
    require_care_database(settings)
    now = datetime.now(UTC)
    with write_transaction(db):
        db.execute(delete(HousingRoomPhoto))
        for room in db.scalars(select(HousingRoom)):
            room.daily_cleaning_enabled = False
            room.cleaning_status = "ready"
            room.cleaning_pending_at = None
            room.cleaning_completed_at = None
            room.cleaning_completed_by_user_id = None
        rooms = {
            room.room_number: (room, floor_name, building_name)
            for room, floor_name, building_name in db.execute(
                select(HousingRoom, HousingFloor.name, HousingBuilding.name)
                .join(HousingFloor, HousingRoom.floor_id == HousingFloor.id)
                .join(HousingBuilding, HousingRoom.building_id == HousingBuilding.id)
                .where(HousingRoom.room_number.in_(CARE_SCENARIOS.values()))
            )
        }
        if set(rooms) != set(CARE_SCENARIOS.values()):
            raise E2ESeedRefused("Housing-care canonical rooms missing")
        for number, (room, _, _) in rooms.items():
            room.room_type = "double"
            room.capacity = 2
            room.verification_status = "pending_verification" if number == "4-205" else "verified"
            room.service_status = "maintenance" if number == "4-204" else "available"
            room.daily_cleaning_enabled = number in {"4-201", "4-202", "4-206"}
            room.version = 1
        for account, role in CARE_ACCOUNT_ROLES.items():
            db.execute(delete(UserRole).where(UserRole.user_id == account))
            db.add(UserRole(user_id=account, role_id=ROLES[role].id))
        for permission in ("housing.read", "housing.rooms.manage"):
            db.add(
                UserPermissionOverride(
                    user_id=base.E2E_READY_MALE_STUDENT_ACCOUNT_ID,
                    permission_id=PERMISSIONS[permission].id,
                    effect="deny",
                    scope_kind="building",
                    scope_id=rooms["4-201"][0].building_id,
                    created_by_user_id=base.E2E_ADMIN_ACCOUNT_ID,
                    grant_reason="Synthetic housing-care cross-building denial",
                )
            )
        # Room permissions alone must not elevate the actual coach role.
        for permission in ("housing.read", "housing.rooms.manage"):
            db.add(
                UserPermissionOverride(
                    user_id=base.E2E_COACH_ACCOUNT_ID,
                    permission_id=PERMISSIONS[permission].id,
                    effect="allow",
                    scope_kind="global",
                    scope_id=None,
                    created_by_user_id=base.E2E_ADMIN_ACCOUNT_ID,
                    grant_reason="Synthetic care actual-role enforcement",
                )
            )
        residents = [
            (base.E2E_SELF_STUDENT_PERSON_ID, "4-201"),
            (base.E2E_BOUND_STUDENT_ID, "4-202"),
            (base.E2E_SECOND_BOUND_STUDENT_ID, "4-202"),
            (base.E2E_VISITOR_PERSON_ID, "4-206"),
        ]
        for index, (person_id, number) in enumerate(residents, 1):
            room, floor_name, building_name = rooms[number]
            db.add(
                HousingStay(
                    id=UUID(f"e4900000-0000-4000-8000-{index:012d}"),
                    person_id=person_id,
                    room_id=room.id,
                    stay_purpose="permanent",
                    duration_mode="long_term",
                    planned_check_in_at=now - timedelta(days=2),
                    actual_check_in_at=now - timedelta(days=2),
                    status="checked_in",
                    room_name_snapshot=room.room_number,
                    floor_name_snapshot=floor_name,
                    building_name_snapshot=building_name,
                    created_by_user_id=base.E2E_ADMIN_ACCOUNT_ID,
                    checked_in_by_user_id=base.E2E_ADMIN_ACCOUNT_ID,
                    version=1,
                )
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-database", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    require_care_database(settings)
    if args.check_database:
        return 0
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        base.seed_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(base._secret_bytes(settings, "field_encryption_master_key")),
            lookup=LookupDigest(base._secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
        seed_housing_care(db, settings)
    print("Synthetic housing-care fixtures rebuilt in the exclusive local database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
