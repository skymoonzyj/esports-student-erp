from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from app.evaluations.survey_models import CampSurveyStaffSnapshot
from app.evaluations.survey_snapshot_service import (
    CampSurveySnapshotService,
    ServiceSegment,
    _group_service_segments_by_staff,
    _merge_service_segments,
    _snapshot_row_sort_key,
)


def _at(day: int) -> datetime:
    return datetime(2026, 8, day, 10, tzinfo=UTC)


def _segment(*, start: int, end: int, source: str = "coach_assignment") -> ServiceSegment:
    return ServiceSegment(
        role_key="primary_coach",
        role_name="主教练",
        role_category="teaching",
        staff_person_id="00000000-0000-4000-8000-000000000001",  # type: ignore[arg-type]
        staff_name="测试教练",
        valid_from=_at(start),
        valid_until=_at(end),
        source=source,
        scope="class:00000000-0000-4000-8000-000000000010",
    )


def test_adjacent_equal_service_segments_merge_into_one_half_open_interval() -> None:
    merged = _merge_service_segments(
        (_segment(start=1, end=3), _segment(start=3, end=5))
    )

    assert len(merged) == 1
    assert merged[0].valid_from == _at(1)
    assert merged[0].valid_until == _at(5)


def test_non_adjacent_or_different_source_service_segments_remain_separate() -> None:
    merged = _merge_service_segments(
        (
            _segment(start=1, end=3),
            _segment(start=3, end=5, source="cohort_staff_assignment"),
            _segment(start=6, end=7),
        )
    )

    assert [(item.valid_from, item.valid_until) for item in merged] == [
        (_at(1), _at(3)),
        (_at(3), _at(5)),
        (_at(6), _at(7)),
    ]


def test_overlapping_equal_segments_merge_to_the_farthest_end() -> None:
    merged = _merge_service_segments(
        (_segment(start=1, end=4), _segment(start=2, end=6))
    )

    assert merged[0].valid_from == _at(1)
    assert merged[0].valid_until == _at(6)
    assert merged[0].valid_until - merged[0].valid_from == timedelta(days=5)


def test_same_staff_multiple_roles_is_one_snapshot_row_with_all_roles() -> None:
    coach = _segment(start=1, end=3)
    life = ServiceSegment(
        role_key="life_teacher",
        role_name="生活老师",
        role_category="life_service",
        staff_person_id=coach.staff_person_id,
        staff_name=coach.staff_name,
        valid_from=_at(2),
        valid_until=_at(4),
        source="cohort_staff_assignment",
        scope="project:00000000-0000-4000-8000-000000000010",
    )

    grouped = _group_service_segments_by_staff((coach, life))

    assert len(grouped) == 1
    row = grouped[0]
    assert row.staff_person_id == coach.staff_person_id
    assert [(item["role_key"], item["role_name"]) for item in row.roles] == [
        ("life_teacher", "生活老师"),
        ("primary_coach", "主教练"),
    ]
    assert {item["source"] for item in row.intervals} == {
        "coach_assignment",
        "cohort_staff_assignment",
    }


def test_same_category_roles_use_role_name_before_role_key() -> None:
    base = _segment(start=1, end=3)
    role_key_first = ServiceSegment(
        role_key="a_key",
        role_name="Z role",
        role_category="teaching",
        staff_person_id=base.staff_person_id,
        staff_name=base.staff_name,
        valid_from=_at(1),
        valid_until=_at(2),
        source="cohort_staff_assignment",
        scope="project:00000000-0000-4000-8000-000000000010",
    )
    role_name_first = ServiceSegment(
        role_key="z_key",
        role_name="A role",
        role_category="teaching",
        staff_person_id=base.staff_person_id,
        staff_name=base.staff_name,
        valid_from=_at(2),
        valid_until=_at(3),
        source="cohort_staff_assignment",
        scope="project:00000000-0000-4000-8000-000000000010",
    )

    grouped = _group_service_segments_by_staff(
        (role_key_first, role_name_first)
    )

    assert [item["role_key"] for item in grouped[0].roles] == [
        "z_key",
        "a_key",
    ]


def test_grouped_staff_use_the_shared_row_order_without_role_key() -> None:
    def segment(
        *,
        person_id: str,
        role_key: str,
        role_name: str,
        role_category: str,
        staff_name: str,
    ) -> ServiceSegment:
        return ServiceSegment(
            role_key=role_key,
            role_name=role_name,
            role_category=role_category,
            staff_person_id=UUID(person_id),
            staff_name=staff_name,
            valid_from=_at(1),
            valid_until=_at(2),
            source="cohort_staff_assignment",
            scope="project:00000000-0000-4000-8000-000000000010",
        )

    grouped = _group_service_segments_by_staff(
        (
            segment(
                person_id="00000000-0000-4000-8000-000000000004",
                role_key="a_key",
                role_name="Same role",
                role_category="teaching",
                staff_name="Zulu staff",
            ),
            segment(
                person_id="00000000-0000-4000-8000-000000000003",
                role_key="z_key",
                role_name="Same role",
                role_category="teaching",
                staff_name="Alpha staff",
            ),
            segment(
                person_id="00000000-0000-4000-8000-000000000002",
                role_key="middle_key",
                role_name="Earlier role",
                role_category="teaching",
                staff_name="Zulu staff",
            ),
            segment(
                person_id="00000000-0000-4000-8000-000000000001",
                role_key="last_key",
                role_name="Later role",
                role_category="operations",
                staff_name="Zulu staff",
            ),
        )
    )

    assert [group.staff_person_id for group in grouped] == [
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
        UUID("00000000-0000-4000-8000-000000000003"),
        UUID("00000000-0000-4000-8000-000000000004"),
    ]


def _snapshot_row(
    *,
    person_id: str,
    role_key: str,
    role_name: str,
    staff_name: str,
    role_category: str = "teaching",
) -> CampSurveyStaffSnapshot:
    return CampSurveyStaffSnapshot(
        snapshot_version_id=UUID("00000000-0000-4000-8000-000000000010"),
        staff_person_id=UUID(person_id),
        staff_name_snapshot=staff_name,
        role_key_snapshot=role_key,
        role_name_snapshot=role_name,
        role_category=role_category,
        roles_snapshot=[],
        service_intervals=[],
        include_in_summary=True,
    )


def test_snapshot_order_uses_role_name_then_staff_name_and_person_uuid() -> None:
    rows = (
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000003",
            role_key="a-key",
            role_name="B role",
            staff_name="A staff",
        ),
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000002",
            role_key="z-key",
            role_name="A role",
            staff_name="B staff",
        ),
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000001",
            role_key="z-key",
            role_name="A role",
            staff_name="B staff",
        ),
    )

    ordered = sorted(rows, key=_snapshot_row_sort_key)

    assert [row.staff_person_id for row in ordered] == [
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
        UUID("00000000-0000-4000-8000-000000000003"),
    ]


def test_roster_normalizes_database_case_and_role_key_order_with_python_key() -> None:
    rows = (
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000004",
            role_key="a-key",
            role_name="same role",
            staff_name="Zulu staff",
        ),
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000003",
            role_key="z-key",
            role_name="same role",
            staff_name="Alpha staff",
        ),
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000002",
            role_key="middle-key",
            role_name="Same role",
            staff_name="Zulu staff",
        ),
        _snapshot_row(
            person_id="00000000-0000-4000-8000-000000000001",
            role_key="last-key",
            role_name="later role",
            staff_name="Zulu staff",
            role_category="operations",
        ),
    )
    db = Mock()
    db.scalars.return_value = rows
    service = CampSurveySnapshotService(db)

    roster = service._roster(
        cycle=SimpleNamespace(),  # type: ignore[arg-type]
        snapshot=SimpleNamespace(  # type: ignore[arg-type]
            id=UUID("00000000-0000-4000-8000-000000000020")
        ),
    )

    assert [row.staff_person_id for row in roster.staff] == [
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
        UUID("00000000-0000-4000-8000-000000000003"),
        UUID("00000000-0000-4000-8000-000000000004"),
    ]
