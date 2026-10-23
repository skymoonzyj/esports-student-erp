from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid5

RoomType = Literal["single", "double", "triple", "custom", "functional"]

SOURCE_NAME = "新建 Microsoft Excel 工作表(2).xls"
SOURCE_SHA256 = "22afc9b882a52c5e286e64c13148c0198c4568eabcb33fc482929fac9576f88c"
HOUSING_SEED_NAMESPACE = UUID("52000000-0000-4000-8000-000000000000")
IMPORT_BATCH_ID = uuid5(HOUSING_SEED_NAMESPACE, f"import:{SOURCE_SHA256}")


def seed_uuid(kind: str, key: str) -> UUID:
    return uuid5(HOUSING_SEED_NAMESPACE, f"{kind}:{key}")


@dataclass(frozen=True)
class BuildingSeed:
    id: UUID
    code: str
    name: str
    display_order: int


@dataclass(frozen=True)
class FloorSeed:
    id: UUID
    building_id: UUID
    building_code: str
    number: int
    name: str
    display_order: int
    room_numbers: tuple[str, ...]
    type_codes: tuple[str, ...]
    areas: tuple[int, ...]


@dataclass(frozen=True)
class RoomSeed:
    id: UUID
    building_id: UUID
    floor_id: UUID
    building_code: str
    floor_number: int
    room_number: str
    normalized_key: str
    room_type: RoomType
    capacity: int
    area_square_meters: Decimal


INITIAL_BUILDINGS = tuple(
    BuildingSeed(seed_uuid("building", code), code, name, display_order)
    for code, name, display_order in (
        ("4", "4幢", 10),
        ("A", "A幢", 20),
        ("9", "9幢", 30),
        ("5", "5幢", 40),
    )
)
_BUILDING_IDS = {building.code: building.id for building in INITIAL_BUILDINGS}

_A_TYPES = "S S S S D S S S S S S S S S S S S S S S S S F"
_A_AREAS = "22 27 26 33 47 32 33 29 33 34 30 30 29 26 26 26 33 26 26 34 32 27 10"
_FIVE_TYPES = "D S S D S D D D D F S S S S S S"
_FIVE_AREAS = "35 21 26 31 31 32 30 37 39 12 31 30 29 25 24 22"

_FLOOR_SOURCE_ROWS = (
    ("4", 2, 201, 220, "D D D F D D D D D S D D D F S S F F S S", "35 35 36 14 33 36 36 36 37 30 34 43 28 18 28 27 16 21 28 29"),
    ("4", 3, 301, 320, "D D D F D D D D D S D D D F S S S F S S", "35 35 36 14 33 36 36 36 37 30 34 43 28 18 28 27 40 21 28 29"),
    ("4", 4, 401, 412, "S S S S S S S S S D D F", "24 24 24 24 24 24 31 33 30 24 24 22"),
    ("A", 2, 201, 223, _A_TYPES, _A_AREAS),
    ("A", 3, 301, 323, _A_TYPES, _A_AREAS),
    ("A", 4, 401, 410, "S S S S S S S S S S", "33 33 33 36 35 24 25 21 33 33"),
    ("9", 2, 201, 220, "S D D D D D S S S D D S F F S S S S S S", "25 34 34 30 31 26 24 24 22 37 45 43 4 9 20 21 23 23 25 27"),
    ("9", 3, 301, 320, "S D D D D D S S S D D S F F S S S S S S", "26 33 34 30 30 27 24 24 22 39 45 43 4 8 20 22 23 22 25 28"),
    ("9", 4, 401, 408, "S S S S S S F S", "31 25 23 23 20 20 13 44"),
    ("5", 2, 201, 216, _FIVE_TYPES, _FIVE_AREAS),
    ("5", 3, 301, 316, _FIVE_TYPES, _FIVE_AREAS),
)


def _floor_seed(
    building_code: str,
    number: int,
    first_room: int,
    last_room: int,
    type_codes: str,
    areas: str,
) -> FloorSeed:
    room_numbers = tuple(
        f"{building_code}-{room}" for room in range(first_room, last_room + 1)
    )
    parsed_type_codes = tuple(type_codes.split())
    parsed_areas = tuple(int(area) for area in areas.split())
    if not (len(room_numbers) == len(parsed_type_codes) == len(parsed_areas)):
        raise ValueError("Floor inventory rows must have matching room, type, and area counts")
    return FloorSeed(
        id=seed_uuid("floor", f"{building_code}:{number}"),
        building_id=_BUILDING_IDS[building_code],
        building_code=building_code,
        number=number,
        name={2: "二楼", 3: "三楼", 4: "四楼"}[number],
        display_order=number * 10,
        room_numbers=room_numbers,
        type_codes=parsed_type_codes,
        areas=parsed_areas,
    )


FLOOR_SEEDS = tuple(_floor_seed(*row) for row in _FLOOR_SOURCE_ROWS)
_ROOM_TYPES: dict[str, RoomType] = {"S": "single", "D": "double", "F": "functional"}
_CAPACITIES = {"single": 1, "double": 2, "triple": 3, "custom": 1, "functional": 0}


def _room_seeds(floor: FloorSeed) -> tuple[RoomSeed, ...]:
    return tuple(
        RoomSeed(
            id=seed_uuid("room", room_number),
            building_id=floor.building_id,
            floor_id=floor.id,
            building_code=floor.building_code,
            floor_number=floor.number,
            room_number=room_number,
            normalized_key=room_number.upper(),
            room_type=_ROOM_TYPES[type_code],
            capacity=_CAPACITIES[_ROOM_TYPES[type_code]],
            area_square_meters=Decimal(area),
        )
        for room_number, type_code, area in zip(
            floor.room_numbers, floor.type_codes, floor.areas, strict=True
        )
    )


INITIAL_ROOMS = tuple(room for floor in FLOOR_SEEDS for room in _room_seeds(floor))

CORRECTION_SUMMARY = {
    "room_number_corrections": {
        "4-4094": "4-409",
        "9-3059": "9-305",
        "spreadsheet_date_value": "5-311",
    },
    "removed_a_building_spaces": 10,
    "normalized_floor_labels": True,
}


def building_values() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "display_order": item.display_order,
            "is_active": True,
            "notes": None,
            "created_by_user_id": None,
            "updated_by_user_id": None,
        }
        for item in INITIAL_BUILDINGS
    ]


def floor_values() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "building_id": item.building_id,
            "number": item.number,
            "name": item.name,
            "display_order": item.display_order,
            "is_active": True,
            "notes": None,
            "created_by_user_id": None,
            "updated_by_user_id": None,
        }
        for item in FLOOR_SEEDS
    ]


def room_values() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "building_id": item.building_id,
            "floor_id": item.floor_id,
            "room_number": item.room_number,
            "normalized_key": item.normalized_key,
            "room_type": item.room_type,
            "capacity": item.capacity,
            "area_square_meters": item.area_square_meters,
            "verification_status": (
                "verified"
                if item.room_type == "functional"
                else "pending_verification"
            ),
            "service_status": "available",
            "notes": None,
            "version": 1,
            "created_by_user_id": None,
            "updated_by_user_id": None,
        }
        for item in INITIAL_ROOMS
    ]
