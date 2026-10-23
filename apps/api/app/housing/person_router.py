"""Private exact-person reads; all writes remain in the existing housing lifecycle."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.time import business_date_at
from app.housing.access import HousingAccessService
from app.housing.api_support import (
    PrivateHousingRoute,
    _not_found,
    _permission_denied,
    _set_sensitive_read_result_count,
)
from app.housing.dependencies import get_housing_cipher, get_housing_lookup
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.housing.occupancy_router import _sensitive_read_context, _stay_actions
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.person_schemas import (
    PersonCheckInOptions,
    PersonCheckInRoom,
    PersonHousingActions,
    PersonHousingResponse,
    PersonHousingStay,
)
from app.housing.resident_readiness import HousingResidentReadinessPolicy
from app.housing.schemas import ResidentOption, Sex, StaySummary
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.identity.router import _person_tokens, _require_permission
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.queries import ManagedPeopleQuery
from app.security.encryption import EnvelopeCipher, LookupDigest

router = APIRouter(prefix="/people", tags=["housing"], route_class=PrivateHousingRoute)


def _subject(db: Session, actor: AuthContext, person_id: UUID) -> Person:
    _require_permission(actor, db, "people.read", _person_tokens(actor, db, person_id))
    person = db.scalar(
        select(Person)
        .where(Person.id == person_id, Person.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    if person is None:
        _not_found()
    return person


def _owner_scope(db: Session, actor: AuthContext, person_id: UUID, now: datetime) -> list[Any]:
    state = PersonnelIntakeAccess(db).snapshot(actor)
    if not state.can_enter_managed_people():
        return []
    affiliations = ManagedPeopleQuery._affiliations(state, now)
    return list(db.execute(select(affiliations).where(affiliations.c.person_id == person_id)))


def _revision(access: HousingAccessService, person_id: UUID, owners: list[Any]) -> str:
    # Include the separate people/owner permission sources as well as room grants.
    rules = [
        (source, rule.permission, rule.effect, rule.scope_kind, str(rule.scope_id or ""))
        for source, collection in (
            ("role", access.context.role_rules),
            ("override", access.context.overrides),
        )
        for rule in collection
        if rule.permission in {"people.read", "personnel_intake.housing.read"}
    ]
    payload = [
        access.access_revision,
        str(person_id),
        sorted(rules),
        sorted(str(row.id) for row in owners),
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def _rooms(
    db: Session, access: HousingAccessService, person: Person, *, limit: int | None = None
) -> list[Any]:
    if person.sex is None:
        return []
    # This is an advisory current-availability chooser, not a reservation lock.
    # Only aggregate compatibility is read: never load or return other residents.
    current = ("checked_in", "checkout_pending")
    occupants = (
        select(func.count(HousingStay.id))
        .where(HousingStay.room_id == HousingRoom.id, HousingStay.status.in_(current))
        .correlate(HousingRoom)
        .scalar_subquery()
    )
    opposite_sex = (
        select(HousingStay.id)
        .join(Person, Person.id == HousingStay.person_id)
        .where(
            HousingStay.room_id == HousingRoom.id,
            HousingStay.status.in_(current),
            or_(Person.sex.is_(None), Person.sex != person.sex),
        )
        .correlate(HousingRoom)
        .exists()
    )
    return list(
        db.execute(
            select(HousingRoom, HousingBuilding.name, HousingFloor.name)
            .join(HousingBuilding, HousingBuilding.id == HousingRoom.building_id)
            .join(HousingFloor, HousingFloor.id == HousingRoom.floor_id)
            .where(
                access.room_permissions_criteria("housing.read", "housing.stays.manage"),
                HousingBuilding.is_active,
                HousingFloor.is_active,
                HousingRoom.verification_status == "verified",
                HousingRoom.service_status == "available",
                HousingRoom.room_type != "functional",
                HousingRoom.capacity > 0,
                occupants < HousingRoom.capacity,
                ~opposite_sex,
            )
            .order_by(
                HousingBuilding.name, HousingFloor.number, HousingRoom.room_number, HousingRoom.id
            )
            .limit(limit)
        )
    )


def _resident(
    db: Session, person: Person, now: datetime, cipher: EnvelopeCipher, lookup: LookupDigest
) -> ResidentOption | None:
    resident_sex: Sex
    if person.sex == "male":
        resident_sex = "male"
    elif person.sex == "female":
        resident_sex = "female"
    else:
        return None
    service = HousingOccupancyService(db)
    row = db.execute(
        select(
            StudentProfile.person_id,
            StaffProfile.staff_type,
            VisitorProfile.person_id,
            service.assisted_kind_expression(),
            UserAccount.status,
        )
        .select_from(Person)
        .outerjoin(UserAccount, UserAccount.person_id == Person.id)
        .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
        .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
        .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
        .where(Person.id == person.id, *service._selectable_resident_predicates())
    ).one_or_none()
    if row is None:
        return None
    readiness = HousingResidentReadinessPolicy(db, cipher=cipher, lookup=lookup).evaluate_many(
        [person.id], reference_date=business_date_at(now)
    )[person.id]
    if not readiness.housing_ready:
        return None
    student_id, staff_type, visitor_id, assisted_kind, account_status = row
    return ResidentOption(
        person_id=person.id,
        full_name=person.full_name,
        person_type=service._person_type(student_id, staff_type, visitor_id, assisted_kind),
        sex=resident_sex,
        housing_ready=True,
        account_status=account_status or "not_activated",
        account_ready=readiness.account_ready,
        profile_ready=readiness.profile_ready,
        guardian_ready=readiness.guardian_ready,
    )


def _stays(db: Session, person_id: UUID) -> list[tuple[HousingStay, HousingRoom]]:
    return list(
        db.execute(
            select(HousingStay, HousingRoom)
            .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
            .where(
                HousingStay.person_id == person_id,
                HousingStay.status.in_(("checked_in", "checkout_pending", "planned")),
            )
            .order_by(HousingStay.planned_check_in_at, HousingStay.id)
        ).tuples()
    )


def _narrow(stay: HousingStay | None) -> PersonHousingStay | None:
    if stay is None:
        return None
    return PersonHousingStay(
        building_name=stay.building_name_snapshot,
        floor_name=stay.floor_name_snapshot,
        room_name=stay.room_name_snapshot,
        stay_purpose=stay.stay_purpose,
        duration_mode=stay.duration_mode,
        planned_check_in_at=stay.planned_check_in_at,
        planned_check_out_at=stay.planned_check_out_at,
        actual_check_in_at=stay.actual_check_in_at,
        status=stay.status,
    )


def _management_summary(
    db: Session,
    person: Person,
    stay: HousingStay,
    room: HousingRoom,
    access: HousingAccessService,
    now: datetime,
) -> StaySummary | None:
    if not access.can_read_room(room) or not any(
        (
            access.can_manage_stays(room),
            access.can_manage_transfers(room),
            access.can_manage_checkouts(room),
        )
    ):
        return None
    row = db.execute(
        select(
            StudentProfile.person_id,
            StaffProfile.staff_type,
            VisitorProfile.person_id,
            HousingOccupancyService.assisted_kind_expression(),
        )
        .select_from(Person)
        .outerjoin(StudentProfile, StudentProfile.person_id == Person.id)
        .outerjoin(StaffProfile, StaffProfile.person_id == Person.id)
        .outerjoin(VisitorProfile, VisitorProfile.person_id == Person.id)
        .where(Person.id == person.id)
    ).one()
    summary = HousingOccupancyService._stay_summary(stay, person.full_name, person.sex, *row)
    return summary.model_copy(update={"allowed_actions": _stay_actions(access, room, summary, now)})


@router.get("/{person_id}/housing", response_model=PersonHousingResponse)
def person_housing(
    person_id: UUID,
    request: Request,
    actor: Annotated[AuthContext, Depends(_sensitive_read_context("housing.person.read"))],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> PersonHousingResponse:
    now = datetime.now(UTC)
    person = _subject(db, actor, person_id)
    access = HousingAccessService(db, actor.account.id, now)
    # Owner summaries are a distinct readonly boundary. Mixed-role managers must
    # retain their actual room scope, including explicit room-read denies.
    owners = _owner_scope(db, actor, person_id, now) if access.owner_only else []
    stays = _stays(db, person_id)
    visible = [(stay, room) for stay, room in stays if owners or access.can_read_room(room)]
    if not visible and (
        stays
        or not owners
        and db.scalar(
            select(HousingRoom.id).where(access.room_permission_criteria("housing.read")).limit(1)
        )
        is None
    ):
        _not_found()
    currents = [(stay, room) for stay, room in visible if stay.status != "planned"]
    if len(currents) > 1:
        # Never silently choose among inconsistent current records.
        from app.core.errors import ProblemError

        raise ProblemError(
            status=409, code="housing_current_inconsistent", detail="当前住宿记录不一致"
        )
    current = currents[0] if currents else None
    planned = next((stay for stay, _ in visible if stay.status == "planned"), None)
    can_create = bool(
        not stays
        and not access.owner_only
        and _rooms(db, access, person, limit=1)
        and _resident(db, person, now, cipher, lookup)
    )
    _set_sensitive_read_result_count(request, len(visible))
    return PersonHousingResponse(
        access_revision=_revision(access, person_id, owners),
        housing_access_revision=access.access_revision,
        current=_narrow(current[0] if current else None),
        planned=_narrow(planned),
        management_stay=_management_summary(db, person, *current, access, now) if current else None,
        allowed_actions=PersonHousingActions(create_stay=can_create),
    )


@router.get("/{person_id}/housing/check-in-options", response_model=PersonCheckInOptions)
def person_check_in_options(
    person_id: UUID,
    request: Request,
    actor: Annotated[
        AuthContext, Depends(_sensitive_read_context("housing.person.check_in_options.read"))
    ],
    db: Annotated[Session, Depends(get_db)],
    cipher: Annotated[EnvelopeCipher, Depends(get_housing_cipher)],
    lookup: Annotated[LookupDigest, Depends(get_housing_lookup)],
) -> PersonCheckInOptions:
    now = datetime.now(UTC)
    person = _subject(db, actor, person_id)
    access = HousingAccessService(db, actor.account.id, now)
    rooms = _rooms(db, access, person)
    if access.owner_only or not rooms or _stays(db, person_id):
        _permission_denied()
    resident = _resident(db, person, now, cipher, lookup)
    if resident is None:
        _permission_denied()
    _set_sensitive_read_result_count(request, len(rooms))
    return PersonCheckInOptions(
        access_revision=_revision(access, person_id, []),
        housing_access_revision=access.access_revision,
        items=[
            PersonCheckInRoom(
                id=room.id,
                room_number=room.room_number,
                building_name=building,
                floor_name=floor,
                allowed_actions=PersonHousingActions(create_stay=True),
            )
            for room, building, floor in rooms
        ],
        resident=resident,
    )
