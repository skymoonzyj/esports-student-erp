"""Read-only, batched prospective-role decisions using the canonical policy.

This is deliberately limited to the prospective-role resource mapping in
RbacService: self -> target person; family/other -> empty resource. A class or
cohort assignment therefore cannot match. Write callers still use the canonical
validators, including their locks and exceptions.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import BUSINESS_TIME_ZONE, business_date_at
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import Permission, Role, RolePermission, UserPermissionOverride, UserRole
from app.rbac.policy import Effect, PolicyRule, RbacPolicy, ScopeKind
from app.rbac.service import _TEAM_PERMISSION_SCOPES, prospective_role_scope


@dataclass(frozen=True)
class ProspectiveRoleCheck:
    actor_user_id: UUID
    person_id: UUID
    role_key: str


class ProspectiveRoleBatch:
    """One bounded SQL batch for any number of grantors, targets and roles."""

    def __init__(
        self,
        db: Session,
        checks: Iterable[ProspectiveRoleCheck],
        *,
        occurred_at: datetime | None = None,
    ) -> None:
        self.now = occurred_at or datetime.now(UTC)
        if self.now.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        self.checks = set(checks)
        self.policy = RbacPolicy()
        actor_ids = {c.actor_user_id for c in self.checks}
        self.accounts = {
            row.id: row
            for row in db.execute(
                select(
                    UserAccount.id,
                    UserAccount.person_id,
                    UserAccount.status,
                    UserAccount.force_password_change,
                    Person.deleted_at,
                )
                .join(Person, Person.id == UserAccount.person_id)
                .where(UserAccount.id.in_(actor_ids))
            )
        }
        self.roles: dict[UUID, set[str]] = defaultdict(set)
        self.rules: dict[UUID, list[PolicyRule]] = defaultdict(list)
        for uid, key in db.execute(
            select(UserRole.user_id, Role.key)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(actor_ids))
        ):
            self.roles[uid].add(key)
        self.bundle: dict[str, list[tuple[str, str]]] = defaultdict(list)
        keys = {c.role_key for c in self.checks} | set().union(*self.roles.values())
        self.existing_roles = set(db.scalars(select(Role.key).where(Role.key.in_(keys))))
        by_role: dict[str, list[PolicyRule]] = defaultdict(list)
        for key, permission, scope in db.execute(
            select(Role.key, Permission.key, RolePermission.scope_kind)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(Role.key.in_(keys))
        ):
            self.bundle[key].append((permission, scope))
            by_role[key].append(PolicyRule(permission, "allow", cast(ScopeKind, scope)))
        for uid, role_keys in self.roles.items():
            self.rules[uid] = [r for key in role_keys for r in by_role[key]]
        self.overrides: dict[UUID, list[tuple[PolicyRule, datetime | None, datetime | None]]] = (
            defaultdict(list)
        )
        for uid, permission, effect, scope, sid, start, end in db.execute(
            select(
                UserPermissionOverride.user_id,
                Permission.key,
                UserPermissionOverride.effect,
                UserPermissionOverride.scope_kind,
                UserPermissionOverride.scope_id,
                UserPermissionOverride.valid_from,
                UserPermissionOverride.valid_until,
            )
            .join(Permission, Permission.id == UserPermissionOverride.permission_id)
            .where(UserPermissionOverride.user_id.in_(actor_ids))
        ):
            self.overrides[uid].append(
                (
                    PolicyRule(permission, cast(Effect, effect), cast(ScopeKind, scope), sid),
                    start,
                    end,
                )
            )
        self.families: dict[UUID, list[GuardianRelationship]] = defaultdict(list)
        for relationship in db.scalars(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id.in_(
                    {a.person_id for a in self.accounts.values()}
                ),
                GuardianRelationship.requires_verification.is_(False),
            )
        ):
            self.families[relationship.guardian_person_id].append(relationship)

    def _live(self, uid: UUID) -> bool:
        a = self.accounts.get(uid)
        return bool(
            a and a.status == "active" and not a.force_password_change and a.deleted_at is None
        )

    def _overrides(self, uid: UUID, at: datetime) -> list[PolicyRule]:
        return [
            r
            for r, start, end in self.overrides[uid]
            if (start is None or start <= at) and (end is None or end > at)
        ]

    def _tokens(self, uid: UUID, pid: UUID, at: datetime, *, manager: bool = False) -> set[str]:
        a = self.accounts[uid]
        tokens = {"global", f"person:{pid}"}
        if a.person_id == pid:
            tokens.add(f"self:{pid}")
        day = business_date_at(at)
        if any(
            r.student_person_id == pid
            and r.valid_from <= day
            and (r.valid_until is None or r.valid_until >= day)
            and (r.ended_at is None if manager else r.ended_at is None or r.ended_at > at)
            for r in self.families[a.person_id]
        ):
            tokens.add(f"family:{pid}")
        return tokens

    def _allowed(self, uid: UUID, permission: str, tokens: set[str], at: datetime) -> bool:
        return self.policy.decide(
            permission, tokens, self.rules[uid], self._overrides(uid, at)
        ).allowed

    def can_assign_team(self, uid: UUID, team_id: UUID) -> bool:
        return (
            self._live(uid)
            and bool(self.roles[uid] & {"administration", "super_admin"})
            and self._allowed(uid, "team_manager_assignments.manage", {"global"}, self.now)
            and self._allowed(
                uid,
                "team_manager_assignments.manage",
                {"global", f"assigned_team:{team_id}"},
                self.now,
            )
        )

    def allowed(self, check: ProspectiveRoleCheck) -> bool:
        uid, pid, key = check.actor_user_id, check.person_id, check.role_key
        if not self._live(uid) or key not in self.existing_roles:
            return False
        if not all(
            self._allowed(uid, "permissions.manage", tokens, self.now)
            for tokens in (
                {"global"},
                self._tokens(uid, pid, self.now, manager=True),
            )
        ):
            return False
        if "super_admin" in self.roles[uid]:
            return True  # Canonical delegation-root shortcut, after both manager checks.
        if key in {"super_admin", "finance"}:
            return False
        for permission, role_scope in self.bundle[key]:
            delegated_scope = prospective_role_scope(role_scope)
            if permission in _TEAM_PERMISSION_SCOPES and delegated_scope == "global":
                return False  # Canonical global team delegation requires actual root.
            boundaries = {self.now}
            for rule, start, end in self.overrides[uid]:
                if rule.permission == permission:
                    boundaries.update(t for t in (start, end) if t is not None and t >= self.now)
            if delegated_scope != "family":
                for r in self.families[self.accounts[uid].person_id]:
                    dates = [datetime.combine(r.valid_from, time.min, tzinfo=BUSINESS_TIME_ZONE)]
                    if r.valid_until is not None:
                        dates.append(
                            datetime.combine(
                                r.valid_until + timedelta(days=1),
                                time.min,
                                tzinfo=BUSINESS_TIME_ZONE,
                            )
                        )
                    if r.ended_at is not None:
                        dates.append(r.ended_at)
                    boundaries.update(t for t in dates if t >= self.now)
            for at in boundaries:
                overrides = self._overrides(uid, at)
                if (
                    permission not in _TEAM_PERMISSION_SCOPES
                    and delegated_scope in {"global", "family"}
                    and any(r.permission == permission and r.effect == "deny" for r in overrides)
                ):
                    return False
                tokens = (
                    self._tokens(uid, pid, at)
                    if delegated_scope == "person" and permission not in _TEAM_PERMISSION_SCOPES
                    else {"global"}
                )
                if not self._allowed(uid, permission, tokens, at):
                    return False
        return True
