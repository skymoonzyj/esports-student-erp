from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.team_management import write_support as support
from app.team_management.errors import (
    TeamAssignmentConflict,
    TeamAssignmentNotFound,
    TeamAssignmentStale,
)
from app.team_management.models import TeamManagerAssignment
from app.team_management.schemas import AssignManagerInput, TeamMutationResult, UnassignManagerInput


class TeamAssignmentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def assign(
        self, actor: AuthContext, team_id: UUID, input: AssignManagerInput
    ) -> TeamMutationResult:
        action = "team_managers.assign"
        digest = support.fingerprint(action, {"team_id": team_id, "account_id": input.account_id})
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
                    self.db, role_key="team_manager", account_id=input.account_id
                )
                current = self.db.scalar(
                    select(TeamManagerAssignment)
                    .where(
                        TeamManagerAssignment.account_id == input.account_id,
                        TeamManagerAssignment.team_id == team_id,
                        TeamManagerAssignment.ended_at.is_(None),
                    )
                    .order_by(TeamManagerAssignment.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if current is not None:
                    raise TeamAssignmentConflict()
                now = support.utc_now()
                result = TeamMutationResult(operation_id=uuid4(), assignment_id=uuid4())
                support.record_operation(
                    self.db, actor, input.idempotency_key, action, digest, result, now
                )
                self.db.add(
                    TeamManagerAssignment(
                        id=result.assignment_id,
                        account_id=input.account_id,
                        team_id=team_id,
                        created_at=now,
                        created_by_user_id=actor.account.id,
                        open_operation_id=result.operation_id,
                    )
                )
                self.db.flush()
                support.append_audit(self.db, actor, input.idempotency_key, action, result, team_id)
                return result
        except IntegrityError as exc:
            translated = support.integrity_conflict(exc)
            if translated is not None:
                raise translated from exc
            raise

    def unassign(
        self, actor: AuthContext, assignment_id: UUID, input: UnassignManagerInput
    ) -> TeamMutationResult:
        action = "team_managers.unassign"
        digest = support.fingerprint(action, {"assignment_id": assignment_id})
        with atomic_write_transaction(self.db):
            support.lock_operation(self.db, actor.account.id, input.idempotency_key)
            located = self.db.execute(
                select(TeamManagerAssignment.account_id, TeamManagerAssignment.team_id).where(
                    TeamManagerAssignment.id == assignment_id
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
            row = self.db.scalar(
                select(TeamManagerAssignment)
                .where(TeamManagerAssignment.id == assignment_id)
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
            row.ended_at = now
            row.ended_by_user_id = actor.account.id
            row.close_operation_id = result.operation_id
            row.end_kind = "unassigned"
            self.db.flush()
            support.append_audit(self.db, actor, input.idempotency_key, action, result, row.team_id)
            return result
