"""Personnel staff commands; never provision an account or a management binding."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.personnel_intake.role_grant_models import ActivationRolePlan
from app.personnel_intake.role_grants import ActivationRoleService, latest_plan, plan_fingerprint
from app.rbac.models import Role
from app.rbac.service import PermissionDelegationDenied
from app.security.encryption import LookupDigest
from app.teaching.models import ProfessionalTeam
from app.team_management import write_support as support
from app.team_management.access import TeamAccessService
from app.team_management.allocation import sync_team_allocation_in_transaction
from app.team_management.errors import (
    TeamAccessDenied,
    TeamAssignmentConflict,
    TeamAssignmentNotFound,
    TeamAssignmentStale,
    TeamServiceError,
)
from app.team_management.models import TeamStaffAssignment
from app.team_management.person_staff_models import (
    PersonTeamStaffAuthorization,
    PersonTeamStaffPosition,
)
from app.team_management.person_staff_schemas import (
    PersonStaffAssignInput,
    PersonStaffEndInput,
    PersonStaffReconfirmInput,
    StaffAuthorizationEvidence,
    StaffRole,
    StaffTeamSelection,
    staff_authorization_digest,
)
from app.team_management.schemas import TeamMutationResult
from app.team_management.staff_service import STAFF_ROLE_KEYS


class PersonStaffSourceInvalid(TeamServiceError):
    code = "person_staff_source_invalid"
    status = 409
    detail = "工作人员角色或队伍授权已失效，请先重新确认角色授权"


class PersonStaffReasonInvalid(TeamServiceError):
    code = "validation_error"
    status = 422
    detail = "操作原因不得包含证件、联系方式或其他敏感资料"


def require_staff_manager(db: Session, actor: AuthContext, team_id: UUID) -> None:
    access = TeamAccessService(db)
    if not access._actor_roles(actor).intersection({"administration", "super_admin"}):
        raise TeamAccessDenied("仅行政或超级管理员可维护队伍工作人员")
    access.require_team(actor, team_id, "assign_managers")


def authorization_evidence(
    position: PersonTeamStaffPosition, proof: PersonTeamStaffAuthorization
) -> StaffAuthorizationEvidence:
    return StaffAuthorizationEvidence(
        position_id=position.id,
        person_id=position.person_id,
        team_id=position.team_id,
        origin_approval_id=position.origin_approval_id,
        staff_role=cast(StaffRole, position.staff_role),
        version=proof.version,
        authorizer_user_id=proof.authorizer_user_id,
        authorized_role_key=cast(
            Literal["team_manager", "coach", "assistant_coach"], proof.authorized_role_key
        ),
        role_plan_id=proof.role_plan_id,
        operation_id=proof.operation_id,
    )


def authentic_authorization(
    lookup: LookupDigest,
    position: PersonTeamStaffPosition,
    proof: PersonTeamStaffAuthorization | None,
) -> bool:
    if proof is None or (position.ended_at is None and proof.version != position.version):
        return False
    try:
        expected = staff_authorization_digest(lookup, authorization_evidence(position, proof))
        return hmac.compare_digest(expected, proof.evidence_digest)
    except ValueError:
        return False


@dataclass(frozen=True)
class PreparedPersonStaffLocks:
    """Read-only discovery to compose with intake/activation's participant prefix.

    Call discover BEFORE any person/account lock. Caller merges all person_ids,
    account_ids, role_ids and team_ids with its other work, locks people/accounts,
    calls lock_before_directory, then locks all directories and positions. No
    method here silently extends an upstream lock set after directory locks.
    """

    person_id: UUID
    person_ids: frozenset[UUID]
    account_ids: frozenset[UUID]
    role_ids: frozenset[UUID]
    team_ids: frozenset[UUID]
    snapshot: tuple[object, ...]

    @classmethod
    def discover(cls, db: Session, person_id: UUID) -> PreparedPersonStaffLocks:
        plan = latest_plan(db, person_id)
        positions = list(
            db.scalars(
                select(PersonTeamStaffPosition)
                .where(
                    PersonTeamStaffPosition.person_id == person_id,
                    PersonTeamStaffPosition.ended_at.is_(None),
                )
                .order_by(PersonTeamStaffPosition.id)
                .execution_options(populate_existing=True)
            )
        )
        proofs = list(
            db.scalars(
                select(PersonTeamStaffAuthorization)
                .where(PersonTeamStaffAuthorization.position_id.in_([p.id for p in positions]))
                .order_by(
                    PersonTeamStaffAuthorization.position_id, PersonTeamStaffAuthorization.version
                )
            )
        )
        plans = list(
            db.scalars(
                select(ActivationRolePlan).where(
                    ActivationRolePlan.id.in_(
                        {p.role_plan_id for p in proofs if p.role_plan_id is not None}
                    )
                )
            )
        )
        if plan is not None:
            plans.append(plan)
        account_ids = {p.authorizer_user_id for p in proofs} | {p.authorizer_user_id for p in plans}
        account_ids.update(
            db.scalars(select(UserAccount.id).where(UserAccount.person_id == person_id))
        )
        person_ids = {person_id} | set(
            db.scalars(select(UserAccount.person_id).where(UserAccount.id.in_(account_ids)))
        )
        role_keys = {k for p in plans for k in p.role_keys} | set(STAFF_ROLE_KEYS.values())
        role_ids = frozenset(db.scalars(select(Role.id).where(Role.key.in_(role_keys))))
        snapshot: tuple[object, ...] = (
            plan_fingerprint(plan),
            tuple(
                (p.id, p.version, p.account_assignment_id, p.team_id, p.staff_role)
                for p in positions
            ),
            tuple(
                (p.id, p.version, p.authorizer_user_id, p.role_plan_id, p.evidence_digest)
                for p in proofs
            ),
            tuple(sorted((plan_fingerprint(p) for p in plans), key=str)),
        )
        return cls(
            person_id,
            frozenset(person_ids),
            frozenset(account_ids),
            role_ids,
            frozenset(p.team_id for p in positions),
            snapshot,
        )

    def lock_before_directory(self, db: Session) -> None:
        if self.discover(db, self.person_id) != self:
            raise TeamAssignmentStale("工作人员岗位或角色计划已更新，请重试")
        list(
            db.scalars(
                select(Role)
                .where(Role.id.in_(self.role_ids))
                .order_by(Role.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if self.discover(db, self.person_id) != self:
            raise TeamAssignmentStale("工作人员岗位或角色计划已更新，请重试")


def lock_person_staff_positions(db: Session, person_id: UUID) -> list[PersonTeamStaffPosition]:
    return list(
        db.scalars(
            select(PersonTeamStaffPosition)
            .where(
                PersonTeamStaffPosition.person_id == person_id,
                PersonTeamStaffPosition.ended_at.is_(None),
            )
            .order_by(PersonTeamStaffPosition.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def end_person_staff_in_transaction(
    db: Session,
    position: PersonTeamStaffPosition,
    *,
    actor_user_id: UUID,
    occurred_at: datetime,
    reason: str,
    operation_id: UUID | None,
    end_kind: str = "unassigned",
) -> None:
    """Caller owns participant/directory/position locks; no upstream lock or commit.

    Historical/unrealized positions can always be ended by the authorized outer
    command; invalid original grantors do not block cleanup.
    """
    if position.ended_at is not None:
        return
    linked = None
    if position.account_assignment_id is not None:
        linked = db.scalar(
            select(TeamStaffAssignment)
            .where(TeamStaffAssignment.id == position.account_assignment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        person_id = (
            db.scalar(select(UserAccount.person_id).where(UserAccount.id == linked.account_id))
            if linked
            else None
        )
        if linked is None or (person_id, linked.team_id, linked.staff_role) != (
            position.person_id,
            position.team_id,
            position.staff_role,
        ):
            raise PersonStaffSourceInvalid("工作人员岗位与账号关联不一致，请联系管理员处理")
    position.ended_at, position.ended_by_user_id, position.end_reason = (
        occurred_at,
        actor_user_id,
        reason.strip(),
    )
    position.version += 1
    if linked is not None and linked.ended_at is None:
        linked.ended_at, linked.ended_by_user_id = occurred_at, actor_user_id
        linked.end_kind, linked.close_operation_id = end_kind, operation_id
    db.flush()


class PersonStaffService:
    def __init__(self, db: Session, *, lookup: LookupDigest) -> None:
        self.db, self.lookup = db, lookup
        self.roles = ActivationRoleService(db, lookup=lookup)

    def _role_source(self, person_id: UUID, staff_role: str) -> ActivationRolePlan:
        plan = latest_plan(self.db, person_id)
        person = self.db.get(Person, person_id)
        profile = self.db.get(AssistedPersonProfile, person_id)
        if (
            person is None
            or person.deleted_at is not None
            or profile is None
            or plan is None
            or STAFF_ROLE_KEYS[staff_role] not in plan.role_keys
        ):
            raise PersonStaffSourceInvalid()
        try:
            self.roles.authenticate(plan)
            self.roles.validate(plan.authorizer_user_id, person_id, [STAFF_ROLE_KEYS[staff_role]])
        except (PersonnelIntakeError, PermissionDelegationDenied) as exc:
            raise PersonStaffSourceInvalid() from exc
        return plan

    def _append_proof(
        self,
        actor: AuthContext,
        position: PersonTeamStaffPosition,
        *,
        role_plan_id: UUID | None,
        operation_id: UUID,
        occurred_at: datetime,
    ) -> None:
        proof = PersonTeamStaffAuthorization(
            position_id=position.id,
            version=position.version,
            authorizer_user_id=actor.account.id,
            authorized_role_key=STAFF_ROLE_KEYS[position.staff_role],
            role_plan_id=role_plan_id,
            operation_id=operation_id,
            created_at=occurred_at,
        )
        proof.evidence_digest = staff_authorization_digest(
            self.lookup, authorization_evidence(position, proof)
        )
        self.db.add(proof)
        self.db.flush()

    def create_in_transaction(
        self,
        actor: AuthContext,
        *,
        person_id: UUID,
        approval_id: UUID,
        selections: list[StaffTeamSelection],
        operation_id: UUID,
        occurred_at: datetime,
        pending_role_keys: list[str] | None = None,
    ) -> list[PersonTeamStaffPosition]:
        """Caller owns complete participant/role/directory locks and atomic replay.

        Never call the public assign command from an outer intake transaction.
        Authorization and allocation writes participate in the caller's rollback.
        """
        if (
            self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is not None
        ):
            raise PersonStaffSourceInvalid("账号已激活，请使用账号工作人员分配入口")
        if (
            self.db.scalar(
                select(AssistedApproval.id).where(
                    AssistedApproval.id == approval_id, AssistedApproval.person_id == person_id
                )
            )
            is None
        ):
            raise PersonStaffSourceInvalid()
        selected: set[tuple[UUID, str]] = set()
        prepared = []
        seen_roles: set[str] = set()
        for group in selections:
            if group.staff_role in seen_roles:
                raise PersonStaffSourceInvalid("工作人员岗位分组不能重复")
            seen_roles.add(group.staff_role)
            if not group.team_ids:
                continue
            if pending_role_keys is not None:
                from app.personnel_intake.role_intent_policy import (
                    REQUESTABLE_ROLE_KEYS,
                    require_direct_intake,
                )
                require_direct_intake(self.db, actor)
                if not set(pending_role_keys) <= REQUESTABLE_ROLE_KEYS or STAFF_ROLE_KEYS[group.staff_role] not in pending_role_keys:
                    raise PersonStaffSourceInvalid()
                plan = None
            else:
                plan = self._role_source(person_id, group.staff_role)
            for team_id in group.team_ids:
                if (team_id, group.staff_role) in selected:
                    raise TeamAssignmentConflict()
                selected.add((team_id, group.staff_role))
                if pending_role_keys is None:
                    require_staff_manager(self.db, actor, team_id)
                else:
                    from app.personnel_intake.access import PersonnelIntakeAccess
                    if not PersonnelIntakeAccess(self.db).snapshot(actor).allowed("personnel_intake.review", team_id=team_id):
                        raise PersonStaffSourceInvalid()
                team = self.db.get(ProfessionalTeam, team_id)
                if team is None:
                    raise PersonStaffSourceInvalid()
                support.require_active_team(self.db, team)
                prepared.append((team_id, group.staff_role, plan))
        current = set(
            self.db.execute(
                select(PersonTeamStaffPosition.team_id, PersonTeamStaffPosition.staff_role).where(
                    PersonTeamStaffPosition.person_id == person_id,
                    PersonTeamStaffPosition.ended_at.is_(None),
                )
            ).tuples()
        )
        if current & selected:
            raise TeamAssignmentConflict()
        result = []
        for team_id, role, plan in sorted(prepared, key=lambda item: (str(item[0]), item[1])):
            position = PersonTeamStaffPosition(
                id=uuid4(),
                person_id=person_id,
                team_id=team_id,
                staff_role=role,
                origin_approval_id=approval_id,
                created_by_user_id=actor.account.id,
                started_at=occurred_at,
                version=1,
            )
            self.db.add(position)
            self.db.flush()
            self._append_proof(
                actor,
                position,
                role_plan_id=plan.id if plan else None,
                operation_id=operation_id,
                occurred_at=occurred_at,
            )
            result.append(position)
        if result:
            sync_team_allocation_in_transaction(
                self.db,
                person_id=person_id,
                actor_user_id=actor.account.id,
                occurred_at=occurred_at,
            )
        return result

    def _lock(self, actor: AuthContext, person_id: UUID, team_id: UUID) -> None:
        prepared = PreparedPersonStaffLocks.discover(self.db, person_id)
        support.lock_participants(
            self.db,
            sorted(prepared.person_ids | {actor.person.id}),
            sorted(prepared.account_ids | {actor.account.id}),
        )
        prepared.lock_before_directory(self.db)
        support.lock_directory(self.db, sorted(prepared.team_ids | {team_id}))
        lock_person_staff_positions(self.db, person_id)
        require_staff_manager(self.db, actor, team_id)

    def assign_person(
        self, actor: AuthContext, team_id: UUID, command: PersonStaffAssignInput
    ) -> TeamMutationResult:
        action = "person_staff.assign"
        digest = support.fingerprint(
            action,
            {"team_id": team_id, "person_id": command.person_id, "staff_role": command.staff_role},
        )
        try:
            with atomic_write_transaction(self.db):
                support.lock_operation(self.db, actor.account.id, command.idempotency_key)
                self._lock(actor, command.person_id, team_id)
                previous = support.replay(
                    self.db, actor.account.id, command.idempotency_key, action, digest
                )
                if previous is not None:
                    return previous
                plan = self._role_source(command.person_id, command.staff_role)
                result = TeamMutationResult(operation_id=uuid4())
                positions = self.create_in_transaction(
                    actor,
                    person_id=command.person_id,
                    approval_id=plan.origin_approval_id,
                    selections=[
                        StaffTeamSelection(staff_role=command.staff_role, team_ids=[team_id])
                    ],
                    operation_id=result.operation_id,
                    occurred_at=support.utc_now(),
                )
                result.assignment_id = positions[0].id
                support.record_operation(
                    self.db,
                    actor,
                    command.idempotency_key,
                    action,
                    digest,
                    result,
                    positions[0].started_at,
                )
                support.append_audit(
                    self.db,
                    actor,
                    command.idempotency_key,
                    action,
                    result,
                    team_id,
                    person_id=command.person_id,
                )
                return result
        except IntegrityError as exc:
            translated = support.integrity_conflict(exc)
            if translated is not None:
                raise translated from exc
            raise

    def _maintain(
        self,
        actor: AuthContext,
        position_id: UUID,
        command: PersonStaffEndInput,
        *,
        reconfirm: bool,
    ) -> TeamMutationResult:
        try:
            AuditService.validate_purpose(command.reason)
        except AuditMetadataRejected as exc:
            raise PersonStaffReasonInvalid() from exc
        action = "person_staff.reconfirm" if reconfirm else "person_staff.end"
        digest = support.fingerprint(
            action,
            {
                "position_id": position_id,
                "expected_version": command.expected_version,
                "reason": command.reason,
            },
        )
        with atomic_write_transaction(self.db):
            support.lock_operation(self.db, actor.account.id, command.idempotency_key)
            located = self.db.get(PersonTeamStaffPosition, position_id)
            if located is None:
                raise TeamAssignmentNotFound()
            self._lock(actor, located.person_id, located.team_id)
            previous = support.replay(
                self.db, actor.account.id, command.idempotency_key, action, digest
            )
            if previous is not None:
                return previous
            position = self.db.scalar(
                select(PersonTeamStaffPosition)
                .where(PersonTeamStaffPosition.id == position_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                position is None
                or position.ended_at is not None
                or position.version != command.expected_version
            ):
                raise TeamAssignmentStale("工作人员岗位已变化，请刷新后重试")
            now = support.utc_now()
            result = TeamMutationResult(operation_id=uuid4(), assignment_id=position.id)
            if reconfirm:
                team = self.db.get(ProfessionalTeam, position.team_id)
                assert team is not None
                support.require_active_team(self.db, team)
                proof = self.db.scalar(
                    select(PersonTeamStaffAuthorization).where(
                        PersonTeamStaffAuthorization.position_id == position.id,
                        PersonTeamStaffAuthorization.version == position.version,
                    )
                )
                if position.account_assignment_id is None:
                    # A null link is not proof of account absence. Once an
                    # account exists, only an intact realized source can be
                    # confirmed; this command never creates or repairs it.
                    if (
                        self.db.scalar(
                            select(UserAccount.id).where(
                                UserAccount.person_id == position.person_id
                            )
                        )
                        is not None
                    ):
                        raise PersonStaffSourceInvalid()
                    plan = self._role_source(position.person_id, position.staff_role)
                    role_plan_id: UUID | None = plan.id
                else:
                    self._require_realized_source(position)
                    role_plan_id = proof.role_plan_id if proof is not None else None
                position.version += 1
                self._append_proof(
                    actor,
                    position,
                    role_plan_id=role_plan_id,
                    operation_id=result.operation_id,
                    occurred_at=now,
                )
            else:
                # Persist operation before its linked assignment FK is written.
                support.record_operation(
                    self.db, actor, command.idempotency_key, action, digest, result, now
                )
                end_person_staff_in_transaction(
                    self.db,
                    position,
                    actor_user_id=actor.account.id,
                    occurred_at=now,
                    reason=command.reason,
                    operation_id=result.operation_id,
                )
                sync_team_allocation_in_transaction(
                    self.db,
                    person_id=position.person_id,
                    actor_user_id=actor.account.id,
                    occurred_at=now,
                )
            if reconfirm:
                support.record_operation(
                    self.db, actor, command.idempotency_key, action, digest, result, now
                )
            support.append_audit(
                self.db,
                actor,
                command.idempotency_key,
                action,
                result,
                position.team_id,
                person_id=position.person_id,
                purpose=command.reason,
            )
            return result

    def _require_realized_source(self, position: PersonTeamStaffPosition) -> None:
        linked = self.db.get(TeamStaffAssignment, position.account_assignment_id)
        if (
            linked is None
            or linked.ended_at is not None
            or (linked.team_id, linked.staff_role) != (position.team_id, position.staff_role)
        ):
            raise PersonStaffSourceInvalid()
        account = support.require_candidate(
            self.db, role_key=STAFF_ROLE_KEYS[position.staff_role], account_id=linked.account_id
        )
        if account.person_id != position.person_id:
            raise PersonStaffSourceInvalid()

    def end(
        self, actor: AuthContext, position_id: UUID, command: PersonStaffEndInput
    ) -> TeamMutationResult:
        return self._maintain(actor, position_id, command, reconfirm=False)

    def reconfirm(
        self, actor: AuthContext, position_id: UUID, command: PersonStaffReconfirmInput
    ) -> TeamMutationResult:
        return self._maintain(actor, position_id, command, reconfirm=True)
