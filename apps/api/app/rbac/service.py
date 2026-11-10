from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admissions.access import AdmissionsVisibility
from app.admissions.models import Enrollment
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.core.time import BUSINESS_TIME_ZONE, business_date_at, business_today
from app.core.transactions import atomic_write_transaction, write_transaction
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.housing.models import HousingBuilding, HousingFloor
from app.housing.sorting import building_order_expressions
from app.identity.models import GuardianRelationship, Person, StaffProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.rbac.account_protection import protected_management_target
from app.rbac.models import (
    Permission,
    Role,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from app.rbac.policy import Effect, PolicyDecision, PolicyRule, RbacPolicy, ScopeKind
from app.rbac.schemas import (
    JobRoleChangeRequest,
    JobRoleChangeResponse,
    JobRoleSummary,
    PermissionOverrideCreate,
)
from app.teaching.access import TeachingVisibility
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    GameProject,
    ProfessionalTeam,
    TeachingClass,
)
from app.team_management.models import TeamManagerAssignment

if TYPE_CHECKING:
    from app.auth.account_readiness import AccountAccessReadinessPolicy

_SENSITIVE_EVALUATION_PERMISSION_KEYS = frozenset(
    {
        "evaluation_media.upload",
        "evaluation_media.original.read",
        "evaluation_jobs.match",
        "evaluation_jobs.manual_input",
        "evaluation_jobs.review",
        "evaluation_adapters.certify",
        "analysis_profiles.manage",
        "student_game_accounts.manage",
        "student_reports.write",
        "student_reports.review",
        "camp_surveys.identity.read",
        "camp_surveys.print",
        "cohort_staff.manage",
    }
)

_FOOD_PREFERENCE_OVERRIDE_SCOPES = {
    "food_preferences.read": frozenset({"self", "family", "person", "global"}),
    "food_preferences.update": frozenset({"self", "family", "person", "global"}),
    "food_preferences.catalog.manage": frozenset({"global"}),
    "food_preferences.export": frozenset({"global"}),
}

_TEAM_PERMISSION_SCOPES = {
    "team_memberships.read": frozenset({"self", "assigned_team", "global"}),
    "team_memberships.manage": frozenset({"assigned_team", "global"}),
    "team_memberships.transfer": frozenset({"global"}),
    "team_manager_assignments.manage": frozenset({"global"}),
}

_PERSONNEL_INTAKE_PERMISSION_SCOPES = {
    key: frozenset({"global", "person", "assigned_team"})
    for key in (
        "personnel_intake.submit",
        "personnel_intake.review",
        "personnel_intake.housing.read",
    )
}


class RbacServiceError(ValueError):
    code = "rbac_service_error"


class RbacResourceNotFound(RbacServiceError):
    code = "rbac_resource_not_found"


class LastAdminProtected(RbacServiceError):
    """Compatibility error code for protection of the recovery super administrator."""

    code = "last_admin_protected"


class SelfPermissionLockout(RbacServiceError):
    code = "self_permission_lockout"


class DuplicatePermissionRule(RbacServiceError):
    code = "duplicate_permission_rule"


class InactivePermissionScope(RbacServiceError):
    code = "permission_scope_inactive"


class PermissionDelegationDenied(RbacServiceError):
    code = "permission_delegation_denied"


def prospective_role_scope(role_scope_kind: str) -> ScopeKind:
    """Indefinite role grants need a global ceiling for every dynamic scope."""
    return (
        "person" if role_scope_kind == "self"
        else "family" if role_scope_kind == "family"
        else "global"
    )


class InvalidPermissionOverride(RbacServiceError):
    code = "invalid_permission_override"


class JobRoleConflict(RbacServiceError):
    code = "job_role_conflict"


_STAFF_ROLE_KEYS = frozenset({
    "admin", "super_admin", "coach", "customer_service", "life_teacher",
    "staff", "administration", "finance", "team_manager", "property", "assistant_coach",
})


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
                GuardianRelationship.requires_verification.is_(False),
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
                GuardianRelationship.requires_verification.is_(False),
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
        if (
            rule.permission in _PERSONNEL_INTAKE_PERMISSION_SCOPES
            and rule.scope_kind == "assigned_team"
        ):
            if rule.scope_id is None or self.db.get(ProfessionalTeam, rule.scope_id) is None:
                return []
            return [{"global", f"assigned_team:{rule.scope_id}"}]
        if rule.permission in _TEAM_PERMISSION_SCOPES:
            if account.status != "active" or account.force_password_change:
                return []
            person = self.db.get(Person, account.person_id)
            if person is None or person.deleted_at is not None:
                return []
            # Callers apply the same live deny-first policy to this global
            # resource; only global rules may use administration's entrance.
            if self.account_has_role(account.id, "super_admin") or (
                rule.scope_kind == "global"
                and self.account_has_role(account.id, "administration")
            ):
                return [{"global"}]
            if (
                rule.permission == "team_memberships.read"
                and rule.scope_kind == "self"
                and self.account_has_role(account.id, "team_member")
            ):
                return [{"global", f"self:{account.person_id}"}]
            if rule.permission in {"team_memberships.read", "team_memberships.manage"}:
                role_keys = set(self.db.scalars(
                    select(Role.key).join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == account.id)
                ))
                team_ids: set[UUID] = set()
                if "team_manager" in role_keys:
                    team_ids.update(self.db.scalars(
                        select(TeamManagerAssignment.team_id)
                        .join(ProfessionalTeam, ProfessionalTeam.id == TeamManagerAssignment.team_id)
                        .join(GameProject, GameProject.id == ProfessionalTeam.project_id)
                        .where(
                            TeamManagerAssignment.account_id == account.id,
                            TeamManagerAssignment.ended_at.is_(None),
                        )
                    ))
                if rule.permission == "team_memberships.read":
                    from app.team_management.staff_service import active_staff_team_ids

                    team_ids.update(active_staff_team_ids(
                        self.db, account.id, role_keys=role_keys,
                    ))
                return [{"global", f"assigned_team:{team_id}"} for team_id in team_ids]
            return []
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
                    GuardianRelationship.requires_verification.is_(False),
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

    def lock_active_super_admin_accounts(self) -> list[UserAccount]:
        return list(
            self.db.scalars(
                select(UserAccount)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "super_admin", UserAccount.status == "active")
                .order_by(UserAccount.id)
                .with_for_update(of=UserAccount)
                .execution_options(populate_existing=True)
            )
        )

    def active_super_admin_person_ids(self) -> tuple[UUID, ...]:
        return tuple(
            self.db.scalars(
                select(UserAccount.person_id)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "super_admin", UserAccount.status == "active")
                .order_by(UserAccount.person_id)
            )
        )

    def active_super_admin_account_ids(self) -> tuple[UUID, ...]:
        return tuple(
            self.db.scalars(
                select(UserAccount.id)
                .join(UserRole, UserRole.user_id == UserAccount.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == "super_admin", UserAccount.status == "active")
                .order_by(UserAccount.id)
            )
        )

    def has_effective_recovery_super_admin(self, accounts: list[UserAccount]) -> bool:
        for account in accounts:
            if account.status != "active":
                continue
            is_super_admin = self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == account.id, Role.key == "super_admin")
            )
            if is_super_admin is None:
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

    def _ensure_recovery_super_admin(self, accounts: list[UserAccount]) -> None:
        if not self.has_effective_recovery_super_admin(accounts):
            raise LastAdminProtected("the last effective super administrator is protected")

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
        """Return the permissions currently usable by an account.

        A permission is included only when one of its allow rules resolves to
        an allowed decision for at least one real resource.  This keeps the
        management view aligned with the same deny-first policy used by
        request authorization, while still retaining the source and scope
        needed to explain why the permission is present.
        """
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
                statement.order_by(*building_order_expressions())
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
                    *building_order_expressions(),
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

    def permission_team_directory(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[ProfessionalTeam, GameProject]], int]:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid page or page_size")
        statement = select(ProfessionalTeam, GameProject).join(
            GameProject, GameProject.id == ProfessionalTeam.project_id
        )
        normalized_query = query.strip() if query is not None else None
        if normalized_query:
            statement = statement.where(
                or_(
                    ProfessionalTeam.name.contains(normalized_query, autoescape=True),
                    GameProject.name.contains(normalized_query, autoescape=True),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(
            self.db.execute(
                statement.order_by(
                    GameProject.sort_order, ProfessionalTeam.name, ProfessionalTeam.id
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
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
                    GuardianRelationship.requires_verification.is_(False),
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
        if input.scope_kind == "assigned_team":
            team = self.db.get(ProfessionalTeam, input.scope_id)
            if team is None:
                raise RbacResourceNotFound("team scope not found")
            return EvaluationResource()
        if input.scope_kind == "building":
            building = self.db.get(HousingBuilding, input.scope_id)
            if building is None:
                raise RbacResourceNotFound("building scope not found")
            if input.effect == "allow" and not building.is_active and not allow_existing_inactive:
                raise InactivePermissionScope("inactive building cannot receive a new allow")
            return EvaluationResource(building_id=building.id)
        if input.scope_kind == "floor":
            floor = self.db.get(HousingFloor, input.scope_id)
            if floor is None:
                raise RbacResourceNotFound("floor scope not found")
            if input.effect == "allow" and not floor.is_active and not allow_existing_inactive:
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
        if permission.key == "referrals.read_invited_housing" and input.scope_kind not in {
            "self",
            "global",
            "person",
        }:
            raise InvalidPermissionOverride(
                "invited housing permission does not support this scope"
            )
        talent_scopes = {
            "reactionx.manage": frozenset({"global"}),
            "reactionx.conduct": frozenset({"global", "person"}),
            "talent_tests.take": frozenset({"self"}),
            "talent_tests.read": frozenset(
                {"self", "family", "person", "assigned_class", "global"}
            ),
            "esports_assessments.take": frozenset({"self"}),
            "esports_assessments.read": frozenset(
                {"self", "family", "person", "assigned_class", "global"}
            ),
        }.get(permission.key)
        if talent_scopes is not None and input.scope_kind not in talent_scopes:
            raise InvalidPermissionOverride("talent permission does not support this scope")
        food_scopes = _FOOD_PREFERENCE_OVERRIDE_SCOPES.get(permission.key)
        if food_scopes is not None and input.scope_kind not in food_scopes:
            raise InvalidPermissionOverride(
                "food preference permission does not support this scope"
            )
        team_scopes = _TEAM_PERMISSION_SCOPES.get(permission.key)
        if team_scopes is not None and input.scope_kind not in team_scopes:
            raise InvalidPermissionOverride("team permission does not support this scope")
        intake_scopes = _PERSONNEL_INTAKE_PERMISSION_SCOPES.get(permission.key)
        if intake_scopes is not None and input.scope_kind not in intake_scopes:
            raise InvalidPermissionOverride(
                "personnel intake permission does not support this scope"
            )
        if input.scope_kind == "assigned_team" and team_scopes is None and intake_scopes is None:
            raise InvalidPermissionOverride("assigned_team is only for team permissions")
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
        if permission.key == "referrals.read_invited_housing":
            actor = self.db.scalar(
                select(UserAccount)
                .join(Person, Person.id == UserAccount.person_id)
                .where(
                    UserAccount.id == actor_user_id,
                    UserAccount.status == "active",
                    Person.deleted_at.is_(None),
                )
                .execution_options(populate_existing=True)
            )
            if actor is None:
                raise PermissionDelegationDenied("actor account is not active")
            if self.account_has_role(actor.id, "super_admin"):
                # This permission is a feature switch, not a source of invited
                # people. The root manages that switch while its own default
                # remains self-only. Relation scope is independently intersected.
                if not self.decide(
                    user_id=actor.id,
                    permission="permissions.manage",
                    resource_tokens={"global"},
                ).allowed:
                    raise PermissionDelegationDenied("actor cannot manage invitation permissions")
                return
            # Other authorized managers retain the existing delegation rules.
        if permission.key in _TEAM_PERMISSION_SCOPES:
            self._require_team_delegation_authority(
                actor_user_id=actor_user_id,
                permission=permission,
                input=input,
            )
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
                boundaries.add(datetime.combine(valid_from, time.min, tzinfo=BUSINESS_TIME_ZONE))
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
            if input.scope_kind in {"family", "global"}:
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
                if (
                    permission.key in _PERSONNEL_INTAKE_PERMISSION_SCOPES
                    and input.scope_kind == "assigned_team"
                ):
                    delegation_allowed = self.decide(
                        user_id=actor_user_id,
                        permission=permission.key,
                        resource_tokens={"global", f"assigned_team:{input.scope_id}"},
                        occurred_at=occurred_at,
                    ).allowed
                else:
                    delegation_allowed = service.decide(
                        account_id=actor_user_id,
                        permission=permission.key,
                        resource=delegated_resource,
                        occurred_at=occurred_at,
                    ).allowed
                if not delegation_allowed:
                    raise PermissionDelegationDenied(
                        "actor does not hold the delegated permission and scope"
                    )

    def _require_team_delegation_authority(
        self,
        *,
        actor_user_id: UUID,
        permission: Permission,
        input: PermissionOverrideCreate,
    ) -> None:
        actor = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == actor_user_id,
                UserAccount.status == "active",
                UserAccount.force_password_change.is_(False),
                Person.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if actor is None:
            raise PermissionDelegationDenied("actor account is not active")
        is_superadmin = self.account_has_role(actor.id, "super_admin")
        is_manager = self.account_has_role(actor.id, "team_manager")
        if input.scope_kind in {"global", "self"} and not is_superadmin:
            raise PermissionDelegationDenied("global team delegation requires super administrator")
        if input.scope_kind == "assigned_team" and not (is_superadmin or is_manager):
            raise PermissionDelegationDenied("actor has no team role")
        if input.scope_kind == "assigned_team" and not is_superadmin:
            binding = self.db.scalar(
                select(TeamManagerAssignment.id)
                .where(
                    TeamManagerAssignment.account_id == actor.id,
                    TeamManagerAssignment.team_id == input.scope_id,
                    TeamManagerAssignment.ended_at.is_(None),
                )
                .limit(1)
            )
            if binding is None:
                raise PermissionDelegationDenied("actor is not assigned to this team")
        starts_at = input.valid_from or datetime.now(UTC)
        boundaries: set[datetime] = set()
        for valid_from, valid_until in self.db.execute(
            select(UserPermissionOverride.valid_from, UserPermissionOverride.valid_until).where(
                UserPermissionOverride.user_id == actor.id,
                UserPermissionOverride.permission_id == permission.id,
            )
        ):
            boundaries.update(value for value in (valid_from, valid_until) if value is not None)
        checkpoints = {starts_at}
        checkpoints.update(
            boundary
            for boundary in boundaries
            if boundary >= starts_at and (input.valid_until is None or boundary < input.valid_until)
        )
        if input.valid_until is not None:
            checkpoints.add(input.valid_until - timedelta(microseconds=1))
        tokens = {"global"}
        if input.scope_kind == "assigned_team" and input.scope_id is not None:
            tokens.add(f"assigned_team:{input.scope_id}")
        elif input.scope_kind == "self":
            tokens.add(f"self:{actor.person_id}")
        for occurred_at in sorted(checkpoints):
            role_rules, overrides = self._rules(actor.id, occurred_at=occurred_at)
            if input.scope_kind == "global" and any(
                rule.permission == permission.key and rule.effect == "deny" for rule in overrides
            ):
                raise PermissionDelegationDenied("actor has a restricted team permission")
            if not self.policy.decide(
                permission.key,
                tokens,
                role_rules,
                overrides,
            ).allowed:
                raise PermissionDelegationDenied("actor lacks delegated team permission")

    def _preview_team_override(
        self,
        *,
        account: UserAccount,
        permission: Permission,
        input: PermissionOverrideCreate,
        occurred_at: datetime,
    ) -> PolicyDecision:
        person = self.db.get(Person, account.person_id)
        if (
            account.status != "active"
            or account.force_password_change
            or person is None
            or person.deleted_at is not None
        ):
            return PolicyDecision(False, "account_unavailable")
        is_superadmin = self.account_has_role(account.id, "super_admin")
        is_manager = self.account_has_role(account.id, "team_manager")
        is_member = self.account_has_role(account.id, "team_member")
        tokens = {"global"}
        assigned_team_ids: list[UUID] = []
        if input.scope_kind == "self":
            if not (is_member or is_superadmin):
                return PolicyDecision(False, "role_required")
            tokens.add(f"self:{account.person_id}")
        elif permission.key in {"team_memberships.transfer", "team_manager_assignments.manage"}:
            if not is_superadmin:
                return PolicyDecision(False, "role_required")
        elif input.scope_kind == "assigned_team":
            if input.scope_id is None:
                return PolicyDecision(False, "scope_required")
            binding = self.db.scalar(
                select(TeamManagerAssignment.id)
                .where(
                    TeamManagerAssignment.account_id == account.id,
                    TeamManagerAssignment.team_id == input.scope_id,
                    TeamManagerAssignment.ended_at.is_(None),
                )
                .limit(1)
            )
            if not is_superadmin and (not is_manager or binding is None):
                return PolicyDecision(False, "assignment_required")
            tokens.add(f"assigned_team:{input.scope_id}")
        elif not is_superadmin:
            if not is_manager:
                return PolicyDecision(False, "role_required")
            assigned_team_ids = list(
                self.db.scalars(
                    select(TeamManagerAssignment.team_id)
                    .where(
                        TeamManagerAssignment.account_id == account.id,
                        TeamManagerAssignment.ended_at.is_(None),
                    )
                    .order_by(TeamManagerAssignment.team_id)
                )
            )
            if not assigned_team_ids:
                return PolicyDecision(False, "assignment_required")
        role_rules, overrides = self._rules(account.id, occurred_at=occurred_at)
        proposed = PolicyRule(
            permission=permission.key,
            effect=input.effect,
            scope_kind=input.scope_kind,
            scope_id=input.scope_id,
        )
        resource_tokens = (
            [tokens | {f"assigned_team:{team_id}"} for team_id in assigned_team_ids]
            if assigned_team_ids
            else [tokens]
        )
        candidate_overrides = [*overrides, proposed]
        first_denial: PolicyDecision | None = None
        for scoped_tokens in resource_tokens:
            decision = self.policy.decide(
                permission.key,
                scoped_tokens,
                role_rules,
                candidate_overrides,
            )
            if decision.allowed:
                return decision
            if first_denial is None:
                first_denial = decision
        assert first_denial is not None
        return first_denial

    def _require_current_role_manager(
        self,
        *,
        actor: UserAccount,
        account: UserAccount,
    ) -> None:
        self._require_current_grant_manager(actor=actor, account=account)
        if not self.decide(
            user_id=actor.id,
            permission="permissions.manage",
            resource_tokens={"global"},
        ).allowed:
            raise PermissionDelegationDenied(
                "actor does not hold current global permission management"
            )

    def _require_current_grant_manager(
        self, *, actor: UserAccount, account: UserAccount
    ) -> None:
        actor_person = self.db.get(Person, actor.person_id)
        target_person = self.db.get(Person, account.person_id)
        if (
            actor.status != "active"
            or actor.force_password_change
            or actor_person is None
            or actor_person.deleted_at is not None
            or target_person is None
            or target_person.deleted_at is not None
        ):
            raise PermissionDelegationDenied("actor or target account is unavailable")
        target_tokens = self.person_resource_tokens(
            actor_person_id=actor.person_id,
            target_person_id=account.person_id,
        )
        if not self.decide(
            user_id=actor.id,
            permission="permissions.manage",
            resource_tokens=target_tokens,
        ).allowed:
            raise PermissionDelegationDenied(
                "actor does not hold current permission management for the account"
            )
        if (
            not self.account_has_role(actor.id, "super_admin")
            and protected_management_target(self.db, account.id)
        ):
            raise PermissionDelegationDenied("only a super administrator may manage this account")

    def _require_role_delegation_authority(
        self,
        *,
        actor: UserAccount,
        account: UserAccount,
        role: Role,
        prospective_person: bool = False,
    ) -> None:
        actor_is_super_admin = self.account_has_role(actor.id, "super_admin")
        if role.key in {"super_admin", "finance"} and not actor_is_super_admin:
            raise PermissionDelegationDenied(
                "only an active super administrator can assign a protected role"
            )
        if actor_is_super_admin:
            # The super administrator role is the explicit delegation root.
            # Comparing self-scoped bundle entries against another account
            # would otherwise prevent normal role administration.
            return

        grants = self.db.execute(
            select(Permission, RolePermission.scope_kind)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == role.id)
            .order_by(Permission.id, RolePermission.scope_kind)
        )
        reference_time = datetime.now(UTC)
        for permission, role_scope_kind in grants:
            scope_kind = prospective_role_scope(role_scope_kind)
            scope_id = account.person_id if scope_kind == "person" else None
            delegation_input = PermissionOverrideCreate(
                permission_id=permission.id,
                effect="allow",
                scope_kind=scope_kind,
                scope_id=scope_id,
            )
            resource = (
                EvaluationResource(student_person_id=account.person_id)
                if prospective_person and scope_kind == "person"
                else self._scope_resource(
                    account=account,
                    input=delegation_input,
                    occurred_at=reference_time,
                )
            )
            self._require_delegation_authority(
                actor_user_id=actor.id,
                permission=permission,
                input=delegation_input,
                resource=resource,
            )

    def require_prospective_role_grant(
        self, *, actor_user_id: UUID, person_id: UUID, role: Role
    ) -> None:
        """Validate the real current manager and bundle for an account not yet created.

        Callers performing writes own the complete sorted participant locks.
        This never persists a placeholder account or bypasses delegation ceilings.
        """
        actor = self.require_prospective_role_manager(
            actor_user_id=actor_user_id, person_id=person_id
        )
        self._require_role_delegation_authority(
            actor=actor,
            account=UserAccount(person_id=person_id),
            role=role,
            prospective_person=True,
        )

    def require_prospective_role_manager(self, *, actor_user_id: UUID, person_id: UUID) -> UserAccount:
        """Current account + global and target permission checks, without granting a role."""
        actor = self.db.get(UserAccount, actor_user_id, populate_existing=True)
        person = self.db.get(Person, actor.person_id, populate_existing=True) if actor else None
        if (
            actor is None or actor.status != "active" or actor.force_password_change
            or person is None or person.deleted_at is not None
        ):
            raise PermissionDelegationDenied("actor account is unavailable")
        tokens = self.person_resource_tokens(
            actor_person_id=actor.person_id, target_person_id=person_id
        )
        if not all(
            self.decide(
                user_id=actor.id, permission="permissions.manage", resource_tokens=scope
            ).allowed
            for scope in ({"global"}, tokens)
        ):
            raise PermissionDelegationDenied("actor cannot manage roles")
        return actor

    def assign_role(
        self,
        *,
        account_id: UUID,
        role_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Role:
        """Assign a role, enforcing delegation for authenticated callers.

        ``actor_user_id=None`` is retained for internal maintenance and test-data
        setup.  Every HTTP role-assignment path passes the authenticated actor.
        """
        try:
            with write_transaction(self.db):
                account_person_id = self.db.scalar(
                    select(UserAccount.person_id).where(UserAccount.id == account_id)
                )
                if account_person_id is None:
                    raise RbacResourceNotFound("account not found")
                actor_person_id = None
                if actor_user_id is not None:
                    actor_person_id = self.db.scalar(
                        select(UserAccount.person_id).where(UserAccount.id == actor_user_id)
                    )
                    if actor_person_id is None:
                        raise PermissionDelegationDenied("actor account is not active")
                readiness = GuardianReadinessPolicy(self.db)
                person_ids = {account_person_id}
                if actor_person_id is not None:
                    person_ids.add(actor_person_id)
                readiness.lock_students(person_ids)
                locked_accounts = readiness.lock_accounts(person_ids)
                account_by_id = {
                    locked_account.id: locked_account for locked_account in locked_accounts
                }
                account = account_by_id.get(account_id)
                if account is None:
                    raise RbacResourceNotFound("account not found")
                actor = account_by_id.get(actor_user_id) if actor_user_id is not None else None
                if actor_user_id is not None and actor is None:
                    raise PermissionDelegationDenied("actor account is not active")
                role = self.db.scalar(
                    select(Role)
                    .where(Role.id == role_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if role is None:
                    raise RbacResourceNotFound("role not found")
                if actor is not None:
                    self._require_current_role_manager(actor=actor, account=account)
                    self._require_role_delegation_authority(
                        actor=actor,
                        account=account,
                        role=role,
                    )
                if self.db.get(UserRole, (account_id, role_id)) is None:
                    self.db.add(UserRole(user_id=account_id, role_id=role_id))
                    self.db.flush()
                result = role
            return result
        except IntegrityError as exc:
            raise DuplicatePermissionRule("role is already assigned") from exc

    def change_job_role(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        input: JobRoleChangeRequest,
        request_id: str,
        before_change: Callable[[], object] | None = None,
    ) -> JobRoleChangeResponse:
        """Replace staff roles and their lifecycle effects in one audited transaction."""
        from app.team_management.lifecycle import TeamLifecycleService
        from app.team_management.staff_service import staff_team_ids_for_account
        from app.team_management.write_support import lock_directory

        with atomic_write_transaction(self.db):
            participants = {user_id: person_id for user_id, person_id in self.db.execute(
                select(UserAccount.id, UserAccount.person_id)
                .where(UserAccount.id.in_({actor_user_id, account_id}))
            )}
            if account_id not in participants:
                raise RbacResourceNotFound("account not found")
            if actor_user_id not in participants:
                raise PermissionDelegationDenied("actor account is unavailable")
            # Discover the full identity lock set before taking any lock. Root
            # accounts share this ordering with existing role-removal paths.
            person_ids = set(participants.values()) | set(self.active_super_admin_person_ids())
            readiness = GuardianReadinessPolicy(self.db)
            people = {person.id: person for person in readiness.lock_students(person_ids)}
            accounts = readiness.lock_accounts(person_ids)
            by_id = {account.id: account for account in accounts}
            account = by_id.get(account_id)
            actor = by_id.get(actor_user_id)
            if account is None or people[account.person_id].deleted_at is not None:
                raise RbacResourceNotFound("account not found")
            if (actor is None or actor.force_password_change
                    or people[actor.person_id].deleted_at is not None):
                raise PermissionDelegationDenied("actor account is unavailable")
            if before_change is not None:
                before_change()
            self._require_current_role_manager(actor=actor, account=account)

            current_ids = set(self.db.scalars(
                select(UserRole.role_id).where(UserRole.user_id == account_id)
            ))
            if current_ids != set(input.expected_role_ids):
                raise JobRoleConflict("岗位已被其他操作更新，请刷新后重新确认")
            requested_ids = {input.to_role_id}
            if input.from_role_id is not None:
                requested_ids.add(input.from_role_id)
            role_by_id = {role.id: role for role in self.db.scalars(
                select(Role).where(Role.id.in_(current_ids | requested_ids))
                .order_by(Role.id).with_for_update().execution_options(populate_existing=True)
            )}
            if not requested_ids <= role_by_id.keys():
                raise RbacResourceNotFound("role not found")
            if any(role_by_id[value].key not in _STAFF_ROLE_KEYS for value in requested_ids):
                raise JobRoleConflict("换岗只能选择工作人员岗位，身份角色保持不变")
            staff_ids = {value for value in current_ids if role_by_id[value].key in _STAFF_ROLE_KEYS}
            if input.from_role_id is None:
                if staff_ids:
                    raise JobRoleConflict("当前已有工作人员岗位，请选择要替换的旧岗位")
            elif input.from_role_id not in staff_ids:
                raise JobRoleConflict("当前账号不再持有所选旧岗位，请刷新后重新确认")
            if input.from_role_id == input.to_role_id:
                raise JobRoleConflict("新岗位不能与旧岗位相同")
            removed_ids = (
                {input.from_role_id} if input.from_role_id is not None else set()
            ) if input.keep_concurrent_roles else staff_ids - {input.to_role_id}
            retained_ids = current_ids - removed_ids
            added_ids = {input.to_role_id} - current_ids
            final_ids = retained_ids | {input.to_role_id}
            to_role = role_by_id[input.to_role_id]
            self._require_role_delegation_authority(actor=actor, account=account, role=to_role)
            removes_root = any(role_by_id[value].key == "super_admin" for value in removed_ids)
            if removes_root:
                if not self.account_has_role(actor.id, "super_admin"):
                    raise PermissionDelegationDenied("only a super administrator can remove a root")
                if actor.id == account.id:
                    raise LastAdminProtected("the current super administrator role is protected")

            removed_staff_keys = {role_by_id[value].key for value in removed_ids}
            team_ids = staff_team_ids_for_account(
                self.db, account.id, role_keys=removed_staff_keys,
            )
            if "team_manager" in removed_staff_keys:
                team_ids.update(self.db.scalars(select(TeamManagerAssignment.team_id).where(
                    TeamManagerAssignment.account_id == account.id,
                    TeamManagerAssignment.ended_at.is_(None),
                )))
            lock_directory(self.db, list(team_ids))
            occurred_at = datetime.now(UTC)
            for removed_key in sorted(removed_staff_keys & {"team_manager", "coach", "assistant_coach"}):
                TeamLifecycleService(self.db).before_role_removal(
                    account.id, removed_key, actor.id, occurred_at,
                )
            for assignment in self.db.scalars(
                select(UserRole).where(UserRole.user_id == account_id,
                                       UserRole.role_id.in_(removed_ids))
                .order_by(UserRole.role_id).with_for_update()
                .execution_options(populate_existing=True)
            ):
                self.db.delete(assignment)
            for role_id in added_ids:
                self.db.add(UserRole(user_id=account_id, role_id=role_id))
            final_roles = sorted((role_by_id[value] for value in final_ids), key=lambda role: role.key)
            final_keys = {role.key for role in final_roles}
            staff_type = "coach" if "coach" in final_keys else (
                to_role.key if to_role.key in {"life_teacher", "customer_service", "administration", "team_manager", "assistant_coach"}
                else "other"
            )
            profile = self.db.scalar(select(StaffProfile).where(
                StaffProfile.person_id == account.person_id
            ).with_for_update().execution_options(populate_existing=True))
            if profile is None:
                self.db.add(StaffProfile(person_id=account.person_id, staff_type=staff_type))
            else:
                profile.staff_type = staff_type
            self.db.flush()
            if removes_root:
                self._ensure_recovery_super_admin(accounts)
            AuditService(self.db).append(
                actor_user_id=actor.id, action="account.job_role.change", resource_type="account",
                resource_id=account_id, purpose=None, outcome="allowed", request_id=request_id,
                metadata={
                    "old_role_keys": sorted(role_by_id[value].key for value in current_ids),
                    "new_role_keys": sorted(final_keys),
                    "keep_concurrent_roles": input.keep_concurrent_roles,
                },
            )
            result = JobRoleChangeResponse(
                account_id=account_id, added_role_ids=sorted(added_ids),
                removed_role_ids=sorted(removed_ids), retained_role_ids=sorted(retained_ids),
                roles=[JobRoleSummary(id=role.id, key=role.key, name=role.name) for role in final_roles],
            )
        return result

    def remove_role(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        role_id: UUID,
        request_id: str,
        account_readiness: AccountAccessReadinessPolicy | None = None,
    ) -> Role:
        from app.team_management.lifecycle import TeamLifecycleService
        from app.team_management.models import TeamMembership
        from app.team_management.write_support import lock_directory

        with atomic_write_transaction(self.db):
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
            actor_person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == actor_user_id)
            )
            if actor_person_id is None:
                raise PermissionDelegationDenied("actor account is not active")
            person_ids.add(actor_person_id)
            if role.key == "super_admin":
                person_ids.update(self.active_super_admin_person_ids())
            readiness_policy.lock_students(person_ids)
            locked_accounts = readiness_policy.lock_accounts(person_ids)
            account_by_id = {account.id: account for account in locked_accounts}
            account = account_by_id.get(account_id)
            if account is None:
                raise RbacResourceNotFound("account not found")
            actor = account_by_id.get(actor_user_id)
            if actor is None:
                raise PermissionDelegationDenied("actor account is not active")
            self._require_current_role_manager(actor=actor, account=account)
            active_admin_ids = (
                set(self.active_super_admin_account_ids()) if role.key == "super_admin" else set()
            )
            active_admins = [
                locked_account
                for locked_account in locked_accounts
                if locked_account.id in active_admin_ids and locked_account.status == "active"
            ]
            refreshed_role = self.db.scalar(
                select(Role).where(Role.id == role_id).with_for_update()
                .execution_options(populate_existing=True)
            )
            if refreshed_role is None:
                raise RbacResourceNotFound("role not found")
            role = refreshed_role
            if role.key == "super_admin" and not self.account_has_role(actor.id, "super_admin"):
                raise PermissionDelegationDenied(
                    "only an active super administrator can remove the super administrator role"
                )
            assignment = self.db.scalar(
                select(UserRole)
                .where(UserRole.user_id == account_id, UserRole.role_id == role_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if assignment is not None and role.key == "super_admin" and actor_user_id == account_id:
                raise LastAdminProtected("the current super administrator role is protected")
            if assignment is not None:
                if role.key in {"team_member", "team_manager", "coach", "assistant_coach"}:
                    from app.team_management.staff_service import staff_team_ids_for_account

                    team_ids = staff_team_ids_for_account(
                        self.db, account.id, role_keys={role.key},
                    )
                    if role.key == "team_member":
                        team_ids.update(
                            self.db.scalars(
                                select(TeamMembership.team_id).where(
                                    TeamMembership.person_id == account.person_id,
                                    TeamMembership.ended_at.is_(None),
                                )
                            )
                        )
                    elif role.key == "team_manager":
                        team_ids.update(
                            self.db.scalars(
                                select(TeamManagerAssignment.team_id).where(
                                    TeamManagerAssignment.account_id == account.id,
                                    TeamManagerAssignment.ended_at.is_(None),
                                )
                            )
                        )
                    lock_directory(self.db, list(team_ids))
                    TeamLifecycleService(self.db).before_role_removal(
                        account.id, role.key, actor_user_id, datetime.now(UTC)
                    )
                self.db.delete(assignment)
                self.db.flush()
                if role.key == "super_admin":
                    self._ensure_recovery_super_admin(active_admins)
                if role.key == "guardian":
                    readiness_policy.disable_unready_dependents(
                        account.person_id,
                        reference_date=business_today(),
                        revoked_at=datetime.now(UTC),
                        audit_actor_user_id=actor_user_id,
                        audit_request_id=request_id,
                        dependent_person_ids=dependent_person_ids,
                        locked_accounts=locked_accounts,
                        account_readiness=account_readiness,
                    )
            # Preserve HTTP success audit for a no-op removal, also for direct callers.
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="account.role.remove",
                resource_type="account",
                resource_id=account_id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={"role_key": role.key},
            )
            result = role
        return result

    def _lock_override_participants(
        self, actor_user_id: UUID, account_id: UUID, permission: Permission
    ) -> tuple[UserAccount, UserAccount]:
        participants = {account: person for account, person in self.db.execute(
            select(UserAccount.id, UserAccount.person_id)
            .where(UserAccount.id.in_({actor_user_id, account_id}))
        )}
        if account_id not in participants:
            raise RbacResourceNotFound("account not found")
        if actor_user_id not in participants:
            raise PermissionDelegationDenied("actor account is unavailable")
        person_ids = set(participants.values())
        if permission.key in {"accounts.manage", "permissions.manage"}:
            # Recovery-root locks must join the initial globally ordered set,
            # not be acquired after a target with a later UUID has been locked.
            person_ids.update(self.active_super_admin_person_ids())
        readiness = GuardianReadinessPolicy(self.db)
        readiness.lock_students(person_ids)
        accounts = {account.id: account for account in readiness.lock_accounts(person_ids)}
        account = accounts.get(account_id)
        actor = accounts.get(actor_user_id)
        if account is None:
            raise RbacResourceNotFound("account not found")
        if actor is None:
            raise PermissionDelegationDenied("actor account is unavailable")
        return actor, account

    def _lock_talent_grant_participants(
        self,
        actor_user_id: UUID,
        target_account_id: UUID,
        permission: Permission,
    ) -> None:
        if not permission.key.startswith(("talent_tests.", "esports_assessments.", "reactionx.")):
            return
        person_ids = set(
            self.db.scalars(
                select(UserAccount.person_id).where(
                    UserAccount.id.in_({actor_user_id, target_account_id})
                )
            )
        )
        readiness = GuardianReadinessPolicy(self.db)
        readiness.lock_students(person_ids)
        readiness.lock_accounts(person_ids)
        # Role maintenance uses the same person -> account -> role ordering.
        self.db.scalar(select(Role).where(Role.key == "super_admin").with_for_update())

    def _lock_team_grant_participants(
        self,
        actor_user_id: UUID,
        target_account_id: UUID,
        permission: Permission,
    ) -> None:
        if permission.key not in _TEAM_PERMISSION_SCOPES:
            return
        person_ids = set(
            self.db.scalars(
                select(UserAccount.person_id).where(
                    UserAccount.id.in_({actor_user_id, target_account_id})
                )
            )
        )
        readiness = GuardianReadinessPolicy(self.db)
        readiness.lock_students(person_ids)
        readiness.lock_accounts(person_ids)

    def _require_team_grant_manager(
        self,
        actor_user_id: UUID,
        target_account_id: UUID,
        permission: Permission,
    ) -> None:
        if permission.key not in _TEAM_PERMISSION_SCOPES:
            return
        actor = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == actor_user_id,
                UserAccount.status == "active",
                UserAccount.force_password_change.is_(False),
                Person.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        target = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(UserAccount.id == target_account_id, Person.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
        if actor is None or target is None:
            raise PermissionDelegationDenied("actor or target account is unavailable")
        tokens = self.person_resource_tokens(
            actor_person_id=actor.person_id,
            target_person_id=target.person_id,
        )
        if not self.decide(
            user_id=actor.id,
            permission="permissions.manage",
            resource_tokens=tokens,
        ).allowed:
            raise PermissionDelegationDenied("actor cannot manage this account's team permissions")

    def _require_talent_grant_manager(
        self,
        actor_user_id: UUID,
        target_account_id: UUID,
        permission: Permission,
    ) -> None:
        if not permission.key.startswith(("talent_tests.", "esports_assessments.", "reactionx.")):
            return
        actor = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == actor_user_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if actor is None or not self.account_has_role(actor.id, "super_admin"):
            raise PermissionDelegationDenied("talent grants require an active super administrator")
        target = self._require_account(target_account_id)
        tokens = self.person_resource_tokens(
            actor_person_id=actor.person_id,
            target_person_id=target.person_id,
        )
        if not self.decide(
            user_id=actor.id, permission="permissions.manage", resource_tokens=tokens
        ).allowed:
            raise PermissionDelegationDenied(
                "actor cannot manage this account's talent permissions"
            )

    def add_override(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        input: PermissionOverrideCreate,
    ) -> tuple[UserPermissionOverride, Permission]:
        try:
            with write_transaction(self.db):
                permission = self._require_permission(input.permission_id)
                actor, account = self._lock_override_participants(
                    actor_user_id, account_id, permission
                )
                self._lock_talent_grant_participants(actor_user_id, account_id, permission)
                self._lock_team_grant_participants(actor_user_id, account_id, permission)
                self._require_current_grant_manager(actor=actor, account=account)
                self._require_team_grant_manager(actor_user_id, account_id, permission)
                self._require_talent_grant_manager(actor_user_id, account_id, permission)
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
                if not permission.key.startswith(
                    ("talent_tests.", "esports_assessments.", "reactionx.")
                ):
                    self._require_delegation_authority(
                        actor_user_id=actor_user_id,
                        permission=permission,
                        input=input,
                        resource=resource,
                    )
                active_admins = (
                    self.lock_active_super_admin_accounts()
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
                    self._ensure_recovery_super_admin(active_admins)
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
            permission_id = self.db.scalar(
                select(UserPermissionOverride.permission_id).where(
                    UserPermissionOverride.id == override_id,
                    UserPermissionOverride.user_id == account_id,
                )
            )
            if permission_id is None:
                raise RbacResourceNotFound("permission override not found")
            permission = self._require_permission(permission_id)
            actor, account = self._lock_override_participants(
                actor_user_id, account_id, permission
            )
            self._lock_talent_grant_participants(actor_user_id, account_id, permission)
            self._lock_team_grant_participants(actor_user_id, account_id, permission)
            override = self.db.scalar(
                select(UserPermissionOverride)
                .where(
                    UserPermissionOverride.id == override_id,
                    UserPermissionOverride.user_id == account_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if override is None:
                raise RbacResourceNotFound("permission override not found")
            self._require_current_grant_manager(actor=actor, account=account)
            permission = self._require_permission(override.permission_id)
            self._require_team_grant_manager(actor_user_id, account_id, permission)
            self._require_talent_grant_manager(actor_user_id, account_id, permission)
            active_admins = (
                self.lock_active_super_admin_accounts()
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
            if override.effect == "deny" and not permission.key.startswith(
                ("talent_tests.", "esports_assessments.", "reactionx.")
            ):
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
                self._ensure_recovery_super_admin(active_admins)
        return result

    def preview_override(
        self,
        *,
        actor_user_id: UUID,
        account_id: UUID,
        input: PermissionOverrideCreate,
    ) -> PolicyDecision:
        account = self._require_account(account_id)
        permission = self._require_permission(input.permission_id)
        self._require_talent_grant_manager(actor_user_id, account_id, permission)
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
        if permission.key in _TEAM_PERMISSION_SCOPES:
            return self._preview_team_override(
                account=account,
                permission=permission,
                input=input,
                occurred_at=reference_time,
            )
        tokens = EvaluationAccessService(self.db).resource_tokens(
            actor_person_id=account.person_id,
            resource=resource,
            occurred_at=reference_time,
        )
        if (
            permission.key in _PERSONNEL_INTAKE_PERMISSION_SCOPES
            and input.scope_kind == "assigned_team"
        ):
            tokens.add(f"assigned_team:{input.scope_id}")
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
