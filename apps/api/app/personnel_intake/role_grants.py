"""Versioned extra roles; approval and activation both enforce current authority."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.activation_roles import activation_role_keys
from app.personnel_intake.editor_context import AssistedEditorContextQuery
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import canonical_json
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
)
from app.personnel_intake.role_grant_models import ActivationRolePlan
from app.personnel_intake.schemas import (
    ActivationRoleOption,
    ActivationRoleOptions,
    ActivationRoleUpdate,
    ActivationRoleView,
    PersonKind,
)
from app.rbac.models import Role
from app.rbac.service import PermissionDelegationDenied, RbacService
from app.registration.verification_service import (
    lock_registration_credential_keys,
    registration_advisory_lock_key,
)
from app.security.encryption import LookupDigest
from app.team_management.write_support import lock_participants


def role_error(code: str, status: int, detail: str) -> PersonnelIntakeError:
    return PersonnelIntakeError(f"activation_roles_{code}", status=status, detail=detail)


def activation_roles_available(db: Session, actor: AuthContext, person_id: UUID) -> bool:
    state = PersonnelIntakeAccess(db).snapshot(actor)
    if not (
        state.roles & {"admin", "super_admin"}
        and state.allowed("personnel_intake.review")
        and state.allowed("people.read", target_person_id=person_id)
    ):
        return False
    try:
        AssistedEditorContextQuery(db).get(actor, person_id)
    except PersonnelIntakeError:
        return False
    return True


def latest_plan(db: Session, person_id: UUID) -> ActivationRolePlan | None:
    return db.scalar(
        select(ActivationRolePlan)
        .where(ActivationRolePlan.person_id == person_id)
        .order_by(ActivationRolePlan.version.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


def plan_fingerprint(plan: ActivationRolePlan | None) -> tuple[object, ...] | None:
    if plan is None:
        return None
    return (
        plan.id,
        plan.person_id,
        plan.origin_approval_id,
        plan.authorizer_user_id,
        plan.version,
        tuple(plan.role_keys),
        plan.plan_digest,
    )


@dataclass(frozen=True)
class PreparedActivationRoleLocks:
    """Read-only discovery; no Role locks may be added after the directory/OTP prefix.

    Include baseline roles even when no explicit plan exists: their UserRole
    inserts acquire FK KEY SHARE locks which cannot safely be upgraded later by
    concurrent extra-role assignments. UUID ordering matches job-role changes.
    """

    person_id: UUID
    approval_id: UUID
    kind: PersonKind
    plan: tuple[object, ...] | None
    role_ids_by_key: dict[str, UUID]

    @classmethod
    def discover(cls, db: Session, person_id: UUID) -> "PreparedActivationRoleLocks":
        row = db.execute(
            select(AssistedPersonProfile.current_approval_id, PersonnelIntakeRequest.kind)
            .select_from(AssistedPersonProfile)
            .join(
                AssistedApproval, AssistedApproval.id == AssistedPersonProfile.current_approval_id
            )
            .join(PersonnelIntakeRequest, PersonnelIntakeRequest.id == AssistedApproval.request_id)
            .where(AssistedPersonProfile.person_id == person_id)
        ).one_or_none()
        if row is None:
            raise role_error("invalidated", 409, "激活角色授权已失效，请联系管理员重新确认")
        plan = latest_plan(db, person_id)
        kind = cast(PersonKind, row.kind)
        keys = activation_role_keys(kind) | set(plan.role_keys if plan else ())
        roles = {
            key: role_id
            for key, role_id in db.execute(select(Role.key, Role.id).where(Role.key.in_(keys)))
        }
        if roles.keys() != keys:
            raise role_error("invalidated", 409, "激活角色授权已失效，请联系管理员重新确认")
        return cls(person_id, row.current_approval_id, kind, plan_fingerprint(plan), roles)

    def lock_before_directory(self, db: Session) -> None:
        # The caller already owns the COMPLETE person/account set, including the
        # grantor. Recheck before taking Role locks, never discover a new grantor
        # or extend the role lock set after the downstream directory/OTP locks.
        if self.discover(db, self.person_id) != self:
            raise role_error("retry", 409, "激活角色计划已更新，请重试激活")
        locked = {
            role.key: role.id
            for role in db.scalars(
                select(Role)
                .where(Role.id.in_(self.role_ids_by_key.values()))
                .order_by(Role.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        if locked != self.role_ids_by_key:
            raise role_error("retry", 409, "激活角色计划已更新，请重试激活")

    def baseline_ids(self, authenticated_kind: PersonKind) -> list[UUID]:
        if authenticated_kind != self.kind:
            raise role_error("retry", 409, "激活角色计划已更新，请重试激活")
        return sorted(self.role_ids_by_key[key] for key in activation_role_keys(authenticated_kind))


class ActivationRoleService:
    def __init__(self, db: Session, *, lookup: LookupDigest):
        self.db, self.lookup = db, lookup
        self.rbac = RbacService(db)

    def _require_manager(self, actor: AuthContext) -> None:
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        if not state.roles & {"admin", "super_admin"} or not state.allowed(
            "personnel_intake.review"
        ):
            raise role_error("forbidden", 403, "无权管理建档激活角色")

    def validate(self, actor_id: UUID, person_id: UUID, keys: list[str]) -> list[Role]:
        roles = list(self.db.scalars(select(Role).where(Role.key.in_(keys)).order_by(Role.key)))
        if (
            len(keys) != len(set(keys))
            or len(roles) != len(keys)
            or set(keys) & {"admin", "super_admin"}
        ):
            raise role_error("invalid", 422, "激活角色重复、不存在或不允许选择")
        try:
            # Clearing a plan requires management, not delegation of baseline identity roles.
            self.rbac.require_prospective_role_manager(actor_user_id=actor_id, person_id=person_id)
            for role in roles:
                self.rbac.require_prospective_role_grant(
                    actor_user_id=actor_id, person_id=person_id, role=role
                )
        except PermissionDelegationDenied as exc:
            raise role_error("forbidden", 403, "所选激活角色超过当前授权范围") from exc
        return roles

    def options(self, actor: AuthContext, *, for_intake: bool = False) -> ActivationRoleOptions:
        from app.personnel_intake.role_intent_policy import (
            REQUESTABLE_ROLE_KEYS,
            require_direct_intake,
        )

        if for_intake:
            require_direct_intake(self.db, actor)
        else:
            self._require_manager(actor)
        items = []
        for role in self.db.scalars(
            select(Role).where(Role.key.not_in({"admin", "super_admin"})).order_by(Role.key)
        ):
            try:
                self._require_manager(actor)
                self.validate(actor.account.id, uuid4(), [role.key])
                reason = None
            except PersonnelIntakeError:
                reason = "超过当前授权范围"
            items.append(
                ActivationRoleOption(
                    key=role.key, name=role.name, assignable=reason is None, reason=reason,
                    requestable=for_intake and role.key in REQUESTABLE_ROLE_KEYS,
                )
            )
        return ActivationRoleOptions(items=items)

    def _digest(self, plan: ActivationRolePlan) -> str:
        return self.lookup.digest_domain(
            "intake-activation-role-plan",
            canonical_json(
                {
                    "person_id": str(plan.person_id),
                    "origin_approval_id": str(plan.origin_approval_id),
                    "authorizer_user_id": str(plan.authorizer_user_id),
                    "version": plan.version,
                    "role_keys": plan.role_keys,
                    "operation_key": str(plan.operation_key),
                    "operation_digest": plan.operation_digest,
                }
            ),
        )

    def authenticate(self, plan: ActivationRolePlan) -> None:
        if not hmac.compare_digest(plan.plan_digest, self._digest(plan)):
            raise role_error("invalidated", 409, "激活角色授权已失效，请联系管理员重新确认")

    def _require_plan_editor(self, actor: AuthContext, plan: ActivationRolePlan | None) -> None:
        if (
            plan
            and "finance" in plan.role_keys
            and not self.rbac.account_has_role(actor.account.id, "super_admin")
        ):
            raise role_error("protected", 403, "财务角色计划仅限超级管理员修改")

    def get(self, actor: AuthContext, person_id: UUID) -> ActivationRoleView:
        if not activation_roles_available(self.db, actor, person_id):
            raise role_error("forbidden", 403, "无权查看激活角色计划")
        plan = latest_plan(self.db, person_id)
        if plan:
            self.authenticate(plan)
        activated = (
            self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is not None
        )
        try:
            self._require_manager(actor)
            self.validate(actor.account.id, person_id, [])
            self._require_plan_editor(actor, plan)
            can_edit = not activated
        except PersonnelIntakeError:
            can_edit = False
        keys = plan.role_keys if plan else []
        names = {
            key: name
            for key, name in self.db.execute(select(Role.key, Role.name).where(Role.key.in_(keys)))
        }
        return ActivationRoleView(
            person_id=person_id,
            version=plan.version if plan else 0,
            role_keys=keys,
            role_names=[names.get(k, k) for k in keys],
            can_edit=can_edit,
            account_activated=activated,
        )

    def create_in_transaction(
        self,
        actor: AuthContext,
        *,
        person_id: UUID,
        approval_id: UUID,
        keys: list[str],
        operation_key: UUID,
        operation_digest: str,
        previous: ActivationRolePlan | None = None,
        approved_intent_id: UUID | None = None,
    ) -> ActivationRolePlan:
        self._require_manager(actor)
        self.validate(actor.account.id, person_id, keys)
        activated = (
            self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is not None
        )
        if activated:
            from app.personnel_intake.role_intent_models import IntakeRoleIntent
            from app.personnel_intake.role_intents import RoleIntentService, pending_intents

            intent = self.db.get(IntakeRoleIntent, approved_intent_id) if approved_intent_id else None
            if intent is None or intent.person_id != person_id or person_id not in pending_intents(self.db, {person_id}):
                raise role_error("activated", 409, "账号已激活，请使用权限管理修改角色")
            reviewer = RoleIntentService(self.db, lookup=self.lookup)
            reviewer._require_approver(actor)
            reviewer.authenticate(intent)
            if set(keys) != set(intent.role_keys) | set(previous.role_keys if previous else []):
                raise role_error("invalid", 422, "审批角色与待授权意向不一致")
        plan = ActivationRolePlan(
            person_id=person_id,
            origin_approval_id=previous.origin_approval_id if previous else approval_id,
            authorizer_user_id=actor.account.id,
            role_keys=sorted(keys),
            version=previous.version + 1 if previous else 1,
            operation_key=operation_key,
            operation_digest=operation_digest,
        )
        plan.plan_digest = self._digest(plan)
        self.db.add(plan)
        self.db.flush()
        AuditService(self.db).append(
            actor_user_id=actor.account.id,
            action="profile_activation.roles_approved",
            resource_type="person",
            resource_id=person_id,
            purpose=None,
            outcome="allowed",
            request_id=str(operation_key),
            metadata={"new_role_keys": plan.role_keys, "operation_id": str(plan.id)},
        )
        return plan

    def update(
        self, actor: AuthContext, person_id: UUID, command: ActivationRoleUpdate
    ) -> ActivationRoleView:
        digest = self.lookup.digest_domain(
            "intake-activation-role-operation",
            canonical_json(
                {
                    "person_id": str(person_id),
                    "role_keys": sorted(command.role_keys),
                    "expected_version": command.expected_version,
                    **({"confirm_end_staff_positions": command.confirm_end_staff_positions}
                       if "confirm_end_staff_positions" in command.model_fields_set else {}),
                }
            ),
        )
        with atomic_write_transaction(self.db):
            self._require_manager(actor)
            lock_registration_credential_keys(
                self.db,
                [
                    registration_advisory_lock_key(
                        "activation-role-operation", f"{actor.account.id}:{command.idempotency_key}"
                    )
                ],
            )
            from app.team_management.allocation import sync_team_allocation_in_transaction
            from app.team_management.errors import TeamServiceError
            from app.team_management.person_staff_service import (
                PreparedPersonStaffLocks,
                end_person_staff_in_transaction,
                lock_person_staff_positions,
                require_staff_manager,
            )
            from app.team_management.schemas import TeamMutationResult
            from app.team_management.staff_service import STAFF_ROLE_KEYS
            from app.team_management.write_support import append_audit, lock_directory

            prepared = PreparedPersonStaffLocks.discover(self.db, person_id)
            ids = {person_id, actor.person.id} | set(prepared.person_ids)
            accounts = list(
                self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(ids)))
            )
            lock_participants(self.db, sorted(ids), sorted(accounts))
            if PreparedPersonStaffLocks.discover(self.db, person_id) != prepared:
                raise role_error("retry", 409, "工作人员岗位或角色计划已更新，请重试")
            role_ids = set(prepared.role_ids) | set(self.db.scalars(
                select(Role.id).where(Role.key.in_(command.role_keys))))
            list(self.db.scalars(select(Role).where(Role.id.in_(role_ids)).order_by(Role.id)
                 .with_for_update().execution_options(populate_existing=True)))
            lock_directory(self.db, sorted(prepared.team_ids))
            positions = lock_person_staff_positions(self.db, person_id)
            if PreparedPersonStaffLocks.discover(self.db, person_id) != prepared:
                raise role_error("retry", 409, "工作人员岗位或角色计划已更新，请重试")
            self._require_manager(actor)
            self.get(actor, person_id)
            from app.personnel_intake.role_intents import pending_intents

            if person_id in pending_intents(self.db, {person_id}):
                raise role_error("pending", 409, "请先处理待授权角色审批，再修改已批准计划")
            self._require_plan_editor(actor, latest_plan(self.db, person_id))
            self.validate(actor.account.id, person_id, command.role_keys)
            if (
                self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
                is not None
            ):
                raise role_error("activated", 409, "账号已激活，请使用权限管理修改角色")
            replay = self.db.scalar(
                select(ActivationRolePlan).where(
                    ActivationRolePlan.authorizer_user_id == actor.account.id,
                    ActivationRolePlan.operation_key == command.idempotency_key,
                )
            )
            if replay:
                if replay.operation_digest != digest:
                    raise role_error("conflict", 409, "操作标识已用于其他角色计划")
                return self.get(actor, person_id)
            previous = latest_plan(self.db, person_id)
            if (previous.version if previous else 0) != command.expected_version:
                raise role_error("version_conflict", 409, "激活角色计划已更新，请刷新后重试")
            affected = [p for p in positions if STAFF_ROLE_KEYS[p.staff_role] not in command.role_keys]
            try:
                for team_id in sorted({p.team_id for p in affected}):
                    require_staff_manager(self.db, actor, team_id)
            except TeamServiceError as exc:
                raise role_error("forbidden", 403, "无权结束受影响的队伍工作人员岗位") from exc
            if affected and not command.confirm_end_staff_positions:
                raise role_error("staff_positions_confirmation_required", 409,
                                 "移除身份将结束对应队伍岗位，请明确确认后重试")
            profile = self.db.get(AssistedPersonProfile, person_id)
            assert profile is not None
            # First explicit plan refers to the original approval, never an email correction.
            origin = self.db.scalar(
                select(AssistedApproval.id).where(
                    AssistedApproval.person_id == person_id,
                    AssistedApproval.supersedes_approval_id.is_(None),
                )
            )
            assert origin is not None
            new_plan = self.create_in_transaction(
                actor,
                person_id=person_id,
                approval_id=origin,
                keys=command.role_keys,
                operation_key=command.idempotency_key,
                operation_digest=digest,
                previous=previous,
            )
            now = datetime.now(UTC)
            for position in affected:
                end_person_staff_in_transaction(self.db, position, actor_user_id=actor.account.id,
                    occurred_at=now, reason="移除激活身份并确认结束岗位", operation_id=None,
                    end_kind="role_removed")
                append_audit(self.db, actor, command.idempotency_key, "person_staff.end",
                    TeamMutationResult(operation_id=new_plan.id, assignment_id=position.id),
                    position.team_id, person_id=person_id, purpose="移除激活身份并确认结束岗位")
            if affected:
                sync_team_allocation_in_transaction(self.db, person_id=person_id,
                    actor_user_id=actor.account.id, occurred_at=now)
            return self.get(actor, person_id)

    def apply_in_transaction(self, plan: ActivationRolePlan | None, account: UserAccount,
                             *, only_role_keys: set[str] | None = None) -> None:
        if plan is None:
            return
        try:
            self.authenticate(plan)
            if not plan.role_keys:
                return
            roles = self.validate(plan.authorizer_user_id, account.person_id, plan.role_keys)
            if only_role_keys is not None:
                if not only_role_keys <= set(plan.role_keys):
                    raise role_error("invalid", 422, "角色授权范围不一致")
                roles = [role for role in roles if role.key in only_role_keys]
            from app.rbac.models import UserRole

            for role in roles:
                # Activation owns complete participants and sorted Role rows before
                # directory/session/OTP locks. Never re-enter public RBAC locking here.
                if self.db.get(UserRole, (account.id, role.id)) is None:
                    self.db.add(UserRole(user_id=account.id, role_id=role.id))
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=plan.authorizer_user_id,
                action="profile_activation.roles_applied",
                resource_type="person",
                resource_id=account.person_id,
                purpose=None,
                outcome="allowed",
                request_id=str(uuid4()),
                metadata={"new_role_keys": [role.key for role in roles], "operation_id": str(plan.id)},
            )
        except (PersonnelIntakeError, PermissionDelegationDenied) as exc:
            raise role_error(
                "invalidated", 409, "激活角色授权已失效，请联系管理员重新确认"
            ) from exc
