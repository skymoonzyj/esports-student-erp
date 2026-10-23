from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import NoReturn, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.housing.access import HousingAccessService
from app.housing.errors import (
    HousingAccountRequired,
    HousingCapacityConflict,
    HousingCheckoutConfirmationRequired,
    HousingGenderConflict,
    HousingInvalidStayTransition,
    HousingParentInactive,
    HousingPersistenceFailure,
    HousingPersonTimeConflict,
    HousingProfileIncomplete,
    HousingResourceNotFound,
    HousingRoomNotVerified,
    HousingRoomUnavailable,
    HousingSensitiveDataUnavailable,
    HousingServiceError,
    HousingStaleVersion,
    HousingVerifiedEmailRequired,
    MinorGuardianRequired,
)
from app.housing.models import (
    HousingBuilding,
    HousingFloor,
    HousingRoom,
    HousingRoomIssue,
    HousingStay,
    HousingTransfer,
)
from app.housing.schemas import (
    CancelReservationRequest,
    CheckoutConfirmation,
    CheckoutRequest,
    CohortStatus,
    CohortStayOption,
    ConfirmCheckInRequest,
    ResidentOption,
    ResidentPersonType,
    RoomIssueSummary,
    Sex,
    StayAllowedActions,
    StayCreate,
    StaySummary,
    StayUpdate,
    TransferRoomOption,
    TransferRoomOptionsResponse,
    TransferStayRequest,
)
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.identity.name_search import (
    is_name_initial_query,
    name_matches_query,
    normalize_name_query,
)
from app.identity.normalization import IdentityValidationError, normalize_phone
from app.identity.readiness import GuardianReadinessPolicy
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest
from app.teaching.models import Cohort

_CURRENT_STATUSES = frozenset({"checked_in", "checkout_pending"})
_ACTIVE_STAY_STATUSES = frozenset({"planned", *_CURRENT_STATUSES})
_SELECTABLE_COHORT_STATUSES = frozenset({"open", "upcoming", "active"})
_PERSON_TIME_EXCLUSION = "excl_housing_stays_person_active_overlap"
_TRANSFER_OPTIONS_LIMIT = 200
RoomAuthorizer = Callable[[HousingRoom], None]


def intervals_overlap(
    first_start: datetime,
    first_end: datetime | None,
    second_start: datetime,
    second_end: datetime | None,
) -> bool:
    return (first_end is None or second_start < first_end) and (
        second_end is None or first_start < second_end
    )


def maximum_interval_occupancy(
    intervals: Sequence[tuple[datetime, datetime | None]],
) -> int:
    events: list[tuple[datetime, int]] = []
    open_ended = 0
    for starts_at, ends_at in intervals:
        events.append((starts_at, 1))
        if ends_at is None:
            open_ended += 1
        else:
            events.append((ends_at, -1))
    events.sort(key=lambda event: (event[0], event[1]))
    occupancy = 0
    peak = 0
    for _, delta in events:
        occupancy += delta
        peak = max(peak, occupancy)
    return max(peak, open_ended)


@dataclass(frozen=True)
class RoomOccupancyProjection:
    current_stays: tuple[StaySummary, ...]
    future_reservation_count: int
    open_issue: RoomIssueSummary | None
    future_stays: tuple[StaySummary, ...] = ()

    @property
    def current_gender(self) -> Sex | None:
        return self.current_stays[0].sex if self.current_stays else None


@dataclass(frozen=True)
class _ResidentConflictProfile:
    id: UUID
    sex: str


@dataclass(frozen=True)
class _StayConflictView:
    id: UUID
    person_id: UUID
    room_id: UUID
    planned_check_in_at: datetime
    planned_check_out_at: datetime | None
    actual_check_in_at: datetime | None
    actual_check_out_at: datetime | None
    status: str


@dataclass(frozen=True)
class _TransferSourceView:
    id: UUID
    person_id: UUID
    room_id: UUID
    status: str
    checkout_requested_actual_at: datetime | None
    checkout_requested_at: datetime | None
    checkout_requested_by_user_id: UUID | None
    duration_mode: str
    planned_check_out_at: datetime | None


@dataclass(frozen=True)
class _TransferRoomView:
    id: UUID
    capacity: int
    building_label: str
    floor_label: str
    room_label: str


@dataclass(frozen=True)
class _LockedStayMutation:
    people: dict[UUID, Person]
    rooms: dict[UUID, HousingRoom]
    active_stays: tuple[HousingStay, ...]


class HousingOccupancyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _person_type(
        student_person_id: UUID | None,
        staff_type: str | None,
        visitor_person_id: UUID | None,
    ) -> ResidentPersonType:
        if student_person_id is not None:
            return "student"
        if staff_type is not None:
            return cast(ResidentPersonType, staff_type)
        if visitor_person_id is not None:
            return "visitor"
        return "other"

    def _authorized_room(
        self,
        room_id: UUID,
        authorize_room: RoomAuthorizer,
    ) -> HousingRoom:
        room = self.db.scalar(
            select(HousingRoom)
            .where(HousingRoom.id == room_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if room is None:
            raise HousingResourceNotFound("housing room not found")
        authorize_room(room)
        return room

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.utcoffset() is None:
            raise HousingServiceError("housing timestamps must be timezone-aware")

    @staticmethod
    def _integrity_constraint(exc: IntegrityError) -> str | None:
        diagnostic = getattr(exc.orig, "diag", None)
        return cast(str | None, getattr(diagnostic, "constraint_name", None))

    @classmethod
    def _raise_mapped_stay_integrity(cls, exc: IntegrityError) -> NoReturn:
        if cls._integrity_constraint(exc) == _PERSON_TIME_EXCLUSION:
            raise HousingPersonTimeConflict(
                "housing person already has an overlapping stay"
            ) from exc
        raise exc

    @classmethod
    def _raise_mapped_transfer_persistence(cls, exc: DBAPIError) -> NoReturn:
        if (
            isinstance(exc, IntegrityError)
            and cls._integrity_constraint(exc) == _PERSON_TIME_EXCLUSION
        ):
            raise HousingPersonTimeConflict(
                "housing person already has an overlapping stay"
            ) from exc
        raise HousingPersistenceFailure(
            "housing transfer could not be completed"
        ) from exc

    def lock_people(self, person_ids: Sequence[UUID]) -> list[Person]:
        ordered_ids = sorted(set(person_ids))
        if not ordered_ids:
            return []
        return list(
            self.db.scalars(
                select(Person)
                .where(Person.id.in_(ordered_ids))
                .order_by(Person.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _selectable_resident_predicates() -> tuple[ColumnElement[bool], ...]:
        return (
            UserAccount.status == "active",
            Person.deleted_at.is_(None),
            or_(
                StudentProfile.person_id.is_not(None),
                StaffProfile.person_id.is_not(None),
                VisitorProfile.person_id.is_not(None),
            ),
        )

    @staticmethod
    def _selectable_cohort_predicates() -> tuple[ColumnElement[bool], ...]:
        return (
            Cohort.status.in_(_SELECTABLE_COHORT_STATUSES),
            Cohort.default_check_out_at > Cohort.default_check_in_at,
        )

    def lock_selectable_residents(self, person_ids: Sequence[UUID]) -> list[Person]:
        ordered_ids = sorted(set(person_ids))
        if not ordered_ids:
            return []
        return list(
            self.db.scalars(
                select(Person)
                .join(UserAccount, UserAccount.person_id == Person.id)
                .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
                .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
                .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
                .where(
                    Person.id.in_(ordered_ids),
                    *self._selectable_resident_predicates(),
                )
                .order_by(Person.id)
                .with_for_update(of=Person)
                .execution_options(populate_existing=True)
            )
        )

    def lock_rooms(self, room_ids: Sequence[UUID]) -> list[HousingRoom]:
        ordered_ids = sorted(set(room_ids))
        if not ordered_ids:
            return []
        return list(
            self.db.scalars(
                select(HousingRoom)
                .where(HousingRoom.id.in_(ordered_ids))
                .order_by(HousingRoom.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def lock_active_stays(
        self,
        person_ids: Sequence[UUID],
        room_ids: Sequence[UUID],
    ) -> list[HousingStay]:
        ordered_person_ids = sorted(set(person_ids))
        ordered_room_ids = sorted(set(room_ids))
        if not ordered_person_ids and not ordered_room_ids:
            return []
        filters = []
        if ordered_person_ids:
            filters.append(HousingStay.person_id.in_(ordered_person_ids))
        if ordered_room_ids:
            filters.append(HousingStay.room_id.in_(ordered_room_ids))
        return list(
            self.db.scalars(
                select(HousingStay)
                .where(HousingStay.status.in_(_ACTIVE_STAY_STATUSES))
                .where(or_(*filters))
                .order_by(HousingStay.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def lock_transfer_stays(
        self,
        *,
        person_id: UUID,
        room_ids: Sequence[UUID],
        target_room_id: UUID,
        target_start: datetime,
        target_end: datetime | None,
    ) -> list[HousingStay]:
        ordered_room_ids = sorted(set(room_ids))
        historical_overlap = [
            HousingStay.room_id == target_room_id,
            HousingStay.status == "checked_out",
            HousingStay.actual_check_out_at > target_start,
        ]
        if target_end is not None:
            historical_overlap.append(HousingStay.actual_check_in_at < target_end)
        return list(
            self.db.scalars(
                select(HousingStay)
                .where(
                    or_(
                        and_(
                            HousingStay.status.in_(_ACTIVE_STAY_STATUSES),
                            or_(
                                HousingStay.person_id == person_id,
                                HousingStay.room_id.in_(ordered_room_ids),
                            ),
                        ),
                        and_(*historical_overlap),
                    )
                )
                .order_by(HousingStay.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _lock_stay_mutation(
        self,
        *,
        person_ids: Sequence[UUID],
        room_ids: Sequence[UUID],
        authorize_room: RoomAuthorizer,
        selectable_residents_only: bool = False,
    ) -> _LockedStayMutation:
        people = (
            self.lock_selectable_residents(person_ids)
            if selectable_residents_only
            else self.lock_people(person_ids)
        )
        rooms = self.lock_rooms(sorted(set(room_ids)))
        active_stays = self.lock_active_stays(
            sorted(set(person_ids)),
            sorted(set(room_ids)),
        )
        people_by_id = {person.id: person for person in people}
        rooms_by_id = {room.id: room for room in rooms}
        if any(room_id not in rooms_by_id for room_id in set(room_ids)):
            raise HousingResourceNotFound("housing room not found")
        for room_id in sorted(rooms_by_id):
            authorize_room(rooms_by_id[room_id])
        if any(person_id not in people_by_id for person_id in set(person_ids)):
            if selectable_residents_only:
                raise HousingResourceNotFound("housing resident not found")
            raise HousingProfileIncomplete("housing resident profile is incomplete")
        return _LockedStayMutation(
            people=people_by_id,
            rooms=rooms_by_id,
            active_stays=tuple(active_stays),
        )

    def _require_resident_ready(
        self,
        person: Person,
        *,
        reference_at: datetime,
    ) -> str:
        if person.deleted_at is not None:
            raise HousingProfileIncomplete("housing resident profile is incomplete")
        account = self.db.scalar(
            select(UserAccount)
            .where(UserAccount.person_id == person.id)
            .execution_options(populate_existing=True)
        )
        if account is None or account.status != "active":
            raise HousingAccountRequired("housing resident requires an active account")
        if not account.email_normalized or not account.email_normalized.strip():
            raise HousingVerifiedEmailRequired("housing resident requires a verified email")
        email_fields_consistent = person.email is None or bool(person.email.strip())
        if not all(
            (
                person.full_name.strip(),
                person.identity_ciphertext,
                person.identity_digest,
                email_fields_consistent,
                person.sex,
                person.date_of_birth,
            )
        ):
            raise HousingProfileIncomplete("housing resident profile is incomplete")

        student = self.db.scalar(
            select(StudentProfile)
            .where(StudentProfile.person_id == person.id)
            .execution_options(populate_existing=True)
        )
        staff = self.db.scalar(
            select(StaffProfile)
            .where(StaffProfile.person_id == person.id)
            .execution_options(populate_existing=True)
        )
        visitor = self.db.scalar(
            select(VisitorProfile)
            .where(VisitorProfile.person_id == person.id)
            .execution_options(populate_existing=True)
        )
        if student is not None:
            if student.status != "active" or not student.student_number.strip():
                raise HousingProfileIncomplete("housing student profile is incomplete")
            profile_kind = "student"
        elif staff is not None:
            if not staff.staff_type.strip():
                raise HousingProfileIncomplete("housing staff profile is incomplete")
            profile_kind = "staff"
        elif visitor is not None:
            if not visitor.visit_reason.strip():
                raise HousingProfileIncomplete("housing visitor profile is incomplete")
            profile_kind = "visitor"
        else:
            raise HousingProfileIncomplete("housing resident profile is incomplete")

        readiness = GuardianReadinessPolicy(self.db).evaluate_one(
            person.id,
            business_date_at(reference_at),
        )
        if (
            profile_kind == "student"
            and readiness.age_status == "minor"
            and readiness.guardian_status == "pending"
        ):
            raise MinorGuardianRequired("minor housing resident requires a guardian")
        return profile_kind

    def _room_parents(
        self,
        room: HousingRoom,
    ) -> tuple[HousingFloor, HousingBuilding]:
        row = self.db.execute(
            select(HousingFloor, HousingBuilding)
            .join(HousingBuilding, HousingBuilding.id == HousingFloor.building_id)
            .where(
                HousingFloor.id == room.floor_id,
                HousingBuilding.id == room.building_id,
            )
            .execution_options(populate_existing=True)
        ).one_or_none()
        if row is None:
            raise HousingResourceNotFound("housing room parent not found")
        return row[0], row[1]

    def _require_allocatable_room(
        self,
        room: HousingRoom,
        *,
        allow_pending_verification: bool,
    ) -> tuple[HousingFloor, HousingBuilding]:
        floor, building = self._room_parents(room)
        if not building.is_active or not floor.is_active:
            raise HousingParentInactive("housing parent is inactive")
        if (
            room.room_type == "functional"
            or room.capacity <= 0
            or room.service_status != "available"
        ):
            raise HousingRoomUnavailable("housing room is unavailable")
        if room.verification_status != "verified" and not allow_pending_verification:
            raise HousingRoomNotVerified("housing room is not verified")
        return floor, building

    def _require_stay_semantics(
        self,
        *,
        profile_kind: str,
        stay_purpose: str,
        duration_mode: str,
        cohort_id: UUID | None,
        visit_reason: str | None,
        planned_check_in_at: datetime,
        planned_check_out_at: datetime | None,
    ) -> None:
        self._require_aware(planned_check_in_at)
        if planned_check_out_at is not None:
            self._require_aware(planned_check_out_at)
        if duration_mode == "fixed_term":
            if planned_check_out_at is None or planned_check_out_at <= planned_check_in_at:
                raise HousingServiceError("fixed-term housing stay has an invalid range")
        elif duration_mode == "long_term":
            if planned_check_out_at is not None:
                raise HousingServiceError("long-term housing stay cannot have an end")
        else:
            raise HousingServiceError("housing stay duration is invalid")

        if profile_kind == "visitor" and stay_purpose != "temporary":
            raise HousingProfileIncomplete("housing visitors require temporary stays")
        if stay_purpose == "temporary":
            if (
                duration_mode != "fixed_term"
                or planned_check_out_at is None
                or visit_reason is None
                or not visit_reason.strip()
                or cohort_id is not None
            ):
                raise HousingServiceError("temporary housing stay is invalid")
            return
        if visit_reason is not None or (visit_reason and visit_reason.strip()):
            raise HousingServiceError("non-temporary housing stay cannot have a visit reason")
        if stay_purpose == "permanent":
            if cohort_id is not None:
                raise HousingServiceError("permanent housing stay cannot have a cohort")
            return
        if stay_purpose != "camp" or cohort_id is None:
            raise HousingServiceError("housing stay purpose is invalid")
        if duration_mode != "fixed_term" or planned_check_out_at is None:
            raise HousingServiceError("camp housing stay must be fixed-term")
        cohort = self.db.scalar(
            select(Cohort)
            .where(Cohort.id == cohort_id, *self._selectable_cohort_predicates())
            .execution_options(populate_existing=True)
        )
        if cohort is None:
            raise HousingResourceNotFound("housing cohort not found")
        if (
            planned_check_in_at < cohort.default_check_in_at
            or planned_check_out_at > cohort.default_check_out_at
        ):
            raise HousingServiceError("camp housing stay is outside the cohort interval")

    @staticmethod
    def _stay_interval(
        stay: HousingStay | _StayConflictView,
        *,
        reference_at: datetime,
    ) -> tuple[datetime, datetime | None]:
        starts_at = stay.actual_check_in_at or stay.planned_check_in_at
        if stay.status == "checked_out":
            return starts_at, stay.actual_check_out_at
        if stay.status in _CURRENT_STATUSES and (
            stay.planned_check_out_at is None
            or stay.planned_check_out_at <= reference_at
        ):
            return starts_at, None
        return starts_at, stay.planned_check_out_at

    @staticmethod
    def _target_interval_end(
        planned_check_out_at: datetime | None,
        *,
        is_checked_in: bool,
        reference_at: datetime,
    ) -> datetime | None:
        if is_checked_in and (
            planned_check_out_at is None
            or planned_check_out_at <= reference_at
        ):
            return None
        return planned_check_out_at

    def _require_no_stay_conflicts(
        self,
        *,
        person: Person | _ResidentConflictProfile,
        room: HousingRoom | _TransferRoomView,
        target_start: datetime,
        target_end: datetime | None,
        reference_at: datetime,
        active_stays: Sequence[HousingStay | _StayConflictView],
        excluded_stay_id: UUID | None = None,
        resident_sexes: Mapping[UUID, str] | None = None,
    ) -> None:
        existing = [stay for stay in active_stays if stay.id != excluded_stay_id]
        for stay in existing:
            starts_at, ends_at = self._stay_interval(
                stay,
                reference_at=reference_at,
            )
            if stay.person_id == person.id and intervals_overlap(
                target_start,
                target_end,
                starts_at,
                ends_at,
            ):
                raise HousingPersonTimeConflict("housing person already has an overlapping stay")

        overlapping_room_stays = []
        for stay in existing:
            if stay.room_id != room.id:
                continue
            starts_at, ends_at = self._stay_interval(
                stay,
                reference_at=reference_at,
            )
            if intervals_overlap(target_start, target_end, starts_at, ends_at):
                overlapping_room_stays.append(stay)
        resident_ids = {stay.person_id for stay in overlapping_room_stays}
        known_resident_sexes = resident_sexes
        if known_resident_sexes is None:
            known_resident_sexes = (
                {
                    resident_id: sex
                    for resident_id, sex in self.db.execute(
                        select(Person.id, Person.sex).where(
                            Person.id.in_(sorted(resident_ids))
                        )
                    )
                }
                if resident_ids
                else {}
            )
        if any(
            known_resident_sexes.get(stay.person_id) != person.sex
            for stay in overlapping_room_stays
        ):
            raise HousingGenderConflict("housing room has an opposite-sex overlap")

        intervals = [
            self._stay_interval(stay, reference_at=reference_at)
            for stay in overlapping_room_stays
        ]
        intervals.append((target_start, target_end))
        if maximum_interval_occupancy(intervals) > room.capacity:
            raise HousingCapacityConflict("housing room capacity would be exceeded")

    def _locked_stay(self, stay_id: UUID) -> HousingStay:
        stay = self.db.scalar(
            select(HousingStay)
            .where(HousingStay.id == stay_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if stay is None:
            raise HousingResourceNotFound("housing stay not found")
        return stay

    def _create_locked_stay(
        self,
        *,
        room: HousingRoom,
        floor: HousingFloor,
        building: HousingBuilding,
        input: StayCreate,
        actor_user_id: UUID,
        occurred_at: datetime,
        active_stays: Sequence[HousingStay],
        person: Person,
        require_checked_in: bool,
    ) -> HousingStay:
        profile_kind = self._require_resident_ready(
            person,
            reference_at=input.planned_check_in_at,
        )
        self._require_stay_semantics(
            profile_kind=profile_kind,
            stay_purpose=input.stay_purpose,
            duration_mode=input.duration_mode,
            cohort_id=input.cohort_id,
            visit_reason=input.visit_reason,
            planned_check_in_at=input.planned_check_in_at,
            planned_check_out_at=input.planned_check_out_at,
        )
        is_checked_in = input.planned_check_in_at <= occurred_at
        if require_checked_in and not is_checked_in:
            raise HousingInvalidStayTransition(
                "housing backfill actual check-in cannot be in the future"
            )
        target_end = self._target_interval_end(
            input.planned_check_out_at,
            is_checked_in=is_checked_in,
            reference_at=occurred_at,
        )
        self._require_no_stay_conflicts(
            person=person,
            room=room,
            target_start=input.planned_check_in_at,
            target_end=target_end,
            reference_at=occurred_at,
            active_stays=active_stays,
        )
        stay = HousingStay(
            person_id=person.id,
            room_id=room.id,
            stay_purpose=input.stay_purpose,
            duration_mode=input.duration_mode,
            cohort_id=input.cohort_id,
            visit_reason=input.visit_reason,
            planned_check_in_at=input.planned_check_in_at,
            planned_check_out_at=input.planned_check_out_at,
            actual_check_in_at=input.planned_check_in_at if is_checked_in else None,
            status="checked_in" if is_checked_in else "planned",
            room_name_snapshot=room.room_number,
            floor_name_snapshot=floor.name,
            building_name_snapshot=building.name,
            notes=input.notes,
            created_by_user_id=actor_user_id,
            checked_in_by_user_id=actor_user_id if is_checked_in else None,
            version=1,
        )
        self.db.add(stay)
        self.db.flush()
        return stay

    def create_stay(
        self,
        room_id: UUID,
        input: StayCreate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingStay:
        self._require_aware(occurred_at)
        try:
            with write_transaction(self.db):
                locked = self._lock_stay_mutation(
                    person_ids=[input.person_id],
                    room_ids=[room_id],
                    authorize_room=authorize_room,
                    selectable_residents_only=True,
                )
                room = locked.rooms[room_id]
                floor, building = self._require_allocatable_room(
                    room,
                    allow_pending_verification=False,
                )
                stay = self._create_locked_stay(
                    room=room,
                    floor=floor,
                    building=building,
                    input=input,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                    active_stays=locked.active_stays,
                    person=locked.people[input.person_id],
                    require_checked_in=False,
                )
            return stay
        except IntegrityError as exc:
            self._raise_mapped_stay_integrity(exc)

    def backfill_existing_stay(
        self,
        room_id: UUID,
        input: StayCreate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room_and_stay: RoomAuthorizer,
    ) -> HousingStay:
        self._require_aware(occurred_at)
        try:
            with write_transaction(self.db):
                locked = self._lock_stay_mutation(
                    person_ids=[input.person_id],
                    room_ids=[room_id],
                    authorize_room=authorize_room_and_stay,
                    selectable_residents_only=True,
                )
                room = locked.rooms[room_id]
                floor, building = self._require_allocatable_room(
                    room,
                    allow_pending_verification=True,
                )
                stay = self._create_locked_stay(
                    room=room,
                    floor=floor,
                    building=building,
                    input=input,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                    active_stays=locked.active_stays,
                    person=locked.people[input.person_id],
                    require_checked_in=True,
                )
            return stay
        except IntegrityError as exc:
            self._raise_mapped_stay_integrity(exc)

    def update_reservation(
        self,
        stay_id: UUID,
        input: StayUpdate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingStay:
        del actor_user_id
        self._require_aware(occurred_at)
        try:
            with write_transaction(self.db):
                preliminary = self.db.get(HousingStay, stay_id)
                if preliminary is None:
                    raise HousingResourceNotFound("housing stay not found")
                locked = self._lock_stay_mutation(
                    person_ids=[preliminary.person_id],
                    room_ids=[preliminary.room_id],
                    authorize_room=authorize_room,
                )
                stay = self._locked_stay(stay_id)
                if stay.status != "planned":
                    raise HousingInvalidStayTransition("only planned housing stays can be edited")
                if stay.version != input.version:
                    raise HousingStaleVersion("housing stay version is stale")
                if input.planned_check_in_at <= occurred_at:
                    raise HousingInvalidStayTransition(
                        "housing reservation must remain in the future"
                    )
                room = locked.rooms[stay.room_id]
                self._require_allocatable_room(
                    room,
                    allow_pending_verification=False,
                )
                person = locked.people[stay.person_id]
                profile_kind = self._require_resident_ready(
                    person,
                    reference_at=input.planned_check_in_at,
                )
                self._require_stay_semantics(
                    profile_kind=profile_kind,
                    stay_purpose=stay.stay_purpose,
                    duration_mode=stay.duration_mode,
                    cohort_id=stay.cohort_id,
                    visit_reason=stay.visit_reason,
                    planned_check_in_at=input.planned_check_in_at,
                    planned_check_out_at=input.planned_check_out_at,
                )
                self._require_no_stay_conflicts(
                    person=person,
                    room=room,
                    target_start=input.planned_check_in_at,
                    target_end=input.planned_check_out_at,
                    reference_at=occurred_at,
                    active_stays=locked.active_stays,
                    excluded_stay_id=stay.id,
                )
                stay.planned_check_in_at = input.planned_check_in_at
                stay.planned_check_out_at = input.planned_check_out_at
                stay.notes = input.notes
                stay.version += 1
                self.db.flush()
            return stay
        except IntegrityError as exc:
            self._raise_mapped_stay_integrity(exc)

    def confirm_check_in(
        self,
        stay_id: UUID,
        input: ConfirmCheckInRequest,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingStay:
        self._require_aware(occurred_at)
        self._require_aware(input.actual_check_in_at)
        try:
            with write_transaction(self.db):
                preliminary = self.db.get(HousingStay, stay_id)
                if preliminary is None:
                    raise HousingResourceNotFound("housing stay not found")
                locked = self._lock_stay_mutation(
                    person_ids=[preliminary.person_id],
                    room_ids=[preliminary.room_id],
                    authorize_room=authorize_room,
                )
                stay = self._locked_stay(stay_id)
                if stay.status != "planned":
                    raise HousingInvalidStayTransition(
                        "only planned housing stays can be checked in"
                    )
                if stay.version != input.version:
                    raise HousingStaleVersion("housing stay version is stale")
                if input.actual_check_in_at > occurred_at:
                    raise HousingInvalidStayTransition(
                        "housing actual check-in cannot be in the future"
                    )
                if (
                    stay.planned_check_out_at is not None
                    and input.actual_check_in_at >= stay.planned_check_out_at
                ):
                    raise HousingInvalidStayTransition(
                        "housing actual check-in must precede planned checkout"
                    )
                room = locked.rooms[stay.room_id]
                self._require_allocatable_room(
                    room,
                    allow_pending_verification=False,
                )
                person = locked.people[stay.person_id]
                profile_kind = self._require_resident_ready(
                    person,
                    reference_at=input.actual_check_in_at,
                )
                self._require_stay_semantics(
                    profile_kind=profile_kind,
                    stay_purpose=stay.stay_purpose,
                    duration_mode=stay.duration_mode,
                    cohort_id=stay.cohort_id,
                    visit_reason=stay.visit_reason,
                    planned_check_in_at=stay.planned_check_in_at,
                    planned_check_out_at=stay.planned_check_out_at,
                )
                self._require_no_stay_conflicts(
                    person=person,
                    room=room,
                    target_start=input.actual_check_in_at,
                    target_end=self._target_interval_end(
                        stay.planned_check_out_at,
                        is_checked_in=True,
                        reference_at=occurred_at,
                    ),
                    reference_at=occurred_at,
                    active_stays=locked.active_stays,
                    excluded_stay_id=stay.id,
                )
                stay.actual_check_in_at = input.actual_check_in_at
                stay.checked_in_by_user_id = actor_user_id
                stay.status = "checked_in"
                stay.version += 1
                self.db.flush()
            return stay
        except IntegrityError as exc:
            self._raise_mapped_stay_integrity(exc)

    def cancel_reservation(
        self,
        stay_id: UUID,
        input: CancelReservationRequest,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingStay:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            preliminary = self.db.get(HousingStay, stay_id)
            if preliminary is None:
                raise HousingResourceNotFound("housing stay not found")
            self._lock_stay_mutation(
                person_ids=[preliminary.person_id],
                room_ids=[preliminary.room_id],
                authorize_room=authorize_room,
            )
            stay = self._locked_stay(stay_id)
            if stay.status != "planned":
                raise HousingInvalidStayTransition("only planned housing stays can be cancelled")
            if stay.version != input.version:
                raise HousingStaleVersion("housing stay version is stale")
            stay.status = "cancelled"
            stay.cancellation_reason = input.reason
            stay.cancelled_by_user_id = actor_user_id
            stay.version += 1
            self.db.flush()
        return stay

    def request_checkout(
        self,
        stay_id: UUID,
        input: CheckoutRequest,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingStay:
        self._require_aware(occurred_at)
        self._require_aware(input.actual_check_out_at)
        with write_transaction(self.db):
            preliminary = self.db.get(HousingStay, stay_id)
            if preliminary is None:
                raise HousingResourceNotFound("housing stay not found")
            self._lock_stay_mutation(
                person_ids=[preliminary.person_id],
                room_ids=[preliminary.room_id],
                authorize_room=authorize_room,
            )
            stay = self._locked_stay(stay_id)
            if stay.status not in {"checked_in", "checkout_pending"}:
                raise HousingInvalidStayTransition(
                    "only current housing stays can request checkout"
                )
            if stay.version != input.version:
                raise HousingStaleVersion("housing stay version is stale")
            if any(
                value is not None
                for value in (
                    stay.checkout_requested_actual_at,
                    stay.checkout_requested_at,
                    stay.checkout_requested_by_user_id,
                )
            ):
                raise HousingInvalidStayTransition("housing checkout is already requested")
            if stay.actual_check_in_at is None:
                raise HousingInvalidStayTransition("housing stay is not checked in")
            if (
                input.actual_check_out_at > occurred_at
                or input.actual_check_out_at <= stay.actual_check_in_at
            ):
                raise HousingInvalidStayTransition(
                    "housing actual checkout must follow check-in and not be in the future"
                )
            stay.status = "checkout_pending"
            stay.checkout_requested_actual_at = input.actual_check_out_at
            stay.checkout_requested_at = occurred_at
            stay.checkout_requested_by_user_id = actor_user_id
            stay.version += 1
            self.db.flush()
        return stay

    def confirm_checkout(
        self,
        stay_id: UUID,
        input: CheckoutConfirmation,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_room: RoomAuthorizer,
    ) -> HousingStay:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            preliminary = self.db.get(HousingStay, stay_id)
            if preliminary is None:
                raise HousingResourceNotFound("housing stay not found")
            self._lock_stay_mutation(
                person_ids=[preliminary.person_id],
                room_ids=[preliminary.room_id],
                authorize_room=authorize_room,
            )
            stay = self._locked_stay(stay_id)
            if stay.status != "checkout_pending":
                raise HousingInvalidStayTransition(
                    "only pending housing stays can confirm checkout"
                )
            if stay.version != input.version:
                raise HousingStaleVersion("housing stay version is stale")
            if (
                stay.checkout_requested_actual_at is None
                or stay.checkout_requested_at is None
                or stay.checkout_requested_by_user_id is None
            ):
                raise HousingCheckoutConfirmationRequired(
                    "housing checkout requires a prior manual request"
                )
            stay.status = "checked_out"
            stay.actual_check_out_at = stay.checkout_requested_actual_at
            stay.checked_out_by_user_id = actor_user_id
            stay.version += 1
            self.db.flush()
        return stay

    def transfer(
        self,
        stay_id: UUID,
        input: TransferStayRequest,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        authorize_source: RoomAuthorizer,
        authorize_target: RoomAuthorizer,
    ) -> tuple[HousingStay, HousingStay, HousingTransfer]:
        self._require_aware(occurred_at)
        self._require_aware(input.transferred_at)
        try:
            with write_transaction(self.db):
                preliminary = self.db.get(HousingStay, stay_id)
                if preliminary is None:
                    raise HousingResourceNotFound("housing stay not found")
                people = self.lock_people([preliminary.person_id])
                self.db.refresh(preliminary)
                rooms = self.lock_rooms(
                    [preliminary.room_id, input.target_room_id]
                )
                target_end = (
                    preliminary.planned_check_out_at
                    if preliminary.duration_mode == "fixed_term"
                    else None
                )
                active_stays = self.lock_transfer_stays(
                    person_id=preliminary.person_id,
                    room_ids=[preliminary.room_id, input.target_room_id],
                    target_room_id=input.target_room_id,
                    target_start=input.transferred_at,
                    target_end=target_end,
                )
                people_by_id = {person.id: person for person in people}
                rooms_by_id = {room.id: room for room in rooms}
                source_room = rooms_by_id.get(preliminary.room_id)
                target_room = rooms_by_id.get(input.target_room_id)
                if source_room is None or target_room is None:
                    raise HousingResourceNotFound("housing room not found")
                person = people_by_id.get(preliminary.person_id)
                if person is None:
                    raise HousingProfileIncomplete(
                        "housing resident profile is incomplete"
                    )
                authorize_source(source_room)
                authorize_target(target_room)
                source = self._locked_stay(stay_id)
                if source.status not in _CURRENT_STATUSES:
                    raise HousingInvalidStayTransition(
                        "only current housing stays can be transferred"
                    )
                if source.version != input.version:
                    raise HousingStaleVersion("housing stay version is stale")
                if source.room_id == input.target_room_id:
                    raise HousingInvalidStayTransition(
                        "housing transfer target must differ from source"
                    )
                if source.actual_check_in_at is None:
                    raise HousingInvalidStayTransition(
                        "housing transfer source is not checked in"
                    )
                if (
                    input.transferred_at > occurred_at
                    or input.transferred_at <= source.actual_check_in_at
                ):
                    raise HousingInvalidStayTransition(
                        "housing transfer time must follow check-in and not be in the future"
                    )
                if (
                    source.duration_mode == "fixed_term"
                    and (
                        source.planned_check_out_at is None
                        or occurred_at >= source.planned_check_out_at
                        or input.transferred_at >= source.planned_check_out_at
                    )
                ):
                    raise HousingInvalidStayTransition(
                        "housing fixed-term stay has no remaining transfer interval"
                    )
                if any(
                    value is not None
                    for value in (
                        source.checkout_requested_actual_at,
                        source.checkout_requested_at,
                        source.checkout_requested_by_user_id,
                    )
                ):
                    raise HousingInvalidStayTransition(
                        "manually requested housing checkout cannot be transferred"
                    )
                target_floor, target_building = self._require_allocatable_room(
                    target_room,
                    allow_pending_verification=False,
                )
                self._require_resident_ready(
                    person,
                    reference_at=input.transferred_at,
                )
                self._require_no_stay_conflicts(
                    person=person,
                    room=target_room,
                    target_start=input.transferred_at,
                    target_end=target_end,
                    reference_at=occurred_at,
                    active_stays=active_stays,
                    excluded_stay_id=source.id,
                )

                destination_status = source.status
                source.status = "checked_out"
                source.actual_check_out_at = input.transferred_at
                source.checked_out_by_user_id = actor_user_id
                source.version += 1
                self.db.flush()

                destination = HousingStay(
                    person_id=source.person_id,
                    room_id=target_room.id,
                    stay_purpose=source.stay_purpose,
                    duration_mode=source.duration_mode,
                    cohort_id=source.cohort_id,
                    visit_reason=source.visit_reason,
                    planned_check_in_at=input.transferred_at,
                    planned_check_out_at=source.planned_check_out_at,
                    actual_check_in_at=input.transferred_at,
                    status=destination_status,
                    room_name_snapshot=target_room.room_number,
                    floor_name_snapshot=target_floor.name,
                    building_name_snapshot=target_building.name,
                    notes=source.notes,
                    checkout_requested_actual_at=None,
                    checkout_requested_at=None,
                    checkout_requested_by_user_id=None,
                    created_by_user_id=actor_user_id,
                    checked_in_by_user_id=actor_user_id,
                    version=1,
                )
                self.db.add(destination)
                self.db.flush()
                transfer = HousingTransfer(
                    person_id=source.person_id,
                    from_stay_id=source.id,
                    to_stay_id=destination.id,
                    transferred_at=input.transferred_at,
                    reason=input.reason,
                    transferred_by_user_id=actor_user_id,
                )
                self.db.add(transfer)
                self.db.flush()
            return source, destination, transfer
        except DBAPIError as exc:
            self._raise_mapped_transfer_persistence(exc)

    def list_transfer_options(
        self,
        stay_id: UUID,
        *,
        occurred_at: datetime,
        access: HousingAccessService,
    ) -> TransferRoomOptionsResponse:
        self._require_aware(occurred_at)
        source_row = self.db.execute(
            select(
                HousingStay.id,
                HousingStay.person_id,
                HousingStay.room_id,
                HousingStay.status,
                HousingStay.checkout_requested_actual_at,
                HousingStay.checkout_requested_at,
                HousingStay.checkout_requested_by_user_id,
                HousingStay.duration_mode,
                HousingStay.planned_check_out_at,
            )
            .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
            .where(
                HousingStay.id == stay_id,
                access.room_permissions_criteria(
                    "housing.read",
                    "housing.transfers.manage",
                ),
            )
        ).one_or_none()
        if source_row is None:
            raise HousingResourceNotFound("housing stay not found")
        (
            source_id,
            source_person_id,
            source_room_id,
            source_status,
            checkout_requested_actual_at,
            checkout_requested_at,
            checkout_requested_by_user_id,
            source_duration_mode,
            source_planned_check_out_at,
        ) = source_row
        source = _TransferSourceView(
            id=source_id,
            person_id=source_person_id,
            room_id=source_room_id,
            status=source_status,
            checkout_requested_actual_at=checkout_requested_actual_at,
            checkout_requested_at=checkout_requested_at,
            checkout_requested_by_user_id=checkout_requested_by_user_id,
            duration_mode=source_duration_mode,
            planned_check_out_at=source_planned_check_out_at,
        )
        if source.status not in _CURRENT_STATUSES:
            raise HousingInvalidStayTransition(
                "only current housing stays have transfer options"
            )
        if any(
            value is not None
            for value in (
                source.checkout_requested_actual_at,
                source.checkout_requested_at,
                source.checkout_requested_by_user_id,
            )
        ):
            raise HousingInvalidStayTransition(
                "manually requested housing checkout cannot be transferred"
            )
        if (
            source.duration_mode == "fixed_term"
            and (
                source.planned_check_out_at is None
                or occurred_at >= source.planned_check_out_at
            )
        ):
            raise HousingInvalidStayTransition(
                "housing fixed-term stay has no remaining transfer interval"
            )
        source_sex = self.db.scalar(
            select(Person.sex).where(Person.id == source.person_id)
        )
        if source_sex is None:
            raise HousingProfileIncomplete("housing resident profile is incomplete")
        person = _ResidentConflictProfile(id=source.person_id, sex=source_sex)

        room_rows = self.db.execute(
            select(
                HousingRoom.id,
                HousingRoom.capacity,
                HousingBuilding.name,
                HousingFloor.name,
                HousingRoom.room_number,
            )
            .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .where(
                HousingRoom.id != source.room_id,
                access.room_permissions_criteria(
                    "housing.read",
                    "housing.transfers.manage",
                ),
                HousingRoom.room_type != "functional",
                HousingRoom.capacity > 0,
                HousingRoom.verification_status == "verified",
                HousingRoom.service_status == "available",
                HousingFloor.is_active.is_(True),
                HousingBuilding.is_active.is_(True),
            )
            .order_by(
                HousingBuilding.display_order,
                HousingBuilding.name,
                HousingFloor.display_order,
                HousingFloor.number,
                HousingRoom.room_number,
                HousingRoom.id,
            )
            .limit(_TRANSFER_OPTIONS_LIMIT)
        ).all()
        rooms = [
            _TransferRoomView(
                id=room_id,
                capacity=capacity,
                building_label=building_label,
                floor_label=floor_label,
                room_label=room_label,
            )
            for room_id, capacity, building_label, floor_label, room_label in room_rows
        ]
        room_ids = [room.id for room in rooms]
        target_end = (
            source.planned_check_out_at
            if source.duration_mode == "fixed_term"
            else None
        )
        interval_filters = [
            HousingStay.room_id.in_(room_ids),
            or_(
                HousingStay.status.in_(_ACTIVE_STAY_STATUSES),
                and_(
                    HousingStay.status == "checked_out",
                    HousingStay.actual_check_out_at > occurred_at,
                    *(
                        (HousingStay.actual_check_in_at < target_end,)
                        if target_end is not None
                        else ()
                    ),
                ),
            ),
        ]
        stay_rows = (
            self.db.execute(
                select(
                    HousingStay.id,
                    HousingStay.person_id,
                    HousingStay.room_id,
                    HousingStay.planned_check_in_at,
                    HousingStay.planned_check_out_at,
                    HousingStay.actual_check_in_at,
                    HousingStay.actual_check_out_at,
                    HousingStay.status,
                    Person.sex,
                )
                .join(Person, Person.id == HousingStay.person_id)
                .where(*interval_filters)
                .order_by(HousingStay.id)
            ).all()
            if room_ids
            else []
        )
        stays_by_room: dict[UUID, list[_StayConflictView]] = {
            room_id: [] for room_id in room_ids
        }
        resident_sexes: dict[UUID, str] = {}
        for (
            row_id,
            person_id,
            room_id,
            planned_check_in_at,
            planned_check_out_at,
            actual_check_in_at,
            actual_check_out_at,
            status,
            sex,
        ) in stay_rows:
            stays_by_room[room_id].append(
                _StayConflictView(
                    id=row_id,
                    person_id=person_id,
                    room_id=room_id,
                    planned_check_in_at=planned_check_in_at,
                    planned_check_out_at=planned_check_out_at,
                    actual_check_in_at=actual_check_in_at,
                    actual_check_out_at=actual_check_out_at,
                    status=status,
                )
            )
            resident_sexes[person_id] = sex

        items: list[TransferRoomOption] = []
        for room in rooms:
            room_stays = stays_by_room[room.id]
            compatible = True
            try:
                self._require_no_stay_conflicts(
                    person=person,
                    room=room,
                    target_start=occurred_at,
                    target_end=target_end,
                    reference_at=occurred_at,
                    active_stays=room_stays,
                    excluded_stay_id=source.id,
                    resident_sexes=resident_sexes,
                )
            except (
                HousingCapacityConflict,
                HousingGenderConflict,
                HousingPersonTimeConflict,
            ):
                compatible = False
            current_stays = [
                stay for stay in room_stays if stay.status in _CURRENT_STATUSES
            ]
            current_gender = (
                resident_sexes[current_stays[0].person_id] if current_stays else None
            )
            items.append(
                TransferRoomOption(
                    room_id=room.id,
                    building_label=room.building_label,
                    floor_label=room.floor_label,
                    room_label=room.room_label,
                    capacity=room.capacity,
                    current_occupants=len(current_stays),
                    current_gender=cast(Sex | None, current_gender),
                    advisory_compatible=compatible,
                    status_text="可调入" if compatible else "容量或性别冲突",
                )
            )
        return TransferRoomOptionsResponse(
            access_revision=access.access_revision,
            items=items,
        )

    def list_resident_options(
        self,
        room_id: UUID,
        query: str,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        authorize_room: RoomAuthorizer,
        reference_date: date,
    ) -> list[ResidentOption]:
        self._authorized_room(room_id, authorize_room)
        normalized_query = normalize_name_query(query)
        try:
            normalized_phone = normalize_phone(normalized_query)
        except IdentityValidationError:
            normalized_phone = None
        if normalized_phone is None and not normalized_query:
            raise HousingServiceError("housing resident query is invalid")
        initial_query = normalized_phone is None and is_name_initial_query(normalized_query)

        candidate_query = (
            select(
                Person,
                StudentProfile.person_id,
                StaffProfile.staff_type,
                VisitorProfile.person_id,
            )
            .join(UserAccount, UserAccount.person_id == Person.id)
            .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
            .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
            .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
            .where(
                *self._selectable_resident_predicates(),
                UserAccount.email_normalized.is_not(None),
                UserAccount.email_normalized != "",
            )
        )
        if normalized_phone is not None:
            candidate_query = candidate_query.where(
                Person.phone_digest == lookup.digest(normalized_phone)
            )
        elif not initial_query:
            escaped_query = (
                normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            candidate_query = candidate_query.where(
                Person.full_name.ilike(f"%{escaped_query}%", escape="\\")
            )
        ordered_query = candidate_query.order_by(Person.full_name, Person.id)
        if not initial_query:
            ordered_query = ordered_query.limit(20)
        rows = list(self.db.execute(ordered_query).all())
        if initial_query:
            rows = [
                row for row in rows if name_matches_query(row[0].full_name, normalized_query)
            ][:20]
        people = [person for person, *_ in rows]
        readiness = GuardianReadinessPolicy(self.db).evaluate_many(
            people,
            student_ids={student_id for _, student_id, _, _ in rows if student_id},
            reference_date=reference_date,
        )
        try:
            options = [
                ResidentOption(
                    person_id=person.id,
                    full_name=person.full_name,
                    person_type=self._person_type(
                        student_id,
                        staff_type,
                        visitor_id,
                    ),
                    sex=cast(Sex, person.sex),
                    masked_phone=(
                        None
                        if person.phone_ciphertext is None
                        else self._masked_phone(cipher, person.phone_ciphertext)
                    ),
                    account_ready=True,
                    profile_ready=True,
                    guardian_ready=readiness[person.id].guardian_status != "pending",
                )
                for person, student_id, staff_type, visitor_id in rows
            ]
        except (EncryptedFieldError, IdentityValidationError):
            raise HousingSensitiveDataUnavailable(
                "housing resident option data is unavailable"
            ) from None
        return options

    @staticmethod
    def _masked_phone(cipher: EnvelopeCipher, phone_ciphertext: str) -> str:
        normalized_phone = normalize_phone(cipher.decrypt(phone_ciphertext, field_name="phone"))
        return f"{normalized_phone[:3]}****{normalized_phone[-4:]}"

    def list_cohort_options(
        self,
        room_id: UUID,
        *,
        authorize_room: RoomAuthorizer,
    ) -> list[CohortStayOption]:
        self._authorized_room(room_id, authorize_room)
        cohorts = self.db.scalars(
            select(Cohort)
            .where(*self._selectable_cohort_predicates())
            .order_by(Cohort.default_check_in_at, Cohort.name, Cohort.id)
        )
        return [
            CohortStayOption(
                id=cohort.id,
                name=cohort.name,
                status=cast(CohortStatus, cohort.status),
                default_check_in_at=cohort.default_check_in_at,
                default_check_out_at=cohort.default_check_out_at,
            )
            for cohort in cohorts
        ]

    @classmethod
    def _stay_summary(
        cls,
        stay: HousingStay,
        person_name: str,
        sex: str,
        student_person_id: UUID | None,
        staff_type: str | None,
        visitor_person_id: UUID | None,
    ) -> StaySummary:
        return StaySummary(
            **stay.__dict__,
            person_name=person_name,
            person_type=cls._person_type(student_person_id, staff_type, visitor_person_id),
            sex=cast(Sex, sex),
            allowed_actions=StayAllowedActions(
                edit_reservation=False,
                confirm_check_in=False,
                cancel_reservation=False,
                transfer=False,
                request_checkout=False,
                confirm_checkout=False,
            ),
        )

    def room_projections(
        self,
        room_ids: Sequence[UUID],
        as_of: datetime,
    ) -> dict[UUID, RoomOccupancyProjection]:
        unique_room_ids = tuple(dict.fromkeys(room_ids))
        if not unique_room_ids:
            return {}
        stay_rows = self.db.execute(
            select(
                HousingStay,
                Person.full_name,
                Person.sex,
                StudentProfile.person_id,
                StaffProfile.staff_type,
                VisitorProfile.person_id,
            )
            .join(Person, Person.id == HousingStay.person_id)
            .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
            .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
            .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
            .where(HousingStay.room_id.in_(unique_room_ids))
            .where(
                (HousingStay.status.in_(_CURRENT_STATUSES))
                | ((HousingStay.status == "planned") & (HousingStay.planned_check_in_at > as_of))
            )
            .order_by(HousingStay.room_id, HousingStay.planned_check_in_at, HousingStay.id)
        ).all()
        current_by_room: dict[UUID, list[StaySummary]] = {
            room_id: [] for room_id in unique_room_ids
        }
        future_by_room: dict[UUID, list[StaySummary]] = {room_id: [] for room_id in unique_room_ids}
        for (
            stay,
            person_name,
            sex,
            student_person_id,
            staff_type,
            visitor_person_id,
        ) in stay_rows:
            summary = self._stay_summary(
                stay,
                person_name,
                sex,
                student_person_id,
                staff_type,
                visitor_person_id,
            )
            target = current_by_room if stay.status in _CURRENT_STATUSES else future_by_room
            target[stay.room_id].append(summary)
        issues_by_room = {
            issue.room_id: RoomIssueSummary.model_validate(issue)
            for issue in self.db.scalars(
                select(HousingRoomIssue)
                .where(HousingRoomIssue.room_id.in_(unique_room_ids))
                .where(HousingRoomIssue.status == "open")
                .order_by(HousingRoomIssue.room_id, HousingRoomIssue.reported_at)
            )
        }
        return {
            room_id: RoomOccupancyProjection(
                current_stays=tuple(current_by_room[room_id]),
                future_reservation_count=len(future_by_room[room_id]),
                open_issue=issues_by_room.get(room_id),
                future_stays=tuple(future_by_room[room_id]),
            )
            for room_id in unique_room_ids
        }
