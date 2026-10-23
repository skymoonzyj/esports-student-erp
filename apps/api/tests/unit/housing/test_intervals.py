from __future__ import annotations

from datetime import UTC, datetime

from app.housing.occupancy_service import (
    intervals_overlap,
    maximum_interval_occupancy,
)


def _at(hour: int) -> datetime:
    return datetime(2026, 8, 4, hour, tzinfo=UTC)


def test_touching_half_open_intervals_do_not_overlap() -> None:
    assert not intervals_overlap(_at(8), _at(10), _at(10), _at(12))
    assert not intervals_overlap(_at(10), _at(12), _at(8), _at(10))


def test_target_touching_two_nonoverlapping_halves_fits_capacity_two() -> None:
    intervals = [
        (_at(8), _at(10)),
        (_at(10), _at(12)),
        (_at(9), _at(11)),
    ]

    assert maximum_interval_occupancy(intervals) == 2


def test_capacity_rejects_only_a_simultaneous_peak_above_capacity() -> None:
    intervals = [
        (_at(8), _at(11)),
        (_at(9), _at(12)),
        (_at(10), _at(13)),
    ]

    assert maximum_interval_occupancy(intervals) == 3


def test_end_events_sort_before_start_events_at_the_same_instant() -> None:
    intervals = [
        (_at(8), _at(10)),
        (_at(9), _at(10)),
        (_at(10), _at(12)),
        (_at(10), _at(13)),
    ]

    assert maximum_interval_occupancy(intervals) == 2
