from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, exists, false, or_, select, true
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.rbac.models import Role, UserRole
from app.rbac.policy import PolicyRule
from app.rbac.service import RbacService
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.errors import TeamAccessDenied, TeamNotFound
from app.team_management.models import TeamManagerAssignment, TeamMembership
from app.team_management.schemas import TeamAction, TeamContext

_ACTION_PERMISSION: dict[TeamAction, str] = {
    "read": "team_memberships.read",
    "manage": "team_memberships.manage",
    "transfer": "team_memberships.transfer",
    "assign_managers": "team_manager_assignments.manage",
}


class TeamAccessService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def _actor_roles(self, actor: AuthContext) -> set[str]:
        row = self.db.execute(
            select(UserAccount.status, UserAccount.force_password_change, Person.deleted_at)
            .join(Person, Person.id == UserAccount.person_id)
            .where(UserAccount.id == actor.account.id)
        ).one_or_none()
        if row is None or row.status != "active" or row.force_password_change or row.deleted_at:
            return set()
        return set(
            self.db.scalars(
                select(Role.key)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == actor.account.id)
            )
        )

    @staticmethod
    def _team_scope(rule: PolicyRule) -> ColumnElement[bool]:
        if rule.scope_kind == "global":
            return true()
        if rule.scope_kind == "assigned_team":
            return ProfessionalTeam.id == rule.scope_id if rule.scope_id is not None else true()
        return false()

    @classmethod
    def _permission_filter(
        cls,
        permission: str,
        role_rules: list[PolicyRule],
        overrides: list[PolicyRule],
    ) -> ColumnElement[bool]:
        relevant_allow = [
            cls._team_scope(rule)
            for rule in [*role_rules, *overrides]
            if rule.permission == permission and rule.effect == "allow"
        ]
        relevant_deny = [
            cls._team_scope(rule)
            for rule in overrides
            if rule.permission == permission and rule.effect == "deny"
        ]
        return and_(
            or_(*relevant_allow) if relevant_allow else false(),
            ~(or_(*relevant_deny) if relevant_deny else false()),
        )

    def _has_any_assignment(self, account_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(TeamManagerAssignment.id)
                .where(
                    TeamManagerAssignment.account_id == account_id,
                    TeamManagerAssignment.ended_at.is_(None),
                )
                .limit(1)
            )
            is not None
        )

    def visible_team_ids(
        self,
        actor: AuthContext,
        action: TeamAction,
    ) -> Select[tuple[UUID]]:
        roles = self._actor_roles(actor)
        permission = _ACTION_PERMISSION[action]
        has_superadmin = "super_admin" in roles
        has_manager = "team_manager" in roles and action in {"read", "manage"}
        has_roster_history = exists(
            select(TeamMembership.id).where(
                TeamMembership.team_id == ProfessionalTeam.id,
            )
        )
        has_manager_history = exists(
            select(TeamManagerAssignment.id).where(
                TeamManagerAssignment.team_id == ProfessionalTeam.id,
            )
        )
        base = (
            select(ProfessionalTeam.id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(
                or_(GameProject.is_team_division.is_(True), has_roster_history, has_manager_history)
            )
        )
        if not has_superadmin and not has_manager:
            return base.where(false())
        role_rules, overrides = self.rbac._rules(actor.account.id)
        assignment_exists = exists(
            select(TeamManagerAssignment.id).where(
                TeamManagerAssignment.account_id == actor.account.id,
                TeamManagerAssignment.team_id == ProfessionalTeam.id,
                TeamManagerAssignment.ended_at.is_(None),
            )
        )
        actor_scope = true() if has_superadmin else assignment_exists
        return base.where(actor_scope, self._permission_filter(permission, role_rules, overrides))

    def require_team(
        self,
        actor: AuthContext,
        team_id: UUID,
        action: TeamAction,
    ) -> ProfessionalTeam:
        roles = self._actor_roles(actor)
        if "super_admin" not in roles:
            if action not in {"read", "manage"} or "team_manager" not in roles:
                raise TeamAccessDenied()
            if not self._has_any_assignment(actor.account.id):
                raise TeamAccessDenied()
        team = self.db.scalar(
            select(ProfessionalTeam).where(
                ProfessionalTeam.id == team_id,
                ProfessionalTeam.id.in_(self.visible_team_ids(actor, action)),
            )
        )
        if team is None:
            raise TeamNotFound()
        return team

    def require_self(self, actor: AuthContext) -> UUID:
        roles = self._actor_roles(actor)
        if not roles.intersection({"team_member", "super_admin"}):
            raise TeamAccessDenied()
        tokens = {"global", f"self:{actor.person.id}"}
        if not self.rbac.decide(
            user_id=actor.account.id,
            permission="team_memberships.read",
            resource_tokens=tokens,
        ).allowed:
            raise TeamAccessDenied()
        return actor.person.id

    def self_history_filter(self, actor: AuthContext) -> ColumnElement[bool]:
        # require_self establishes the live self grant; roster/manager grants are
        # deliberately not required. Apply denies to each historical row's team.
        self.require_self(actor)
        _, overrides = self.rbac._rules(actor.account.id)
        denied: list[ColumnElement[bool]] = []
        for rule in overrides:
            if rule.permission != "team_memberships.read" or rule.effect != "deny":
                continue
            if rule.scope_kind == "self":
                denied.append(true() if rule.scope_id in (None, actor.person.id) else false())
            else:
                denied.append(self._team_scope(rule))
        return ~(or_(*denied) if denied else false())

    def readable_unassigned_person_ids(
        self,
        actor: AuthContext,
        person_ids: Sequence[UUID],
    ) -> set[UUID]:
        requested = set(person_ids)
        if not requested:
            return set()
        roles = self._actor_roles(actor)
        if not roles.intersection({"team_member", "super_admin"}):
            return set()
        decision = self.rbac.decision_context(actor.account.id)
        visible: set[UUID] = set()
        if "super_admin" in roles and decision.decide("team_memberships.read", {"global"}).allowed:
            visible = requested.copy()
        if actor.person.id in requested:
            own_tokens = {"global", f"self:{actor.person.id}"}
            if decision.decide("team_memberships.read", own_tokens).allowed:
                if "super_admin" in roles or "team_member" in roles:
                    visible.add(actor.person.id)
            else:
                visible.discard(actor.person.id)
        return visible

    def context(self, actor: AuthContext) -> TeamContext:
        roles = self._actor_roles(actor)
        is_superadmin = "super_admin" in roles
        decision = self.rbac.decision_context(actor.account.id) if roles else None
        global_tokens = {"global"}
        can_manage_catalog = (
            is_superadmin
            and decision is not None
            and any(
                decision.decide(permission, global_tokens).allowed
                for permission in ("projects.manage", "professional_teams.manage")
            )
        )
        can_transfer = (
            is_superadmin
            and decision is not None
            and decision.decide("team_memberships.transfer", global_tokens).allowed
        )
        can_assign_managers = (
            is_superadmin
            and decision is not None
            and decision.decide("team_manager_assignments.manage", global_tokens).allowed
        )
        can_read_self = False
        if roles.intersection({"team_member", "super_admin"}) and decision is not None:
            can_read_self = decision.decide(
                "team_memberships.read", {"global", f"self:{actor.person.id}"}
            ).allowed
        has_roster = False
        if is_superadmin or (
            "team_manager" in roles and self._has_any_assignment(actor.account.id)
        ):
            has_roster = any(self.db.scalars(self.visible_team_ids(actor, "read").limit(1)))
            if not has_roster:
                has_roster = any(self.db.scalars(self.visible_team_ids(actor, "manage").limit(1)))
        return TeamContext(
            can_open_management=bool(
                can_manage_catalog or can_transfer or can_assign_managers or has_roster
            ),
            can_read_self=can_read_self,
            can_manage_catalog=bool(can_manage_catalog),
            can_assign_managers=bool(can_assign_managers),
            can_transfer=bool(can_transfer),
        )
