from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingImportBatch,
    HousingRoom,
)
from app.housing.seed_inventory import (
    CORRECTION_SUMMARY,
    IMPORT_BATCH_ID,
    INITIAL_ROOMS,
    SOURCE_NAME,
    SOURCE_SHA256,
    building_values,
    floor_values,
    room_values,
)


@dataclass(frozen=True)
class HousingSeedResult:
    buildings: int
    floors: int
    rooms: int
    import_batches: int


def seed_initial_housing_inventory(db: Session) -> HousingSeedResult:
    for values in building_values():
        db.execute(
            postgresql_insert(HousingBuilding)
            .values(**values)
            .on_conflict_do_nothing()
        )
    for values in floor_values():
        db.execute(
            postgresql_insert(HousingFloor).values(**values).on_conflict_do_nothing()
        )
    for values in room_values():
        db.execute(
            postgresql_insert(HousingRoom).values(**values).on_conflict_do_nothing()
        )
    db.execute(
        postgresql_insert(HousingImportBatch)
        .values(
            id=IMPORT_BATCH_ID,
            source_name=SOURCE_NAME,
            source_sha256=SOURCE_SHA256,
            row_count=len(INITIAL_ROOMS),
            correction_summary=CORRECTION_SUMMARY,
            imported_by_user_id=None,
        )
        .on_conflict_do_nothing()
    )
    db.flush()
    return HousingSeedResult(
        buildings=db.scalar(select(func.count(HousingBuilding.id))) or 0,
        floors=db.scalar(select(func.count(HousingFloor.id))) or 0,
        rooms=db.scalar(select(func.count(HousingRoom.id))) or 0,
        import_batches=db.scalar(select(func.count(HousingImportBatch.id))) or 0,
    )
