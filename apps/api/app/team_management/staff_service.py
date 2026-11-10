"""Independent read-only staff assignments; no legacy management authority."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.rbac.models import Role, UserRole
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.errors import (
    TeamAssignmentConflict,
    TeamAssignmentNotFound,
    TeamAssignmentStale,
)
from app.team_management.models import TeamOperation, TeamStaffAssignment
from app.team_management.schemas import AssignStaffInput, TeamMutationResult, UnassignStaffInput

STAFF_ROLE_KEYS = {
    "manager": "team_manager",
    "coach": "coach",
    "assistant_coach": "assistant_coach",
}


def active_staff_team_ids(db: Session, account_id: UUID, *, role_keys: set[str]) -> set[UUID]:
    roles = [(position, key) for position, key in STAFF_ROLE_KEYS.items() if key in role_keys]
    if not roles:
        return set()
    return set(
        db.scalars(
            select(TeamStaffAssignment.team_id)
            .distinct()
            .join(UserAccount, UserAccount.id == TeamStaffAssignment.account_id)
            .join(Person, Person.id == UserAccount.person_id)
            .join(UserRole, UserRole.user_id == UserAccount.id)
            .join(Role, Role.id == UserRole.role_id)
            .join(ProfessionalTeam, ProfessionalTeam.id == TeamStaffAssignment.team_id)
            .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
            .where(
                TeamStaffAssignment.account_id == account_id,
                TeamStaffAssignment.ended_at.is_(None),
                UserAccount.status == "active",
                UserAccount.force_password_change.is_(False),
                Person.deleted_at.is_(None),
                ProfessionalTeam.is_active.is_(True),
                GameProject.is_active.is_(True),
                GameProject.is_team_division.is_(True),
                or_(
                    *(
                        and_(TeamStaffAssignment.staff_role == position, Role.key == key)
                        for position, key in roles
                    )
                ),
            )
        )
    )


def staff_team_ids_for_account(
    db: Session, account_id: UUID, *, role_keys: set[str] | None = None
) -> set[UUID]:
    """Discover every current relation for directory locks, including inactive ones."""
    query = select(TeamStaffAssignment.team_id).where(
        TeamStaffAssignment.account_id == account_id, TeamStaffAssignment.ended_at.is_(None)
    )
    if role_keys is not None:
        query = query.where(
            TeamStaffAssignment.staff_role.in_(
                [position for position, key in STAFF_ROLE_KEYS.items() if key in role_keys]
            )
        )
    from app.team_management.person_staff_models import PersonTeamStaffPosition

    personnel = select(PersonTeamStaffPosition.team_id).join(
        UserAccount, UserAccount.person_id == PersonTeamStaffPosition.person_id).where(
        UserAccount.id == account_id, PersonTeamStaffPosition.ended_at.is_(None))
    if role_keys is not None:
        personnel = personnel.where(PersonTeamStaffPosition.staff_role.in_(
            [position for position, key in STAFF_ROLE_KEYS.items() if key in role_keys]))
    return set(db.scalars(query)) | set(db.scalars(personnel))


def _close_staff_assignments(
    db: Session,
    account_id: UUID,
    removed_role_keys: set[str],
    *,
    actor_account_id: UUID | None,
    operation_id: UUID | None,
    occurred_at: datetime,
    end_kind: str,
) -> None:
    positions = [position for position, key in STAFF_ROLE_KEYS.items() if key in removed_role_keys]
    if not positions:
        return
    from app.team_management.person_staff_models import PersonTeamStaffPosition
    from app.team_management.person_staff_service import end_person_staff_in_transaction

    personnel = list(db.scalars(select(PersonTeamStaffPosition).join(
        UserAccount, UserAccount.person_id == PersonTeamStaffPosition.person_id).where(
        UserAccount.id == account_id, PersonTeamStaffPosition.ended_at.is_(None),
        PersonTeamStaffPosition.staff_role.in_(positions)).order_by(PersonTeamStaffPosition.id)
        .with_for_update(of=PersonTeamStaffPosition).execution_options(populate_existing=True)))
    if personnel and actor_account_id is None:
        raise ValueError("Ending personnel staff positions requires an authenticated audit actor")
    for position in personnel:
        assert actor_account_id is not None
        end_person_staff_in_transaction(db, position, actor_user_id=actor_account_id,
            occurred_at=occurred_at, reason="账号删除" if end_kind == "account_deleted" else "账号岗位角色移除",
            operation_id=operation_id, end_kind=end_kind)
    rows = db.scalars(
        select(TeamStaffAssignment)
        .where(
            TeamStaffAssignment.account_id == account_id,
            TeamStaffAssignment.ended_at.is_(None),
            TeamStaffAssignment.staff_role.in_(positions),
        )
        .order_by(TeamStaffAssignment.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for row in rows:
        row.ended_at, row.ended_by_user_id = occurred_at, actor_account_id
        row.end_kind, row.close_operation_id = end_kind, operation_id
    db.flush()


def close_staff_assignments_for_roles(
    db: Session,
    account_id: UUID,
    removed_role_keys: set[str],
    *,
    operation_id: UUID,
    occurred_at: datetime,
) -> None:
    """Caller holds all affected identity/directory locks; never commits or invents operations."""
    operation = db.get(TeamOperation, operation_id)
    if operation is None:
        raise ValueError("A persisted team operation is required")
    _close_staff_assignments(
        db,
        account_id,
        removed_role_keys,
        actor_account_id=operation.actor_account_id,
        operation_id=operation_id,
        occurred_at=occurred_at,
        end_kind="role_removed",
    )
    from app.team_management.allocation import sync_team_allocation_in_transaction

    person_id = db.scalar(select(UserAccount.person_id).where(UserAccount.id == account_id))
    if person_id is not None:
        sync_team_allocation_in_transaction(
            db,
            person_id=person_id,
            actor_user_id=operation.actor_account_id,
            occurred_at=occurred_at,
        )


def _assign_staff_in_transaction(
    db: Session, *, account_id: UUID, team_id: UUID, staff_role: str,
    actor_user_id: UUID, occurred_at: datetime,
) -> TeamStaffAssignment:
    """Internal realization only: caller validates authority and owns every lock."""
    row = TeamStaffAssignment(account_id=account_id, team_id=team_id, staff_role=staff_role,
        created_by_user_id=actor_user_id, created_at=occurred_at)
    db.add(row)
    db.flush()
    return row


class TeamStaffService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _sync_allocation(self, account_id: UUID, actor_id: UUID, now: datetime) -> None:
        from app.team_management.allocation import sync_team_allocation_in_transaction

        person_id = self.db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == account_id)
        )
        assert person_id is not None
        sync_team_allocation_in_transaction(
            self.db, person_id=person_id, actor_user_id=actor_id, occurred_at=now
        )

    def assign(
        self, actor: AuthContext, team_id: UUID, input: AssignStaffInput
    ) -> TeamMutationResult:
        from app.team_management import write_support as support

        action = "team_staff.assign"
        digest = support.fingerprint(
            action,
            {"team_id": team_id, "account_id": input.account_id, "staff_role": input.staff_role},
        )
        try:
            with atomic_write_transaction(self.db):
                support.lock_operation(self.db, actor.account.id, input.idempotency_key)
                support.lock_command_participants(self.db, actor, account_id=input.account_id)
                support.lock_directory(self.db, [team_id])
                team = support.require_team(self.db, actor, team_id, "assign_managers")
                previous = support.replay(
                    self.db, actor.account.id, input.idempotency_key, action, digest
                )
                if previous is not None:
                    return previous
                support.require_active_team(self.db, team)
                support.require_candidate(
                    self.db, role_key=STAFF_ROLE_KEYS[input.staff_role], account_id=input.account_id
                )
                current = self.db.scalar(
                    select(TeamStaffAssignment)
                    .where(
                        TeamStaffAssignment.account_id == input.account_id,
                        TeamStaffAssignment.team_id == team_id,
                        TeamStaffAssignment.staff_role == input.staff_role,
                        TeamStaffAssignment.ended_at.is_(None),
                    )
                    .with_for_update()
                )
                if current is not None:
                    raise TeamAssignmentConflict()
                now = support.utc_now()
                result = TeamMutationResult(operation_id=uuid4(), assignment_id=uuid4())
                support.record_operation(
                    self.db, actor, input.idempotency_key, action, digest, result, now
                )
                self.db.add(
                    TeamStaffAssignment(
                        id=result.assignment_id,
                        account_id=input.account_id,
                        team_id=team_id,
                        staff_role=input.staff_role,
                        created_at=now,
                        created_by_user_id=actor.account.id,
                        open_operation_id=result.operation_id,
                    )
                )
                self.db.flush()
                self._sync_allocation(input.account_id, actor.account.id, now)
                support.append_audit(self.db, actor, input.idempotency_key, action, result, team_id)
                return result
        except IntegrityError as exc:
            translated = support.integrity_conflict(exc)
            if translated is not None:
                raise translated from exc
            raise

    def unassign(
        self, actor: AuthContext, assignment_id: UUID, input: UnassignStaffInput
    ) -> TeamMutationResult:
        from app.team_management import write_support as support

        action = "team_staff.unassign"
        digest = support.fingerprint(action, {"assignment_id": assignment_id})
        with atomic_write_transaction(self.db):
            support.lock_operation(self.db, actor.account.id, input.idempotency_key)
            located = self.db.execute(
                select(TeamStaffAssignment.account_id, TeamStaffAssignment.team_id).where(
                    TeamStaffAssignment.id == assignment_id
                )
            ).one_or_none()
            if located is None:
                raise TeamAssignmentNotFound()
            support.lock_command_participants(self.db, actor, account_id=located.account_id)
            support.lock_directory(self.db, [located.team_id])
            support.require_team(self.db, actor, located.team_id, "assign_managers")
            previous = support.replay(
                self.db, actor.account.id, input.idempotency_key, action, digest
            )
            if previous is not None:
                return previous
            # A realized personnel position is the authoritative source row.
            # The target person/account and directory are already locked above;
            # take its position lock before the downstream account assignment.
            from app.team_management.person_staff_models import PersonTeamStaffPosition
            from app.team_management.person_staff_service import end_person_staff_in_transaction

            position = self.db.scalar(
                select(PersonTeamStaffPosition)
                .where(PersonTeamStaffPosition.account_assignment_id == assignment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            row = self.db.scalar(
                select(TeamStaffAssignment)
                .where(TeamStaffAssignment.id == assignment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise TeamAssignmentNotFound()
            if row.ended_at is not None:
                raise TeamAssignmentStale()
            now = support.utc_now()
            result = TeamMutationResult(operation_id=uuid4(), assignment_id=row.id)
            support.record_operation(
                self.db, actor, input.idempotency_key, action, digest, result, now
            )
            if position is not None:
                end_person_staff_in_transaction(
                    self.db,
                    position,
                    actor_user_id=actor.account.id,
                    occurred_at=now,
                    reason="通过账号工作人员入口结束岗位",
                    operation_id=result.operation_id,
                )
            else:
                row.ended_at, row.ended_by_user_id = now, actor.account.id
                row.end_kind, row.close_operation_id = "unassigned", result.operation_id
            self.db.flush()
            self._sync_allocation(row.account_id, actor.account.id, now)
            support.append_audit(self.db, actor, input.idempotency_key, action, result, row.team_id)
            return result
