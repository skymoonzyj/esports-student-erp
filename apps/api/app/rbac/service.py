from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admissions.access import AdmissionsVisibility
from app.admissions.models import Enrollment
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.core.time import BUSINESS_TIME_ZONE, business_date_at, business_today
from app.core.transactions import write_transaction
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.housing.models import HousingBuilding, HousingFloor
from app.identity.models import GuardianRelationship, Person
from app.identity.readiness import GuardianReadinessPolicy
from app.rbac.models import (
    Permission,
    Role,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from app.rbac.policy import Effect, PolicyDecision, PolicyRule, RbacPolicy, ScopeKind
from app.rbac.schemas import PermissionOverrideCreate
from app.teaching.access import TeachingVisibility
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

_SENSITIVE_EVALUATION_PERMISSION_KEYS = frozenset(
    {
        "evaluation_media.upload",
        "evaluation_media.original.read",
        "evaluation_jobs.review",
        "analysis_profiles.manage",
        "student_game_accounts.manage",
        "student_reports.write",
        "student_reports.review",
        "camp_surveys.identity.read",
        "camp_surveys.print",
        "cohort_staff.manage",
    }
)


class RbacServiceError(ValueError):
    code = "rbac_service_error"


class RbacResourceNotFound(RbacServiceError):
    code = "rbac_resource_not_found"


class LastAdminProtected(RbacServiceError):
    code = "last_admin_protected"


class SelfPermissionLockout(RbacServiceError):
    code = "self_permission_lockout"


class DuplicatePermissionRule(RbacServiceError):
    code = "duplicate_permission_rule"


class InactivePermissionScope(RbacServiceError):
    code = "permission_scope_inactive"


class PermissionDelegationDenied(RbacServiceError):
    code = "permission_delegation_denied"


class InvalidPermissionOverride(RbacServiceError):
    code = "invalid_permission_override"


@dataclass(frozen=True)
class PermissionOverrideChange:
    permission_key: str
    effect: Effect
    scope_kind: ScopeKind
    scope_id: UUID | None


@dataclass(frozen=True)
class EffectivePermissionDetail:
    permission: Permission
    scope_kinds: tuple[ScopeKind, ...]
    sources: tuple[str, ...]


@dataclass(frozen=True)
class RbacDecisionContext:
    policy: RbacPolicy
    role_rules: list[PolicyRule]
    overrides: list[PolicyRule]

    def decide(self, permission: str, resource_tokens: set[str]) -> PolicyDecision:
        return self.policy.decide(
            permission,
            resource_tokens,
            self.role_rules,
            self.overrides,
        )


class RbacService:
    def __init__(self, db: Session, *, policy: RbacPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or RbacPolicy()

    def _rules(
        self,
        user_id: UUID,
        *,
        occurred_at: datetime | None = None,
    ) -> tuple[list[PolicyRule], list[PolicyRule]]:
        reference_time = occurred_at or datetime.now(UTC)
        if reference_time.tzinfo is None or reference_time.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        role_rules = [
            PolicyRule(
                permission=permission_key,
                effect="allow",
                scope_kind=cast(ScopeKind, scope_kind),
            )
            for permission_key, scope_kind in self.db.execute(
                select(Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(UserRole, UserRole.role_id == RolePermission.role_id)
                .where(UserRole.user_id == user_id)
            )
        ]
        overrides = [
            PolicyRule(
                permission=permission_key,
                effect=cast(Effect, effect),
                scope_kind=cast(ScopeKind, scope_kind),
                scope_id=scope_id,
            )
            for permission_key, effect, scope_kind, scope_id in self.db.execute(
                select(
                    Permission.key,
                    UserPermissionOverride.effect,
                    UserPermissionOverride.scope_kind,
                    UserPermissionOverride.scope_id,
                )
                .join(
                    UserPermissionOverride,
                    UserPermissionOverride.permission_id == Permission.id,
                )
                .where(
                    UserPermissionOverride.user_id == user_id,
                    or_(
                        UserPermissionOverride.valid_from.is_(None),
                        UserPermissionOverride.valid_from <= reference_time,
                    ),
                    or_(
                        UserPermissionOverride.valid_until.is_(None),
                        UserPermissionOverride.valid_until > reference_time,
                    ),
                )
            )
        ]
        return role_rules, overrides

    def decide(
        self,
        *,
        user_id: UUID,
        permission: str,
        resource_tokens: set[str],
        occurred_at: datetime | None = None,
    ) -> PolicyDecision:
        role_rules, overrides = self._rules(user_id, occurred_at=occurred_at)
        return self.policy.decide(permission, resource_tokens, role_rules, overrides)

    def decision_context(
        self,
        user_id: UUID,
        *,
        occurred_at: datetime | None = None,
    ) -> RbacDecisionContext:
        role_rules, overrides = self._rules(user_id, occurred_at=occurred_at)
        return RbacDecisionContext(self.policy, role_rules, overrides)

    def allowed_permissions(
        self,
        *,
        user_id: UUID,
        resource_tokens: set[str],
    ) -> list[str]:
        role_rules, overrides = self._rules(user_id)
        permission_keys = {rule.permission for rule in [*role_rules, *overrides]}
        return sorted(
            permission
            for permission in permission_keys
            if self.policy.decide(
                permission,
                resource_tokens,
                role_rules,
                overrides,
            ).allowed
        )

    def admissions_visibility(self, *, user_id: UUID) -> AdmissionsVisibility:
        role_rules, overrides = self._rules(user_id)

        def allowed(permission: str) -> bool:
            return self.policy.decide(
                permission,
                {"global"},
                role_rules,
                overrides,
            ).allowed

        return AdmissionsVisibility(
            can_read=any(
                allowed(permission)
                for permission in (
                    "admission_cases.read",
                    "enrollments.read",
                    "payments.read",
                )
            ),
            can_manage=any(
                allowed(permission)
                for permission in (
                    "admission_cases.manage",
                    "enrollments.manage",
                    "payments.manage",
                    "refunds.manage",
                    "payment_imports.manage",
                )
            ),
            can_read_amounts=allowed("payments.read"),
        )

    def person_resource_tokens(
        self,
        *,
        actor_person_id: UUID,
        target_person_id: UUID,
        reference_date: date | None = None,
    ) -> set[str]:
        tokens = {"global", f"person:{target_person_id}"}
        if actor_person_id == target_person_id:
            tokens.add(f"self:{target_person_id}")
        on_date = reference_date or business_today()
        family_member = self.db.scalar(
            select(GuardianRelationship.student_person_id).where(
                GuardianRelationship.guardian_person_id == actor_person_id,
                GuardianRelationship.student_person_id == target_person_id,
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.valid_from <= on_date,
                (
                    GuardianRelationship.valid_until.is_(None)
                    | (GuardianRelationship.valid_until >= on_date)
                ),
            )
        )
        if family_member is not None:
            tokens.add(f"family:{target_person_id}")
        return tokens

    def person_resource_tokens_many(
        self,
        *,
        actor_person_id: UUID,
        target_person_ids: set[UUID],
        reference_date: date | None = None,
    ) -> dict[UUID, set[str]]:
        if not target_person_ids:
            return {}
        tokens_by_person = {
            target_id: {"global", f"person:{target_id}"} for target_id in target_person_ids
        }
        if actor_person_id in tokens_by_person:
            tokens_by_person[actor_person_id].add(f"self:{actor_person_id}")
        on_date = reference_date or business_today()
        family_ids = self.db.scalars(
            select(GuardianRelationship.student_person_id).where(
                GuardianRelationship.guardian_person_id == actor_person_id,
                GuardianRelationship.student_person_id.in_(target_person_ids),
                GuardianRelationship.ended_at.is_(None),
                GuardianRelationship.valid_from <= on_date,
                or_(
                    GuardianRelationship.valid_until.is_(None),
                    GuardianRelationship.valid_until >= on_date,
                ),
            )
        )
        for student_id in family_ids:
            tokens_by_person[student_id].add(f"family:{student_id}")
        return tokens_by_person

    def _person_has_current_assignment(
        self,
        actor_person_id: UUID,
        class_id: UUID,
        *,
        at: datetime,
    ) -> bool:
        return (
            self.db.scalar(
                select(CoachAssignment.id)
                .where(
                    CoachAssignment.class_id == class_id,
                    CoachAssignment.coach_person_id == actor_person_id,
                    CoachAssignment.valid_from <= at,
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > at,
                    ),
                    or_(
                        CoachAssignment.ended_at.is_(None),
                        CoachAssignment.ended_at > at,
                    ),
                    CoachEligibility.eligible_person_predicate(CoachAssignment.coach_person_id),
                )
                .limit(1)
            )
            is not None
        )

    def _current_assigned_class_ids(
        self,
        actor_person_id: UUID,
        *,
        at: datetime,
    ) -> frozenset[UUID]:
        return frozenset(
            self.db.scalars(
                select(CoachAssignment.class_id)
                .where(
                    CoachAssignment.coach_person_id == actor_person_id,
                    CoachAssignment.valid_from <= at,
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > at,
                    ),
                    or_(
                        CoachAssignment.ended_at.is_(None),
                        CoachAssignment.ended_at > at,
                    ),
                    CoachEligibility.eligible_person_predicate(CoachAssignment.coach_person_id),
                )
                .distinct()
            )
        )

    def class_resource_tokens(
        self,
        *,
        actor_person_id: UUID,
        class_id: UUID,
        at: datetime | None = None,
    ) -> set[str]:
        reference_time = at or datetime.now(UTC)
        tokens = {"global", f"assigned_class:{class_id}"}
        if self._person_has_current_assignment(
            actor_person_id,
            class_id,
            at=reference_time,
        ):
            tokens.add("assigned_class")
        return tokens

    def class_resource_tokens_many(
        self,
        *,
        actor_person_id: UUID,
        class_ids: set[UUID],
        at: datetime | None = None,
    ) -> dict[UUID, set[str]]:
        if not class_ids:
            return {}
        reference_time = at or datetime.now(UTC)
        current = self._current_assigned_class_ids(
            actor_person_id,
            at=reference_time,
        )
        tokens_by_class = {
            class_id: {"global", f"assigned_class:{class_id}"} for class_id in class_ids
        }
        for class_id in class_ids & current:
            tokens_by_class[class_id].add("assigned_class")
        return tokens_by_class

    def enrollment_resource_tokens(
        self,
        *,
        actor_person_id: UUID,
        student_person_id: UUID,
        class_id: UUID,
        at: datetime | None = None,
        reference_date: date | None = None,
    ) -> set[str]:
        return self.person_resource_tokens(
            actor_person_id=actor_person_id,
            target_person_id=student_person_id,
            reference_date=reference_date,
        ) | self.class_resource_tokens(
            actor_person_id=actor_person_id,
            class_id=class_id,
            at=at,
        )

    def _class_ids_for_cohort(self, cohort_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            self.db.scalars(
                select(TeachingClass.id)
                .where(TeachingClass.cohort_id == cohort_id)
                .order_by(TeachingClass.id)
            )
        )

    def cohort_resource_tokens(
        self,
        *,
        actor_person_id: UUID,
        cohort_id: UUID,
        at: datetime | None = None,
    ) -> set[str]:
        reference_time = at or datetime.now(UTC)
        class_ids = self._class_ids_for_cohort(cohort_id)
        tokens = {"global", *(f"assigned_class:{class_id}" for class_id in class_ids)}
        current = self._current_assigned_class_ids(
            actor_person_id,
            at=reference_time,
        )
        if current.intersection(class_ids):
            tokens.add("assigned_class")
        return tokens

    def teaching_visibility(
        self,
        *,
        user_id: UUID,
        permission: str,
        actor_person_id: UUID,
        at: datetime | None = None,
    ) -> TeachingVisibility:
        reference_time = at or datetime.now(UTC)
        role_rules, overrides = self._rules(
            user_id,
            occurred_at=reference_time,
        )
        global_access = self.policy.decide(
            permission,
            {"global"},
            role_rules,
            overrides,
        ).allowed
        scoped_overrides = [
            rule
            for rule in overrides
            if rule.permission == permission
            and rule.scope_kind == "assigned_class"
            and rule.scope_id is not None
        ]
        if global_access:
            excluded_ids = frozenset(
                rule.scope_id
                for rule in scoped_overrides
                if rule.effect == "deny" and rule.scope_id is not None
            )
            return TeachingVisibility(
                global_access=True,
                class_ids=frozenset(),
                excluded_class_ids=excluded_ids,
            )
        assigned_ids = self._current_assigned_class_ids(
            actor_person_id,
            at=reference_time,
        )
        candidate_ids = set(assigned_ids)
        candidate_ids.update(
            rule.scope_id for rule in scoped_overrides if rule.scope_id is not None
        )
        allowed_ids: set[UUID] = set()
        for class_id in sorted(candidate_ids):
            tokens = {"global", f"assigned_class:{class_id}"}
            if class_id in assigned_ids:
                tokens.add("assigned_class")
            decision = self.policy.decide(permission, tokens, role_rules, overrides)
            if decision.allowed:
                allowed_ids.add(class_id)
        return TeachingVisibility(
            global_access=False,
            class_ids=frozenset(allowed_ids),
            excluded_class_ids=frozenset(),
        )

    def cohort_permission_allowed(
        self,
        *,
        user_id: UUID,
        permission: str,
        actor_person_id: UUID,
        cohort_id: UUID,
        at: datetime | None = None,
    ) -> bool:
        reference_time = at or datetime.now(UTC)
        role_rules, overrides = self._rules(
            user_id,
            occurred_at=reference_time,
        )
        class_ids = self._class_ids_for_cohort(cohort_id)
        if not class_ids:
            return self.policy.decide(
                permission,
                {"global"},
                role_rules,
                overrides,
            ).allowed
        assigned_ids = self._current_assigned_class_ids(
            actor_person_id,
            at=reference_time,
        )
        for class_id in class_ids:
            tokens = {"global", f"assigned_class:{class_id}"}
            if class_id in assigned_ids:
                tokens.add("assigned_class")
            if not self.policy.decide(permission, tokens, role_rules, overrides).allowed:
                return False
        return True

    def _resource_tokens_for_rule(
        self,
        *,
        account: UserAccount,
        rule: PolicyRule,
    ) -> list[set[str]]:
        if rule.scope_kind == "global":
            return [{"global"}]
        if rule.scope_kind == "self":
            return [
                self.person_resource_tokens(
                    actor_person_id=account.person_id,
                    target_person_id=account.person_id,
                )
            ]
        if rule.scope_kind == "family":
            on_date = business_today()
            targets = self.db.scalars(
                select(GuardianRelationship.student_person_id).where(
                    GuardianRelationship.guardian_person_id == account.person_id,
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.valid_from <= on_date,
                    (
                        GuardianRelationship.valid_until.is_(None)
                        | (GuardianRelationship.valid_until >= on_date)
                    ),
                )
            )
            return [
                self.person_resource_tokens(
                    actor_person_id=account.person_id,
                    target_person_id=target,
                    reference_date=on_date,
                )
                for target in targets
            ]
        if rule.scope_kind == "person":
            if rule.scope_id is not None:
                target_ids = [rule.scope_id]
            else:
                target_ids = list(
                    self.db.scalars(
                        select(Person.id).where(Person.deleted_at.is_(None)).order_by(Person.id)
                    )
                )
            return [
                self.person_resource_tokens(
                    actor_person_id=account.person_id,
                    target_person_id=target,
                )
                for target in target_ids
            ]
        reference_time = datetime.now(UTC)
        resource: EvaluationResource | None = None
        if rule.scope_kind == "building" and rule.scope_id is not None:
            building = self.db.get(HousingBuilding, rule.scope_id)
            if building is not None:
                resource = EvaluationResource(building_id=building.id)
        elif rule.scope_kind == "floor" and rule.scope_id is not None:
            floor = self.db.get(HousingFloor, rule.scope_id)
            if floor is not None:
                resource = EvaluationResource(
                    building_id=floor.building_id,
                    floor_id=floor.id,
                )
        elif rule.scope_kind == "cohort" and rule.scope_id is not None:
            if self.db.get(Cohort, rule.scope_id) is not None:
                resource = EvaluationResource(cohort_id=rule.scope_id)
        elif rule.scope_kind == "project" and rule.scope_id is not None:
            if self.db.get(GameProject, rule.scope_id) is not None:
                resource = EvaluationResource(project_id=rule.scope_id)
        elif rule.scope_kind == "enrollment" and rule.scope_id is not None:
            enrollment = self.db.get(Enrollment, rule.scope_id)
            if enrollment is not None:
                teaching_class = self.db.get(TeachingClass, enrollment.class_id)
                if teaching_class is not None:
                    resource = EvaluationResource(
                        student_person_id=enrollment.student_person_id,
                        cohort_id=enrollment.cohort_id,
                        project_id=teaching_class.project_id,
                        class_id=enrollment.class_id,
                        enrollment_id=enrollment.id,
                    )
        if resource is not None:
            return [
                EvaluationAccessService(self.db).resource_tokens(
                    actor_person_id=account.person_id,
                    resource=resource,
                    occurred_at=reference_time,
                )
            ]
        if rule.scope_kind in {
            "cohort",
            "project",
            "enrollment",
            "building",
            "floor",
        }:
            return []
        if rule.scope_id is not None:
            teaching_class = self.db.get(TeachingClass, rule.scope_id)
            if teaching_class is None:
                return []
            return [
                self.class_resource_tokens(
                    actor_person_id=account.person_id,
                    class_id=teaching_class.id,
                )
            ]
        return [
            {"global", "assigned_class", f"assigned_class:{class_id}"}
            for class_id in sorted(
                self._current_assigned_class_ids(
                    account.person_id,
                    at=datetime.now(UTC),
                )
            )
        ]

    def account_has_role(self, account_id: UUID, role_key: str) -> bool:
        return (
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == account_id, Role.key == role_key)
                .limit(1)
            )
            is not None
        )

    def available_permissions(self, *, user_id: UUID) -> list[str]:
        """Return permissions allowed for at least one real resource."""
        account = self._require_account(user_id)
        role_rules, overrides = self._rules(user_id)
        rules = [*role_rules, *overrides]
        available: set[str] = set()
        for rule in rules:
            if rule.effect != "allow":
                continue
            if any(
                self.policy.decide(rule.permission, tokens, role_rules, overrides).allowed
                for tokens in self._resource_tokens_for_rule(account=account, rule=rule)
            ):
                available.add(rule.permission)
        return sorted(available)

    def lock_active_admin_accounts(self) -> list[UserAccount]:
        return list(
            self.db.scalars(
                select(UserAccount)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "admin", UserAccount.status == "active")
                .order_by(UserAccount.id)
                .with_for_update(of=UserAccount)
                .execution_options(populate_existing=True)
            )
        )

    def active_admin_person_ids(self) -> tuple[UUID, ...]:
        return tuple(
            self.db.scalars(
                select(UserAccount.person_id)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "admin", UserAccount.status == "active")
                .order_by(UserAccount.person_id)
            )
        )

    def active_admin_account_ids(self) -> tuple[UUID, ...]:
        return tuple(
            self.db.scalars(
                select(UserAccount.id)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "admin", UserAccount.status == "active")
                .order_by(UserAccount.id)
            )
        )

    def has_effective_recovery_admin(self, accounts: list[UserAccount]) -> bool:
        for account in accounts:
            if account.status != "active":
                continue
            is_admin = self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == account.id, Role.key == "admin")
            )
            if is_admin is None:
                continue
            if all(
                self.decide(
                    user_id=account.id,
                    permission=permission,
                    resource_tokens={"global"},
                ).allowed
                for permission in ("accounts.manage", "permissions.manage")
            ):
                return True
        return False

    def _ensure_recovery_admin(self, accounts: list[UserAccount]) -> None:
        if not self.has_effective_recovery_admin(accounts):
            raise LastAdminProtected("the last effective administrator is protected")

    def list_roles(self) -> list[Role]:
        return list(self.db.scalars(select(Role).order_by(Role.key)))

    def list_permissions(self) -> list[Permission]:
        return list(self.db.scalars(select(Permission).order_by(Permission.key)))

    def account_access(
        self,
        account_id: UUID,
    ) -> tuple[list[Role], list[UserPermissionOverride]]:
        self._require_account(account_id)
        roles = list(
            self.db.scalars(
                select(Role)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == account_id)
                .order_by(Role.key)
            )
        )
        overrides = list(
            self.db.scalars(
                select(UserPermissionOverride)
                .where(UserPermissionOverride.user_id == account_id)
                .order_by(
                    UserPermissionOverride.permission_id,
                    UserPermissionOverride.effect,
                    UserPermissionOverride.scope_kind,
                    UserPermissionOverride.scope_id,
                )
            )
        )
        return roles, overrides

    def account_effective_permissions(
        self,
        account_id: UUID,
    ) -> list[EffectivePermissionDetail]:
        """Return the permissions currently usable by an account."""
        account = self._require_account(account_id)
        role_rules, overrides = self._rules(account_id)
        details: dict[str, tuple[set[ScopeKind], set[str]]] = {}

        for source, rules in (("role", role_rules), ("account_override", overrides)):
            for rule in rules:
                if rule.effect != "allow":
                    continue
                resource_tokens = self._resource_tokens_for_rule(account=account, rule=rule)
                if not any(
                    self.policy.decide(
                        rule.permission,
                        tokens,
                        role_rules,
                        overrides,
                    ).allowed
                    for tokens in resource_tokens
                ):
                    continue
                entry = details.setdefault(rule.permission, (set(), set()))
                entry[0].add(rule.scope_kind)
                entry[1].add(source)

        permissions = {
            permission.key: permission
            for permission in self.list_permissions()
            if permission.key in details
        }
        return [
            EffectivePermissionDetail(
                permission=permissions[key],
                scope_kinds=tuple(sorted(details[key][0])),
                sources=tuple(sorted(details[key][1])),
            )
            for key in sorted(permissions)
        ]

    def permission_account_directory(self) -> list[tuple[UserAccount, Person]]:
        return list(
            self.db.execute(
                select(UserAccount, Person)
                .join(Person, Person.id == UserAccount.person_id)
                .where(Person.deleted_at.is_(None))
                .order_by(Person.full_name, UserAccount.id)
            ).tuples()
        )

    def permission_person_directory(self, name: str) -> list[Person]:
        return list(
            self.db.scalars(
                select(Person)
                .where(
                    Person.deleted_at.is_(None),
                    Person.full_name.contains(name, autoescape=True),
                )
                .order_by(Person.full_name, Person.id)
                .limit(20)
            )
        )

    def permission_building_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[HousingBuilding], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = select(HousingBuilding)
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(
                or_(
                    HousingBuilding.code.contains(normalized_query, autoescape=True),
                    HousingBuilding.name.contains(normalized_query, autoescape=True),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.scalars(
                statement.order_by(
                    HousingBuilding.display_order,
                    HousingBuilding.code,
                    HousingBuilding.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def permission_floor_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[HousingFloor, HousingBuilding]], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = select(HousingFloor, HousingBuilding).join(
            HousingBuilding,
            HousingBuilding.id == HousingFloor.building_id,
        )
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(
                or_(
                    HousingBuilding.code.contains(normalized_query, autoescape=True),
                    HousingBuilding.name.contains(normalized_query, autoescape=True),
                    HousingFloor.name.contains(normalized_query, autoescape=True),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.execute(
                statement.order_by(
                    HousingBuilding.display_order,
                    HousingFloor.display_order,
                    HousingFloor.number,
                    HousingFloor.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        return rows, total

    def permission_class_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[TeachingClass, Cohort, GameProject]], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = (
            select(TeachingClass, Cohort, GameProject)
            .join(Cohort, Cohort.id == TeachingClass.cohort_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(TeachingClass.is_active.is_(True))
        )
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(
                or_(
                    TeachingClass.name.contains(normalized_query, autoescape=True),
                    Cohort.name.contains(normalized_query, autoescape=True),
                    GameProject.name.contains(normalized_query, autoescape=True),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.execute(
                statement.order_by(Cohort.name, TeachingClass.name, TeachingClass.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        return rows, total

    def permission_cohort_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Cohort], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = select(Cohort)
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(Cohort.name.contains(normalized_query, autoescape=True))
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.scalars(
                statement.order_by(Cohort.starts_at.desc(), Cohort.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def permission_project_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[GameProject], int]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = select(GameProject)
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(
                GameProject.name.contains(normalized_query, autoescape=True)
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.scalars(
                statement.order_by(GameProject.sort_order, GameProject.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return rows, total

    def permission_enrollment_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[
        list[tuple[Enrollment, Person, Cohort, TeachingClass, GameProject]],
        int,
    ]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        statement = (
            select(Enrollment, Person, Cohort, TeachingClass, GameProject)
            .join(Person, Person.id == Enrollment.student_person_id)
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(Person.deleted_at.is_(None))
        )
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(
                or_(
                    Person.full_name.contains(normalized_query, autoescape=True),
                    Cohort.name.contains(normalized_query, autoescape=True),
                    TeachingClass.name.contains(normalized_query, autoescape=True),
                    GameProject.name.contains(normalized_query, autoescape=True),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.execute(
                statement.order_by(
                    Cohort.starts_at.desc(),
                    Person.full_name,
                    Enrollment.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        return rows, total

    def class_scope_label(self, class_id: UUID) -> str | None:
        row = self.db.execute(
            select(Cohort.name, TeachingClass.name, GameProject.name)
            .join(Cohort, Cohort.id == TeachingClass.cohort_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(TeachingClass.id == class_id)
        ).one_or_none()
        if row is None:
            return None
        return " · ".join(row)

    def _require_class_scope(self, class_id: UUID) -> TeachingClass:
        teaching_class = self.db.get(TeachingClass, class_id)
        if teaching_class is None:
            raise RbacResourceNotFound("class scope not found")
        return teaching_class

    def _require_account(self, account_id: UUID) -> UserAccount:
        account = self.db.get(UserAccount, account_id)
        if account is None:
            raise RbacResourceNotFound("account not found")
        return account

    def _require_role(self, role_id: UUID) -> Role:
        role = self.db.get(Role, role_id)
        if role is None:
            raise RbacResourceNotFound("role not found")
        return role

    def _require_permission(self, permission_id: UUID) -> Permission:
        permission = self.db.get(Permission, permission_id)
        if permission is None:
            raise RbacResourceNotFound("permission not found")
        return permission

    def _scope_resource(
        self,
        *,
        account: UserAccount,
        input: PermissionOverrideCreate,
        occurred_at: datetime,
        allow_existing_inactive: bool = False,
    ) -> EvaluationResource:
        if input.scope_kind == "global":
            return EvaluationResource()
        if input.scope_kind == "self":
            return EvaluationResource(student_person_id=account.person_id)
        if input.scope_kind == "family":
            reference_date = business_date_at(occurred_at)
            student_id = self.db.scalar(
                select(GuardianRelationship.student_person_id)
                .where(
                    GuardianRelationship.guardian_person_id == account.person_id,
                    GuardianRelationship.valid_from <= reference_date,
                    or_(
                        GuardianRelationship.valid_until.is_(None),
                        GuardianRelationship.valid_until >= reference_date,
                    ),
                    or_(
                        GuardianRelationship.ended_at.is_(None),
                        GuardianRelationship.ended_at > occurred_at,
                    ),
                )
                .order_by(GuardianRelationship.student_person_id)
                .limit(1)
            )
            return EvaluationResource(student_person_id=student_id)
        if input.scope_id is None:
            raise RbacResourceNotFound("scope target not found")
        if input.scope_kind == "person":
            person = self.db.scalar(
                select(Person).where(
                    Person.id == input.scope_id,
                    Person.deleted_at.is_(None),
                )
            )
            if person is None:
                raise RbacResourceNotFound("person scope not found")
            return EvaluationResource(student_person_id=person.id)
        if input.scope_kind == "building":
            building = self.db.get(HousingBuilding, input.scope_id)
            if building is None:
                raise RbacResourceNotFound("building scope not found")
            if (
                input.effect == "allow"
                and not building.is_active
                and not allow_existing_inactive
            ):
                raise InactivePermissionScope(
                    "inactive building cannot receive a new allow"
                )
            return EvaluationResource(building_id=building.id)
        if input.scope_kind == "floor":
            floor = self.db.get(HousingFloor, input.scope_id)
            if floor is None:
                raise RbacResourceNotFound("floor scope not found")
            if (
                input.effect == "allow"
                and not floor.is_active
                and not allow_existing_inactive
            ):
                raise InactivePermissionScope("inactive floor cannot receive a new allow")
            return EvaluationResource(
                building_id=floor.building_id,
                floor_id=floor.id,
            )
        if input.scope_kind == "assigned_class":
            teaching_class = self._require_class_scope(input.scope_id)
            if (
                input.effect == "allow"
                and not teaching_class.is_active
                and not allow_existing_inactive
            ):
                raise InactivePermissionScope("inactive class cannot receive a new allow")
            return EvaluationResource(
                cohort_id=teaching_class.cohort_id,
                project_id=teaching_class.project_id,
                class_id=teaching_class.id,
            )
        if input.scope_kind == "cohort":
            cohort = self.db.get(Cohort, input.scope_id)
            if cohort is None:
                raise RbacResourceNotFound("cohort scope not found")
            return EvaluationResource(cohort_id=cohort.id)
        if input.scope_kind == "project":
            project = self.db.get(GameProject, input.scope_id)
            if project is None:
                raise RbacResourceNotFound("project scope not found")
            if input.effect == "allow" and not project.is_active and not allow_existing_inactive:
                raise InactivePermissionScope("inactive project cannot receive a new allow")
            return EvaluationResource(project_id=project.id)
        if input.scope_kind == "enrollment":
            enrollment = self.db.get(Enrollment, input.scope_id)
            if enrollment is None:
                raise RbacResourceNotFound("enrollment scope not found")
            teaching_class = self._require_class_scope(enrollment.class_id)
            return EvaluationResource(
                student_person_id=enrollment.student_person_id,
                cohort_id=enrollment.cohort_id,
                project_id=teaching_class.project_id,
                class_id=enrollment.class_id,
                enrollment_id=enrollment.id,
            )
        raise RbacResourceNotFound("scope target not found")

    @staticmethod
    def _validate_override_input(
        *,
        permission: Permission,
        input: PermissionOverrideCreate,
    ) -> None:
        if input.grant_reason is not None:
            try:
                AuditService.validate_purpose(input.grant_reason)
            except AuditMetadataRejected as exc:
                raise InvalidPermissionOverride("grant reason contains sensitive data") from exc
        if (
            input.effect != "allow"
            or not permission.sensitive
            or permission.key not in _SENSITIVE_EVALUATION_PERMISSION_KEYS
        ):
            return
        if input.scope_kind == "global":
            raise InvalidPermissionOverride(
                "sensitive evaluation allows require a non-global scope"
            )
        if input.valid_from is None or input.valid_until is None:
            raise InvalidPermissionOverride(
                "sensitive evaluation allows require a finite validity window"
            )
        if input.grant_reason is None or not input.grant_reason.strip():
            raise InvalidPermissionOverride("sensitive evaluation allows require a grant reason")

    def _require_delegation_authority(
        self,
        *,
        actor_user_id: UUID,
        permission: Permission,
        input: PermissionOverrideCreate,
        resource: EvaluationResource,
    ) -> None:
        if input.effect != "allow":
            return
        starts_at = input.valid_from or datetime.now(UTC)
        actor = self.db.get(UserAccount, actor_user_id)
        if actor is None or actor.status != "active":
            raise PermissionDelegationDenied("actor account is not active")
        boundaries: set[datetime] = set()
        for valid_from, valid_until in self.db.execute(
            select(
                UserPermissionOverride.valid_from,
                UserPermissionOverride.valid_until,
            )
            .join(Permission, Permission.id == UserPermissionOverride.permission_id)
            .where(
                UserPermissionOverride.user_id == actor_user_id,
                Permission.key == permission.key,
            )
        ):
            boundaries.update(value for value in (valid_from, valid_until) if value is not None)
        for valid_from, valid_until, ended_at in self.db.execute(
            select(
                CoachAssignment.valid_from,
                CoachAssignment.valid_until,
                CoachAssignment.ended_at,
            ).where(CoachAssignment.coach_person_id == actor.person_id)
        ):
            boundaries.update(
                value for value in (valid_from, valid_until, ended_at) if value is not None
            )
        for valid_from, valid_until, ended_at in self.db.execute(
            select(
                CohortStaffAssignment.valid_from,
                CohortStaffAssignment.valid_until,
                CohortStaffAssignment.ended_at,
            )
            .join(
                CohortStaffAssignmentPermission,
                CohortStaffAssignmentPermission.assignment_id == CohortStaffAssignment.id,
            )
            .join(
                Permission,
                Permission.id == CohortStaffAssignmentPermission.permission_id,
            )
            .where(
                CohortStaffAssignment.staff_person_id == actor.person_id,
                Permission.key == permission.key,
            )
        ):
            boundaries.update(
                value for value in (valid_from, valid_until, ended_at) if value is not None
            )
        if input.scope_kind != "family":
            for valid_from, valid_until, ended_at in self.db.execute(
                select(
                    GuardianRelationship.valid_from,
                    GuardianRelationship.valid_until,
                    GuardianRelationship.ended_at,
                ).where(GuardianRelationship.guardian_person_id == actor.person_id)
            ):
                boundaries.add(
                    datetime.combine(valid_from, time.min, tzinfo=BUSINESS_TIME_ZONE)
                )
                if valid_until is not None:
                    boundaries.add(
                        datetime.combine(
                            valid_until + timedelta(days=1),
                            time.min,
                            tzinfo=BUSINESS_TIME_ZONE,
                        )
                    )
                if ended_at is not None:
                    boundaries.add(ended_at)

        checkpoints = {starts_at}
        for boundary in boundaries:
            if boundary < starts_at:
                continue
            if input.valid_until is not None and boundary >= input.valid_until:
                continue
            checkpoints.add(boundary)
        if input.valid_until is not None:
            checkpoints.add(input.valid_until - timedelta(microseconds=1))
        service = EvaluationAccessService(self.db)
        for occurred_at in sorted(checkpoints):
            resources = [resource]
            if input.scope_kind == "family":
                active_deny = self.db.scalar(
                    select(UserPermissionOverride.id)
                    .where(
                        UserPermissionOverride.user_id == actor_user_id,
                        UserPermissionOverride.permission_id == permission.id,
                        UserPermissionOverride.effect == "deny",
                        or_(
                            UserPermissionOverride.valid_from.is_(None),
                            UserPermissionOverride.valid_from <= occurred_at,
                        ),
                        or_(
                            UserPermissionOverride.valid_until.is_(None),
                            UserPermissionOverride.valid_until > occurred_at,
                        ),
                    )
                    .limit(1)
                )
                if active_deny is not None:
                    raise PermissionDelegationDenied(
                        "actor does not hold unrestricted global authority"
                    )
                resources = [EvaluationResource()]
            for delegated_resource in resources:
                decision = service.decide(
                    account_id=actor_user_id,
                    permission=permission.key,
                    resource=delegated_resource,
                    occurred_at=occurred_at,
                )
                if not decision.allowed:
                    raise PermissionDelegationDenied(
                        "actor does not hold the delegated permission and scope"
                    )

    def assign_role(self, *, account_id: UUID, role_id: UUID) -> Role:
        try:
            with write_transaction(self.db):
                self._require_account(account_id)
                role = self._require_role(role_id)
                if self.db.get(UserRole, (account_id, role_id)) is None:
                    self.db.add(UserRole(user_id=account_id, role_id=role_id))
                    self.db.flush()
                result = role
            return result
        except IntegrityError as exc:
            raise DuplicatePermissionRule("role is already assigned") from exc

    def remove_role(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        role_id: UUID,
        request_id: str,
    ) -> Role:
        with write_transaction(self.db):
            account_person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == account_id)
            )
            if account_person_id is None:
                raise RbacResourceNotFound("account not found")
            role = self._require_role(role_id)
            readiness_policy = GuardianReadinessPolicy(self.db)
            dependent_person_ids = tuple(
                readiness_policy.guardian_and_dependent_person_ids(account_person_id)
            )
            person_ids = set(dependent_person_ids)
            if role.key == "admin":
                person_ids.update(self.active_admin_person_ids())
            readiness_policy.lock_students(person_ids)
            locked_accounts = readiness_policy.lock_accounts(person_ids)
            account_by_id = {account.id: account for account in locked_accounts}
            account = account_by_id.get(account_id)
            if account is None:
                raise RbacResourceNotFound("account not found")
            active_admin_ids = (
                set(self.active_admin_account_ids()) if role.key == "admin" else set()
            )
            active_admins = [
                locked_account
                for locked_account in locked_accounts
                if locked_account.id in active_admin_ids and locked_account.status == "active"
            ]
            refreshed_role = self.db.scalar(
                select(Role).where(Role.id == role_id).execution_options(populate_existing=True)
            )
            if refreshed_role is None:
                raise RbacResourceNotFound("role not found")
            role = refreshed_role
            assignment = self.db.scalar(
                select(UserRole)
                .where(UserRole.user_id == account_id, UserRole.role_id == role_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if assignment is None:
                return role
            if role.key == "admin" and actor_user_id == account_id:
                raise LastAdminProtected("the last active administrator is protected")
            self.db.delete(assignment)
            self.db.flush()
            if role.key == "admin":
                self._ensure_recovery_admin(active_admins)
            if role.key == "guardian":
                readiness_policy.disable_unready_dependents(
                    account.person_id,
                    reference_date=business_today(),
                    revoked_at=datetime.now(UTC),
                    audit_actor_user_id=actor_user_id,
                    audit_request_id=request_id,
                    dependent_person_ids=dependent_person_ids,
                    locked_accounts=locked_accounts,
                )
            result = role
        return result

    def add_override(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        input: PermissionOverrideCreate,
    ) -> tuple[UserPermissionOverride, Permission]:
        try:
            with write_transaction(self.db):
                account = self._require_account(account_id)
                permission = self._require_permission(input.permission_id)
                self._validate_override_input(
                    permission=permission,
                    input=input,
                )
                reference_time = input.valid_from or datetime.now(UTC)
                resource = self._scope_resource(
                    account=account,
                    input=input,
                    occurred_at=reference_time,
                )
                self._require_delegation_authority(
                    actor_user_id=actor_user_id,
                    permission=permission,
                    input=input,
                    resource=resource,
                )
                active_admins = (
                    self.lock_active_admin_accounts()
                    if permission.key
                    in {
                        "accounts.manage",
                        "permissions.manage",
                    }
                    else []
                )
                protects_recovery = account_id in {admin.id for admin in active_admins}
                if (
                    actor_user_id == account_id
                    and input.effect == "deny"
                    and permission.key == "permissions.manage"
                ):
                    raise SelfPermissionLockout("cannot deny your own permission management")
                override = UserPermissionOverride(
                    user_id=account_id,
                    permission_id=input.permission_id,
                    effect=input.effect,
                    scope_kind=input.scope_kind,
                    scope_id=input.scope_id,
                    valid_from=input.valid_from,
                    valid_until=input.valid_until,
                    created_by_user_id=actor_user_id,
                    grant_reason=(
                        input.grant_reason.strip() if input.grant_reason is not None else None
                    ),
                )
                self.db.add(override)
                self.db.flush()
                if protects_recovery:
                    self._ensure_recovery_admin(active_admins)
                result = (override, permission)
            return result
        except IntegrityError as exc:
            raise DuplicatePermissionRule("permission override already exists") from exc

    def remove_override(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        override_id: UUID,
        before_delete: Callable[[PermissionOverrideChange], None],
    ) -> PermissionOverrideChange:
        with write_transaction(self.db):
            account = self._require_account(account_id)
            override = self.db.scalar(
                select(UserPermissionOverride)
                .where(
                    UserPermissionOverride.id == override_id,
                    UserPermissionOverride.user_id == account_id,
                )
                .with_for_update()
            )
            if override is None:
                raise RbacResourceNotFound("permission override not found")
            permission = self._require_permission(override.permission_id)
            active_admins = (
                self.lock_active_admin_accounts()
                if permission.key
                in {
                    "accounts.manage",
                    "permissions.manage",
                }
                else []
            )
            protects_recovery = account_id in {admin.id for admin in active_admins}
            if (
                actor_user_id == account_id
                and override.effect == "allow"
                and permission.key == "permissions.manage"
            ):
                raise SelfPermissionLockout("cannot remove your own permission management")
            if override.effect == "deny":
                delegation_input = PermissionOverrideCreate(
                    permission_id=override.permission_id,
                    effect="allow",
                    scope_kind=cast(ScopeKind, override.scope_kind),
                    scope_id=override.scope_id,
                    valid_from=override.valid_from,
                    valid_until=override.valid_until,
                )
                reference_time = override.valid_from or datetime.now(UTC)
                resource = self._scope_resource(
                    account=account,
                    input=delegation_input,
                    occurred_at=reference_time,
                    allow_existing_inactive=True,
                )
                self._require_delegation_authority(
                    actor_user_id=actor_user_id,
                    permission=permission,
                    input=delegation_input,
                    resource=resource,
                )
            result = PermissionOverrideChange(
                permission_key=permission.key,
                effect=cast(Effect, override.effect),
                scope_kind=cast(ScopeKind, override.scope_kind),
                scope_id=override.scope_id,
            )
            before_delete(result)
            self.db.delete(override)
            self.db.flush()
            if protects_recovery:
                self._ensure_recovery_admin(active_admins)
        return result

    def preview_override(
        self,
        *,
        account_id: UUID,
        input: PermissionOverrideCreate,
    ) -> PolicyDecision:
        account = self._require_account(account_id)
        permission = self._require_permission(input.permission_id)
        self._validate_override_input(permission=permission, input=input)
        reference_time = input.valid_from or datetime.now(UTC)
        existing = self.db.scalar(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == account_id,
                UserPermissionOverride.permission_id == input.permission_id,
                UserPermissionOverride.effect == input.effect,
                UserPermissionOverride.scope_kind == input.scope_kind,
                UserPermissionOverride.scope_id.is_not_distinct_from(input.scope_id),
            )
        )
        resource = self._scope_resource(
            account=account,
            input=input,
            occurred_at=reference_time,
            allow_existing_inactive=existing is not None,
        )
        tokens = EvaluationAccessService(self.db).resource_tokens(
            actor_person_id=account.person_id,
            resource=resource,
            occurred_at=reference_time,
        )
        role_rules, overrides = self._rules(
            account_id,
            occurred_at=reference_time,
        )
        proposed = PolicyRule(
            permission=permission.key,
            effect=input.effect,
            scope_kind=input.scope_kind,
            scope_id=input.scope_id,
        )
        return self.policy.decide(
            permission.key,
            tokens,
            role_rules,
            [*overrides, proposed],
        )
