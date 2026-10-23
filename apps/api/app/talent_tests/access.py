from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, case, exists, false, not_, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute, Session, aliased

from app.admissions.models import Enrollment
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import Role, UserRole
from app.rbac.policy import PolicyRule
from app.rbac.service import RbacService
from app.talent_tests.errors import TalentForbidden, TalentNotFound
from app.talent_tests.models import TalentTestSession
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment, Cohort, TeachingClass


def current_coach_student_predicate(
    actor_person_id: UUID,
    person_id_column: ColumnElement[UUID] | InstrumentedAttribute[UUID],
    now: datetime,
    *,
    class_id: UUID | None = None,
) -> ColumnElement[bool]:
    """A live, eligible teaching assignment and a still-current enrollment."""
    enrollment_end = case(
        (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
        else_=Cohort.ends_at,
    )
    return exists(
        select(Enrollment.id)
        .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
        .join(Cohort, Cohort.id == TeachingClass.cohort_id)
        .join(CoachAssignment, CoachAssignment.class_id == TeachingClass.id)
        .where(
            Enrollment.student_person_id == person_id_column,
            Enrollment.status == "active",
            Enrollment.cohort_id == TeachingClass.cohort_id,
            enrollment_end > now,
            TeachingClass.is_active.is_(True),
            Cohort.status.in_(("open", "upcoming", "active")),
            CoachAssignment.coach_person_id == actor_person_id,
            CoachAssignment.valid_from <= now,
            or_(CoachAssignment.valid_until.is_(None), CoachAssignment.valid_until > now),
            or_(CoachAssignment.ended_at.is_(None), CoachAssignment.ended_at > now),
            CoachEligibility.eligible_person_predicate(CoachAssignment.coach_person_id),
            TeachingClass.id == class_id if class_id is not None else true(),
        )
    )


class TalentAccess:
    def __init__(self, db: Session, actor: AuthContext, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        self.db = db
        self.actor = actor
        self.now = now

    def _active_actor_predicate(self) -> ColumnElement[bool]:
        live_person = aliased(Person)
        return exists(
            select(UserAccount.id)
            .join(live_person, live_person.id == UserAccount.person_id)
            .where(
                UserAccount.id == self.actor.account.id,
                UserAccount.person_id == self.actor.person.id,
                UserAccount.status == "active",
                live_person.deleted_at.is_(None),
            )
        )

    def _scope_predicate(self, rule: PolicyRule) -> ColumnElement[bool]:
        person_id = Person.id
        if rule.scope_kind == "global":
            return true() if rule.scope_id is None else false()
        if rule.scope_kind == "self":
            return and_(
                person_id == self.actor.person.id,
                person_id == rule.scope_id if rule.scope_id is not None else true(),
            )
        if rule.scope_kind == "person":
            return person_id == rule.scope_id if rule.scope_id is not None else false()
        if rule.scope_kind == "family":
            today = business_date_at(self.now)
            return exists(
                select(GuardianRelationship.id).where(
                    GuardianRelationship.guardian_person_id == self.actor.person.id,
                    GuardianRelationship.student_person_id == person_id,
                    GuardianRelationship.valid_from <= today,
                    or_(
                        GuardianRelationship.valid_until.is_(None),
                        GuardianRelationship.valid_until >= today,
                    ),
                    or_(
                        GuardianRelationship.ended_at.is_(None),
                        GuardianRelationship.ended_at > self.now,
                    ),
                    person_id == rule.scope_id if rule.scope_id is not None else true(),
                )
            )
        if rule.scope_kind == "assigned_class":
            return current_coach_student_predicate(
                self.actor.person.id,
                person_id,
                self.now,
                class_id=rule.scope_id,
            )
        return false()

    def _permission_predicate(self, permission: str) -> ColumnElement[bool]:
        context = RbacService(self.db).decision_context(self.actor.account.id, occurred_at=self.now)
        rules = [
            rule
            for rule in [*context.role_rules, *context.overrides]
            if rule.permission == permission
        ]
        allows = [
            self._scope_predicate(rule)
            for rule in rules
            if rule.effect == "allow"
            and (permission != "talent_tests.take" or rule.scope_kind == "self")
        ]
        denies = [self._scope_predicate(rule) for rule in rules if rule.effect == "deny"]
        return and_(or_(false(), *allows), not_(or_(false(), *denies)))

    def visible_person_ids(self) -> Select[tuple[UUID]]:
        return select(Person.id).where(
            Person.deleted_at.is_(None),
            self._active_actor_predicate(),
            self._permission_predicate("talent_tests.read"),
        )

    def require_person(self, person_id: UUID) -> None:
        if self.db.scalar(self.visible_person_ids().where(Person.id == person_id)) is None:
            raise TalentNotFound("天赋测试资源不存在")

    def require_take(self) -> None:
        statement = self.visible_person_ids().where(
            Person.id == self.actor.person.id,
            self._permission_predicate("talent_tests.take"),
        )
        if self.db.scalar(statement) is None:
            raise TalentForbidden("无权进行天赋测试")

    def can_read_partial(self) -> bool:
        """Global partial access; callers must additionally apply person visibility."""
        is_superadmin = (
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(
                    UserRole.user_id == self.actor.account.id,
                    Role.key == "super_admin",
                    self._active_actor_predicate(),
                )
            )
            is not None
        )
        return (
            is_superadmin
            and RbacService(self.db)
            .decision_context(
                self.actor.account.id,
                occurred_at=self.now,
            )
            .decide("talent_tests.read", {"global"})
            .allowed
        )

    def visible_session_predicate(self) -> ColumnElement[bool]:
        return and_(
            TalentTestSession.person_id.in_(self.visible_person_ids()),
            or_(
                TalentTestSession.status == "completed",
                TalentTestSession.person_id == self.actor.person.id,
                true() if self.can_read_partial() else false(),
            ),
        )

    def require_session(self, session: TalentTestSession) -> None:
        self.require_person(session.person_id)
        if (
            session.status != "completed"
            and session.person_id != self.actor.person.id
            and not self.can_read_partial()
        ):
            raise TalentNotFound("天赋测试资源不存在")
