from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.time import business_date_at
from app.housing.errors import HousingResourceNotFound, HousingServiceError
from app.housing.models import HousingStay
from app.housing.schemas import (
    HousingPortalResponse,
    HousingPortalStay,
    HousingPortalSubject,
    PortalStayStatus,
    StayPurpose,
)
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.service import RbacService


@dataclass(frozen=True)
class HousingPortalAuthorizedSubject:
    person_id: UUID
    name: str
    is_self: bool


def _stay_projection(
    stay: HousingStay,
    *,
    roommates: list[str] | None,
) -> HousingPortalStay:
    return HousingPortalStay(
        building_name=stay.building_name_snapshot,
        floor_name=stay.floor_name_snapshot,
        room_name=stay.room_name_snapshot,
        stay_purpose=cast(StayPurpose, stay.stay_purpose),
        planned_check_in_at=stay.planned_check_in_at,
        planned_check_out_at=stay.planned_check_out_at,
        actual_check_in_at=stay.actual_check_in_at,
        actual_check_out_at=stay.actual_check_out_at,
        status=cast(PortalStayStatus, stay.status),
        roommates=roommates,
    )


class HousingPortalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def list_authorized_subjects(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        as_of: datetime,
    ) -> list[HousingPortalAuthorizedSubject]:
        reference_date = business_date_at(as_of)
        candidate_ids = set(
            self.db.scalars(
                select(GuardianRelationship.student_person_id).where(
                    GuardianRelationship.guardian_person_id == actor_person_id,
                    GuardianRelationship.requires_verification.is_(False),
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.valid_from <= reference_date,
                    or_(
                        GuardianRelationship.valid_until.is_(None),
                        GuardianRelationship.valid_until >= reference_date,
                    ),
                )
            )
        )
        if self.db.get(StudentProfile, actor_person_id) is not None:
            candidate_ids.add(actor_person_id)
        if not candidate_ids:
            return []

        people = list(
            self.db.scalars(
                select(Person)
                .join(StudentProfile, StudentProfile.person_id == Person.id)
                .where(
                    Person.id.in_(candidate_ids),
                    Person.deleted_at.is_(None),
                )
            )
        )
        if not people:
            return []

        people_by_id = {person.id: person for person in people}
        tokens_by_person = self.rbac.person_resource_tokens_many(
            actor_person_id=actor_person_id,
            target_person_ids=set(people_by_id),
            reference_date=reference_date,
        )
        decisions = self.rbac.decision_context(user_id, occurred_at=as_of)
        if (
            decisions.decide("housing.read", {"global"}).reason
            == "explicit_deny"
        ):
            return []
        subjects: list[HousingPortalAuthorizedSubject] = []
        for person in people_by_id.values():
            tokens = tokens_by_person[person.id]
            has_current_relationship = (
                f"self:{person.id}" in tokens or f"family:{person.id}" in tokens
            )
            if has_current_relationship and decisions.decide(
                "housing.read",
                tokens - {"global"},
            ).allowed:
                subjects.append(
                    HousingPortalAuthorizedSubject(
                        person_id=person.id,
                        name=person.full_name,
                        is_self=person.id == actor_person_id,
                    )
                )
        return sorted(
            subjects,
            key=lambda subject: (
                not subject.is_self,
                subject.name,
                subject.person_id.int,
            ),
        )

    def has_authorized_subjects(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        as_of: datetime,
    ) -> bool:
        return bool(
            self.list_authorized_subjects(
                user_id=user_id,
                actor_person_id=actor_person_id,
                as_of=as_of,
            )
        )

    def get_housing(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        as_of: datetime,
    ) -> HousingPortalResponse:
        subjects = self.list_authorized_subjects(
            user_id=user_id,
            actor_person_id=actor_person_id,
            as_of=as_of,
        )
        if not subjects:
            raise HousingResourceNotFound("personal housing subject not found")

        subject_person_ids = {subject.person_id for subject in subjects}
        stays = list(
            self.db.scalars(
                select(HousingStay).where(
                    HousingStay.person_id.in_(subject_person_ids),
                    HousingStay.status.in_(
                        ("planned", "checked_in", "checkout_pending")
                    ),
                )
            )
        )

        current_stays_by_person: dict[UUID, HousingStay] = {}
        planned_stays_by_person: dict[UUID, list[HousingStay]] = {}
        for stay in stays:
            if stay.status == "planned":
                planned_stays_by_person.setdefault(stay.person_id, []).append(stay)
                continue
            if stay.person_id in current_stays_by_person:
                raise HousingServiceError(
                    "personal housing subject has multiple current stays"
                )
            current_stays_by_person[stay.person_id] = stay

        current_room_ids = {
            stay.room_id for stay in current_stays_by_person.values()
        }
        occupants_by_room: dict[UUID, dict[UUID, str]] = {}
        if current_room_ids:
            occupant_rows = self.db.execute(
                select(HousingStay.room_id, Person.id, Person.full_name)
                .join(Person, Person.id == HousingStay.person_id)
                .where(
                    HousingStay.room_id.in_(current_room_ids),
                    HousingStay.status.in_(("checked_in", "checkout_pending")),
                    HousingStay.actual_check_in_at <= as_of,
                    HousingStay.actual_check_out_at.is_(None),
                    Person.deleted_at.is_(None),
                )
            ).all()
            for room_id, person_id, full_name in occupant_rows:
                occupants_by_room.setdefault(room_id, {})[person_id] = full_name

        projected_subjects: list[HousingPortalSubject] = []
        for subject in subjects:
            current_stay = current_stays_by_person.get(subject.person_id)
            current_projection: HousingPortalStay | None = None
            if current_stay is not None:
                roommates = [
                    name
                    for person_id, name in sorted(
                        occupants_by_room.get(current_stay.room_id, {}).items(),
                        key=lambda item: (item[1], item[0].int),
                    )
                    if person_id != subject.person_id
                ]
                current_projection = _stay_projection(
                    current_stay,
                    roommates=roommates,
                )

            planned_stays = sorted(
                planned_stays_by_person.get(subject.person_id, []),
                key=lambda stay: (stay.planned_check_in_at, stay.id.int),
            )
            projected_subjects.append(
                HousingPortalSubject(
                    subject_person_id=subject.person_id,
                    subject_name=subject.name,
                    current_stay=current_projection,
                    planned_stays=[
                        _stay_projection(stay, roommates=None)
                        for stay in planned_stays
                    ],
                )
            )

        return HousingPortalResponse(as_of=as_of, subjects=projected_subjects)
