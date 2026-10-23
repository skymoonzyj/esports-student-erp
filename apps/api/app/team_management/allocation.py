"""Explicit allocation state; never infer a pending task from an empty roster."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, and_, exists, false, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import SQLColumnExpression

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.team_management import write_support as support
from app.team_management.access import TeamAccessService
from app.team_management.errors import TeamNotFound, TeamOperationConflict, TeamServiceError
from app.team_management.models import TeamManagerAssignment, TeamMembership, TeamOperation
from app.team_management.projection import current_membership_read_filter
from app.team_management.schemas import (
    TeamAllocationInput,
    TeamAllocationProjection,
    TeamAllocationStatus,
)


class TeamAllocationStale(TeamServiceError):
    code = "team_allocation_stale"
    status = 409
    detail = "队伍归属状态已变化，请刷新后重试"


class TeamAllocationHasRelationships(TeamServiceError):
    code = "team_allocation_has_relationships"
    status = 409
    detail = "存在真实队伍关系，请先通过队伍管理结束关系"


def _relationships(
    person_id: UUID | SQLColumnExpression[UUID],
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    member = exists(
        select(TeamMembership.id).where(
            TeamMembership.person_id == person_id, TeamMembership.ended_at.is_(None)
        )
    )
    manager = exists(
        select(TeamManagerAssignment.id)
        .join(UserAccount, UserAccount.id == TeamManagerAssignment.account_id)
        .where(UserAccount.person_id == person_id, TeamManagerAssignment.ended_at.is_(None))
    )
    return member, manager


def allocation_read_scope(db: Session, actor: AuthContext) -> tuple[ColumnElement[bool], bool]:
    """One authorized predicate for projections, counts and pre-pagination filters.

    Hidden relationships never become a readable empty assignment. Unassigned
    access is exclusively the existing global administrator or live self grant.
    """
    access = TeamAccessService(db)
    roles = access._actor_roles(actor)
    decisions = access.rbac.decision_context(actor.account.id)
    global_read = (
        "super_admin" in roles and decisions.decide("team_memberships.read", {"global"}).allowed
    )
    own_decision = decisions.decide("team_memberships.read", {"global", f"self:{actor.person.id}"})
    own_read = bool(roles & {"super_admin", "team_member"}) and own_decision.allowed
    empty_allowed = or_(
        true() if global_read else false(), Person.id == actor.person.id if own_read else false()
    )
    member, manager = _relationships(Person.id)
    member_readable = exists(
        select(TeamMembership.id).where(
            TeamMembership.person_id == Person.id,
            TeamMembership.ended_at.is_(None),
            current_membership_read_filter(db, actor),
        )
    )
    visible_teams = access.visible_team_ids(actor, "read")
    manager_readable = exists(
        select(TeamManagerAssignment.id)
        .join(UserAccount, UserAccount.id == TeamManagerAssignment.account_id)
        .where(
            UserAccount.person_id == Person.id,
            TeamManagerAssignment.ended_at.is_(None),
            TeamManagerAssignment.team_id.in_(visible_teams),
        )
    )
    scope = or_(member_readable, manager_readable, and_(~member, ~manager, empty_allowed))
    # Self denies win across alternative entrances, just as in real team reads.
    if own_decision.reason == "explicit_deny":
        scope = and_(scope, Person.id != actor.person.id)
    # A deny on the current own team must also revoke filter/count entry; an
    # empty SQL result is not a substitute for missing read authority.
    effective_self = own_read and bool(
        db.scalar(select(exists(select(Person.id).where(Person.id == actor.person.id, scope))))
    )
    enabled = bool(global_read or effective_self or db.scalar(select(exists(visible_teams))))
    return scope, enabled


def project_team_allocations(
    db: Session, actor: AuthContext, person_ids: Sequence[UUID]
) -> dict[UUID, TeamAllocationProjection]:
    result = {
        pid: TeamAllocationProjection(visibility="denied", status=None, version=None)
        for pid in person_ids
    }
    if not result:
        return result
    scope, _ = allocation_read_scope(db, actor)
    update_permissions = _update_permissions(db, actor, set(result))
    for pid, status, version in db.execute(
        select(Person.id, Person.team_allocation_status, Person.team_allocation_version).where(
            Person.id.in_(result), Person.deleted_at.is_(None), scope
        )
    ):
        result[pid] = TeamAllocationProjection(
            visibility="allowed",
            status=status,
            version=version,
            can_update=update_permissions[pid],
        )
    return result


def can_update_allocation(db: Session, actor: AuthContext, person_id: UUID) -> bool:
    return _update_permissions(db, actor, {person_id})[person_id]


def _update_permissions(db: Session, actor: AuthContext, person_ids: set[UUID]) -> dict[UUID, bool]:
    access = TeamAccessService(db)
    if not access._actor_roles(actor) & {"admin", "super_admin"}:
        return dict.fromkeys(person_ids, False)
    decisions = access.rbac.decision_context(actor.account.id)
    tokens = access.rbac.person_resource_tokens_many(
        actor_person_id=actor.person.id, target_person_ids=person_ids
    )
    global_update = decisions.decide("people.update", {"global"}).allowed
    return {
        pid: global_update
        and decisions.decide("people.update", tokens[pid]).allowed
        and decisions.decide("people.read", tokens[pid]).allowed
        for pid in person_ids
    }


def _audit(
    db: Session,
    *,
    person: Person,
    actor_user_id: UUID | None,
    old_status: str,
    old_version: int,
    occurred_at: datetime,
    key: UUID | None = None,
) -> None:
    AuditService(db).append(
        actor_user_id=actor_user_id,
        action="people.team_allocation.update",
        resource_type="person",
        resource_id=person.id,
        purpose=None,
        outcome="allowed",
        request_id=str(key or uuid4()),
        occurred_at=occurred_at,
        metadata={
            "person_id": str(person.id),
            "allocation_old_status": old_status,
            "allocation_new_status": person.team_allocation_status,
            "old_version": old_version,
            "new_version": person.team_allocation_version,
        },
    )


def sync_team_allocation_in_transaction(
    db: Session, *, person_id: UUID, actor_user_id: UUID | None, occurred_at: datetime
) -> None:
    """Caller owns Person locks and transaction; state/audit share its rollback."""
    db.flush()
    person = db.get(Person, person_id)
    if person is None:
        raise TeamNotFound()
    member, manager = _relationships(person_id)
    has_relationship = bool(db.scalar(select(or_(member, manager))))
    desired = (
        "assigned"
        if has_relationship
        else (
            "pending"
            if person.team_allocation_status == "assigned"
            else person.team_allocation_status
        )
    )
    if desired == person.team_allocation_status:
        return
    old_status, old_version = person.team_allocation_status, person.team_allocation_version
    person.team_allocation_status = desired
    person.team_allocation_version += 1
    _audit(
        db,
        person=person,
        actor_user_id=actor_user_id,
        old_status=old_status,
        old_version=old_version,
        occurred_at=occurred_at,
    )
    db.flush()


class TeamAllocationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def update(
        self, actor: AuthContext, person_id: UUID, input: TeamAllocationInput
    ) -> TeamAllocationProjection:
        action = "people.team_allocation.update"
        digest = support.fingerprint(
            action, {"person_id": person_id, **input.model_dump(exclude={"idempotency_key"})}
        )
        with atomic_write_transaction(self.db):
            support.lock_operation(self.db, actor.account.id, input.idempotency_key)
            support.lock_command_participants(self.db, actor, person_id=person_id)
            access = TeamAccessService(self.db)
            if not access._actor_roles(actor) & {"admin", "super_admin"}:
                raise TeamNotFound()
            person = self.db.get(Person, person_id)
            if person is None or person.deleted_at is not None:
                raise TeamNotFound()
            if not can_update_allocation(self.db, actor, person_id):
                raise TeamNotFound()
            projection = project_team_allocations(self.db, actor, [person_id])[person_id]
            if projection.visibility != "allowed":
                raise TeamNotFound()
            previous = self.db.scalar(
                select(TeamOperation).where(
                    TeamOperation.actor_account_id == actor.account.id,
                    TeamOperation.idempotency_key == input.idempotency_key,
                )
            )
            if previous is not None:
                if previous.action != action or previous.request_fingerprint != digest:
                    raise TeamOperationConflict()
                return TeamAllocationProjection.model_validate(previous.result_ids)
            if person.team_allocation_version != input.expected_version:
                raise TeamAllocationStale()
            if self.db.scalar(select(or_(*_relationships(person_id)))):
                raise TeamAllocationHasRelationships()
            old_status, old_version = person.team_allocation_status, person.team_allocation_version
            person.team_allocation_status = input.status
            person.team_allocation_version += 1
            now = support.utc_now()
            result = TeamAllocationProjection(
                visibility="allowed",
                status=cast(TeamAllocationStatus, person.team_allocation_status),
                version=person.team_allocation_version,
                can_update=True,
            )
            self.db.add(
                TeamOperation(
                    id=uuid4(),
                    actor_account_id=actor.account.id,
                    idempotency_key=input.idempotency_key,
                    action=action,
                    request_fingerprint=digest,
                    result_ids=result.model_dump(mode="json"),
                    created_at=now,
                )
            )
            _audit(
                self.db,
                person=person,
                actor_user_id=actor.account.id,
                old_status=old_status,
                old_version=old_version,
                occurred_at=now,
                key=input.idempotency_key,
            )
            self.db.flush()
            return result
