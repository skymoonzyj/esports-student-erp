from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import CoachPersonResponsibility, PersonnelIntakeRequest
from app.personnel_intake.schemas import (
    AssistedContext,
    AssistedOption,
    IntakeInput,
    IntakePayloadView,
    PersonKind,
)
from app.rbac.models import Role, UserRole
from app.rbac.policy import PolicyRule
from app.rbac.service import RbacDecisionContext, RbacService
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import GameProject, ProfessionalTeam, TeachingClass
from app.team_management.models import TeamManagerAssignment, TeamMembership

PRIVILEGED = {"admin", "super_admin", "administration"}


@dataclass(frozen=True)
class IntakeAccessSnapshot:
    account_id: UUID
    person_id: UUID | None
    roles: frozenset[str]
    decisions: RbacDecisionContext
    own_coach_eligible: bool
    managed_team_ids: frozenset[UUID]

    def allowed(
        self,
        permission: str,
        *,
        coaches: Collection[UUID] = (),
        team_id: UUID | None = None,
        target_person_id: UUID | None = None,
    ) -> bool:
        if self.person_id is None:
            return False
        tokens = {"global"}
        if target_person_id is not None:
            tokens.add(f"person:{target_person_id}")
            if target_person_id == self.person_id:
                tokens.add(f"self:{self.person_id}")
        if self.person_id in coaches:
            tokens.add("assigned_class")
        if team_id is not None:
            tokens.add(f"assigned_team:{team_id}")
        return self.decisions.decide(permission, tokens).allowed

    def permission_predicate(self, permission: str) -> ColumnElement[bool]:
        request = PersonnelIntakeRequest

        def scope(rule: PolicyRule) -> ColumnElement[bool]:
            if rule.scope_kind == "global":
                return true()
            if rule.scope_kind == "person":
                return (
                    request.person_id.is_not_distinct_from(rule.scope_id)
                    if rule.scope_id
                    else request.person_id.is_not(None)
                )
            if rule.scope_kind == "self":
                return request.person_id.is_not_distinct_from(self.person_id)
            if rule.scope_kind == "assigned_team":
                return (
                    request.target_team_id.is_not_distinct_from(rule.scope_id)
                    if rule.scope_id
                    else request.target_team_id.is_not(None)
                )
            if rule.scope_kind == "assigned_class" and rule.scope_id is None:
                return request.target_coach_person_ids.contains([self.person_id])
            return false()

        allows = [
            scope(r)
            for r in [*self.decisions.role_rules, *self.decisions.overrides]
            if r.permission == permission and r.effect == "allow"
        ]
        denies = [
            scope(r)
            for r in self.decisions.overrides
            if r.permission == permission and r.effect == "deny"
        ]
        return and_(or_(*allows) if allows else false(), ~(or_(*denies) if denies else false()))

    def readable_predicate(self) -> ColumnElement[bool]:
        request = PersonnelIntakeRequest
        if self.person_id is None or not self.roles & (PRIVILEGED | {"coach", "team_manager"}):
            return false()
        ownership: ColumnElement[bool]
        if self.roles & PRIVILEGED:
            ownership = true()
        else:
            coach = (
                request.target_coach_person_ids == [self.person_id]
                if self.own_coach_eligible and "coach" in self.roles
                else false()
            )
            manager = (
                request.target_team_id.in_(self.managed_team_ids)
                if "team_manager" in self.roles
                else false()
            )
            ownership = or_(
                and_(request.kind == "student", coach, request.target_team_id.is_(None)),
                and_(request.kind == "player", manager, request.target_coach_person_ids == []),
                and_(request.kind == "both", coach, manager),
            )
        submission = and_(
            request.submitted_by_user_id == self.account_id,
            ownership,
            self.permission_predicate("personnel_intake.submit"),
        )
        if self.roles & {"admin", "super_admin"}:
            return or_(submission, self.permission_predicate("personnel_intake.review"))
        return submission

    def can_enter_review_queue(self) -> bool:
        """Recognize current review grants without requiring submit or existing rows."""
        if self.person_id is None or not self.roles & {"admin", "super_admin"}:
            return False
        permission = "personnel_intake.review"
        for rule in [*self.decisions.role_rules, *self.decisions.overrides]:
            if rule.permission != permission or rule.effect != "allow":
                continue
            # These are the supported review scopes in the existing intake SQL
            # predicate. Evaluate each grant with the same explicit-deny policy.
            if rule.scope_kind not in {"global", "person", "assigned_team"}:
                continue
            token = f"{rule.scope_kind}:{rule.scope_id}" if rule.scope_id else rule.scope_kind
            if self.decisions.decide(permission, {"global", token}).allowed:
                return True
        return False

    def can_enter_managed_people(self) -> bool:
        if self.person_id is None or not self.roles & (PRIVILEGED | {"coach", "team_manager"}):
            return False
        permission = "personnel_intake.housing.read"
        for rule in [*self.decisions.role_rules, *self.decisions.overrides]:
            if rule.permission != permission or rule.effect != "allow":
                continue
            if rule.scope_kind not in {
                "global",
                "person",
                "self",
                "assigned_class",
                "assigned_team",
            }:
                continue
            token = f"{rule.scope_kind}:{rule.scope_id}" if rule.scope_id else rule.scope_kind
            if self.decisions.decide(permission, {"global", token}).allowed:
                return True
        return False


class PersonnelIntakeAccess:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def snapshot(self, actor: AuthContext) -> IntakeAccessSnapshot:
        roles, person_id = self.actor_state(actor)
        eligible = bool(
            "coach" in roles
            and self.db.scalar(
                select(Person.id).where(
                    Person.id == person_id, CoachEligibility.eligible_person_predicate(Person.id)
                )
            )
            is not None
        )
        teams = (
            frozenset(
                self.db.scalars(
                    select(TeamManagerAssignment.team_id).where(
                        TeamManagerAssignment.account_id == actor.account.id,
                        TeamManagerAssignment.ended_at.is_(None),
                    )
                )
            )
            if "team_manager" in roles
            else frozenset()
        )
        return IntakeAccessSnapshot(
            actor.account.id,
            person_id,
            frozenset(roles),
            self.rbac.decision_context(actor.account.id),
            eligible,
            teams,
        )

    def actor_state(self, actor: AuthContext) -> tuple[set[str], UUID | None]:
        row = self.db.execute(
            select(
                UserAccount.person_id,
                UserAccount.status,
                UserAccount.force_password_change,
                Person.deleted_at,
            )
            .join(Person, Person.id == UserAccount.person_id)
            .where(UserAccount.id == actor.account.id)
        ).one_or_none()
        if row is None or row.status != "active" or row.force_password_change or row.deleted_at:
            return set(), None
        return set(
            self.db.scalars(
                select(Role.key)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == actor.account.id)
            )
        ), row.person_id

    def allowed(
        self,
        actor: AuthContext,
        permission: str,
        *,
        coaches: Collection[UUID] = (),
        team_id: UUID | None = None,
        target_person_id: UUID | None = None,
    ) -> bool:
        return self.snapshot(actor).allowed(
            permission, coaches=coaches, team_id=team_id, target_person_id=target_person_id
        )

    def team_ids(
        self,
        actor: AuthContext,
        roles: set[str] | None = None,
        *,
        snapshot: IntakeAccessSnapshot | None = None,
        permission: str = "personnel_intake.submit",
    ) -> list[UUID]:
        state = snapshot or self.snapshot(actor)
        roles = set(state.roles)
        query = (
            select(ProfessionalTeam.id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(ProfessionalTeam.is_active, GameProject.is_active, GameProject.is_team_division)
        )
        if not roles & PRIVILEGED:
            if "team_manager" not in roles:
                return []
            query = query.where(ProfessionalTeam.id.in_(state.managed_team_ids))
        return [
            tid
            for tid in self.db.scalars(query.order_by(ProfessionalTeam.id))
            if state.allowed(permission, team_id=tid)
        ]

    def coach_ids(
        self,
        actor: AuthContext,
        *,
        snapshot: IntakeAccessSnapshot | None = None,
        permission: str = "personnel_intake.submit",
        team_ids: Collection[UUID] = (),
    ) -> list[UUID]:
        state = snapshot or self.snapshot(actor)
        roles, person_id = state.roles, state.person_id
        if not roles & PRIVILEGED and "coach" not in roles:
            return []
        query = select(Person.id).where(CoachEligibility.eligible_person_predicate(Person.id))
        if not roles & PRIVILEGED:
            query = query.where(Person.id == person_id)
        return [
            pid
            for pid in self.db.scalars(query.order_by(Person.id))
            if any(
                state.allowed(
                    permission,
                    coaches=[pid],
                    team_id=team_id,
                    target_person_id=pid if permission == "personnel_intake.review" else None,
                )
                for team_id in [None, *team_ids]
            )
        ]

    def context(self, actor: AuthContext) -> AssistedContext:
        state = self.snapshot(actor)
        roles = state.roles
        qualified = bool(roles & (PRIVILEGED | {"coach", "team_manager"}))
        coaches = self.coach_ids(actor, snapshot=state) if qualified else []
        teams = self.team_ids(actor, snapshot=state) if qualified else []
        submit = qualified and (
            bool(coaches or teams)
            or bool(roles & PRIVILEGED)
            and state.allowed("personnel_intake.submit")
        )
        review_teams = (
            self.team_ids(actor, snapshot=state, permission="personnel_intake.review")
            if roles & {"admin", "super_admin"}
            else []
        )
        review_coaches = (
            self.coach_ids(
                actor, snapshot=state, permission="personnel_intake.review", team_ids=review_teams
            )
            if roles & {"admin", "super_admin"}
            else []
        )
        direct_create = bool(roles & {"admin", "super_admin"}) and (
            state.allowed("personnel_intake.review") or bool(review_coaches or review_teams)
        )
        review = state.can_enter_review_queue()
        if review:
            coaches = sorted(set(coaches) | set(review_coaches))
            teams = sorted(set(teams) | set(review_teams))
        managed = state.can_enter_managed_people()
        return AssistedContext(
            can_submit=submit,
            can_review=review,
            can_direct_create=direct_create,
            can_direct_create_pending=bool(roles & {"admin", "super_admin"})
            and state.allowed("personnel_intake.review"),
            can_read_managed=managed,
            allowed_team_ids=teams if submit or review else [],
            eligible_coaches=[
                AssistedOption(id=p.id, label=p.full_name)
                for p in self.db.scalars(
                    select(Person)
                    .where(Person.id.in_(coaches))
                    .order_by(Person.full_name, Person.id)
                )
            ]
            if submit or review
            else [],
            eligible_teams=[
                AssistedOption(id=t.id, label=t.name)
                for t in self.db.scalars(
                    select(ProfessionalTeam)
                    .where(ProfessionalTeam.id.in_(teams))
                    .order_by(ProfessionalTeam.name, ProfessionalTeam.id)
                )
            ]
            if submit or review
            else [],
        )

    def covers(
        self,
        actor: AuthContext,
        *,
        kind: PersonKind,
        coaches: Collection[UUID],
        team_id: UUID | None,
        require_eligible: bool = True,
        target_person_id: UUID | None = None,
        snapshot: IntakeAccessSnapshot | None = None,
        permission: str = "personnel_intake.submit",
    ) -> bool:
        state = snapshot or self.snapshot(actor)
        roles, person_id = state.roles, state.person_id
        if not roles & (PRIVILEGED | {"coach", "team_manager"}):
            return False
        if not state.allowed(
            permission,
            coaches=coaches,
            team_id=team_id,
            target_person_id=target_person_id,
        ):
            return False
        if kind in {"student", "both"}:
            if not coaches or (
                not roles & PRIVILEGED and ("coach" not in roles or set(coaches) != {person_id})
            ):
                return False
            if require_eligible and not set(coaches).issubset(
                self.coach_ids(
                    actor,
                    snapshot=state,
                    permission=permission,
                    team_ids=[team_id]
                    if permission == "personnel_intake.review" and team_id
                    else (),
                )
            ):
                return False
            if not roles & PRIVILEGED and not state.own_coach_eligible:
                return False
        elif coaches:
            return False
        if kind in {"player", "both"}:
            if team_id is None:
                return False
            if require_eligible and team_id not in self.team_ids(
                actor, snapshot=state, permission=permission
            ):
                return False
            if (
                not require_eligible
                and not roles & PRIVILEGED
                and ("team_manager" not in roles or team_id not in state.managed_team_ids)
            ):
                return False
        elif team_id:
            return False
        return True

    def require_submit(self, actor: AuthContext, input: IntakeInput | IntakePayloadView) -> None:
        if not self.covers(
            actor, kind=input.kind, coaches=input.coach_person_ids, team_id=input.team_id
        ):
            raise PersonnelIntakeError(
                "personnel_intake_forbidden", status=403, detail="无权向该归属提交人员资料"
            )

    def can_read(self, actor: AuthContext, request: PersonnelIntakeRequest) -> bool:
        state = self.snapshot(actor)
        return (
            self.db.scalar(
                select(PersonnelIntakeRequest.id).where(
                    PersonnelIntakeRequest.id == request.id, state.readable_predicate()
                )
            )
            is not None
        )

    def _legacy_teaching_owner(self, actor: AuthContext, person_id: UUID) -> bool:
        roles, own_id = self.actor_state(actor)
        if "coach" not in roles or own_id is None:
            return False
        now = datetime.now(UTC)
        current_classes = self.rbac._current_assigned_class_ids(own_id, at=now)
        classes = self.db.scalars(
            select(Enrollment.class_id)
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .where(
                Enrollment.student_person_id == person_id,
                Enrollment.status == "active",
                Enrollment.class_id.in_(current_classes),
                TeachingClass.is_active,
            )
        )
        return any(
            self.rbac.decide(
                user_id=actor.account.id,
                permission="personnel_intake.submit",
                resource_tokens=self.rbac.class_resource_tokens(
                    actor_person_id=own_id, class_id=class_id, at=now
                ),
            ).allowed
            for class_id in classes
        )

    def can_reuse(self, actor: AuthContext, person_id: UUID, payload: IntakePayloadView) -> bool:
        roles, own_id = self.actor_state(actor)
        if not self.allowed(
            actor,
            "personnel_intake.submit",
            coaches=payload.coach_person_ids,
            team_id=payload.team_id,
            target_person_id=person_id,
        ):
            return False
        if roles & PRIVILEGED:
            return True
        if (
            payload.kind in {"student", "both"}
            and self.db.scalar(
                select(CoachPersonResponsibility.id).where(
                    CoachPersonResponsibility.coach_person_id == own_id,
                    CoachPersonResponsibility.person_id == person_id,
                    CoachPersonResponsibility.ended_at.is_(None),
                )
            )
            is None
            and not self._legacy_teaching_owner(actor, person_id)
        ):
            return False
        return not (
            payload.kind in {"player", "both"}
            and self.db.scalar(
                select(TeamMembership.id).where(
                    TeamMembership.person_id == person_id,
                    TeamMembership.team_id == payload.team_id,
                    TeamMembership.ended_at.is_(None),
                )
            )
            is None
        )
