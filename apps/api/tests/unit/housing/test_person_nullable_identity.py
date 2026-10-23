from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import true
from sqlalchemy.orm import Session

from app.housing.models import HousingRoom
from app.housing.occupancy_router import _stay_actions
from app.housing.person_router import _resident, _rooms
from app.housing.schemas import StayAllowedActions, StaySummary
from app.identity.models import Person
from app.security.encryption import EnvelopeCipher, LookupDigest


class RoomAccess:
    def room_permissions_criteria(self, *permissions):
        return true()

    def can_manage_stays(self, room):
        return True

    def can_manage_transfers(self, room):
        return True

    def can_manage_checkouts(self, room):
        return True


def summary(status, sex):
    now = datetime(2026, 10, 21, tzinfo=UTC)
    return StaySummary(
        id=uuid4(),
        person_id=uuid4(),
        room_id=uuid4(),
        stay_purpose="permanent",
        duration_mode="long_term",
        cohort_id=None,
        visit_reason=None,
        planned_check_in_at=now,
        planned_check_out_at=None,
        actual_check_in_at=None if status == "planned" else now,
        actual_check_out_at=None,
        status=status,
        room_name_snapshot="本人房间",
        floor_name_snapshot="二楼",
        building_name_snapshot="4幢",
        notes="保留备注",
        cancellation_reason=None,
        checkout_requested_actual_at=now if status == "checkout_pending" else None,
        checkout_requested_at=now if status == "checkout_pending" else None,
        person_name="待补人员",
        person_type="other",
        sex=sex,
        version=1,
        allowed_actions=StayAllowedActions(
            edit_reservation=False,
            confirm_check_in=False,
            cancel_reservation=False,
            transfer=False,
            request_checkout=False,
            confirm_checkout=False,
        ),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (
            "planned",
            {
                "void_history": False,
                "edit_planned_departure": False,
                "edit_reservation": True,
                "confirm_check_in": False,
                "cancel_reservation": True,
                "transfer": False,
                "request_checkout": False,
                "confirm_checkout": False,
            },
        ),
        (
            "checked_in",
            {
                "void_history": False,
                "edit_planned_departure": True,
                "edit_reservation": False,
                "confirm_check_in": False,
                "cancel_reservation": False,
                "transfer": False,
                "request_checkout": True,
                "confirm_checkout": False,
            },
        ),
        (
            "checkout_pending",
            {
                "void_history": False,
                "edit_planned_departure": False,
                "edit_reservation": False,
                "confirm_check_in": False,
                "cancel_reservation": False,
                "transfer": False,
                "request_checkout": False,
                "confirm_checkout": True,
            },
        ),
    ],
)
def test_unknown_sex_stay_only_offers_safe_existing_record_actions(status, expected):
    stay = summary(status, None)
    actions = _stay_actions(RoomAccess(), HousingRoom(id=stay.room_id), stay, stay.created_at)
    assert actions.model_dump() == expected


@pytest.mark.parametrize("sex", ["male", "female"])
@pytest.mark.parametrize(
    ("status", "action"), [("planned", "confirm_check_in"), ("checked_in", "transfer")]
)
def test_known_sex_stay_retains_existing_check_in_and_transfer_actions(sex, status, action):
    stay = summary(status, sex)
    actions = _stay_actions(RoomAccess(), HousingRoom(id=stay.room_id), stay, stay.created_at)
    assert actions.model_dump()[action] is True


def test_unknown_sex_person_has_no_check_in_room_options():
    person = Person(sex=None, document_type="passport", intake_mode="pending")
    # An unbound session proves the incomplete subject is rejected before a
    # room lookup; no database or authorization source can turn it into an option.
    with Session() as db:
        assert _rooms(db, RoomAccess(), person) == []


def test_unknown_sex_person_is_not_a_selectable_resident():
    person = Person(sex=None, document_type="passport", intake_mode="pending")
    with Session() as db:
        assert (
            _resident(
                db,
                person,
                datetime(2026, 10, 21, tzinfo=UTC),
                EnvelopeCipher(bytes(range(32))),
                LookupDigest(bytes(range(32))),
            )
            is None
        )
