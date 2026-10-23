from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingImportBatch,
    HousingRoom,
)
from app.housing.seed import seed_initial_housing_inventory
from app.housing.seed_inventory import INITIAL_ROOMS, SOURCE_SHA256


def test_initial_housing_seed_is_idempotent_and_preserves_admin_edits(
    db: Session,
) -> None:
    first = seed_initial_housing_inventory(db)
    room_seed = next(item for item in INITIAL_ROOMS if item.room_number == "4-409")
    room = db.get(HousingRoom, room_seed.id)
    assert room is not None
    room.notes = "管理员已核对原表"
    room.version = 7
    db.flush()

    second = seed_initial_housing_inventory(db)
    db.expire_all()
    preserved = db.get(HousingRoom, room_seed.id)

    assert first == second
    assert (second.buildings, second.floors, second.rooms, second.import_batches) == (
        4,
        11,
        188,
        1,
    )
    assert preserved is not None
    assert preserved.notes == "管理员已核对原表"
    assert preserved.version == 7


def test_initial_housing_seed_reconstructs_an_empty_catalog_without_source_volume(
    db: Session,
    monkeypatch,
) -> None:
    def reject_source_read(*args, **kwargs):
        del args, kwargs
        raise AssertionError("runtime housing seed must not read source files")

    monkeypatch.setattr(Path, "read_bytes", reject_source_read)
    monkeypatch.setattr(Path, "read_text", reject_source_read)
    db.execute(delete(HousingImportBatch))
    db.execute(delete(HousingRoom))
    db.execute(delete(HousingFloor))
    db.execute(delete(HousingBuilding))
    db.flush()

    result = seed_initial_housing_inventory(db)

    assert (result.buildings, result.floors, result.rooms, result.import_batches) == (
        4,
        11,
        188,
        1,
    )
    assert db.scalar(select(func.count(HousingRoom.id))) == 188
    assert db.scalar(select(HousingImportBatch.source_sha256)) == SOURCE_SHA256
