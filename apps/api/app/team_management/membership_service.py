from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.core.transactions import atomic_write_transaction
from app.team_management import write_support as support
from app.team_management.errors import (
    TeamMemberConflict,
    TeamMemberNotFound,
    TeamMembershipStale,
    TeamSameTeam,
)
from app.team_management.models import TeamMembership
from app.team_management.schemas import (
    JoinTeamInput,
    LeaveTeamInput,
    TeamMutationResult,
    TransferTeamInput,
)


class TeamMembershipService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def join_assisted_in_transaction(
        self, actor: AuthContext, team_id: UUID, person_id: UUID, now: datetime
    ) -> UUID:
        """Approval owns all locks and its idempotency record; never transfers."""
        from app.personnel_intake.guardian_records import conflict
        from app.teaching.models import ProfessionalTeam

        team = self.db.get(ProfessionalTeam, team_id)
        if team is None:
            raise conflict()
        support.require_active_team(self.db, team)
        support.require_member_candidate(self.db, person_id=person_id)
        current = self.db.scalar(
            select(TeamMembership)
            .where(TeamMembership.person_id == person_id, TeamMembership.ended_at.is_(None))
            .with_for_update()
        )
        if current:
            if current.team_id != team_id:
                raise conflict()
            return current.id
        row = TeamMembership(
            person_id=person_id,
            team_id=team_id,
            joined_on=business_date_at(now),
            created_at=now,
            created_by_user_id=actor.account.id,
        )
        self.db.add(row)
        self.db.flush()
        return row.id

    def join(self, actor: AuthContext, team_id: UUID, input: JoinTeamInput) -> TeamMutationResult:
        action = "team_memberships.join"
        digest = support.fingerprint(action, {"team_id": team_id, "person_id": input.person_id})
        try:
            with atomic_write_transaction(self.db):
                support.lock_operation(self.db, actor.account.id, input.idempotency_key)
                support.lock_command_participants(self.db, actor, person_id=input.person_id)
                support.lock_directory(self.db, [team_id])
                team = support.require_team(self.db, actor, team_id, "manage")
                previous = support.replay(
                    self.db, actor.account.id, input.idempotency_key, action, digest
                )
                if previous is not None:
                    return previous
                support.require_active_team(self.db, team)
                support.require_member_candidate(self.db, person_id=input.person_id)
                current = self.db.scalar(
                    select(TeamMembership)
                    .where(
                        TeamMembership.person_id == input.person_id,
                        TeamMembership.ended_at.is_(None),
                    )
                    .order_by(TeamMembership.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if current is not None:
                    raise TeamMemberConflict()
                now = support.utc_now()
                result = TeamMutationResult(operation_id=uuid4(), membership_id=uuid4())
                support.record_operation(
                    self.db, actor, input.idempotency_key, action, digest, result, now
                )
                self.db.add(
                    TeamMembership(
                        id=result.membership_id,
                        person_id=input.person_id,
                        team_id=team_id,
                        joined_on=business_date_at(now),
                        created_at=now,
                        created_by_user_id=actor.account.id,
                        open_operation_id=result.operation_id,
                    )
                )
                self.db.flush()
                support.append_audit(
                    self.db,
                    actor,
                    input.idempotency_key,
                    action,
                    result,
                    team_id,
                    person_id=input.person_id,
                )
                return result
        except IntegrityError as exc:
            translated = support.integrity_conflict(exc)
            if translated is not None:
                raise translated from exc
            raise

    def _locate(self, membership_id: UUID) -> tuple[UUID, UUID]:
        row = self.db.execute(
            select(TeamMembership.person_id, TeamMembership.team_id).where(
                TeamMembership.id == membership_id
            )
        ).one_or_none()
        if row is None:
            raise TeamMemberNotFound()
        return row.person_id, row.team_id

    def _lock_membership(self, membership_id: UUID) -> TeamMembership:
        row = self.db.scalar(
            select(TeamMembership)
            .where(TeamMembership.id == membership_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise TeamMemberNotFound()
        return row

    def leave(
        self, actor: AuthContext, membership_id: UUID, input: LeaveTeamInput
    ) -> TeamMutationResult:
        return self._end(actor, membership_id, input)

    def transfer(
        self, actor: AuthContext, membership_id: UUID, input: TransferTeamInput
    ) -> TeamMutationResult:
        return self._end(actor, membership_id, input)

    def _end(
        self, actor: AuthContext, membership_id: UUID, input: LeaveTeamInput | TransferTeamInput
    ) -> TeamMutationResult:
        transferring = isinstance(input, TransferTeamInput)
        action = "team_memberships.transfer" if transferring else "team_memberships.leave"
        payload: dict[str, object] = {"membership_id": membership_id}
        if isinstance(input, TransferTeamInput):
            payload.update(target_team_id=input.target_team_id, reason=input.reason)
        digest = support.fingerprint(action, payload)
        try:
            with atomic_write_transaction(self.db):
                support.lock_operation(self.db, actor.account.id, input.idempotency_key)
                person_id, source_team_id = self._locate(membership_id)
                support.lock_command_participants(self.db, actor, person_id=person_id)
                team_ids = [source_team_id]
                if isinstance(input, TransferTeamInput):
                    team_ids.append(input.target_team_id)
                support.lock_directory(self.db, team_ids)
                support.require_team(
                    self.db, actor, source_team_id, "transfer" if transferring else "manage"
                )
                target = None
                if isinstance(input, TransferTeamInput):
                    target = support.require_team(self.db, actor, input.target_team_id, "transfer")
                previous = support.replay(
                    self.db, actor.account.id, input.idempotency_key, action, digest
                )
                if previous is not None:
                    return previous
                row = self._lock_membership(membership_id)
                if row.ended_at is not None:
                    raise TeamMembershipStale()
                if isinstance(input, TransferTeamInput):
                    if input.target_team_id == row.team_id:
                        raise TeamSameTeam()
                    assert target is not None
                    support.require_active_team(self.db, target)
                    support.require_member_candidate(self.db, person_id=row.person_id)
                now = support.utc_now()
                result = TeamMutationResult(
                    operation_id=uuid4(),
                    closed_membership_id=row.id,
                    membership_id=uuid4() if transferring else None,
                )
                support.record_operation(
                    self.db, actor, input.idempotency_key, action, digest, result, now
                )
                row.left_on = business_date_at(now)
                row.ended_at = now
                row.ended_by_user_id = actor.account.id
                row.close_operation_id = result.operation_id
                row.end_kind = "transferred" if transferring else "left"
                row.transfer_reason = input.reason if isinstance(input, TransferTeamInput) else None
                # Release the unique open-person slot before inserting the successor.
                self.db.flush()
                if isinstance(input, TransferTeamInput):
                    self.db.add(
                        TeamMembership(
                            id=result.membership_id,
                            person_id=row.person_id,
                            team_id=input.target_team_id,
                            joined_on=business_date_at(now),
                            created_at=now,
                            created_by_user_id=actor.account.id,
                            open_operation_id=result.operation_id,
                        )
                    )
                    self.db.flush()
                support.append_audit(
                    self.db,
                    actor,
                    input.idempotency_key,
                    action,
                    result,
                    input.target_team_id
                    if isinstance(input, TransferTeamInput)
                    else source_team_id,
                    person_id=row.person_id,
                    source_team_id=source_team_id if transferring else None,
                )
                return result
        except IntegrityError as exc:
            translated = support.integrity_conflict(exc)
            if translated is not None:
                raise translated from exc
            raise
