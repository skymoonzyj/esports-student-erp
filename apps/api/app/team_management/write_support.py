from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.rbac.models import Role, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.access import TeamAccessService
from app.team_management.errors import (
    TeamAccessDenied,
    TeamAssignmentConflict,
    TeamCandidateIneligible,
    TeamInactive,
    TeamMemberConflict,
    TeamNotFound,
    TeamOperationConflict,
    TeamServiceError,
)
from app.team_management.models import TeamOperation
from app.team_management.schemas import TeamAction, TeamMutationResult


def utc_now() -> datetime:
    return datetime.now(UTC)


def lock_operation(db: Session, actor_account_id: UUID, idempotency_key: UUID) -> None:
    """The key domain is shared across all five commands, not scoped to action."""
    digest = hashlib.sha256(
        f"team-operation:{actor_account_id}:{idempotency_key}".encode()
    ).digest()
    lock_id = int.from_bytes(digest[:8], byteorder="big", signed=True)
    db.execute(select(func.pg_advisory_xact_lock(lock_id)))


def lock_participants(db: Session, person_ids: Sequence[UUID], account_ids: Sequence[UUID]) -> None:
    """Call once with every participant, before any project/team/relationship lock."""
    for model, ids in ((Person, person_ids), (UserAccount, account_ids)):
        if ids:
            list(
                db.scalars(
                    select(model)
                    .where(model.id.in_(set(ids)))
                    .order_by(model.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )


def lock_directory(db: Session, team_ids: Sequence[UUID]) -> dict[UUID, ProfessionalTeam]:
    """After identity locks, acquire sorted projects, then sorted teams.

    Lifecycle callers discover their current relation team IDs after account locks
    and before this helper. It never acquires additional identity locks.
    """
    ids = set(team_ids)
    project_ids = set(
        db.scalars(select(ProfessionalTeam.project_id).where(ProfessionalTeam.id.in_(ids)))
    )
    if project_ids:
        list(
            db.scalars(
                select(GameProject)
                .where(GameProject.id.in_(project_ids))
                .order_by(GameProject.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
    return {
        team.id: team
        for team in db.scalars(
            select(ProfessionalTeam)
            .where(ProfessionalTeam.id.in_(ids))
            .order_by(ProfessionalTeam.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    }


def lock_command_participants(
    db: Session,
    actor: AuthContext,
    *,
    person_id: UUID | None = None,
    account_id: UUID | None = None,
) -> None:
    person_ids = {actor.person.id}
    account_ids = {actor.account.id}
    if person_id is not None:
        person_ids.add(person_id)
        account_ids.update(
            db.scalars(select(UserAccount.id).where(UserAccount.person_id == person_id))
        )
    if account_id is not None:
        account_ids.add(account_id)
    person_ids.update(
        db.scalars(select(UserAccount.person_id).where(UserAccount.id.in_(account_ids)))
    )
    lock_participants(db, list(person_ids), list(account_ids))


def fingerprint(action: str, payload: Mapping[str, object]) -> str:
    normalized = {
        key: str(value)
        if isinstance(value, UUID)
        else value.strip()
        if key == "reason" and isinstance(value, str)
        else value
        for key, value in payload.items()
    }
    encoded = json.dumps(
        {"action": action, "payload": normalized},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def require_team(
    db: Session, actor: AuthContext, team_id: UUID, action: TeamAction
) -> ProfessionalTeam:
    try:
        return TeamAccessService(db).require_team(actor, team_id, action)
    except TeamAccessDenied as exc:
        # Write resource paths reveal neither role changes nor object existence.
        raise TeamNotFound() from exc


def require_active_team(db: Session, team: ProfessionalTeam) -> None:
    project = db.get(GameProject, team.project_id)
    if (
        project is None
        or not project.is_team_division
        or not project.is_active
        or not team.is_active
    ):
        raise TeamInactive()


def require_candidate(
    db: Session, *, role_key: str, person_id: UUID | None = None, account_id: UUID | None = None
) -> UserAccount:
    statement = (
        select(UserAccount)
        .join(Person, Person.id == UserAccount.person_id)
        .join(UserRole, UserRole.user_id == UserAccount.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            UserAccount.status == "active",
            UserAccount.force_password_change.is_(False),
            Person.deleted_at.is_(None),
            Role.key == role_key,
        )
    )
    statement = (
        statement.where(UserAccount.person_id == person_id)
        if person_id is not None
        else statement.where(UserAccount.id == account_id)
    )
    account = db.scalar(statement)
    if account is None:
        raise TeamCandidateIneligible(
            "该人员当前不符合管理人员条件" if role_key == "team_manager" else None
        )
    return account


def member_eligible_predicate() -> ColumnElement[bool]:
    """The accountless exception is exclusively an approved assisted player."""
    from app.identity.assignments.models import PersonAssignmentState
    from app.personnel_intake.models import (
        AssistedApproval,
        AssistedPersonProfile,
        PersonnelIntakeRequest,
    )

    independent_player = exists(
        select(PersonAssignmentState.person_id).where(
            PersonAssignmentState.person_id == Person.id, PersonAssignmentState.is_player
        )
    )

    approved = exists(
        select(AssistedPersonProfile.person_id)
        .join(AssistedApproval, AssistedApproval.id == AssistedPersonProfile.current_approval_id)
        .join(PersonnelIntakeRequest, PersonnelIntakeRequest.id == AssistedApproval.request_id)
        .where(
            AssistedPersonProfile.person_id == Person.id,
            or_(PersonnelIntakeRequest.kind.in_(["player", "both"]), independent_player),
        )
    )
    role = exists(
        select(UserRole.user_id)
        .join(Role)
        .where(UserRole.user_id == UserAccount.id, Role.key == "team_member")
    )
    return and_(
        Person.deleted_at.is_(None),
        or_(
            and_(UserAccount.id.is_(None), approved),
            and_(
                UserAccount.status == "active", UserAccount.force_password_change.is_(False), role
            ),
        ),
    )


def require_member_candidate(
    db: Session,
    *,
    person_id: UUID,
    cipher: EnvelopeCipher | None = None,
    lookup: LookupDigest | None = None,
) -> Person:
    person = db.scalar(
        select(Person)
        .outerjoin(UserAccount, UserAccount.person_id == Person.id)
        .where(Person.id == person_id, member_eligible_predicate())
    )
    if person is None:
        raise TeamCandidateIneligible()
    if db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None:
        from app.identity.assignments.team_eligibility import trusted_business_player

        if not trusted_business_player(db, person_id, cipher=cipher, lookup=lookup):
            raise TeamCandidateIneligible()
    return person


def replay(
    db: Session, actor_id: UUID, key: UUID, action: str, digest: str
) -> TeamMutationResult | None:
    """Only call after current resource authorization, before new-write eligibility."""
    operation = db.scalar(
        select(TeamOperation).where(
            TeamOperation.actor_account_id == actor_id, TeamOperation.idempotency_key == key
        )
    )
    if operation is None:
        return None
    if operation.action != action or operation.request_fingerprint != digest:
        raise TeamOperationConflict()
    return TeamMutationResult.model_validate(operation.result_ids)


def record_operation(
    db: Session,
    actor: AuthContext,
    key: UUID,
    action: str,
    digest: str,
    result: TeamMutationResult,
    now: datetime,
) -> None:
    db.add(
        TeamOperation(
            id=result.operation_id,
            actor_account_id=actor.account.id,
            idempotency_key=key,
            action=action,
            request_fingerprint=digest,
            result_ids=result.model_dump(mode="json"),
            created_at=now,
        )
    )
    db.flush()


def append_audit(
    db: Session,
    actor: AuthContext,
    key: UUID,
    action: str,
    result: TeamMutationResult,
    team_id: UUID,
    *,
    person_id: UUID | None = None,
    source_team_id: UUID | None = None,
    purpose: str | None = None,
) -> None:
    metadata = {"operation_key": str(key), "professional_team_id": str(team_id)}
    for name, value in result.model_dump().items():
        if value is not None:
            metadata[name] = str(value)
    if person_id is not None:
        metadata["person_id"] = str(person_id)
    if source_team_id is not None:
        metadata.update(source_team_id=str(source_team_id), target_team_id=str(team_id))
    AuditService(db).append(
        actor_user_id=actor.account.id,
        action=action,
        resource_type=(
            "person_team_staff_position"
            if action.startswith("person_staff.")
            else "team_staff_assignment"
            if action.startswith("team_staff.")
            else "team_manager_assignment"
            if result.assignment_id
            else "team_membership"
        ),
        resource_id=result.assignment_id or result.membership_id or result.closed_membership_id,
        purpose=purpose,
        outcome="allowed",
        request_id=str(key),
        metadata=metadata,
    )


def integrity_conflict(exc: IntegrityError) -> TeamServiceError | None:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_team_memberships_open_person":
        return TeamMemberConflict()
    if constraint in {
        "uq_team_manager_assignments_open_account_team",
        "uq_team_staff_assignments_open_account_team_role",
        "uq_person_team_staff_current",
    }:
        return TeamAssignmentConflict()
    if constraint == "uq_team_operations_actor_key":
        return TeamOperationConflict()
    return None
