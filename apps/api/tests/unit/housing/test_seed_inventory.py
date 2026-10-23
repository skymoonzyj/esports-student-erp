from collections import Counter

from app.housing.seed_inventory import (
    FLOOR_SEEDS,
    INITIAL_BUILDINGS,
    INITIAL_ROOMS,
    SOURCE_SHA256,
)


def test_initial_inventory_matches_confirmed_workbook_totals() -> None:
    assert SOURCE_SHA256 == "22afc9b882a52c5e286e64c13148c0198c4568eabcb33fc482929fac9576f88c"
    assert len(INITIAL_BUILDINGS) == 4
    assert len(FLOOR_SEEDS) == 11
    assert len(INITIAL_ROOMS) == 188

    by_type = Counter(room.room_type for room in INITIAL_ROOMS)
    assert by_type == {"single": 119, "double": 52, "functional": 17}
    assert sum(room.capacity for room in INITIAL_ROOMS) == 223
    assert sum(room.room_type != "functional" for room in INITIAL_ROOMS) == 171


def test_initial_inventory_is_normalized_and_unique() -> None:
    room_numbers = [room.room_number for room in INITIAL_ROOMS]
    assert len(room_numbers) == len(set(room_numbers))
    assert {room.room_number for room in INITIAL_ROOMS} >= {
        "4-409",
        "9-305",
        "5-311",
        "A-205",
        "A-209",
        "A-323",
        "A-409",
    }
    assert {"4-4094", "9-3059", "5-31", "A- 205"}.isdisjoint(room_numbers)
    assert all(" " not in room.room_number for room in INITIAL_ROOMS)
    assert all(room.area_square_meters > 0 for room in INITIAL_ROOMS)
    assert all(
        room.capacity
        == {"single": 1, "double": 2, "functional": 0}[room.room_type]
        for room in INITIAL_ROOMS
    )


def test_initial_inventory_uses_stable_ids() -> None:
    building_ids = {item.id for item in INITIAL_BUILDINGS}
    floor_ids = {item.id for item in FLOOR_SEEDS}
    room_ids = {item.id for item in INITIAL_ROOMS}
    assert len(building_ids) == 4
    assert len(floor_ids) == 11
    assert len(room_ids) == 188
