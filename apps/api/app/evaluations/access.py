from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    SQLColumnExpression,
    and_,
    false,
    or_,
    select,
    true,
    tuple_,
)
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.identity.models import GuardianRelationship
from app.rbac.models import (
    Permission,
    RolePermission,
    UserPermissionOverride,
    UserRole,
)
from app.rbac.policy import Effect, PolicyRule, RbacPolicy, ScopeKind
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment


@dataclass(frozen=True)
class EvaluationResource:
    student_person_id: UUID | None = None
    staff_person_id: UUID | None = None
    cohort_id: UUID | None = None
    project_id: UUID | None = None
    class_id: UUID | None = None
    enrollment_id: UUID | None = None
    building_id: UUID | None = None
    floor_id: UUID | None = None


@dataclass(frozen=True)
class EvaluationSqlResource:
    student_person_id: SQLColumnExpression[UUID] | None = None
    staff_person_id: SQLColumnExpression[UUID] | None = None
    cohort_id: SQLColumnExpression[UUID] | None = None
    project_id: SQLColumnExpression[UUID] | None = None
    class_id: SQLColumnExpression[UUID] | None = None
    enrollment_id: SQLColumnExpression[UUID] | None = None


@dataclass(frozen=True)
class EvaluationSqlContext:
    actor_person_id: UUID | None
    role_rules: tuple[PolicyRule, ...]
    overrides: tuple[PolicyRule, ...]
    family_student_ids: frozenset[UUID]
    assigned_class_ids: frozenset[UUID]
    staff_project_scopes: frozenset[tuple[UUID, UUID]]
    staff_class_scopes: frozenset[UUID]
    account_active: bool
    center: EvaluationSqlContext | None = None

    @staticmethod
    def _any(conditions: list[ColumnElement[bool]]) -> ColumnElement[bool]:
        return or_(*conditions) if conditions else false()

    def _scope_matches(
        self,
        rule: PolicyRule,
        resource: EvaluationSqlResource,
    ) -> ColumnElement[bool]:
        if rule.scope_kind == "global":
            return true()
        if rule.scope_kind == "self":
            if self.actor_person_id is None:
                return false()
            return self._any(
                [
                    column == self.actor_person_id
                    for column in (
                        resource.student_person_id,
                        resource.staff_person_id,
                    )
                    if column is not None
                ]
            )
        if rule.scope_kind == "family":
            if resource.student_person_id is None or not self.family_student_ids:
                return false()
            return resource.student_person_id.in_(self.family_student_ids)
        if rule.scope_kind == "person":
            person_columns = [
                column
                for column in (
                    resource.student_person_id,
                    resource.staff_person_id,
                )
                if column is not None
            ]
            if rule.scope_id is None:
                return self._any([column.is_not(None) for column in person_columns])
            return self._any([column == rule.scope_id for column in person_columns])
        if rule.scope_kind == "assigned_class":
            if resource.class_id is None:
                return false()
            if rule.scope_id is not None:
                return resource.class_id == rule.scope_id
            if not self.assigned_class_ids:
                return false()
            return resource.class_id.in_(self.assigned_class_ids)

        column = {
            "cohort": resource.cohort_id,
            "project": resource.project_id,
            "enrollment": resource.enrollment_id,
        }.get(rule.scope_kind)
        if column is None:
            return false()
        return column.is_not(None) if rule.scope_id is None else column == rule.scope_id

    def _rules_match(
        self,
        rules: Iterable[PolicyRule],
        resource: EvaluationSqlResource,
    ) -> ColumnElement[bool]:
        return self._any([self._scope_matches(rule, resource) for rule in rules])

    def _staff_assignment_matches(
        self,
        resource: EvaluationSqlResource,
    ) -> ColumnElement[bool]:
        matches: list[ColumnElement[bool]] = []
        if resource.class_id is not None and self.staff_class_scopes:
            matches.append(resource.class_id.in_(self.staff_class_scopes))
        if (
            resource.cohort_id is not None
            and resource.project_id is not None
            and self.staff_project_scopes
        ):
            matches.append(
                tuple_(resource.cohort_id, resource.project_id).in_(
                    self.staff_project_scopes
                )
            )
        return self._any(matches)

    def allowed(self, resource: EvaluationSqlResource) -> ColumnElement[bool]:
        if not self.account_active:
            return false()
        explicit_deny = self._rules_match(
            (rule for rule in self.overrides if rule.effect == "deny"),
            resource,
        )
        account_allow = self._rules_match(
            (rule for rule in self.overrides if rule.effect == "allow"),
            resource,
        )
        role_allow = self._rules_match(self.role_rules, resource)
        return and_(
            self.center.allowed(resource) if self.center is not None else true(),
            # A missing nullable target does not match a scoped deny.  SQL
            # NOT(NULL) would otherwise hide unrelated unmatched tasks.
            explicit_deny.is_not(true()),
            or_(role_allow, account_allow, self._staff_assignment_matches(resource)),
        )


@dataclass(frozen=True)
class EvaluationDecision:
    allowed: bool
    reason: str


class EvaluationAccessService:
    def __init__(self, db: Session, *, policy: RbacPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or RbacPolicy()

    def center_scoped_sql_context(
        self,
        *,
        account_id: UUID,
        permission: str,
        occurred_at: datetime,
    ) -> EvaluationSqlContext:
        """Intersect an operation with center access on the same SQL resource.

        Opt-in at evaluation resource boundaries: generic RBAC consumers and
        relationship metadata retain their existing semantics.
        """
        return replace(
            self.sql_context(account_id=account_id, permission=permission, occurred_at=occurred_at),
            center=self.sql_context(
                account_id=account_id,
                permission="evaluation_center.access",
                occurred_at=occurred_at,
            ),
        )

    @staticmethod
    def _require_aware(occurred_at: datetime) -> None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def _active_overrides(
        self,
        *,
        account_id: UUID,
        permission: str,
        occurred_at: datetime,
    ) -> list[PolicyRule]:
        return [
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
                    UserPermissionOverride.user_id == account_id,
                    Permission.key == permission,
                    or_(
                        UserPermissionOverride.valid_from.is_(None),
                        UserPermissionOverride.valid_from <= occurred_at,
                    ),
                    or_(
                        UserPermissionOverride.valid_until.is_(None),
                        UserPermissionOverride.valid_until > occurred_at,
                    ),
                )
            )
        ]

    def _role_rules(self, *, account_id: UUID, permission: str) -> list[PolicyRule]:
        return [
            PolicyRule(
                permission=permission_key,
                effect="allow",
                scope_kind=cast(ScopeKind, scope_kind),
            )
            for permission_key, scope_kind in self.db.execute(
                select(Permission.key, RolePermission.scope_kind)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(UserRole, UserRole.role_id == RolePermission.role_id)
                .where(
                    UserRole.user_id == account_id,
                    Permission.key == permission,
                )
            )
        ]

    def sql_context(
        self,
        *,
        account_id: UUID,
        permission: str,
        occurred_at: datetime,
    ) -> EvaluationSqlContext:
        self._require_aware(occurred_at)
        account = self.db.get(UserAccount, account_id)
        if account is None or account.status != "active":
            return EvaluationSqlContext(
                actor_person_id=None,
                role_rules=(),
                overrides=(),
                family_student_ids=frozenset(),
                assigned_class_ids=frozenset(),
                staff_project_scopes=frozenset(),
                staff_class_scopes=frozenset(),
                account_active=False,
            )

        reference_date = business_date_at(occurred_at)
        family_student_ids = frozenset(
            self.db.scalars(
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
                .distinct()
            )
        )
        assigned_class_ids = frozenset(
            self.db.scalars(
                select(CoachAssignment.class_id)
                .where(
                    CoachAssignment.coach_person_id == account.person_id,
                    CoachAssignment.valid_from <= occurred_at,
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > occurred_at,
                    ),
                    or_(
                        CoachAssignment.ended_at.is_(None),
                        CoachAssignment.ended_at > occurred_at,
                    ),
                    CoachEligibility.eligible_person_predicate(
                        CoachAssignment.coach_person_id
                    ),
                )
                .distinct()
            )
        )
        staff_project_scopes: set[tuple[UUID, UUID]] = set()
        staff_class_scopes: set[UUID] = set()
        for cohort_id, project_id, class_id in self.db.execute(
            select(
                CohortStaffAssignment.cohort_id,
                CohortStaffAssignment.project_id,
                CohortStaffAssignment.class_id,
            )
            .join(
                CohortStaffAssignmentPermission,
                CohortStaffAssignmentPermission.assignment_id
                == CohortStaffAssignment.id,
            )
            .join(
                Permission,
                Permission.id == CohortStaffAssignmentPermission.permission_id,
            )
            .where(
                CohortStaffAssignment.staff_person_id == account.person_id,
                Permission.key == permission,
                CohortStaffAssignment.valid_from <= occurred_at,
                or_(
                    CohortStaffAssignment.valid_until.is_(None),
                    CohortStaffAssignment.valid_until > occurred_at,
                ),
                or_(
                    CohortStaffAssignment.ended_at.is_(None),
                    CohortStaffAssignment.ended_at > occurred_at,
                ),
            )
        ):
            if project_id is not None:
                staff_project_scopes.add((cohort_id, project_id))
            if class_id is not None:
                staff_class_scopes.add(class_id)
        return EvaluationSqlContext(
            actor_person_id=account.person_id,
            role_rules=tuple(
                self._role_rules(account_id=account_id, permission=permission)
            ),
            overrides=tuple(
                self._active_overrides(
                    account_id=account_id,
                    permission=permission,
                    occurred_at=occurred_at,
                )
            ),
            family_student_ids=family_student_ids,
            assigned_class_ids=assigned_class_ids,
            staff_project_scopes=frozenset(staff_project_scopes),
            staff_class_scopes=frozenset(staff_class_scopes),
            account_active=True,
        )

    def _has_family_relationship(
        self,
        *,
        actor_person_id: UUID,
        student_person_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        reference_date = business_date_at(occurred_at)
        return (
            self.db.scalar(
                select(GuardianRelationship.id)
                .where(
                    GuardianRelationship.guardian_person_id == actor_person_id,
                    GuardianRelationship.student_person_id == student_person_id,
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
                .limit(1)
            )
            is not None
        )

    def _active_family_student_ids(
        self,
        *,
        actor_person_id: UUID,
        student_person_ids: set[UUID],
        occurred_at: datetime,
    ) -> set[UUID]:
        if not student_person_ids:
            return set()
        reference_date = business_date_at(occurred_at)
        return set(
            self.db.scalars(
                select(GuardianRelationship.student_person_id)
                .where(
                    GuardianRelationship.guardian_person_id == actor_person_id,
                    GuardianRelationship.student_person_id.in_(student_person_ids),
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
                .distinct()
            )
        )

    def _has_current_coach_assignment(
        self,
        *,
        actor_person_id: UUID,
        class_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        return (
            self.db.scalar(
                select(CoachAssignment.id)
                .where(
                    CoachAssignment.coach_person_id == actor_person_id,
                    CoachAssignment.class_id == class_id,
                    CoachAssignment.valid_from <= occurred_at,
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > occurred_at,
                    ),
                    or_(
                        CoachAssignment.ended_at.is_(None),
                        CoachAssignment.ended_at > occurred_at,
                    ),
                    CoachEligibility.eligible_person_predicate(CoachAssignment.coach_person_id),
                )
                .limit(1)
            )
            is not None
        )

    def resource_tokens(
        self,
        *,
        actor_person_id: UUID,
        resource: EvaluationResource,
        occurred_at: datetime,
    ) -> set[str]:
        self._require_aware(occurred_at)
        tokens = {"global"}
        person_ids = {
            person_id
            for person_id in (resource.student_person_id, resource.staff_person_id)
            if person_id is not None
        }
        for person_id in person_ids:
            tokens.add(f"person:{person_id}")
            if person_id == actor_person_id:
                tokens.add(f"self:{person_id}")
        if resource.student_person_id is not None and self._has_family_relationship(
            actor_person_id=actor_person_id,
            student_person_id=resource.student_person_id,
            occurred_at=occurred_at,
        ):
            tokens.add(f"family:{resource.student_person_id}")
        if resource.cohort_id is not None:
            tokens.add(f"cohort:{resource.cohort_id}")
        if resource.project_id is not None:
            tokens.add(f"project:{resource.project_id}")
        if resource.class_id is not None:
            tokens.add(f"assigned_class:{resource.class_id}")
            if self._has_current_coach_assignment(
                actor_person_id=actor_person_id,
                class_id=resource.class_id,
                occurred_at=occurred_at,
            ):
                tokens.add("assigned_class")
        if resource.enrollment_id is not None:
            tokens.add(f"enrollment:{resource.enrollment_id}")
        if resource.building_id is not None:
            tokens.add(f"building:{resource.building_id}")
        if resource.floor_id is not None:
            tokens.add(f"floor:{resource.floor_id}")
        return tokens

    def _staff_assignment_allows(
        self,
        *,
        actor_person_id: UUID,
        permission: str,
        resource: EvaluationResource,
        occurred_at: datetime,
    ) -> bool:
        scope_match: list[ColumnElement[bool]] = []
        if resource.project_id is not None and resource.cohort_id is not None:
            scope_match.append(
                (CohortStaffAssignment.project_id == resource.project_id)
                & (CohortStaffAssignment.cohort_id == resource.cohort_id)
            )
        if resource.class_id is not None:
            scope_match.append(CohortStaffAssignment.class_id == resource.class_id)
        if not scope_match:
            return False
        return (
            self.db.scalar(
                select(CohortStaffAssignment.id)
                .join(
                    CohortStaffAssignmentPermission,
                    CohortStaffAssignmentPermission.assignment_id == CohortStaffAssignment.id,
                )
                .join(
                    Permission,
                    Permission.id == CohortStaffAssignmentPermission.permission_id,
                )
                .where(
                    CohortStaffAssignment.staff_person_id == actor_person_id,
                    Permission.key == permission,
                    CohortStaffAssignment.valid_from <= occurred_at,
                    or_(
                        CohortStaffAssignment.valid_until.is_(None),
                        CohortStaffAssignment.valid_until > occurred_at,
                    ),
                    or_(
                        CohortStaffAssignment.ended_at.is_(None),
                        CohortStaffAssignment.ended_at > occurred_at,
                    ),
                    or_(*scope_match),
                )
                .limit(1)
            )
            is not None
        )

    def decide(
        self,
        *,
        account_id: UUID,
        permission: str,
        resource: EvaluationResource,
        occurred_at: datetime,
    ) -> EvaluationDecision:
        self._require_aware(occurred_at)
        account = self.db.get(UserAccount, account_id)
        if account is None or account.status != "active":
            return EvaluationDecision(False, "inactive_account")

        tokens = self.resource_tokens(
            actor_person_id=account.person_id,
            resource=resource,
            occurred_at=occurred_at,
        )
        overrides = self._active_overrides(
            account_id=account_id,
            permission=permission,
            occurred_at=occurred_at,
        )
        override_decision = self.policy.decide(permission, tokens, [], overrides)
        if override_decision.reason == "explicit_deny":
            return EvaluationDecision(False, "explicit_deny")

        role_decision = self.policy.decide(
            permission,
            tokens,
            self._role_rules(account_id=account_id, permission=permission),
            [],
        )
        if role_decision.allowed:
            return EvaluationDecision(True, "role_allow")
        if override_decision.allowed:
            return EvaluationDecision(True, "account_allow")
        if self._staff_assignment_allows(
            actor_person_id=account.person_id,
            permission=permission,
            resource=resource,
            occurred_at=occurred_at,
        ):
            return EvaluationDecision(True, "staff_assignment_allow")
        return EvaluationDecision(False, "no_matching_grant")

    def decide_resources(
        self,
        *,
        account_id: UUID,
        permission: str,
        resources: Iterable[EvaluationResource],
        occurred_at: datetime,
    ) -> list[EvaluationDecision]:
        self._require_aware(occurred_at)
        resource_list = list(resources)
        if not resource_list:
            return []
        account = self.db.get(UserAccount, account_id)
        if account is None or account.status != "active":
            return [
                EvaluationDecision(False, "inactive_account")
                for _resource in resource_list
            ]
        overrides = self._active_overrides(
            account_id=account_id,
            permission=permission,
            occurred_at=occurred_at,
        )
        role_rules = self._role_rules(account_id=account_id, permission=permission)
        family_student_ids = self._active_family_student_ids(
            actor_person_id=account.person_id,
            student_person_ids={
                resource.student_person_id
                for resource in resource_list
                if resource.student_person_id is not None
            },
            occurred_at=occurred_at,
        )
        resource_class_ids = {
            resource.class_id
            for resource in resource_list
            if resource.class_id is not None
        }
        assigned_class_ids = (
            set(
                self.db.scalars(
                    select(CoachAssignment.class_id)
                    .where(
                        CoachAssignment.coach_person_id == account.person_id,
                        CoachAssignment.class_id.in_(resource_class_ids),
                        CoachAssignment.valid_from <= occurred_at,
                        or_(
                            CoachAssignment.valid_until.is_(None),
                            CoachAssignment.valid_until > occurred_at,
                        ),
                        or_(
                            CoachAssignment.ended_at.is_(None),
                            CoachAssignment.ended_at > occurred_at,
                        ),
                        CoachEligibility.eligible_person_predicate(
                            CoachAssignment.coach_person_id
                        ),
                    )
                    .distinct()
                )
            )
            if resource_class_ids
            else set()
        )
        staff_project_scopes: set[tuple[UUID, UUID]] = set()
        staff_class_scopes: set[UUID] = set()
        if any(
            resource.class_id is not None
            or (resource.cohort_id is not None and resource.project_id is not None)
            for resource in resource_list
        ):
            for cohort_id, project_id, class_id in self.db.execute(
                select(
                    CohortStaffAssignment.cohort_id,
                    CohortStaffAssignment.project_id,
                    CohortStaffAssignment.class_id,
                )
                .join(
                    CohortStaffAssignmentPermission,
                    CohortStaffAssignmentPermission.assignment_id
                    == CohortStaffAssignment.id,
                )
                .join(
                    Permission,
                    Permission.id == CohortStaffAssignmentPermission.permission_id,
                )
                .where(
                    CohortStaffAssignment.staff_person_id == account.person_id,
                    Permission.key == permission,
                    CohortStaffAssignment.valid_from <= occurred_at,
                    or_(
                        CohortStaffAssignment.valid_until.is_(None),
                        CohortStaffAssignment.valid_until > occurred_at,
                    ),
                    or_(
                        CohortStaffAssignment.ended_at.is_(None),
                        CohortStaffAssignment.ended_at > occurred_at,
                    ),
                )
            ):
                if project_id is not None:
                    staff_project_scopes.add((cohort_id, project_id))
                if class_id is not None:
                    staff_class_scopes.add(class_id)

        decisions: list[EvaluationDecision] = []
        for resource in resource_list:
            tokens = {"global"}
            person_ids = {
                person_id
                for person_id in (
                    resource.student_person_id,
                    resource.staff_person_id,
                )
                if person_id is not None
            }
            for person_id in person_ids:
                tokens.add(f"person:{person_id}")
                if person_id == account.person_id:
                    tokens.add(f"self:{person_id}")
            if resource.student_person_id in family_student_ids:
                tokens.add(f"family:{resource.student_person_id}")
            if resource.cohort_id is not None:
                tokens.add(f"cohort:{resource.cohort_id}")
            if resource.project_id is not None:
                tokens.add(f"project:{resource.project_id}")
            if resource.class_id is not None:
                tokens.add(f"assigned_class:{resource.class_id}")
                if resource.class_id in assigned_class_ids:
                    tokens.add("assigned_class")
            if resource.enrollment_id is not None:
                tokens.add(f"enrollment:{resource.enrollment_id}")
            if resource.building_id is not None:
                tokens.add(f"building:{resource.building_id}")
            if resource.floor_id is not None:
                tokens.add(f"floor:{resource.floor_id}")

            override_decision = self.policy.decide(permission, tokens, [], overrides)
            if override_decision.reason == "explicit_deny":
                decisions.append(EvaluationDecision(False, "explicit_deny"))
                continue
            role_decision = self.policy.decide(
                permission,
                tokens,
                role_rules,
                [],
            )
            staff_allowed = (
                resource.class_id in staff_class_scopes
                if resource.class_id is not None
                else False
            ) or (
                resource.cohort_id is not None
                and resource.project_id is not None
                and (resource.cohort_id, resource.project_id)
                in staff_project_scopes
            )
            if role_decision.allowed:
                decisions.append(EvaluationDecision(True, "role_allow"))
            elif override_decision.allowed:
                decisions.append(EvaluationDecision(True, "account_allow"))
            elif staff_allowed:
                decisions.append(EvaluationDecision(True, "staff_assignment_allow"))
            else:
                decisions.append(EvaluationDecision(False, "no_matching_grant"))
        return decisions

    def filter_resources(
        self,
        *,
        account_id: UUID,
        permission: str,
        resources: Iterable[EvaluationResource],
        occurred_at: datetime,
    ) -> list[EvaluationResource]:
        resource_list = list(resources)
        decisions = self.decide_resources(
            account_id=account_id,
            permission=permission,
            resources=resource_list,
            occurred_at=occurred_at,
        )
        return [
            resource
            for resource, decision in zip(resource_list, decisions, strict=True)
            if decision.allowed
        ]
