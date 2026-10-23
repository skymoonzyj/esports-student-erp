from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

import app.housing.errors as housing_errors
import app.housing.schemas as housing_schemas
from app.housing.schemas import (
    BuildingCreate,
    BuildingUpdate,
    FloorCreate,
    FloorUpdate,
    RoomBatchVerifyRequest,
    RoomCreate,
    RoomUpdate,
    RoomVerifyRequest,
    RoomVersionInput,
)

FLOOR_ID = UUID("52000000-0000-4000-8000-000000000001")
PERSON_ID = UUID("51000000-0000-4000-8000-000000000001")
COHORT_ID = UUID("53000000-0000-4000-8000-000000000001")
ROOM_ID = UUID("52000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 4, 9, tzinfo=UTC)


def _schema(name: str) -> type[BaseModel]:
    schema = getattr(housing_schemas, name, None)
    assert schema is not None, f"housing schema {name} is missing"
    return schema


def _error(name: str) -> type[Exception]:
    error = getattr(housing_errors, name, None)
    assert error is not None, f"housing error {name} is missing"
    return error


@pytest.mark.parametrize(
    ("room_type", "capacity"),
    [("single", 1), ("double", 2), ("triple", 3), ("custom", 4), ("functional", 0)],
)
def test_room_create_accepts_exact_type_capacity_pairs(
    room_type: str,
    capacity: int,
) -> None:
    value = RoomCreate(
        floor_id=FLOOR_ID,
        room_number=" T-201 ",
        room_type=room_type,
        capacity=capacity,
        area_square_meters=Decimal("28.50"),
    )

    assert value.capacity == capacity
    assert value.room_number == "T-201"


@pytest.mark.parametrize(
    ("room_type", "capacity"),
    [("single", 2), ("double", 1), ("triple", 4), ("custom", 0), ("functional", 1)],
)
def test_room_create_rejects_invalid_type_capacity_pairs(
    room_type: str,
    capacity: int,
) -> None:
    with pytest.raises(ValidationError):
        RoomCreate(
            floor_id=FLOOR_ID,
            room_number="T-201",
            room_type=room_type,
            capacity=capacity,
        )


@pytest.mark.parametrize(
    "value",
    [
        BuildingCreate(code=" B ", name=" B 幢 ", notes="   "),
        BuildingUpdate(name="新楼栋", notes="   "),
        FloorCreate(building_id=FLOOR_ID, number=2, name=" 二楼 ", notes="   "),
        FloorUpdate(name="新二楼", notes="   "),
        RoomCreate(
            floor_id=FLOOR_ID,
            room_number="T-201",
            room_type="single",
            capacity=1,
            notes="   ",
        ),
        RoomUpdate(version=1, notes="   "),
    ],
)
def test_blank_notes_are_normalized_to_none(value: BaseModel) -> None:
    assert value.model_dump()["notes"] is None


def test_room_area_must_be_positive_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RoomCreate(
            floor_id=FLOOR_ID,
            room_number="T-201",
            room_type="single",
            capacity=1,
            area_square_meters=Decimal(0),
        )

    with pytest.raises(ValidationError):
        BuildingCreate.model_validate(
            {"code": "T", "name": "测试楼", "unexpected": "forbidden"}
        )


def test_update_models_require_a_mutable_field_and_positive_version() -> None:
    with pytest.raises(ValidationError):
        BuildingUpdate()
    with pytest.raises(ValidationError):
        FloorUpdate()
    with pytest.raises(ValidationError):
        RoomUpdate(version=1)
    with pytest.raises(ValidationError):
        RoomUpdate(version=0, notes="核对")
    with pytest.raises(ValidationError):
        RoomVerifyRequest(version=0)

    assert RoomUpdate(version=1, notes="核对").version == 1
    assert RoomVerifyRequest(version=1).version == 1


def test_batch_verify_requires_unique_bounded_room_versions() -> None:
    first = RoomVersionInput(
        room_id=UUID("52000000-0000-4000-8000-000000000011"),
        version=1,
    )
    second = RoomVersionInput(
        room_id=UUID("52000000-0000-4000-8000-000000000012"),
        version=2,
    )
    value = RoomBatchVerifyRequest(rooms=[first, second])
    assert value.rooms == [first, second]

    with pytest.raises(ValidationError):
        RoomBatchVerifyRequest(rooms=[])
    with pytest.raises(ValidationError):
        RoomBatchVerifyRequest(rooms=[first, first])
    with pytest.raises(ValidationError):
        RoomBatchVerifyRequest(rooms=[first] * 501)


def test_stay_create_accepts_linked_purpose_and_duration_variants() -> None:
    stay_create = _schema("StayCreate")

    permanent = stay_create(
        person_id=PERSON_ID,
        stay_purpose="permanent",
        duration_mode="long_term",
        planned_check_in_at=NOW,
        visit_reason="   ",
        notes="   ",
    )
    assert permanent.planned_check_out_at is None
    assert permanent.visit_reason is None
    assert permanent.notes is None

    camp = stay_create(
        person_id=PERSON_ID,
        stay_purpose="camp",
        duration_mode="fixed_term",
        cohort_id=COHORT_ID,
        planned_check_in_at=NOW,
        planned_check_out_at=NOW + timedelta(days=14),
    )
    assert camp.cohort_id == COHORT_ID

    temporary = stay_create(
        person_id=PERSON_ID,
        stay_purpose="temporary",
        duration_mode="fixed_term",
        visit_reason="  临时探访  ",
        planned_check_in_at=NOW,
        planned_check_out_at=NOW + timedelta(hours=2),
    )
    assert temporary.visit_reason == "临时探访"


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration_mode": "fixed_term"},
        {
            "duration_mode": "fixed_term",
            "planned_check_out_at": NOW,
        },
        {
            "duration_mode": "long_term",
            "planned_check_out_at": NOW + timedelta(days=1),
        },
        {"stay_purpose": "camp"},
        {"stay_purpose": "temporary"},
        {
            "stay_purpose": "temporary",
            "duration_mode": "fixed_term",
            "planned_check_out_at": NOW + timedelta(days=1),
        },
        {
            "stay_purpose": "temporary",
            "duration_mode": "fixed_term",
            "planned_check_out_at": NOW + timedelta(days=1),
            "visit_reason": "   ",
        },
    ],
)
def test_stay_create_rejects_invalid_purpose_duration_combinations(
    overrides: dict[str, object],
) -> None:
    stay_create = _schema("StayCreate")
    payload: dict[str, object] = {
        "person_id": PERSON_ID,
        "stay_purpose": "permanent",
        "duration_mode": "long_term",
        "planned_check_in_at": NOW,
    }
    payload.update(overrides)

    with pytest.raises(ValidationError):
        stay_create.model_validate(payload)


def test_stay_update_requires_a_forward_planned_range() -> None:
    stay_update = _schema("StayUpdate")

    value = stay_update(
        version=1,
        planned_check_in_at=NOW,
        planned_check_out_at=NOW + timedelta(days=1),
        notes="   ",
    )
    assert value.notes is None

    with pytest.raises(ValidationError):
        stay_update(
            version=1,
            planned_check_in_at=NOW,
            planned_check_out_at=NOW,
        )


@pytest.mark.parametrize(
    ("schema_name", "payload", "datetime_field"),
    [
        (
            "StayCreate",
            {
                "person_id": PERSON_ID,
                "stay_purpose": "permanent",
                "duration_mode": "long_term",
            },
            "planned_check_in_at",
        ),
        ("StayUpdate", {"version": 1}, "planned_check_in_at"),
        ("ConfirmCheckInRequest", {"version": 1}, "actual_check_in_at"),
        (
            "TransferStayRequest",
            {"version": 1, "target_room_id": ROOM_ID, "reason": "调房"},
            "transferred_at",
        ),
        ("CheckoutRequest", {"version": 1}, "actual_check_out_at"),
    ],
)
def test_lifecycle_inputs_require_timezone_aware_datetimes(
    schema_name: str,
    payload: dict[str, object],
    datetime_field: str,
) -> None:
    schema = _schema(schema_name)

    assert schema.model_validate({**payload, datetime_field: NOW})
    with pytest.raises(ValidationError):
        schema.model_validate({**payload, datetime_field: NOW.replace(tzinfo=None)})


@pytest.mark.parametrize(
    ("schema_name", "payload", "version_field"),
    [
        ("StayUpdate", {"planned_check_in_at": NOW}, "version"),
        ("ConfirmCheckInRequest", {"actual_check_in_at": NOW}, "version"),
        ("CancelReservationRequest", {"reason": "取消"}, "version"),
        (
            "TransferStayRequest",
            {"target_room_id": ROOM_ID, "transferred_at": NOW, "reason": "调房"},
            "version",
        ),
        ("CheckoutRequest", {"actual_check_out_at": NOW}, "version"),
        ("CheckoutConfirmation", {}, "version"),
        ("RoomIssueCreate", {"description": "灯具故障"}, "room_version"),
        ("RoomIssueResolve", {"resolution_note": "已更换灯具"}, "room_version"),
    ],
)
def test_lifecycle_versions_are_positive_strict_integers(
    schema_name: str,
    payload: dict[str, object],
    version_field: str,
) -> None:
    schema = _schema(schema_name)
    assert schema.model_validate({**payload, version_field: 1})

    for invalid in (0, True, "1"):
        with pytest.raises(ValidationError):
            schema.model_validate({**payload, version_field: invalid})


@pytest.mark.parametrize(
    ("schema_name", "field", "value"),
    [
        ("CancelReservationRequest", "reason", "  取消预约  "),
        ("TransferStayRequest", "reason", "  房间调整  "),
        ("RoomIssueCreate", "description", "  空调故障  "),
        ("RoomIssueResolve", "resolution_note", "  已修复  "),
    ],
)
def test_required_lifecycle_text_is_trimmed_and_non_blank(
    schema_name: str,
    field: str,
    value: str,
) -> None:
    schema = _schema(schema_name)
    base_payloads: dict[str, dict[str, object]] = {
        "CancelReservationRequest": {"version": 1},
        "TransferStayRequest": {
            "version": 1,
            "target_room_id": ROOM_ID,
            "transferred_at": NOW,
        },
        "RoomIssueCreate": {"room_version": 1},
        "RoomIssueResolve": {"room_version": 1},
    }
    value_model = schema.model_validate({**base_payloads[schema_name], field: value})
    assert getattr(value_model, field) == value.strip()

    with pytest.raises(ValidationError):
        schema.model_validate({**base_payloads[schema_name], field: "   "})


def test_lifecycle_inputs_forbid_unknown_fields_and_bound_free_text() -> None:
    stay_create = _schema("StayCreate")
    with pytest.raises(ValidationError):
        stay_create(
            person_id=PERSON_ID,
            stay_purpose="permanent",
            duration_mode="long_term",
            planned_check_in_at=NOW,
            unexpected="forbidden",
        )
    with pytest.raises(ValidationError):
        stay_create(
            person_id=PERSON_ID,
            stay_purpose="temporary",
            duration_mode="fixed_term",
            visit_reason="x" * 501,
            planned_check_in_at=NOW,
            planned_check_out_at=NOW + timedelta(days=1),
        )

    room_issue_create = _schema("RoomIssueCreate")
    with pytest.raises(ValidationError):
        room_issue_create(room_version=1, description="x" * 1001)


def test_resident_and_portal_contracts_expose_only_intended_identity_fields() -> None:
    resident_option = _schema("ResidentOption")
    portal_stay = _schema("HousingPortalStay")
    portal_subject = _schema("HousingPortalSubject")
    portal_response = _schema("HousingPortalResponse")
    stay_summary = _schema("StaySummary")

    assert set(resident_option.model_fields) == {
        "person_id",
        "full_name",
        "person_type",
        "sex",
        "masked_phone",
        "account_ready",
        "housing_ready",
        "account_status",
        "profile_ready",
        "guardian_ready",
    }
    assert set(portal_stay.model_fields) == {
        "building_name",
        "floor_name",
        "room_name",
        "stay_purpose",
        "planned_check_in_at",
        "planned_check_out_at",
        "actual_check_in_at",
        "actual_check_out_at",
        "status",
        "roommates",
    }
    assert set(portal_subject.model_fields) == {
        "subject_person_id",
        "subject_name",
        "current_stay",
        "planned_stays",
    }
    assert set(portal_response.model_fields) == {"as_of", "subjects"}

    with pytest.raises(ValidationError):
        portal_stay(
            building_name="4幢",
            floor_name="二楼",
            room_name="4-201",
            stay_purpose="permanent",
            planned_check_in_at=NOW,
            planned_check_out_at=None,
            actual_check_in_at=NOW,
            actual_check_out_at=None,
            status="checked_in",
            roommates=[],
            phone="13800138000",
        )

    forbidden = {"phone", "identity_number", "email"}
    assert forbidden.isdisjoint(stay_summary.model_fields)
    assert forbidden.isdisjoint(portal_stay.model_fields)


def test_room_card_supports_occupancy_and_keeps_new_actions_compatible() -> None:
    allowed_actions = _schema("HousingAllowedActions")(
        edit=True,
        delete=False,
        verify=False,
    )
    assert allowed_actions.model_dump() == {
        "edit": True,
        "delete": False,
        "verify": False,
        "create_stay": False,
        "report_issue": False,
        "view_detail": False,
    }

    room_card = _schema("HousingRoomCard")(
        id=ROOM_ID,
        building_id=UUID("52000000-0000-4000-8000-000000000010"),
        floor_id=FLOOR_ID,
        room_number="201",
        room_type="double",
        capacity=2,
        area_square_meters=Decimal("20.00"),
        verification_status="verified",
        service_status="available",
        notes=None,
        version=1,
        created_at=NOW,
        updated_at=NOW,
        building_code="A",
        building_name="A楼",
        building_is_active=True,
        floor_number=2,
        floor_name="二层",
        floor_is_active=True,
        current_occupants=1,
        remaining_capacity=1,
        current_gender="female",
        has_future_reservation=True,
        occupancy_status="occupied",
        is_assignable=True,
        status_text="已入住",
        allowed_actions=allowed_actions,
    )
    assert room_card.current_occupants == 1
    assert room_card.occupants == []
    assert room_card.future_reservation_count == 0
    assert room_card.open_issue_summary is None
    assert room_card.pending_transfer_count == 0

    payload = room_card.model_dump()
    payload["current_occupants"] = -1
    with pytest.raises(ValidationError):
        _schema("HousingRoomCard").model_validate(payload)


@pytest.mark.parametrize(
    ("class_name", "code"),
    [
        ("HousingAccountRequired", "housing_account_required"),
        ("HousingProfileIncomplete", "housing_profile_incomplete"),
        ("MinorGuardianRequired", "minor_guardian_required"),
        ("HousingRoomNotVerified", "housing_room_not_verified"),
        ("HousingRoomUnavailable", "housing_room_unavailable"),
        ("HousingCapacityConflict", "housing_capacity_conflict"),
        ("HousingGenderConflict", "housing_gender_conflict"),
        ("HousingPersonTimeConflict", "housing_person_time_conflict"),
        ("HousingStaleVersion", "housing_stale_version"),
        (
            "HousingCheckoutConfirmationRequired",
            "housing_checkout_confirmation_required",
        ),
        ("HousingRoomHasActiveStays", "housing_room_has_active_stays"),
        ("HousingInvalidStayTransition", "housing_invalid_stay_transition"),
        ("HousingOpenIssueExists", "housing_open_issue_exists"),
        ("HousingIssueNotOpen", "housing_issue_not_open"),
    ],
)
def test_housing_lifecycle_errors_are_typed_and_stable(
    class_name: str,
    code: str,
) -> None:
    error = _error(class_name)
    assert issubclass(error, housing_errors.HousingServiceError)
    assert error.code == code
