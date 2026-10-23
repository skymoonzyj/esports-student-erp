from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.identity.models import Person
from tests.invited_housing_helpers import NOW, account, person, stay


def summaries(db, ids):
    from app.housing.invited_projection import InvitedHousingProjectionService

    return InvitedHousingProjectionService(db).summaries_for_people(
        authorized_person_ids=ids, as_of=NOW
    )


@pytest.mark.parametrize(
    "stored,start,want",
    [
        (None, 0, "unarranged"),
        ("cancelled", 1, "unarranged"),
        ("planned", 2, "reserved"),
        ("planned", -1, "check_in_pending"),
        ("checked_in", -3, "checked_in"),
        ("checkout_pending", -3, "checkout_pending"),
        ("checked_out", -3, "checked_out"),
    ],
)
def test_six_states_and_sql_filter_share_contract(db: Session, stored, start, want):
    from app.housing.invited_projection import invited_housing_status_expression

    actor, student = account(db), person(db, student=True)
    if stored:
        stay(
            db,
            actor,
            student,
            status=stored,
            start=NOW + timedelta(days=start),
            actual_end=NOW - timedelta(days=1) if stored == "checked_out" else None,
        )
    result = summaries(db, [student.id])[student.id]
    assert result.status == want
    assert (result.stay is None) == (want == "unarranged")
    assert (
        db.scalar(
            select(invited_housing_status_expression(Person.id, as_of=NOW)).where(
                Person.id == student.id
            )
        )
        == want
    )


def test_current_and_next_stay_are_independent_and_long_term_dates_preserved(db: Session):
    actor, student = account(db), person(db, student=True)
    stay(
        db,
        actor,
        student,
        status="checked_in",
        start=NOW - timedelta(days=20),
        end=NOW - timedelta(days=10),
    )
    next_stay = stay(db, actor, student, start=NOW + timedelta(days=5))
    result = summaries(db, [student.id])[student.id]
    assert result.status == "checked_in"
    assert result.stay.actual_check_out_at is None
    assert result.next_planned_stay.planned_check_in_at == next_stay.planned_check_in_at
    assert result.next_planned_stay.duration_mode == "long_term"
    assert result.next_planned_stay.planned_check_out_at is None


def test_pending_arrival_uses_china_date_not_utc_date_or_clock(db: Session):
    actor, student = account(db), person(db, student=True)
    stay(db, actor, student, start=NOW + timedelta(hours=7), end=NOW + timedelta(days=1))
    assert summaries(db, [student.id])[student.id].status == "check_in_pending"


def test_latest_real_checkout_not_updated_at_or_transfer_end(db: Session):
    from app.housing.models import HousingTransfer

    actor, student = account(db), person(db, student=True)
    old = stay(
        db,
        actor,
        student,
        status="checked_out",
        start=NOW - timedelta(days=30),
        actual_end=NOW - timedelta(days=20),
    )
    recent = stay(
        db,
        actor,
        student,
        status="checked_out",
        start=NOW - timedelta(days=10),
        actual_end=NOW - timedelta(days=5),
    )
    old.updated_at = NOW + timedelta(days=30)
    db.flush()
    assert (
        summaries(db, [student.id])[student.id].stay.actual_check_out_at
        == recent.actual_check_out_at
    )
    current = stay(db, actor, student, status="checked_in", start=NOW - timedelta(days=5))
    db.add(
        HousingTransfer(
            person_id=student.id,
            from_stay_id=recent.id,
            to_stay_id=current.id,
            transferred_at=recent.actual_check_out_at,
            reason="调房",
            transferred_by_user_id=actor.id,
        )
    )
    db.flush()
    assert summaries(db, [student.id])[student.id].status == "checked_in"


def test_earliest_reservation_is_bounded_and_ignores_cancelled(db: Session):
    actor, student = account(db), person(db, student=True)
    stay(db, actor, student, status="cancelled", start=NOW - timedelta(days=1))
    first = stay(
        db,
        actor,
        student,
        start=NOW + timedelta(days=1),
        end=NOW + timedelta(days=2),
        stay_id=UUID(int=1),
    )
    stay(db, actor, student, start=NOW + timedelta(days=3), end=NOW + timedelta(days=4))
    assert (
        summaries(db, [student.id])[student.id].stay.planned_check_in_at
        == first.planned_check_in_at
    )


def test_only_transfer_ended_stays_fall_back_to_unarranged_in_sql_and_projection(db: Session):
    from app.housing.invited_projection import invited_housing_status_expression
    from app.housing.models import HousingTransfer

    actor, student = account(db), person(db, student=True)
    origin = stay(
        db,
        actor,
        student,
        status="checked_out",
        start=NOW - timedelta(days=10),
        actual_end=NOW - timedelta(days=5),
    )
    cancelled_destination = stay(
        db, actor, student, status="cancelled", start=NOW - timedelta(days=5)
    )
    db.add(
        HousingTransfer(
            person_id=student.id,
            from_stay_id=origin.id,
            to_stay_id=cancelled_destination.id,
            transferred_at=origin.actual_check_out_at,
            reason="调房后预约取消的异常历史数据",
            transferred_by_user_id=actor.id,
        )
    )
    db.flush()

    result = summaries(db, [student.id])[student.id]
    assert result.status == "unarranged"
    assert result.stay is None
    assert result.next_planned_stay is None
    assert (
        db.scalar(
            select(invited_housing_status_expression(Person.id, as_of=NOW)).where(
                Person.id == student.id
            )
        )
        == "unarranged"
    )


def test_completed_transfer_chain_uses_terminal_real_checkout_in_sql_and_projection(db: Session):
    from app.housing.invited_projection import invited_housing_status_expression
    from app.housing.models import HousingTransfer

    actor, student = account(db), person(db, student=True)
    origins = [
        stay(
            db,
            actor,
            student,
            status="checked_out",
            start=NOW - timedelta(days=30),
            actual_end=NOW - timedelta(days=20),
        ),
        stay(
            db,
            actor,
            student,
            status="checked_out",
            start=NOW - timedelta(days=20),
            actual_end=NOW - timedelta(days=10),
        ),
    ]
    terminal = stay(
        db,
        actor,
        student,
        status="checked_out",
        start=NOW - timedelta(days=10),
        actual_end=NOW - timedelta(days=2),
    )
    terminal.room_name_snapshot = "最终退房房间"
    for origin, destination in zip(origins, [origins[1], terminal], strict=True):
        origin.updated_at = NOW + timedelta(days=30)
        db.add(
            HousingTransfer(
                person_id=student.id,
                from_stay_id=origin.id,
                to_stay_id=destination.id,
                transferred_at=origin.actual_check_out_at,
                reason="连续调房",
                transferred_by_user_id=actor.id,
            )
        )
    db.flush()

    result = summaries(db, [student.id])[student.id]
    assert result.status == "checked_out"
    assert result.stay.room_name == "最终退房房间"
    assert result.stay.actual_check_in_at == terminal.actual_check_in_at
    assert result.stay.actual_check_out_at == terminal.actual_check_out_at
    assert result.next_planned_stay is None
    assert (
        db.scalar(
            select(invited_housing_status_expression(Person.id, as_of=NOW)).where(
                Person.id == student.id
            )
        )
        == "checked_out"
    )


def test_multiple_logically_current_stays_raise_safe_error(db: Session):
    actor, student = account(db), person(db, student=True)
    stay(
        db,
        actor,
        student,
        status="checked_in",
        start=NOW - timedelta(days=20),
        end=NOW - timedelta(days=10),
    )
    stay(db, actor, student, status="checkout_pending", start=NOW - timedelta(days=9))
    with pytest.raises(ProblemError) as error:
        summaries(db, [student.id])
    assert (error.value.status, error.value.code) == (409, "invited_housing_inconsistent")
    assert student.full_name not in error.value.detail


def test_equal_reservation_times_use_stable_id_and_exclude_unauthorized_people(db: Session):
    # Synthetic pathological data only; transactional rollback restores the
    # exclusion constraint. Real scheduling normally prevents equal-time stays.
    db.execute(
        text("ALTER TABLE housing_stays DROP CONSTRAINT excl_housing_stays_person_active_overlap")
    )
    actor, student, other = account(db), person(db, student=True), person(db, student=True)
    larger = stay(db, actor, student, stay_id=UUID(int=20))
    smaller = stay(db, actor, student, stay_id=UUID(int=10))
    larger.room_name_snapshot = "大ID房"
    smaller.room_name_snapshot = "小ID房"
    stay(db, actor, other)
    db.flush()
    result = summaries(db, [student.id])
    assert set(result) == {student.id}
    assert result[student.id].stay.room_name == "小ID房"


def test_dto_rejects_extra_private_fields_and_naive_dates():
    from pydantic import ValidationError

    from app.referrals.schemas import InvitedHousingStay

    payload = {
        "building_name": "楼",
        "floor_name": "层",
        "room_name": "房",
        "duration_mode": "long_term",
        "planned_check_in_at": NOW,
        "planned_check_out_at": None,
        "actual_check_in_at": None,
        "actual_check_out_at": None,
    }
    with pytest.raises(ValidationError):
        InvitedHousingStay.model_validate({**payload, "roommates": ["秘密"]})
    with pytest.raises(ValidationError):
        InvitedHousingStay.model_validate(
            {**payload, "planned_check_in_at": NOW.replace(tzinfo=None)}
        )
