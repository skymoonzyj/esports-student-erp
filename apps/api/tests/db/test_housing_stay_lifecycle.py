from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.housing.errors import (
    HousingAccountRequired,
    HousingCapacityConflict,
    HousingGenderConflict,
    HousingInvalidStayTransition,
    HousingParentInactive,
    HousingPersonTimeConflict,
    HousingProfileIncomplete,
    HousingResourceNotFound,
    HousingRoomNotVerified,
    HousingRoomUnavailable,
    HousingServiceError,
    HousingStaleVersion,
    HousingVerifiedEmailRequired,
    MinorGuardianRequired,
)
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.schemas import (
    CancelReservationRequest,
    ConfirmCheckInRequest,
    StayCreate,
    StayUpdate,
)
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.teaching.models import Cohort

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)


def _actor(db: Session) -> UUID:
    person = _person(db, name="住宿生命周期操作人", sex="female", profile="staff")
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
    assert account is not None
    return account.id


def _person(
    db: Session,
    *,
    name: str,
    sex: str = "male",
    profile: str | None = "staff",
    birth_date: date = date(1990, 1, 1),
    account_status: str | None = "active",
    complete: bool = True,
    student_status: str = "active",
    login_identifier_kind: str | None = "email",
) -> Person:
    person = Person(
        id=uuid4(),
        full_name=name,
        identity_ciphertext=f"cipher-{uuid4().hex}",
        identity_digest=f"digest-{uuid4().hex}",
        phone_ciphertext=f"phone-cipher-{uuid4().hex}" if complete else None,
        phone_digest=f"phone-digest-{uuid4().hex}" if complete else None,
        email=None,
        email_ciphertext=f"email-cipher-{uuid4().hex}" if complete else None,
        email_digest=f"email-digest-{uuid4().hex}" if complete else None,
        sex=sex,
        date_of_birth=birth_date,
    )
    db.add(person)
    db.flush()
    if profile == "student":
        db.add(
            StudentProfile(
                person_id=person.id,
                student_number=f"S-{uuid4().hex[:16]}",
                status=student_status,
                game_account_limit=3,
            )
        )
    elif profile == "staff":
        db.add(StaffProfile(person_id=person.id, staff_type="life_teacher"))
    elif profile == "visitor":
        db.add(VisitorProfile(person_id=person.id, visit_reason="探亲"))
    if account_status is not None:
        account = UserAccount(
            id=uuid4(),
            person_id=person.id,
            login_phone_digest=f"login-{uuid4().hex}",
            email_normalized=f"login-{uuid4().hex}@example.test",
            password_hash="test-password-hash",
            status=account_status,
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        if login_identifier_kind is not None:
            db.add(
                AccountLoginIdentifier(
                    account_id=account.id,
                    kind=login_identifier_kind,
                    identifier_digest=uuid4().hex + uuid4().hex,
                    verified_at=NOW,
                    verification_source="test_verified",
                    is_primary=True,
                )
            )
    db.flush()
    return person


def _room(
    db: Session,
    *,
    capacity: int = 2,
    verification_status: str = "verified",
    service_status: str = "available",
    room_type: str = "custom",
    building_active: bool = True,
    floor_active: bool = True,
) -> HousingRoom:
    suffix = uuid4().hex[:10].upper()
    building = HousingBuilding(
        id=uuid4(),
        code=f"L{suffix[:8]}",
        name=f"生命周期楼{suffix}",
        display_order=0,
        is_active=building_active,
    )
    floor = HousingFloor(
        id=uuid4(),
        building_id=building.id,
        number=2,
        name=f"二楼{suffix}",
        display_order=0,
        is_active=floor_active,
    )
    room = HousingRoom(
        id=uuid4(),
        building_id=building.id,
        floor_id=floor.id,
        room_number=f"{suffix}-201",
        normalized_key=f"{suffix}-201",
        room_type=room_type,
        capacity=capacity,
        verification_status=verification_status,
        service_status=service_status,
        version=1,
    )
    db.add_all([building, floor, room])
    db.flush()
    return room


def _cohort(db: Session, actor_id: UUID) -> Cohort:
    cohort = Cohort(
        id=uuid4(),
        name="住宿生命周期营期",
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=10),
        default_check_in_at=NOW + timedelta(days=1),
        default_check_out_at=NOW + timedelta(days=10),
        status="upcoming",
        created_by_user_id=actor_id,
    )
    db.add(cohort)
    db.flush()
    return cohort


def _input(
    person_id: UUID,
    *,
    start: datetime,
    end: datetime | None = None,
    purpose: str = "permanent",
    duration: str | None = None,
    cohort_id: UUID | None = None,
    visit_reason: str | None = None,
) -> StayCreate:
    resolved_duration = duration or ("fixed_term" if end is not None else "long_term")
    return StayCreate(
        person_id=person_id,
        stay_purpose=purpose,
        duration_mode=resolved_duration,
        cohort_id=cohort_id,
        visit_reason=visit_reason,
        planned_check_in_at=start,
        planned_check_out_at=end,
    )


def _allow(room: HousingRoom) -> None:
    del room


def _create(
    db: Session,
    room: HousingRoom,
    input: StayCreate,
    actor_id: UUID,
    *,
    occurred_at: datetime = NOW,
) -> HousingStay:
    return HousingOccupancyService(db).create_stay(
        room.id,
        input,
        actor_user_id=actor_id,
        occurred_at=occurred_at,
        authorize_room=_allow,
    )


def test_email_only_person_can_receive_stay_without_phone(db: Session) -> None:
    actor_id = _actor(db)
    room = _room(db)
    person = _person(
        db,
        name="仅邮箱住宿人员",
        login_identifier_kind="email",
    )
    person.phone_ciphertext = None
    person.phone_digest = None
    db.flush()

    stay = _create(
        db,
        room,
        _input(person.id, start=NOW + timedelta(days=1)),
        actor_id,
    )

    assert stay.person_id == person.id
    assert stay.status == "planned"


def test_phone_only_person_cannot_receive_stay_with_zero_writes(db: Session) -> None:
    actor_id = _actor(db)
    room = _room(db)
    person = _person(
        db,
        name="仅手机号住宿人员",
        login_identifier_kind="phone",
    )
    person.email = None
    person.email_ciphertext = None
    person.email_digest = None
    db.flush()

    before = db.scalar(select(func.count()).select_from(HousingStay)) or 0
    with pytest.raises(HousingVerifiedEmailRequired):
        _create(
            db,
            room,
            _input(person.id, start=NOW + timedelta(days=1)),
            actor_id,
        )
    assert db.scalar(select(func.count()).select_from(HousingStay)) == before


def test_future_is_planned_while_present_or_past_is_checked_in_with_snapshot(
    db: Session,
) -> None:
    actor_id = _actor(db)
    room = _room(db)
    future_person = _person(db, name="未来住户")
    current_person = _person(db, name="当前住户")

    future = _create(
        db,
        room,
        _input(
            future_person.id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        ),
        actor_id,
    )
    current = _create(
        db,
        room,
        _input(current_person.id, start=NOW - timedelta(hours=1)),
        actor_id,
    )

    assert future.status == "planned"
    assert future.actual_check_in_at is None
    assert current.status == "checked_in"
    assert current.actual_check_in_at == NOW - timedelta(hours=1)
    assert current.checked_in_by_user_id == actor_id
    assert current.created_by_user_id == actor_id
    assert current.room_name_snapshot == room.room_number
    assert current.floor_name_snapshot.startswith("二楼")
    assert current.building_name_snapshot.startswith("生命周期楼")


def test_confirm_rejects_future_actual_check_in_without_mutating_reservation(
    db: Session,
) -> None:
    actor_id = _actor(db)
    stay = _create(
        db,
        _room(db),
        _input(
            _person(db, name="未来确认人").id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        ),
        actor_id,
    )
    authorized: list[UUID] = []

    def authorize_room(room: HousingRoom) -> None:
        authorized.append(room.id)

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).confirm_check_in(
            stay.id,
            ConfirmCheckInRequest(
                version=stay.version,
                actual_check_in_at=NOW + timedelta(seconds=1),
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=authorize_room,
        )

    assert authorized == [stay.room_id]
    db.expire_all()
    preserved = db.get(HousingStay, stay.id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_in_at, preserved.version) == (
        "planned",
        None,
        1,
    )


@pytest.mark.parametrize("offset", [timedelta(0), timedelta(hours=1)])
def test_confirm_rejects_actual_check_in_at_or_after_fixed_term_checkout_without_mutation(
    db: Session,
    offset: timedelta,
) -> None:
    actor_id = _actor(db)
    planned_checkout = NOW + timedelta(days=2)
    stay = _create(
        db,
        _room(db),
        _input(
            _person(db, name=f"过期预约确认人-{offset}").id,
            start=NOW + timedelta(days=1),
            end=planned_checkout,
        ),
        actor_id,
    )
    actual_check_in = planned_checkout + offset

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).confirm_check_in(
            stay.id,
            ConfirmCheckInRequest(
                version=stay.version,
                actual_check_in_at=actual_check_in,
            ),
            actor_user_id=actor_id,
            occurred_at=actual_check_in,
            authorize_room=_allow,
        )

    db.expire_all()
    preserved = db.get(HousingStay, stay.id)
    assert preserved is not None
    assert (preserved.status, preserved.actual_check_in_at, preserved.version) == (
        "planned",
        None,
        1,
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("account", HousingAccountRequired),
        ("room", HousingRoomUnavailable),
        ("gender", HousingGenderConflict),
        ("capacity", HousingCapacityConflict),
    ],
)
def test_confirmation_rechecks_current_account_room_gender_and_capacity(
    db: Session,
    mutation: str,
    expected_error: type[HousingServiceError],
) -> None:
    actor_id = _actor(db)
    room = _room(db, capacity=1 if mutation == "capacity" else 2)
    person = _person(db, name=f"重新校验-{mutation}", sex="male")
    stay = _create(
        db,
        room,
        _input(
            person.id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        ),
        actor_id,
    )
    if mutation == "account":
        account = db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
        assert account is not None
        account.status = "disabled"
    elif mutation == "room":
        room.service_status = "maintenance"
    else:
        competing = _person(
            db,
            name=f"竞争住户-{mutation}",
            sex="female" if mutation == "gender" else "male",
        )
        db.add(
            HousingStay(
                person_id=competing.id,
                room_id=room.id,
                stay_purpose="permanent",
                duration_mode="long_term",
                planned_check_in_at=NOW - timedelta(hours=1),
                actual_check_in_at=NOW - timedelta(hours=1),
                status="checked_in",
                room_name_snapshot=room.room_number,
                floor_name_snapshot="二楼",
                building_name_snapshot="生命周期楼",
                created_by_user_id=actor_id,
                checked_in_by_user_id=actor_id,
                version=1,
            )
        )
    db.flush()

    with pytest.raises(expected_error):
        HousingOccupancyService(db).confirm_check_in(
            stay.id,
            ConfirmCheckInRequest(version=stay.version, actual_check_in_at=NOW),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )


def test_confirmation_rechecks_minor_guardian_at_actual_date(db: Session) -> None:
    actor_id = _actor(db)
    person = _person(
        db,
        name="即将成年学员",
        profile="student",
        birth_date=date(2008, 8, 5),
    )
    stay = _create(
        db,
        _room(db),
        _input(
            person.id,
            start=datetime(2026, 8, 6, 12, tzinfo=UTC),
            end=datetime(2026, 8, 7, 12, tzinfo=UTC),
        ),
        actor_id,
    )

    with pytest.raises(MinorGuardianRequired):
        HousingOccupancyService(db).confirm_check_in(
            stay.id,
            ConfirmCheckInRequest(version=1, actual_check_in_at=NOW),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )


def test_only_planned_stays_can_be_edited_or_cancelled_and_version_is_atomic(
    db: Session,
) -> None:
    actor_id = _actor(db)
    room = _room(db)
    planned = _create(
        db,
        room,
        _input(
            _person(db, name="预约编辑人").id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        ),
        actor_id,
    )
    updated = HousingOccupancyService(db).update_reservation(
        planned.id,
        StayUpdate(
            version=planned.version,
            planned_check_in_at=NOW + timedelta(days=2),
            planned_check_out_at=NOW + timedelta(days=3),
            notes="调整预约",
        ),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    assert updated.version == 2
    assert updated.notes == "调整预约"

    with pytest.raises(HousingStaleVersion):
        HousingOccupancyService(db).cancel_reservation(
            updated.id,
            CancelReservationRequest(version=1, reason="过期取消"),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    db.expire_all()
    unchanged = db.get(HousingStay, updated.id)
    assert unchanged is not None
    assert (unchanged.status, unchanged.notes, unchanged.version) == (
        "planned",
        "调整预约",
        2,
    )

    cancelled = HousingOccupancyService(db).cancel_reservation(
        updated.id,
        CancelReservationRequest(version=2, reason="行程取消"),
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room=_allow,
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancellation_reason == "行程取消"
    assert cancelled.cancelled_by_user_id == actor_id
    assert cancelled.version == 3

    still_future = _create(
        db,
        _room(db),
        _input(
            _person(db, name="预约不可改为过去").id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        ),
        actor_id,
    )
    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).update_reservation(
            still_future.id,
            StayUpdate(
                version=still_future.version,
                planned_check_in_at=NOW,
                planned_check_out_at=NOW + timedelta(days=1),
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    db.expire_all()
    preserved_future = db.get(HousingStay, still_future.id)
    assert preserved_future is not None
    assert (preserved_future.status, preserved_future.version) == ("planned", 1)
    assert preserved_future.planned_check_in_at == NOW + timedelta(days=1)

    current = _create(
        db,
        room,
        _input(_person(db, name="已入住不可编辑").id, start=NOW - timedelta(hours=1)),
        actor_id,
    )
    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).update_reservation(
            current.id,
            StayUpdate(
                version=current.version,
                planned_check_in_at=NOW + timedelta(days=1),
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )


@pytest.mark.parametrize(
    "input_factory",
    [
        lambda person, cohort: _input(
            person.id,
            start=NOW + timedelta(days=1),
            purpose="camp",
            duration="long_term",
            cohort_id=cohort.id,
        ),
        lambda person, cohort: _input(
            person.id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
            purpose="permanent",
            cohort_id=cohort.id,
        ),
        lambda person, cohort: StayCreate.model_construct(
            person_id=person.id,
            stay_purpose="temporary",
            duration_mode="fixed_term",
            cohort_id=None,
            visit_reason="",
            planned_check_in_at=NOW + timedelta(days=1),
            planned_check_out_at=NOW + timedelta(days=2),
            notes=None,
        ),
    ],
)
def test_stay_purpose_duration_and_cohort_semantics_are_enforced(
    db: Session,
    input_factory,
) -> None:
    actor_id = _actor(db)
    with pytest.raises(HousingServiceError):
        _create(
            db,
            _room(db),
            input_factory(_person(db, name="用途校验人"), _cohort(db, actor_id)),
            actor_id,
        )


def test_camp_requires_a_current_usable_cohort_and_bounded_interval(db: Session) -> None:
    actor_id = _actor(db)
    person = _person(db, name="随营住户")
    cohort = _cohort(db, actor_id)
    room = _room(db)
    valid = _create(
        db,
        room,
        _input(
            person.id,
            start=cohort.default_check_in_at,
            end=cohort.default_check_out_at,
            purpose="camp",
            cohort_id=cohort.id,
        ),
        actor_id,
    )
    assert valid.cohort_id == cohort.id

    archived = _cohort(db, actor_id)
    archived.status = "archived"
    db.flush()
    with pytest.raises(HousingServiceError):
        _create(
            db,
            _room(db),
            _input(
                _person(db, name="归档营住户").id,
                start=archived.default_check_in_at,
                end=archived.default_check_out_at,
                purpose="camp",
                cohort_id=archived.id,
            ),
            actor_id,
        )


def test_visitor_can_only_have_fixed_temporary_stay_with_reason(db: Session) -> None:
    actor_id = _actor(db)
    visitor = _person(db, name="临时访客", profile="visitor")
    room = _room(db)
    db.commit()
    with pytest.raises(HousingProfileIncomplete):
        _create(
            db,
            room,
            _input(visitor.id, start=NOW + timedelta(days=1)),
            actor_id,
        )

    accepted = _create(
        db,
        room,
        _input(
            visitor.id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
            purpose="temporary",
            visit_reason="探亲住宿",
        ),
        actor_id,
    )
    assert accepted.stay_purpose == "temporary"
    assert accepted.visit_reason == "探亲住宿"


@pytest.mark.parametrize(
    ("person_kwargs", "expected_error"),
    [
        ({"account_status": None}, HousingResourceNotFound),
        ({"account_status": "disabled"}, HousingResourceNotFound),
        ({"complete": False}, HousingProfileIncomplete),
        ({"profile": None}, HousingResourceNotFound),
        ({"profile": "student", "student_status": "draft"}, HousingProfileIncomplete),
        (
            {
                "profile": "student",
                "birth_date": date(2012, 1, 1),
            },
            MinorGuardianRequired,
        ),
    ],
)
def test_account_profile_and_minor_guardian_readiness_are_required(
    db: Session,
    person_kwargs: dict[str, object],
    expected_error: type[HousingServiceError],
) -> None:
    actor_id = _actor(db)
    person = _person(db, name=f"就绪校验-{uuid4().hex[:6]}", **person_kwargs)

    with pytest.raises(expected_error):
        _create(
            db,
            _room(db),
            _input(person.id, start=NOW + timedelta(days=1)),
            actor_id,
        )


def test_encrypted_email_without_digest_is_not_housing_ready(db: Session) -> None:
    actor_id = _actor(db)
    person = _person(db, name="邮箱摘要缺失人员")
    person.email = None
    person.email_ciphertext = "encrypted-email"
    person.email_digest = None
    db.flush()

    with pytest.raises(HousingProfileIncomplete):
        _create(
            db,
            _room(db),
            _input(person.id, start=NOW + timedelta(days=1)),
            actor_id,
        )


def test_orphan_email_digest_is_not_housing_ready(db: Session) -> None:
    actor_id = _actor(db)
    person = _person(db, name="旧邮箱摘要孤儿人员")
    person.email = None
    person.email_ciphertext = None
    person.email_digest = "orphan-digest"
    db.flush()

    with pytest.raises(HousingProfileIncomplete):
        _create(
            db,
            _room(db),
            _input(person.id, start=NOW + timedelta(days=1)),
            actor_id,
        )


def test_blank_encrypted_email_pair_is_not_housing_ready(db: Session) -> None:
    actor_id = _actor(db)
    person = _person(db, name="空邮箱加密字段人员")
    person.email = None
    person.email_ciphertext = ""
    person.email_digest = ""
    db.flush()

    with pytest.raises(HousingProfileIncomplete):
        _create(
            db,
            _room(db),
            _input(person.id, start=NOW + timedelta(days=1)),
            actor_id,
        )


def test_deleted_person_is_not_a_selectable_resident(db: Session) -> None:
    actor_id = _actor(db)
    person = _person(db, name="已删除住宿人")
    person.deleted_at = NOW
    db.flush()
    with pytest.raises(HousingResourceNotFound):
        _create(
            db,
            _room(db),
            _input(person.id, start=NOW + timedelta(days=1)),
            actor_id,
        )


def test_missing_person_still_reauthorizes_locked_room_before_private_not_found(
    db: Session,
) -> None:
    actor_id = _actor(db)
    room = _room(db)
    authorized: list[UUID] = []

    with pytest.raises(HousingResourceNotFound):
        HousingOccupancyService(db).create_stay(
            room.id,
            _input(uuid4(), start=NOW + timedelta(days=1)),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=lambda locked_room: authorized.append(locked_room.id),
        )

    assert authorized == [room.id]


def test_pending_room_requires_explicit_backfill_and_dual_authorization(
    db: Session,
) -> None:
    actor_id = _actor(db)
    room = _room(db, verification_status="pending_verification")
    person = _person(db, name="初始补录住户")
    input = _input(person.id, start=NOW - timedelta(days=3))
    db.commit()

    with pytest.raises(HousingRoomNotVerified):
        _create(db, room, input, actor_id)

    authorized: list[UUID] = []

    def authorize_room_and_stay(locked_room: HousingRoom) -> None:
        authorized.append(locked_room.id)

    backfilled = HousingOccupancyService(db).backfill_existing_stay(
        room.id,
        input,
        actor_user_id=actor_id,
        occurred_at=NOW,
        authorize_room_and_stay=authorize_room_and_stay,
    )
    assert authorized == [room.id]
    assert backfilled.status == "checked_in"
    assert backfilled.actual_check_in_at == NOW - timedelta(days=3)

    with pytest.raises(HousingInvalidStayTransition):
        HousingOccupancyService(db).backfill_existing_stay(
            _room(db, verification_status="pending_verification").id,
            _input(
                _person(db, name="未来补录人").id,
                start=NOW + timedelta(seconds=1),
            ),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room_and_stay=_allow,
        )


@pytest.mark.parametrize(
    "room_kwargs",
    [
        {"room_type": "functional", "capacity": 0},
        {"service_status": "problem"},
        {"service_status": "maintenance"},
        {"service_status": "disabled"},
        {"building_active": False},
        {"floor_active": False},
    ],
)
@pytest.mark.parametrize("backfill", [False, True])
def test_unavailable_rooms_reject_allocation_and_backfill(
    db: Session,
    room_kwargs: dict[str, object],
    backfill: bool,
) -> None:
    actor_id = _actor(db)
    room = _room(db, **room_kwargs)
    input = _input(
        _person(db, name=f"不可用房住户-{uuid4().hex[:6]}").id,
        start=NOW - timedelta(hours=1),
    )
    expected = (
        HousingParentInactive
        if (room_kwargs.get("building_active") is False or room_kwargs.get("floor_active") is False)
        else HousingRoomUnavailable
    )
    with pytest.raises(expected):
        if backfill:
            HousingOccupancyService(db).backfill_existing_stay(
                room.id,
                input,
                actor_user_id=actor_id,
                occurred_at=NOW,
                authorize_room_and_stay=_allow,
            )
        else:
            _create(db, room, input, actor_id)


def test_conflicts_are_rejected_in_person_gender_then_capacity_order(db: Session) -> None:
    actor_id = _actor(db)
    room = _room(db, capacity=1)
    male = _person(db, name="冲突男甲", sex="male")
    female = _person(db, name="冲突女乙", sex="female")
    first = _create(
        db,
        room,
        _input(male.id, start=NOW - timedelta(hours=1)),
        actor_id,
    )
    assert first.status == "checked_in"

    with pytest.raises(HousingPersonTimeConflict):
        _create(
            db,
            _room(db),
            _input(male.id, start=NOW - timedelta(minutes=30)),
            actor_id,
        )
    with pytest.raises(HousingGenderConflict):
        _create(
            db,
            room,
            _input(female.id, start=NOW - timedelta(minutes=30)),
            actor_id,
        )
    with pytest.raises(HousingCapacityConflict):
        _create(
            db,
            room,
            _input(
                _person(db, name="冲突男丙", sex="male").id,
                start=NOW - timedelta(minutes=30),
            ),
            actor_id,
        )


def test_nonoverlapping_future_intervals_may_use_different_sexes(db: Session) -> None:
    actor_id = _actor(db)
    room = _room(db, capacity=1)
    first = _create(
        db,
        room,
        _input(
            _person(db, name="未来男住户", sex="male").id,
            start=NOW + timedelta(days=1),
            end=NOW + timedelta(days=2),
        ),
        actor_id,
    )
    second = _create(
        db,
        room,
        _input(
            _person(db, name="未来女住户", sex="female").id,
            start=NOW + timedelta(days=2),
            end=NOW + timedelta(days=3),
        ),
        actor_id,
    )
    assert first.status == second.status == "planned"


def test_fixed_current_stay_allows_planning_after_checkout_but_blocks_actual_check_in(
    db: Session,
) -> None:
    actor_id = _actor(db)
    room = _room(db, capacity=1)
    planned_checkout = NOW + timedelta(days=1)
    current = _person(db, name="按期离开男住户", sex="male")
    future = _person(db, name="接续入住女住户", sex="female")
    db.add(
        HousingStay(
            person_id=current.id,
            room_id=room.id,
            stay_purpose="permanent",
            duration_mode="fixed_term",
            planned_check_in_at=NOW - timedelta(days=1),
            planned_check_out_at=planned_checkout,
            actual_check_in_at=NOW - timedelta(days=1),
            status="checked_in",
            room_name_snapshot=room.room_number,
            floor_name_snapshot="二楼",
            building_name_snapshot="生命周期楼",
            created_by_user_id=actor_id,
            checked_in_by_user_id=actor_id,
            version=1,
        )
    )
    db.flush()

    reservation = _create(
        db,
        room,
        _input(
            future.id,
            start=planned_checkout,
            end=planned_checkout + timedelta(days=1),
        ),
        actor_id,
    )
    assert reservation.status == "planned"

    with pytest.raises(HousingGenderConflict):
        HousingOccupancyService(db).confirm_check_in(
            reservation.id,
            ConfirmCheckInRequest(
                version=reservation.version,
                actual_check_in_at=planned_checkout,
            ),
            actor_user_id=actor_id,
            occurred_at=planned_checkout,
            authorize_room=_allow,
        )


def test_checked_in_stay_occupies_room_even_after_planned_end(db: Session) -> None:
    actor_id = _actor(db)
    room = _room(db, capacity=1)
    occupant = _person(db, name="逾期仍住人员", sex="male")
    db.add(
        HousingStay(
            person_id=occupant.id,
            room_id=room.id,
            stay_purpose="permanent",
            duration_mode="fixed_term",
            planned_check_in_at=NOW - timedelta(days=3),
            planned_check_out_at=NOW - timedelta(days=1),
            actual_check_in_at=NOW - timedelta(days=3),
            status="checked_in",
            room_name_snapshot=room.room_number,
            floor_name_snapshot="二楼",
            building_name_snapshot="生命周期楼",
            created_by_user_id=actor_id,
            checked_in_by_user_id=actor_id,
            version=1,
        )
    )
    db.flush()

    with pytest.raises(HousingCapacityConflict):
        _create(
            db,
            room,
            _input(
                _person(db, name="未来同房人", sex="male").id,
                start=NOW + timedelta(days=1),
                end=NOW + timedelta(days=2),
            ),
            actor_id,
        )


def test_projection_clears_gender_after_last_checkout(db: Session) -> None:
    actor_id = _actor(db)
    room = _room(db)
    person = _person(db, name="已退房人员", sex="female")
    db.add(
        HousingStay(
            person_id=person.id,
            room_id=room.id,
            stay_purpose="temporary",
            duration_mode="fixed_term",
            visit_reason="历史住宿",
            planned_check_in_at=NOW - timedelta(days=3),
            planned_check_out_at=NOW - timedelta(days=1),
            actual_check_in_at=NOW - timedelta(days=3),
            actual_check_out_at=NOW - timedelta(days=1),
            status="checked_out",
            room_name_snapshot=room.room_number,
            floor_name_snapshot="二楼",
            building_name_snapshot="生命周期楼",
            created_by_user_id=actor_id,
            checked_in_by_user_id=actor_id,
            checked_out_by_user_id=actor_id,
            version=2,
        )
    )
    db.flush()

    projection = HousingOccupancyService(db).room_projections([room.id], NOW)[room.id]
    assert projection.current_stays == ()
    assert projection.current_gender is None
    assert db.scalar(select(func.count(HousingStay.id))) == 1


def test_exclusion_integrity_is_mapped_and_other_integrity_errors_are_not_swallowed(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor_id = _actor(db)
    person = _person(db, name="约束映射住户")
    first_room = _room(db)
    second_room = _room(db)
    _create(
        db,
        first_room,
        _input(person.id, start=NOW - timedelta(hours=1)),
        actor_id,
    )

    service = HousingOccupancyService(db)
    monkeypatch.setattr(service, "_require_no_stay_conflicts", lambda **_kwargs: None)
    with pytest.raises(HousingPersonTimeConflict):
        service.create_stay(
            second_room.id,
            _input(person.id, start=NOW - timedelta(minutes=30)),
            actor_user_id=actor_id,
            occurred_at=NOW,
            authorize_room=_allow,
        )
    assert db.scalar(select(literal(1))) == 1

    unrelated_person = _person(db, name="非目标约束住户")
    with pytest.raises(IntegrityError):
        service.create_stay(
            _room(db).id,
            _input(unrelated_person.id, start=NOW + timedelta(days=1)),
            actor_user_id=uuid4(),
            occurred_at=NOW,
            authorize_room=_allow,
        )
    assert db.scalar(select(literal(1))) == 1
