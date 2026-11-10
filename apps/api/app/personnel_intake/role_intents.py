"""Pending role intent is not an authorization; only a new root decision grants it."""

import hmac
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.guardian_records import canonical_json
from app.personnel_intake.models import AssistedApproval, PersonnelIntakeRequest
from app.personnel_intake.role_grants import ActivationRoleService, latest_plan, role_error
from app.personnel_intake.role_intent_models import IntakeRoleDecision, IntakeRoleIntent
from app.personnel_intake.role_intent_policy import REQUESTABLE_ROLE_KEYS, require_direct_intake
from app.rbac.models import Role
from app.security.encryption import LookupDigest
from app.team_management.person_staff_models import (
    PersonTeamStaffAuthorization,
    PersonTeamStaffPosition,
)


class RoleIntentView(BaseModel):
    id: UUID
    person_id: UUID
    version: int
    role_keys: list[str]
    role_names: list[str]
    status: Literal["pending", "approved"]
    account_activated: bool
    can_approve: bool


class RoleIntentRow(RoleIntentView):
    full_name: str


class RoleIntentPage(BaseModel):
    items: list[RoleIntentRow]
    page: int
    page_size: int
    total: int


class RoleIntentApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    idempotency_key: UUID


def pending_intents(db: Session, person_ids: set[UUID]) -> dict[UUID, IntakeRoleIntent]:
    return {r.person_id: r for r in db.scalars(select(IntakeRoleIntent).where(
        IntakeRoleIntent.person_id.in_(person_ids),
        ~select(IntakeRoleDecision.id).where(IntakeRoleDecision.intent_id == IntakeRoleIntent.id).exists(),
    ))}


class RoleIntentService:
    def __init__(self, db: Session, *, lookup: LookupDigest):
        self.db, self.lookup = db, lookup

    def _digest(self, intent: IntakeRoleIntent) -> str:
        return self.lookup.digest_domain("intake-pending-role-intent", canonical_json({
            "id": str(intent.id), "person_id": str(intent.person_id),
            "origin_approval_id": str(intent.origin_approval_id),
            "created_by_user_id": str(intent.created_by_user_id), "version": intent.version,
            "role_keys": intent.role_keys, "position_evidence": intent.position_evidence,
        }))

    def authenticate(self, intent: IntakeRoleIntent) -> None:
        if (not intent.role_keys or not set(intent.role_keys) <= REQUESTABLE_ROLE_KEYS
                or len(intent.role_keys) != len(set(intent.role_keys))
                or not hmac.compare_digest(intent.intent_digest, self._digest(intent))):
            raise role_error("intent_invalid", 409, "待审批角色记录校验失败，请联系超级管理员")

    def pending_position(self, intent: IntakeRoleIntent | None,
                         position: PersonTeamStaffPosition,
                         proof: PersonTeamStaffAuthorization | None) -> bool:
        from app.team_management.person_staff_service import authentic_authorization
        from app.team_management.staff_service import STAFF_ROLE_KEYS

        if intent is None:
            return False
        self.authenticate(intent)
        return bool(proof and position.person_id == intent.person_id
            and position.origin_approval_id == intent.origin_approval_id
            and proof.role_plan_id is None and position.account_assignment_id is None
            and position.ended_at is None
            and STAFF_ROLE_KEYS[position.staff_role] in intent.role_keys
            and intent.position_evidence.get(str(position.id)) == proof.evidence_digest
            and proof.version == position.version
            and authentic_authorization(self.lookup, position, proof))

    def record_in_transaction(self, actor: AuthContext, *, person_id: UUID,
                              approval_id: UUID, keys: list[str],
                              positions: list[PersonTeamStaffPosition]) -> IntakeRoleIntent:
        require_direct_intake(self.db, actor)
        if not keys or not set(keys) <= REQUESTABLE_ROLE_KEYS or len(set(keys)) != len(keys):
            raise role_error("invalid", 422, "不能申请此账号角色")
        proofs = list(self.db.scalars(select(PersonTeamStaffAuthorization).where(
            PersonTeamStaffAuthorization.position_id.in_([p.id for p in positions]),
            PersonTeamStaffAuthorization.version == 1)))
        intent = IntakeRoleIntent(id=uuid4(), person_id=person_id, origin_approval_id=approval_id,
            created_by_user_id=actor.account.id, role_keys=sorted(keys), version=1,
            position_evidence={str(p.position_id): p.evidence_digest for p in proofs})
        intent.intent_digest = self._digest(intent)
        self.db.add(intent)
        self.db.flush()
        AuditService(self.db).append(actor_user_id=actor.account.id,
            action="profile_activation.roles_requested", resource_type="person", resource_id=person_id,
            purpose=None, outcome="allowed", request_id=str(intent.id),
            metadata={"new_role_keys": intent.role_keys, "operation_id": str(intent.id)})
        return intent

    def _target_allowed(self, actor: AuthContext, intent: IntakeRoleIntent) -> bool:
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        request = self.db.scalar(select(PersonnelIntakeRequest).join(
            AssistedApproval, AssistedApproval.request_id == PersonnelIntakeRequest.id).where(
            AssistedApproval.id == intent.origin_approval_id,
            AssistedApproval.person_id == intent.person_id))
        return bool(request and all(state.allowed(permission,
            target_person_id=intent.person_id, coaches=request.target_coach_person_ids,
            team_id=request.target_team_id) for permission in ("people.read", "personnel_intake.review")))

    def _can_approve(self, actor: AuthContext, intent: IntakeRoleIntent | None = None) -> bool:
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        return bool("super_admin" in state.roles and state.allowed("permissions.manage")
                    and state.allowed("personnel_intake.review") and state.allowed("people.read")
                    and (intent is None or self._target_allowed(actor, intent)
                         and state.allowed("permissions.manage", target_person_id=intent.person_id)))

    def _require_approver(self, actor: AuthContext, intent: IntakeRoleIntent | None = None) -> None:
        if not self._can_approve(actor, intent):
            raise role_error("forbidden", 403, "仅超级管理员可批准待授权角色")

    def _view(self, actor: AuthContext, intent: IntakeRoleIntent) -> RoleIntentView:
        self.authenticate(intent)
        decided = self.db.scalar(select(IntakeRoleDecision.id).where(IntakeRoleDecision.intent_id == intent.id)) is not None
        names = {key: name for key, name in self.db.execute(select(Role.key, Role.name).where(Role.key.in_(intent.role_keys)))}
        return RoleIntentView(id=intent.id, person_id=intent.person_id, version=intent.version,
            role_keys=intent.role_keys, role_names=[names.get(k, k) for k in intent.role_keys],
            status="approved" if decided else "pending", can_approve=not decided and self._can_approve(actor, intent),
            account_activated=self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == intent.person_id)) is not None)

    def get(self, actor: AuthContext, person_id: UUID) -> RoleIntentView | None:
        require_direct_intake(self.db, actor)
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        if not state.allowed("people.read", target_person_id=person_id):
            raise role_error("forbidden", 403, "无权查看待授权角色")
        intent = self.db.scalar(select(IntakeRoleIntent).join(Person, Person.id == IntakeRoleIntent.person_id)
            .where(IntakeRoleIntent.person_id == person_id, Person.deleted_at.is_(None)))
        if intent and not self._target_allowed(actor, intent):
            raise role_error("forbidden", 403, "无权查看待授权角色")
        return self._view(actor, intent) if intent else None

    def list(self, actor: AuthContext, *, page: int = 1, page_size: int = 20) -> RoleIntentPage:
        self._require_approver(actor)
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        query = select(IntakeRoleIntent, Person.full_name).join(Person, Person.id == IntakeRoleIntent.person_id).join(
            AssistedApproval, AssistedApproval.id == IntakeRoleIntent.origin_approval_id).join(
            PersonnelIntakeRequest, PersonnelIntakeRequest.id == AssistedApproval.request_id).where(
            state.permission_predicate("people.read"), state.permission_predicate("personnel_intake.review"),
            Person.deleted_at.is_(None), ~select(IntakeRoleDecision.id).where(IntakeRoleDecision.intent_id == IntakeRoleIntent.id).exists())
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(query.order_by(IntakeRoleIntent.created_at, IntakeRoleIntent.id)
            .offset((page - 1) * page_size).limit(page_size))
        return RoleIntentPage(page=page, page_size=page_size, total=total,
            items=[RoleIntentRow(**self._view(actor, row.IntakeRoleIntent).model_dump(), full_name=row.full_name) for row in rows])

    def approve(self, actor: AuthContext, person_id: UUID, command: RoleIntentApproval) -> RoleIntentView:
        from app.registration.verification_service import (
            lock_registration_credential_keys,
            registration_advisory_lock_key,
        )
        from app.teaching.models import ProfessionalTeam
        from app.team_management.person_staff_service import (
            PersonStaffService,
            PreparedPersonStaffLocks,
            lock_person_staff_positions,
            require_staff_manager,
        )
        from app.team_management.staff_service import STAFF_ROLE_KEYS, _assign_staff_in_transaction
        from app.team_management.write_support import (
            lock_directory,
            lock_participants,
            require_active_team,
            require_candidate,
        )

        digest = self.lookup.digest_domain("intake-role-decision", canonical_json({
            "person_id": str(person_id), "expected_version": command.expected_version}))
        with atomic_write_transaction(self.db):
            self._require_approver(actor)
            lock_registration_credential_keys(self.db, [registration_advisory_lock_key(
                "intake-role-decision", f"{actor.account.id}:{command.idempotency_key}")])
            intent = self.db.scalar(select(IntakeRoleIntent).where(IntakeRoleIntent.person_id == person_id))
            if intent is None:
                raise role_error("not_found", 404, "未找到待授权角色")
            self.authenticate(intent)
            prepared = PreparedPersonStaffLocks.discover(self.db, person_id)
            people = prepared.person_ids | {person_id, actor.person.id}
            accounts = set(self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(people))))
            lock_participants(self.db, sorted(people), sorted(accounts))
            if PreparedPersonStaffLocks.discover(self.db, person_id) != prepared:
                raise role_error("retry", 409, "人员角色或岗位已更新，请刷新重试")
            role_ids = set(prepared.role_ids) | set(self.db.scalars(select(Role.id).where(Role.key.in_(intent.role_keys))))
            list(self.db.scalars(select(Role).where(Role.id.in_(role_ids)).order_by(Role.id).with_for_update().execution_options(populate_existing=True)))
            lock_directory(self.db, sorted(prepared.team_ids))
            positions = lock_person_staff_positions(self.db, person_id)
            self._require_approver(actor, intent)
            if PreparedPersonStaffLocks.discover(self.db, person_id) != prepared:
                raise role_error("retry", 409, "人员角色或岗位已更新，请刷新重试")
            replay = self.db.scalar(select(IntakeRoleDecision).where(
                IntakeRoleDecision.approved_by_user_id == actor.account.id,
                IntakeRoleDecision.operation_key == command.idempotency_key))
            if replay:
                if replay.operation_digest != digest or replay.intent_id != intent.id:
                    raise role_error("conflict", 409, "操作标识已用于其他审批")
                return self._view(actor, intent)
            if command.expected_version != intent.version or self.db.scalar(select(IntakeRoleDecision.id).where(IntakeRoleDecision.intent_id == intent.id)):
                raise role_error("version_conflict", 409, "角色审批状态已变化，请刷新后重试")
            person = self.db.get(Person, person_id)
            if person is None or person.deleted_at is not None or person_id == actor.person.id:
                raise role_error("forbidden", 403, "不能批准此人员的角色")
            roles = ActivationRoleService(self.db, lookup=self.lookup)
            previous = latest_plan(self.db, person_id)
            if previous:
                roles.authenticate(previous)
            keys = sorted(set(intent.role_keys) | set(previous.role_keys if previous else []))
            roles.validate(actor.account.id, person_id, keys)
            active = self.db.scalar(select(UserAccount).where(UserAccount.person_id == person_id).execution_options(populate_existing=True))
            if active and active.status != "active":
                raise role_error("invalidated", 409, "账号状态异常，不能批准角色")
            pending_positions = []
            for position in positions:
                if str(position.id) not in intent.position_evidence:
                    continue
                proof = self.db.scalar(select(PersonTeamStaffAuthorization).where(
                    PersonTeamStaffAuthorization.position_id == position.id,
                    PersonTeamStaffAuthorization.version == position.version))
                if not self.pending_position(intent, position, proof):
                    raise role_error("invalidated", 409, "待批准队伍岗位已变化，请先核实")
                require_staff_manager(self.db, actor, position.team_id)
                team = self.db.get(ProfessionalTeam, position.team_id)
                if team is None:
                    raise role_error("invalidated", 409, "队伍已不可用")
                require_active_team(self.db, team)
                pending_positions.append(position)
            plan = roles.create_in_transaction(actor, person_id=person_id,
                approval_id=intent.origin_approval_id, keys=keys, previous=previous,
                operation_key=command.idempotency_key, operation_digest=digest,
                approved_intent_id=intent.id if active else None)
            if active:
                roles.apply_in_transaction(plan, active, only_role_keys=set(intent.role_keys))
            now = datetime.now(UTC)
            staff = PersonStaffService(self.db, lookup=self.lookup)
            for position in pending_positions:
                position.version += 1
                staff._append_proof(actor, position, role_plan_id=plan.id,
                    operation_id=plan.id, occurred_at=now)
                if active:
                    require_candidate(self.db, role_key=STAFF_ROLE_KEYS[position.staff_role], account_id=active.id)
                    position.account_assignment_id = _assign_staff_in_transaction(self.db,
                        account_id=active.id, team_id=position.team_id, staff_role=position.staff_role,
                        actor_user_id=actor.account.id, occurred_at=now).id
            self.db.add(IntakeRoleDecision(intent_id=intent.id, approved_by_user_id=actor.account.id,
                role_plan_id=plan.id, operation_key=command.idempotency_key, operation_digest=digest))
            self.db.flush()
            return self._view(actor, intent)
