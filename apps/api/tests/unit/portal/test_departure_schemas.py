from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from app.portal.departure_schemas import (
    DepartureEvent,
    DeparturePerson,
    DepartureRemindersResponse,
    EnrollmentEndEvent,
    PlannedCheckoutEvent,
)

SOURCE_ID = "11111111-1111-4111-8111-111111111111"
PERSON_ID = "22222222-2222-4222-8222-222222222222"
ROOM_ID = "33333333-3333-4333-8333-333333333333"


def event_data(kind: str = "planned_checkout") -> dict[str, Any]:
    common = {"kind": kind, "source_id": SOURCE_ID, "date": "2026-10-24"}
    if kind == "planned_checkout":
        return {**common, "building_name": "1 号楼", "room_number": "206"}
    return {**common, "project_name": "项目", "cohort_name": "营期", "enrollment_status": "active"}


def response_data(**changes: Any) -> dict[str, Any]:
    return {
        "available": True,
        "observed_at": "2026-10-24T09:00:00+08:00",
        "business_date": "2026-10-24",
        "timezone": "Asia/Shanghai",
        "next_refresh_at": "2026-10-25T00:00:00+08:00",
        "window": "three_days",
        "today_count": 1,
        "three_day_count": 1,
        "page": 1,
        "page_size": 20,
        "total": 1,
        "items": [
            {
                "person_id": PERSON_ID,
                "full_name": "学员",
                "nickname": None,
                "events": [event_data(), event_data("enrollment_end")],
            }
        ],
        **changes,
    }


def test_response_serializes_two_source_facts_without_internal_fields() -> None:
    result = DepartureRemindersResponse.model_validate(response_data())
    payload = result.model_dump(mode="json")
    assert payload["business_date"] == "2026-10-24"
    assert payload["items"][0]["person_id"] == PERSON_ID
    events = result.items[0].events
    assert isinstance(events[0], PlannedCheckoutEvent)
    assert isinstance(events[1], EnrollmentEndEvent)
    assert payload["items"][0]["events"][0] == {
        "kind": "planned_checkout",
        "source_id": SOURCE_ID,
        "date": "2026-10-24",
        "detail_path": None,
        "building_name": "1 号楼",
        "room_number": "206",
    }
    assert payload["items"][0]["events"][1]["detail_path"] is None


@pytest.mark.parametrize("status", ["active", "completed"])
def test_teaching_status_accepts_current_and_completed_enrollments(status: str) -> None:
    event = TypeAdapter(DepartureEvent).validate_python(
        {**event_data("enrollment_end"), "enrollment_status": status}
    )
    assert isinstance(event, EnrollmentEndEvent)
    assert event.enrollment_status == status


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "unknown"},
        {"source_id": "not-a-uuid"},
        {"date": "2026-02-30"},
        {"project_name": "unauthorized teaching field"},
        {"phone": "private"},
    ],
)
def test_housing_event_rejects_unknown_kind_and_cross_source_or_private_fields(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DepartureEvent).validate_python({**event_data(), **changes})


@pytest.mark.parametrize(
    "changes", [{"building_name": "private"}, {"enrollment_status": "cancelled"}]
)
def test_teaching_event_rejects_housing_fields_and_cancelled_status(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DepartureEvent).validate_python({**event_data("enrollment_end"), **changes})


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("planned_checkout", f"/housing/rooms/{ROOM_ID}"),
        ("enrollment_end", f"/teaching/enrollments/{SOURCE_ID}"),
    ],
)
def test_source_specific_uuid_detail_paths_are_allowed(kind: str, path: str) -> None:
    event = TypeAdapter(DepartureEvent).validate_python({**event_data(kind), "detail_path": path})
    assert event.detail_path == path


@pytest.mark.parametrize("kind", ["planned_checkout", "enrollment_end"])
@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/path",
        "//evil.example/path",
        "/people/private",
        "/housing/rooms/not-uuid",
        f"/housing/rooms/{ROOM_ID}?edit=1",
        f"/housing/rooms/{ROOM_ID}/../private",
        f"/teaching/enrollments/{SOURCE_ID}#private",
        "javascript:alert(1)",
        "",
    ],
)
def test_detail_paths_cannot_escape_approved_detail_routes(kind: str, path: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DepartureEvent).validate_python({**event_data(kind), "detail_path": path})


@pytest.mark.parametrize(
    ("kind", "path"),
    [
        ("planned_checkout", f"/teaching/enrollments/{SOURCE_ID}"),
        ("enrollment_end", f"/housing/rooms/{ROOM_ID}"),
    ],
)
def test_detail_paths_cannot_link_a_different_source_kind(kind: str, path: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(DepartureEvent).validate_python({**event_data(kind), "detail_path": path})


@pytest.mark.parametrize("field", ["today_count", "three_day_count", "total"])
def test_response_rejects_negative_person_counts(field: str) -> None:
    with pytest.raises(ValidationError):
        DepartureRemindersResponse.model_validate(response_data(**{field: -1}))


@pytest.mark.parametrize("page_size", [0, 51])
def test_response_rejects_page_sizes_outside_one_to_fifty(page_size: int) -> None:
    with pytest.raises(ValidationError):
        DepartureRemindersResponse.model_validate(response_data(page_size=page_size))


@pytest.mark.parametrize("page_size", [1, 50])
def test_response_accepts_page_size_boundaries(page_size: int) -> None:
    assert (
        DepartureRemindersResponse.model_validate(response_data(page_size=page_size)).page_size
        == page_size
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"page": 0},
        {"window": "week"},
        {"timezone": "UTC"},
        {"observed_at": "2026-10-24T09:00:00"},
        {"next_refresh_at": "2026-10-25T00:00:00"},
        {"private_field": "secret"},
    ],
)
def test_response_rejects_invalid_metadata(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DepartureRemindersResponse.model_validate(response_data(**changes))


def test_person_rejects_private_fields() -> None:
    with pytest.raises(ValidationError):
        DeparturePerson.model_validate(
            {
                "person_id": PERSON_ID,
                "full_name": "学员",
                "nickname": None,
                "events": [],
                "phone": "private",
            }
        )


def test_unavailable_response_keeps_business_metadata_but_has_no_people() -> None:
    result = DepartureRemindersResponse.model_validate(
        response_data(available=False, today_count=0, three_day_count=0, total=0, items=[])
    )
    assert result.business_date.isoformat() == "2026-10-24"
    assert result.items == []
    assert result.page == 1
    assert result.page_size == 20


@pytest.mark.parametrize("field", ["today_count", "three_day_count", "total", "items"])
def test_unavailable_response_rejects_populated_counts_or_items(field: str) -> None:
    payload = response_data(available=False, today_count=0, three_day_count=0, total=0, items=[])
    payload[field] = response_data()[field]
    with pytest.raises(ValidationError):
        DepartureRemindersResponse.model_validate(payload)
