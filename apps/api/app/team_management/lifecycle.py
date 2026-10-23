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
from app.team_management.models import TeamManagerAssignment, TeamMembership


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

    def close_for_deleted_account(
        self,
        account_id: UUID,
        actor_account_id: UUID | None,
        occurred_at: datetime,
    ) -> None:
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
        self.db.flush()

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
        for model in (TeamMembership, TeamManagerAssignment):
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
        for model in (TeamMembership, TeamManagerAssignment):
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
