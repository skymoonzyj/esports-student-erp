"""Live, deny-first SQL authorization independent of the legacy test module."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, exists, false, not_, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.admissions.models import Enrollment
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import Role, UserRole
from app.rbac.policy import PolicyRule
from app.rbac.service import RbacService
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment, Cohort, TeachingClass

from . import catalog
from .config_schemas import MEMBERS, SuiteId
from .errors import AssessmentForbidden, AssessmentNotFound, AssessmentUnavailable
from .models import AssessmentSession


class AssessmentAccess:
    def __init__(self, db: Session, actor: AuthContext, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        self.db, self.actor, self.now = db, actor, now

    def _active_actor(self) -> ColumnElement[bool]:
        person = aliased(Person)
        return exists(
            select(UserAccount.id)
            .join(person, person.id == UserAccount.person_id)
            .where(
                UserAccount.id == self.actor.account.id,
                UserAccount.person_id == self.actor.person.id,
                UserAccount.status == "active",
                person.deleted_at.is_(None),
            )
        )

    def _scope(self, rule: PolicyRule) -> ColumnElement[bool]:
        if rule.scope_kind == "global":
            return true() if rule.scope_id is None else false()
        if rule.scope_kind == "self":
            return and_(
                Person.id == self.actor.person.id,
                Person.id == rule.scope_id if rule.scope_id else true(),
            )
        if rule.scope_kind == "person":
            return Person.id == rule.scope_id if rule.scope_id else false()
        if rule.scope_kind == "family":
            today = business_date_at(self.now)
            return exists(
                select(GuardianRelationship.id).where(
                    GuardianRelationship.guardian_person_id == self.actor.person.id,
                    GuardianRelationship.student_person_id == Person.id,
                    GuardianRelationship.valid_from <= today,
                    or_(
                        GuardianRelationship.valid_until.is_(None),
                        GuardianRelationship.valid_until >= today,
                    ),
                    or_(
                        GuardianRelationship.ended_at.is_(None),
                        GuardianRelationship.ended_at > self.now,
                    ),
                    Person.id == rule.scope_id if rule.scope_id else true(),
                )
            )
        if rule.scope_kind == "assigned_class":
            ends_at = case(
                (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
                else_=Cohort.ends_at,
            )
            return exists(
                select(Enrollment.id)
                .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
                .join(Cohort, Cohort.id == TeachingClass.cohort_id)
                .join(CoachAssignment, CoachAssignment.class_id == TeachingClass.id)
                .where(
                    Enrollment.student_person_id == Person.id,
                    Enrollment.status == "active",
                    Enrollment.cohort_id == TeachingClass.cohort_id,
                    ends_at > self.now,
                    TeachingClass.is_active.is_(True),
                    Cohort.status.in_(("open", "upcoming", "active")),
                    CoachAssignment.coach_person_id == self.actor.person.id,
                    CoachAssignment.valid_from <= self.now,
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > self.now,
                    ),
                    or_(CoachAssignment.ended_at.is_(None), CoachAssignment.ended_at > self.now),
                    CoachEligibility.eligible_person_predicate(CoachAssignment.coach_person_id),
                    TeachingClass.id == rule.scope_id if rule.scope_id else true(),
                )
            )
        return false()

    def _permission(self, permission: str) -> ColumnElement[bool]:
        context = RbacService(self.db).decision_context(self.actor.account.id, occurred_at=self.now)
        rules = [r for r in (*context.role_rules, *context.overrides) if r.permission == permission]
        allows = [
            self._scope(r)
            for r in rules
            if r.effect == "allow"
            and (permission != "esports_assessments.take" or r.scope_kind == "self")
        ]
        denies = [self._scope(r) for r in rules if r.effect == "deny"]
        return and_(or_(false(), *allows), not_(or_(false(), *denies)))

    def visible_person_ids(self) -> Select[tuple[UUID]]:
        return select(Person.id).where(
            Person.deleted_at.is_(None),
            self._active_actor(),
            self._permission("esports_assessments.read"),
        )

    def require_person(self, person_id: UUID) -> None:
        if self.db.scalar(self.visible_person_ids().where(Person.id == person_id)) is None:
            raise AssessmentNotFound()

    def require_take(self) -> None:
        if (
            self.db.scalar(
                self.visible_person_ids().where(
                    Person.id == self.actor.person.id,
                    self._permission("esports_assessments.take"),
                )
            )
            is None
        ):
            raise AssessmentForbidden("无权参加天赋评测")

    def _is_actual_superadmin(self) -> bool:
        return (
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role)
                .where(
                    UserRole.user_id == self.actor.account.id,
                    Role.key == "super_admin",
                    self._active_actor(),
                )
            )
            is not None
        )

    def can_preview_suites(self) -> bool:
        """Closed suites are testable only by a live, permitted superadministrator."""
        if not self._is_actual_superadmin():
            return False
        try:
            self.require_take()
        except AssessmentForbidden:
            return False
        return True

    def available_suite_ids(self) -> tuple[SuiteId, ...]:
        preview = self.can_preview_suites()
        return tuple(
            cast(SuiteId, suite_id)
            for suite_id in MEMBERS
            if preview or suite_id in catalog.AVAILABLE_SUITES
        )

    def require_available(self, suite_id: str) -> None:
        if suite_id not in self.available_suite_ids():
            raise AssessmentUnavailable()

    def can_read_partial(self) -> bool:
        return (
            self._is_actual_superadmin()
            and RbacService(self.db)
            .decision_context(
                self.actor.account.id,
                occurred_at=self.now,
            )
            .decide("esports_assessments.read", {"global"})
            .allowed
        )

    def require_private_run(self, owner_person_id: UUID) -> None:
        self.require_person(owner_person_id)
        if owner_person_id != self.actor.person.id and not self.can_read_partial():
            raise AssessmentNotFound()

    def visible_reports(self) -> ColumnElement[bool]:
        """Apply to a report query joined to AssessmentSession."""
        return and_(
            AssessmentSession.person_id.in_(self.visible_person_ids()),
            AssessmentSession.status == "completed",
        )

    def visible_session_predicate(self) -> ColumnElement[bool]:
        return and_(
            AssessmentSession.person_id.in_(self.visible_person_ids()),
            or_(
                AssessmentSession.status == "completed",
                AssessmentSession.person_id == self.actor.person.id,
                true() if self.can_read_partial() else false(),
            ),
        )

    def require_session(self, session: AssessmentSession) -> None:
        if session.status == "completed":
            self.require_person(session.person_id)
        else:
            self.require_private_run(session.person_id)
