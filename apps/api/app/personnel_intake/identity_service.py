"""Management-only identity materialization inside the approval transaction."""

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.identity.models import StaffProfile
from app.personnel_intake.activation_roles import activation_role_keys as baseline_roles
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.identity_models import IntakeIdentitySelection
from app.personnel_intake.identity_selection import (
    ManagedIdentityInput,
    ResolvedIntakeIdentity,
    StaffTeamSelection,
    canonical_identity_selection,
    identity_selection_fingerprint,
    normalize_staff_team_selections,
    resolve_managed_identity,
)
from app.personnel_intake.role_grants import ActivationRoleService
from app.security.encryption import LookupDigest
from app.team_management.person_staff_service import PersonStaffService


def resolve_management_identity(
    *, kind: str | None, identity: ManagedIdentityInput | None,
    role_keys: list[str] | None, staff_teams: list[StaffTeamSelection] | None,
) -> tuple[ResolvedIntakeIdentity | None, list[StaffTeamSelection], list[str] | None]:
    try:
        if identity is None:
            if kind is None or staff_teams:
                raise ValueError("缺少人员身份")
            return None, [], role_keys
        resolved = resolve_managed_identity(identity)
        groups = normalize_staff_team_selections(identity, staff_teams or [])
        if kind is not None and kind != resolved.kind:
            raise ValueError("人员身份表达不一致")
        if role_keys is not None and (
            len(role_keys) != len(set(role_keys))
            or set(role_keys) - baseline_roles(resolved.kind) != set(resolved.activation_role_keys)
        ):
            raise ValueError("账号角色与人员身份不一致")
        return resolved, groups, resolved.activation_role_keys
    except ValueError as exc:
        raise PersonnelIntakeError(
            "validation_error", status=422, detail="人员身份与归属参数不一致，请检查后重试"
        ) from exc


class IntakeIdentityService:
    def __init__(self, db: Session, *, lookup: LookupDigest):
        self.db, self.lookup = db, lookup

    def materialize_in_transaction(
        self, actor: AuthContext, *, person_id: UUID, approval_id: UUID,
        selection: ResolvedIntakeIdentity, staff_teams: list[StaffTeamSelection],
        operation_key: UUID, operation_digest: str, occurred_at: datetime,
    ) -> None:
        """Outer approval owns all locks, commit/rollback and idempotency."""
        if selection.staff_type is not None:
            staff = self.db.get(StaffProfile, person_id)
            if staff is None:
                staff = StaffProfile(person_id=person_id, staff_type=selection.staff_type)
                self.db.add(staff)
            else:
                staff.staff_type = selection.staff_type
        self.db.add(IntakeIdentitySelection(
            person_id=person_id, origin_approval_id=approval_id,
            primary_identity=selection.selection.primary_identity,
            concurrent_identities=selection.selection.concurrent_identities,
            primary_staff_identity=selection.selection.primary_staff_identity,
            resolved_staff_type=selection.staff_type, created_by_user_id=actor.account.id,
            created_at=occurred_at,
            operation_summary=json.loads(canonical_identity_selection(selection.selection, staff_teams)),
            operation_digest=identity_selection_fingerprint(
                self.lookup, selection.selection, staff_teams
            ),
        ))
        from app.personnel_intake.role_intent_policy import (
            REQUESTABLE_ROLE_KEYS,
            require_direct_intake,
        )
        from app.personnel_intake.role_intents import RoleIntentService

        pending = False
        roles = ActivationRoleService(self.db, lookup=self.lookup)
        if selection.activation_role_keys:
            try:
                roles._require_manager(actor)
                roles.validate(actor.account.id, person_id, selection.activation_role_keys)
            except PersonnelIntakeError as exc:
                if exc.code != "activation_roles_forbidden" or not set(selection.activation_role_keys) <= REQUESTABLE_ROLE_KEYS:
                    raise
                require_direct_intake(self.db, actor)
                pending = True
            if staff_teams and not roles.rbac.account_has_role(actor.account.id, "super_admin"):
                require_direct_intake(self.db, actor)
                pending = True
        if selection.activation_role_keys and not pending:
            roles.create_in_transaction(
                actor, person_id=person_id, approval_id=approval_id,
                keys=selection.activation_role_keys, operation_key=operation_key,
                operation_digest=operation_digest,
            )
        self.db.flush()
        positions = PersonStaffService(self.db, lookup=self.lookup).create_in_transaction(
            actor, person_id=person_id, approval_id=approval_id, selections=staff_teams,
            operation_id=approval_id, occurred_at=occurred_at,
            pending_role_keys=selection.activation_role_keys if pending else None,
        )
        if pending:
            RoleIntentService(self.db, lookup=self.lookup).record_in_transaction(
                actor, person_id=person_id, approval_id=approval_id,
                keys=selection.activation_role_keys, positions=positions)
