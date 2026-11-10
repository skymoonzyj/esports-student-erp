"""Source-local departure scopes; never infer one source from another."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, false, or_, select, true
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.housing.access import HousingAccessService
from app.housing.models import HousingRoom, HousingStay
from app.housing.portal_service import HousingPortalService
from app.identity.models import Person
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.queries import ManagedPeopleQuery
from app.portal.coach_service import PortalCoachService
from app.portal.errors import PortalPermissionDenied
from app.portal.learning_service import PortalLearningService
from app.rbac.service import RbacDecisionContext, RbacService
from app.referrals.access import InvitedHousingAccess
from app.referrals.errors import InvitationSourceNotFound, InvitedHousingPermissionDenied
from app.teaching.access import TeachingVisibility


@dataclass(frozen=True)
class DepartureHousingRow:
    stay_id: UUID
    person_id: UUID
    room_id: UUID


@dataclass(frozen=True)
class DepartureEnrollmentRow:
    enrollment_id: UUID
    person_id: UUID
    class_id: UUID


class DepartureAccess:
    """One request's current scopes; no fact/date-window result is cached."""

    def __init__(self, db: Session, actor: AuthContext, now: datetime) -> None:
        self.db, self.actor, self.now = db, actor, now
        self.rbac = RbacService(db)

    @cached_property
    def _decisions(self) -> RbacDecisionContext:
        return self.rbac.decision_context(self.actor.account.id, occurred_at=self.now)

    @cached_property
    def _housing(self) -> HousingAccessService:
        return HousingAccessService(self.db, self.actor.account.id, self.now)

    @cached_property
    def _managed(self) -> Select[tuple[UUID]]:
        state = PersonnelIntakeAccess(self.db).snapshot(self.actor)
        return ManagedPeopleQuery(self.db).authorized_person_ids_statement(state, self.now)

    @cached_property
    def _invited(self) -> Select[Any] | None:
        try:
            return InvitedHousingAccess(self.db).authorized_people_statement(
                user_id=self.actor.account.id,
                actor_person_id=self.actor.person.id,
                source_kind="all",
                organization_id=None,
                as_of=self.now,
            )
        except (InvitationSourceNotFound, InvitedHousingPermissionDenied):
            return None

    @cached_property
    def _housing_subject_ids(self) -> frozenset[UUID]:
        return frozenset(
            subject.person_id
            for subject in HousingPortalService(self.db).list_authorized_subjects(
                user_id=self.actor.account.id,
                actor_person_id=self.actor.person.id,
                as_of=self.now,
            )
        )

    @cached_property
    def _teaching(self) -> TeachingVisibility:
        # This independent, existing /enrollments list path intentionally has
        # no coach-portal combination gate. Preserve its excluded classes too.
        return self.rbac.teaching_visibility(
            user_id=self.actor.account.id,
            permission="enrollments.read",
            actor_person_id=self.actor.person.id,
            at=self.now,
        )

    @cached_property
    def _learning_subject_ids(self) -> frozenset[UUID]:
        try:
            subjects = PortalLearningService(self.db).authorized_student_people(
                user_id=self.actor.account.id,
                actor_person_id=self.actor.person.id,
                reference_date=business_date_at(self.now),
                decisions=self._decisions,
            )
        except PortalPermissionDenied:
            return frozenset()
        return frozenset(subjects)

    @cached_property
    def _coach_class_ids(self) -> frozenset[UUID]:
        service = PortalCoachService(self.db)
        try:
            service._require_capability(user_id=self.actor.account.id, decisions=self._decisions)
        except PortalPermissionDenied:
            return frozenset()
        # Use the same current-assignment source as class_resource_tokens_many;
        # never borrow the portal's future-cohort/active-enrollment candidates.
        current = self.rbac._current_assigned_class_ids(self.actor.person.id, at=self.now)
        return service.authorized_class_ids(
            actor_person_id=self.actor.person.id,
            class_ids=set(current),
            now=self.now,
            decisions=self._decisions,
        )

    def has_scope(self) -> bool:
        return bool(
            self._teaching.global_access
            or self._teaching.class_ids
            or self._learning_subject_ids
            or self._coach_class_ids
            or self._housing_subject_ids
            or self._invited is not None
            or self.db.scalar(select(self._managed.exists()))
            or self.db.scalar(
                select(
                    select(HousingRoom.id)
                    .where(self._housing.room_permission_criteria("housing.read"))
                    .exists()
                )
            )
        )

    def housing_criteria(self) -> ColumnElement[bool]:
        invited = self._invited.subquery() if self._invited is not None else None
        return or_(
            self._housing.room_permission_criteria("housing.read"),
            HousingStay.person_id.in_(self._managed),
            HousingStay.person_id.in_(select(invited.c.student_person_id))
            if invited is not None
            else false(),
            HousingStay.person_id.in_(self._housing_subject_ids),
        )

    def enrollment_criteria(self) -> ColumnElement[bool]:
        visibility = self._teaching
        generic = (
            (
                Enrollment.class_id.not_in(visibility.excluded_class_ids)
                if visibility.excluded_class_ids
                else true()
            )
            if visibility.global_access
            else Enrollment.class_id.in_(visibility.class_ids)
        )
        return and_(
            Person.deleted_at.is_(None),
            or_(
                generic,
                Enrollment.student_person_id.in_(self._learning_subject_ids),
                Enrollment.class_id.in_(self._coach_class_ids),
            ),
        )

    def detail_paths(
        self,
        housing_rows: Sequence[DepartureHousingRow],
        enrollment_rows: Sequence[DepartureEnrollmentRow],
    ) -> dict[tuple[str, UUID], str]:
        result: dict[tuple[str, UUID], str] = {}
        housing = {row.stay_id: row for row in housing_rows}
        if housing:
            for stay_id, person_id, room_id in self.db.execute(
                select(HousingStay.id, HousingStay.person_id, HousingStay.room_id)
                .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
                .where(
                    HousingStay.id.in_(housing),
                    self._housing.room_permission_criteria("housing.read"),
                )
            ):
                if housing[stay_id] == DepartureHousingRow(stay_id, person_id, room_id):
                    result[("planned_checkout", stay_id)] = f"/housing/rooms/{room_id}"
        enrollments = {row.enrollment_id: row for row in enrollment_rows}
        if enrollments:
            rows = self.db.execute(
                select(Enrollment.id, Enrollment.student_person_id, Enrollment.class_id)
                .join(Person, Person.id == Enrollment.student_person_id)
                .where(Enrollment.id.in_(enrollments), Person.deleted_at.is_(None))
            ).all()
            person_tokens = self.rbac.person_resource_tokens_many(
                actor_person_id=self.actor.person.id,
                target_person_ids={row.student_person_id for row in rows},
                reference_date=business_date_at(self.now),
            )
            class_tokens = self.rbac.class_resource_tokens_many(
                actor_person_id=self.actor.person.id,
                class_ids={row.class_id for row in rows},
                at=self.now,
            )
            for identifier, person_id, class_id in rows:
                if (
                    enrollments[identifier]
                    == DepartureEnrollmentRow(identifier, person_id, class_id)
                    and self._decisions.decide(
                        "enrollments.read", person_tokens[person_id] | class_tokens[class_id]
                    ).allowed
                ):
                    result[("enrollment_end", identifier)] = f"/teaching/enrollments/{identifier}"
        return result
