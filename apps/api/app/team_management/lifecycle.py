from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.teaching.models import ProfessionalTeam
from app.team_management.allocation import sync_team_allocation_in_transaction
from app.team_management.errors import TeamHasActiveRelationships
from app.team_management.models import TeamManagerAssignment, TeamMembership, TeamStaffAssignment
from app.team_management.person_staff_models import PersonTeamStaffPosition
from app.team_management.staff_service import STAFF_ROLE_KEYS, _close_staff_assignments


class TeamLifecycleService:
    """Join the caller's identity/directory-locked transaction without committing.

    Callers hold all Person/Account (and changed Role/UserRole) locks, discover
    current directory IDs and lock projects then teams before calling helpers.
    These helpers never acquire new identity or directory locks.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def before_role_removal(
        self,
        account_id: UUID,
        role_key: str,
        actor_account_id: UUID | None,
        occurred_at: datetime,
    ) -> None:
        self._require_personnel_actor(account_id, actor_account_id, {role_key})
        if role_key == "team_member":
            person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == account_id)
            )
            if (
                self.db.scalar(
                    select(TeamMembership.id)
                    .where(TeamMembership.person_id == person_id, TeamMembership.ended_at.is_(None))
                    .limit(1)
                )
                is not None
            ):
                raise TeamHasActiveRelationships("队员仍有当前队伍，请先结束队员关系")
        elif role_key == "team_manager":
            self._close_assignments(account_id, actor_account_id, occurred_at, "role_removed")
        if role_key in STAFF_ROLE_KEYS.values():
            _close_staff_assignments(
                self.db,
                account_id,
                {role_key},
                actor_account_id=actor_account_id,
                operation_id=None,
                occurred_at=occurred_at,
                end_kind="role_removed",
            )
            self._sync_allocation(account_id, actor_account_id, occurred_at)

    def close_for_deleted_account(
        self,
        account_id: UUID,
        actor_account_id: UUID | None,
        occurred_at: datetime,
    ) -> None:
        self._require_personnel_actor(account_id, actor_account_id, set(STAFF_ROLE_KEYS.values()))
        person_id = self.db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == account_id)
        )
        ended_on = business_date_at(occurred_at)
        memberships = self.db.scalars(
            select(TeamMembership)
            .where(TeamMembership.person_id == person_id, TeamMembership.ended_at.is_(None))
            .order_by(TeamMembership.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for membership in memberships:
            membership.left_on = ended_on
            membership.ended_at = occurred_at
            membership.ended_by_user_id = actor_account_id
            membership.end_kind = "account_deleted"
            membership.close_operation_id = None
        self._close_assignments(account_id, actor_account_id, occurred_at, "account_deleted")
        if person_id is not None:
            self.close_for_deleted_person(person_id, actor_account_id, occurred_at)
        _close_staff_assignments(
            self.db,
            account_id,
            set(STAFF_ROLE_KEYS.values()),
            actor_account_id=actor_account_id,
            operation_id=None,
            occurred_at=occurred_at,
            end_kind="account_deleted",
        )
        self._sync_allocation(account_id, actor_account_id, occurred_at)
        self.db.flush()

    def _require_personnel_actor(self, account_id: UUID, actor_id: UUID | None,
                                roles: set[str]) -> None:
        if actor_id is None and self.db.scalar(select(PersonTeamStaffPosition.id)
            .join(UserAccount, UserAccount.person_id == PersonTeamStaffPosition.person_id)
            .where(UserAccount.id == account_id, PersonTeamStaffPosition.ended_at.is_(None),
                   PersonTeamStaffPosition.staff_role.in_(
                       [role for role, key in STAFF_ROLE_KEYS.items() if key in roles]))
            .limit(1)) is not None:
            raise ValueError("Ending personnel staff positions requires an authenticated audit actor")

    def close_for_deleted_person(self, person_id: UUID, actor_account_id: UUID | None,
                                 occurred_at: datetime) -> None:
        """Internal deletion hook; caller owns participants and complete directory locks."""
        from app.team_management.person_staff_service import (
            end_person_staff_in_transaction,
            lock_person_staff_positions,
        )
        positions = lock_person_staff_positions(self.db, person_id)
        if positions and actor_account_id is None:
            raise ValueError("Ending personnel staff positions requires an authenticated audit actor")
        for position in positions:
            assert actor_account_id is not None
            end_person_staff_in_transaction(self.db, position, actor_user_id=actor_account_id,
                occurred_at=occurred_at, reason="人员删除", operation_id=None, end_kind="account_deleted")
        if positions:
            sync_team_allocation_in_transaction(self.db, person_id=person_id,
                actor_user_id=actor_account_id, occurred_at=occurred_at)

    def _close_assignments(
        self,
        account_id: UUID,
        actor_account_id: UUID | None,
        occurred_at: datetime,
        end_kind: str,
    ) -> None:
        assignments = self.db.scalars(
            select(TeamManagerAssignment)
            .where(
                TeamManagerAssignment.account_id == account_id,
                TeamManagerAssignment.ended_at.is_(None),
            )
            .order_by(TeamManagerAssignment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for assignment in assignments:
            assignment.ended_at = occurred_at
            assignment.ended_by_user_id = actor_account_id
            assignment.end_kind = end_kind
            assignment.close_operation_id = None
        self.db.flush()
        self._sync_allocation(account_id, actor_account_id, occurred_at)

    def _sync_allocation(
        self, account_id: UUID, actor_account_id: UUID | None, occurred_at: datetime
    ) -> None:
        person_id = self.db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == account_id)
        )
        if person_id is not None:
            sync_team_allocation_in_transaction(
                self.db,
                person_id=person_id,
                actor_user_id=actor_account_id,
                occurred_at=occurred_at,
            )

    def assert_team_clear(self, team_id: UUID) -> None:
        for model in (TeamMembership, TeamManagerAssignment, TeamStaffAssignment, PersonTeamStaffPosition):
            if (
                self.db.scalar(
                    select(model.id)
                    .where(model.team_id == team_id, model.ended_at.is_(None))
                    .limit(1)
                )
                is not None
            ):
                raise TeamHasActiveRelationships()

    def assert_project_clear(self, project_id: UUID) -> None:
        for model in (TeamMembership, TeamManagerAssignment, TeamStaffAssignment, PersonTeamStaffPosition):
            if (
                self.db.scalar(
                    select(model.id)
                    .join(ProfessionalTeam, ProfessionalTeam.id == model.team_id)
                    .where(ProfessionalTeam.project_id == project_id, model.ended_at.is_(None))
                    .limit(1)
                )
                is not None
            ):
                raise TeamHasActiveRelationships("分部仍有当前成员或管理绑定")
