import csv
import importlib
from datetime import UTC, datetime
from io import StringIO
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.housing.inventory_service import HousingInventoryService, HousingOverviewFilters
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.housing.occupancy_service import RoomOccupancyProjection
from app.housing.schemas import (
    HousingAllowedActions,
    HousingOccupancyExportRequest,
    HousingRoomCard,
    HousingRoomStatusExportRequest,
    RoomCreate,
    RoomUpdate,
)
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from tests.api.test_housing_cleaning import care_room as _care_room
from tests.api.test_housing_occupancy import _sign_in
from tests.api.test_housing_room_status_export import snapshot_client as _snapshot_client
from tests.api.test_housing_room_status_export import snapshot_context as _snapshot_context
from tests.api.test_housing_summary_filters import mixed_rooms as _mixed_rooms

care_room = _care_room
mixed_rooms = _mixed_rooms
snapshot_client = _snapshot_client
snapshot_context = _snapshot_context


@pytest.mark.parametrize("enabled,frequency", [(True, "daily"), (False, "unspecified")])
def test_legacy_room_writes_normalize_frequency(enabled, frequency):
    create = RoomCreate(
        floor_id=uuid4(), room_number="101", room_type="single", capacity=1,
        daily_cleaning_enabled=enabled,
    )
    update = RoomUpdate(version=1, daily_cleaning_enabled=enabled)
    assert create.cleaning_frequency == frequency
    assert update.cleaning_frequency == frequency


@pytest.mark.parametrize("frequency", ["unspecified", "daily", "weekly", "on_demand"])
def test_frequency_writes_project_legacy_boolean(frequency):
    create = RoomCreate(
        floor_id=uuid4(), room_number="101", room_type="single", capacity=1,
        cleaning_frequency=frequency,
    )
    update = RoomUpdate(version=1, cleaning_frequency=frequency)
    assert create.daily_cleaning_enabled is (frequency == "daily")
    assert update.daily_cleaning_enabled is (frequency == "daily")
    assert create.model_fields_set & {"cleaning_frequency", "daily_cleaning_enabled"} == {
        "cleaning_frequency"
    }


@pytest.mark.parametrize("frequency,enabled", [("weekly", True), ("daily", False)])
def test_disagreeing_legacy_boolean_and_frequency_rejected(frequency, enabled):
    with pytest.raises(ValidationError):
        RoomUpdate(version=1, cleaning_frequency=frequency, daily_cleaning_enabled=enabled)


def test_false_boolean_and_weekly_are_consistent():
    update = RoomUpdate(version=1, cleaning_frequency="weekly", daily_cleaning_enabled=False)
    assert update.cleaning_frequency == "weekly"


@pytest.mark.parametrize("value", [None, "monthly", ""])
def test_explicit_invalid_frequency_rejected(value):
    with pytest.raises(ValidationError):
        RoomUpdate(version=1, cleaning_frequency=value)


def _card(frequency, *, building_id=None, cleaning_status="pending"):
    now = datetime.now(UTC)
    return HousingRoomCard(
        id=uuid4(), building_id=building_id or uuid4(), floor_id=uuid4(),
        room_number="101", room_type="single", capacity=1, area_square_meters=None,
        verification_status="verified", service_status="available",
        cleaning_frequency=frequency, cleaning_status=cleaning_status,
        notes=None, version=1, created_at=now, updated_at=now,
        building_code="A", building_name="一栋", building_is_active=True,
        floor_number=1, floor_name="一楼", floor_is_active=True,
        current_occupants=0, remaining_capacity=1, current_gender=None,
        has_future_reservation=False, occupancy_status="unoccupied", is_assignable=False,
        status_text="退房待保洁",
        allowed_actions=HousingAllowedActions(edit=False, delete=False, verify=False),
    )


def test_frequency_summary_and_quick_filters_do_not_clear_pending_cleaning():
    cards = [_card(value) for value in ("daily", "weekly", "on_demand", "unspecified")]
    summary = HousingInventoryService._summary(cards)
    assert summary.daily_cleaning_rooms == 1
    assert summary.weekly_cleaning_rooms == 1
    assert summary.on_demand_cleaning_rooms == 1
    assert summary.pending_cleaning_rooms == 4
    for frequency, card in zip(("daily", "weekly", "on_demand"), cards, strict=False):
        matches = [
            item for item in cards
            if HousingInventoryService._matches_quick_filter(item, "cleaning_" + frequency)
        ]
        assert matches == [card]
        assert card.cleaning_status == "pending"


def test_pending_cleaning_status_text_distinguishes_checkout_cleaning_from_frequency():
    room = HousingRoom(
        room_type="single", verification_status="verified", service_status="available",
        cleaning_status="pending", cleaning_frequency="weekly",
    )
    assert HousingInventoryService._status_text(
        room, HousingBuilding(is_active=True), HousingFloor(is_active=True)
    ) == "退房待保洁"


def test_frequency_filter_ors_values_but_ands_building_and_legacy_false():
    building_id = uuid4()
    filters = HousingOverviewFilters(
        building_id=building_id, cleaning_frequencies=frozenset({"weekly", "on_demand"}),
        daily_cleaning_enabled=False,
    )
    for frequency, same_building, expected in [
        ("weekly", True, True), ("on_demand", True, True),
        ("daily", True, False), ("unspecified", True, False), ("weekly", False, False),
    ]:
        card = _card(frequency, building_id=building_id if same_building else uuid4())
        projection = RoomOccupancyProjection(current_stays=(), future_reservation_count=0, open_issue=None)
        assert HousingInventoryService._matches_filters(card, filters, projection) is expected


@pytest.mark.parametrize("schema", [HousingOccupancyExportRequest, HousingRoomStatusExportRequest])
def test_both_export_requests_preserve_frequency_and_building_filters(schema):
    building_id = uuid4()
    scope = {"building_id": building_id} if schema is HousingOccupancyExportRequest else {
        "building_ids": [building_id]
    }
    request = schema(
        purpose="保洁核对", cleaning_frequencies=["weekly", "on_demand"],
        quick_filter="cleaning_weekly", **scope,
    )
    assert request.cleaning_frequencies == ["weekly", "on_demand"]
    assert request.model_dump()[next(iter(scope))] == next(iter(scope.values()))


@pytest.mark.parametrize("frequency", ["daily", "weekly", "on_demand", "unspecified"])
def test_frequency_api_write_persists_without_clearing_pending_cleaning(
    db_client, db, platform_users, care_room, frequency,
):
    csrf = _sign_in(db_client, platform_users, "admin")
    response = db_client.patch(
        f"/api/v1/housing/rooms/{care_room.id}",
        json={"version": care_room.version, "cleaning_frequency": frequency},
        headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["cleaning_frequency"] == frequency
    assert response.json()["daily_cleaning_enabled"] is (frequency == "daily")
    assert response.json()["cleaning_status"] == "pending"
    db.expire_all()
    persisted = db.get(HousingRoom, care_room.id)
    assert persisted.cleaning_frequency == frequency
    assert persisted.cleaning_status == "pending"


def test_frequency_quick_filter_preserves_building_and_counts_base_scope(
    db_client, db, platform_users, mixed_rooms,
):
    building, _, _, rooms = mixed_rooms
    for key, frequency in [("partial", "daily"), ("empty", "weekly"), ("dirty", "on_demand")]:
        rooms[key].cleaning_frequency = frequency
        rooms[key].daily_cleaning_enabled = frequency == "daily"
    db.commit()
    _sign_in(db_client, platform_users, "admin")
    response = db_client.get(
        "/api/v1/housing/overview",
        params={"building_id": str(building.id), "quick_filter": "cleaning_weekly"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["rooms"][0]["id"] == str(rooms["empty"].id)
    assert body["rooms"][0]["building_id"] == str(building.id)
    assert body["summary"]["total_rooms"] == 10
    assert body["summary"]["daily_cleaning_rooms"] == 1
    assert body["summary"]["weekly_cleaning_rooms"] == 1
    assert body["summary"]["on_demand_cleaning_rooms"] == 1
    multi = db_client.get(
        "/api/v1/housing/overview",
        params=[("building_id", str(building.id)), ("cleaning_frequency", "weekly"),
                ("cleaning_frequency", "on_demand")],
    )
    assert multi.status_code == 200, multi.text
    assert {room["id"] for room in multi.json()["rooms"]} == {
        str(rooms["empty"].id), str(rooms["dirty"].id)
    }


@pytest.mark.parametrize("changes", [{"notes": "未经审批备注"}, {"cleaning_frequency": "weekly"}])
def test_property_cannot_bypass_approval_via_legacy_room_mutation(
    db_client, db, platform_users, care_room, changes,
):
    actor = platform_users["accounts"]["admin"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES["property"].id))
    db.add(UserPermissionOverride(
        user_id=actor.id, permission_id=PERMISSIONS["housing.rooms.manage"].id,
        effect="allow", scope_kind="global",
    ))
    db.commit()
    before = (care_room.notes, care_room.cleaning_frequency, care_room.version)
    csrf = _sign_in(db_client, platform_users, "admin")
    response = db_client.patch(
        f"/api/v1/housing/rooms/{care_room.id}",
        json={"version": care_room.version, **changes}, headers={"x-csrf-token": csrf},
    )
    assert response.status_code == 403, response.text
    db.expire_all()
    persisted = db.get(HousingRoom, care_room.id)
    assert (persisted.notes, persisted.cleaning_frequency, persisted.version) == before


@pytest.mark.parametrize("export_kind", ["snapshot", "history"])
def test_both_exports_execute_frequency_filters_with_building_scope(snapshot_client, export_kind):
    client, context, _ = snapshot_client
    with Session(context.engine) as db:
        room = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        room.cleaning_frequency = "weekly"
        db.commit()
    path, scope = (
        ("room-status/exports", {"building_ids": [str(context.building_id)]})
        if export_kind == "snapshot" else ("exports", {"building_id": str(context.building_id)})
    )
    payload = {"purpose": "保洁频率范围核对", "format": "csv", **scope}
    matching = client.post(
        f"/api/v1/housing/{path}",
        json={**payload, "cleaning_frequencies": ["weekly", "on_demand"]},
    )
    assert matching.status_code == 200, matching.text
    records = list(csv.DictReader(StringIO(matching.content.decode("utf-8-sig"))))
    assert len(records) == 1
    if export_kind == "snapshot":
        assert records[0]["保洁频率"] == "每周保洁"
    nonmatching = client.post(
        f"/api/v1/housing/{path}", json={**payload, "cleaning_frequencies": ["daily"]},
    )
    if export_kind == "snapshot":
        assert nonmatching.status_code == 422
        assert nonmatching.json()["code"] == "housing_export_empty"
    else:
        assert nonmatching.status_code == 200
        assert list(csv.DictReader(StringIO(nonmatching.content.decode("utf-8-sig")))) == []


def test_frequency_migration_backfills_legacy_null_safely_and_refuses_lossy_downgrade(db):
    # Transaction-local shadow table exercises the real migration without moving
    # the shared database revision or altering any real housing table.
    connection = db.connection()
    connection.exec_driver_sql(
        "CREATE TEMPORARY TABLE housing_rooms "
        "(id integer PRIMARY KEY, daily_cleaning_enabled boolean) ON COMMIT DROP"
    )
    connection.exec_driver_sql(
        "INSERT INTO housing_rooms VALUES (1, true), (2, false), (3, NULL)"
    )
    migration = importlib.import_module("migrations.versions.0081_housing_cleaning_frequency")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        assert connection.exec_driver_sql(
            "SELECT cleaning_frequency FROM housing_rooms ORDER BY id"
        ).scalars().all() == ["daily", "unspecified", "unspecified"]
        connection.exec_driver_sql("INSERT INTO housing_rooms (id) VALUES (4)")
        assert connection.exec_driver_sql(
            "SELECT cleaning_frequency FROM housing_rooms WHERE id=4"
        ).scalar_one() == "unspecified"
        for invalid in ("monthly", None):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.exec_driver_sql(
                    "UPDATE housing_rooms SET cleaning_frequency=%s WHERE id=4", (invalid,)
                )
        connection.exec_driver_sql("UPDATE housing_rooms SET cleaning_frequency='weekly' WHERE id=2")
        with pytest.raises(RuntimeError, match="losing business data"):
            migration.downgrade()
        assert connection.exec_driver_sql(
            "SELECT cleaning_frequency FROM housing_rooms WHERE id=2"
        ).scalar_one() == "weekly"
        connection.exec_driver_sql(
            "UPDATE housing_rooms SET cleaning_frequency='unspecified' WHERE id=2"
        )
        migration.downgrade()
        assert connection.exec_driver_sql(
            "SELECT daily_cleaning_enabled FROM housing_rooms ORDER BY id"
        ).scalars().all() == [True, False, False, False]


def test_frequency_downgrade_preserves_legacy_writer_positive_cleaning_intent(db):
    connection = db.connection()
    connection.exec_driver_sql(
        "CREATE TEMPORARY TABLE housing_rooms "
        "(id integer PRIMARY KEY, daily_cleaning_enabled boolean) ON COMMIT DROP"
    )
    connection.exec_driver_sql(
        "INSERT INTO housing_rooms VALUES (1, false), (2, false), (3, false)"
    )
    migration = importlib.import_module("migrations.versions.0081_housing_cleaning_frequency")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        # Simulate an old writer after backfill: its positive intent must not
        # disappear merely because it cannot write the newer enum column.
        connection.exec_driver_sql("UPDATE housing_rooms SET daily_cleaning_enabled=true WHERE id=1")
        connection.exec_driver_sql("UPDATE housing_rooms SET cleaning_frequency='daily' WHERE id=2")
        migration.downgrade()
        assert connection.exec_driver_sql(
            "SELECT daily_cleaning_enabled FROM housing_rooms ORDER BY id"
        ).scalars().all() == [True, True, False]


def test_frequency_downgrade_guard_locks_out_concurrent_frequency_writes(snapshot_context):
    context = snapshot_context
    with Session(context.engine) as db:
        room = db.scalar(select(HousingRoom).where(HousingRoom.floor_id == context.floor_id))
        room_id = room.id
        room.cleaning_frequency = "weekly"
        db.commit()
    migration = importlib.import_module("migrations.versions.0081_housing_cleaning_frequency")
    with context.engine.connect() as guard:
        transaction = guard.begin()
        try:
            with (
                Operations.context(MigrationContext.configure(guard)),
                pytest.raises(RuntimeError, match="losing business data"),
            ):
                migration.downgrade()
            # A refused downgrade still holds its table lock until rollback,
            # proving the guard owns the same exclusion as the following DDL.
            with context.engine.connect() as writer:
                writer.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
                with pytest.raises(OperationalError) as exc:
                    writer.exec_driver_sql(
                        "UPDATE housing_rooms SET cleaning_frequency='on_demand' WHERE id=%s",
                        (room_id,),
                    )
                assert getattr(exc.value.orig, "sqlstate", None) == "55P03"
                writer.rollback()
        finally:
            transaction.rollback()
