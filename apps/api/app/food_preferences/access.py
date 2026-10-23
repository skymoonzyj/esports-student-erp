from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.food_preferences.errors import (
    FoodPreferenceNotFound,
    FoodPreferencePermissionDenied,
)
from app.identity.models import GuardianRelationship, Person
from app.rbac.policy import PolicyDecision, PolicyRule
from app.rbac.service import RbacDecisionContext, RbacService

_FOOD_PERMISSIONS = frozenset(
    {
        "food_preferences.read",
        "food_preferences.update",
        "food_preferences.catalog.manage",
        "food_preferences.export",
    }
)
_SELF_BASELINE_PERMISSIONS = frozenset(
    {"food_preferences.read", "food_preferences.update"}
)


class FoodPreferenceAccess:
    def __init__(
        self,
        db: Session,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> None:
        if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        self.db = db
        self.account_id = account_id
        self.occurred_at = occurred_at
        self.rbac = RbacService(db)

    def _active_account(self) -> UserAccount:
        account = self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == self.account_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        if account is None:
            raise FoodPreferencePermissionDenied(
                "food preference operation is not available"
            )
        return account

    def _context(self) -> RbacDecisionContext:
        return self.rbac.decision_context(
            self.account_id,
            occurred_at=self.occurred_at,
        )

    def _self_decision(
        self,
        permission: str,
        *,
        account: UserAccount,
        context: RbacDecisionContext,
    ) -> PolicyDecision:
        baseline_rules = list(context.role_rules)
        if permission in _SELF_BASELINE_PERMISSIONS:
            baseline_rules.append(
                PolicyRule(
                    permission=permission,
                    effect="allow",
                    scope_kind="self",
                )
            )
        tokens = self.rbac.person_resource_tokens(
            actor_person_id=account.person_id,
            target_person_id=account.person_id,
            reference_date=business_date_at(self.occurred_at),
        )
        return context.policy.decide(
            permission,
            tokens,
            baseline_rules,
            context.overrides,
        )

    def require_self(self, permission: str) -> Person:
        if permission not in _SELF_BASELINE_PERMISSIONS:
            raise FoodPreferencePermissionDenied(
                "food preference self operation is not available"
            )
        account = self._active_account()
        context = self._context()
        if not self._self_decision(
            permission,
            account=account,
            context=context,
        ).allowed:
            raise FoodPreferencePermissionDenied(
                "food preference self operation is not available"
            )
        person = self.db.scalar(
            select(Person).where(
                Person.id == account.person_id,
                Person.deleted_at.is_(None),
            )
        )
        if person is None:
            raise FoodPreferencePermissionDenied(
                "food preference self operation is not available"
            )
        return person

    def require_any_capability(self, permission: str) -> None:
        if permission not in _FOOD_PERMISSIONS:
            raise FoodPreferencePermissionDenied(
                "food preference operation is not available"
            )
        account = self._active_account()
        context = self._context()
        global_deny = any(
            rule.permission == permission
            and rule.effect == "deny"
            and rule.scope_kind == "global"
            for rule in context.overrides
        )
        if global_deny:
            raise FoodPreferencePermissionDenied(
                "food preference operation is not available"
            )
        if self._self_decision(
            permission,
            account=account,
            context=context,
        ).allowed:
            return
        matching_denies = [
            rule
            for rule in context.overrides
            if rule.permission == permission and rule.effect == "deny"
        ]
        for rule in (*context.role_rules, *context.overrides):
            if rule.permission != permission or rule.effect != "allow":
                continue
            if rule.scope_kind == "global":
                return
            if rule.scope_kind == "self":
                continue
            if rule.scope_kind == "family":
                if not any(deny.scope_kind == "family" for deny in matching_denies):
                    return
                continue
            if rule.scope_kind == "person":
                if rule.scope_id is None:
                    if not any(
                        deny.scope_kind == "person" and deny.scope_id is None
                        for deny in matching_denies
                    ):
                        return
                    continue
                tokens = {"global", f"person:{rule.scope_id}"}
                if context.decide(permission, tokens).allowed:
                    return
        raise FoodPreferencePermissionDenied(
            "food preference operation is not available"
        )

    def require_global(self, permission: str) -> None:
        if permission not in _FOOD_PERMISSIONS:
            raise FoodPreferencePermissionDenied(
                "food preference operation is not available"
            )
        self._active_account()
        if not self._context().decide(permission, {"global"}).allowed:
            raise FoodPreferencePermissionDenied(
                "food preference global operation is not available"
            )

    def require_target(self, permission: str, person_id: UUID) -> Person:
        self.require_any_capability(permission)
        account = self._active_account()
        target = self.db.scalar(
            select(Person).where(
                Person.id == person_id,
                Person.deleted_at.is_(None),
            )
        )
        if target is None:
            raise FoodPreferenceNotFound("food preference target not found")
        tokens = self.rbac.person_resource_tokens(
            actor_person_id=account.person_id,
            target_person_id=target.id,
            reference_date=business_date_at(self.occurred_at),
        )
        if not self._context().decide(permission, tokens).allowed:
            raise FoodPreferenceNotFound("food preference target not found")
        return target

    def can_target(self, permission: str, person_id: UUID) -> bool:
        try:
            self.require_target(permission, person_id)
        except (FoodPreferenceNotFound, FoodPreferencePermissionDenied):
            return False
        return True

    @property
    def access_revision(self) -> str:
        account = self._active_account()
        context = self._context()
        rules = [
            (
                source,
                rule.permission,
                rule.effect,
                rule.scope_kind,
                str(rule.scope_id) if rule.scope_id is not None else "",
            )
            for source, source_rules in (
                ("role", context.role_rules),
                ("override", context.overrides),
            )
            for rule in source_rules
            if rule.permission in _FOOD_PERMISSIONS
        ]
        reference_date = business_date_at(self.occurred_at)
        relationships = [
            (
                str(relationship_id),
                str(student_id),
                valid_from.isoformat(),
                valid_until.isoformat() if valid_until is not None else "",
            )
            for relationship_id, student_id, valid_from, valid_until in self.db.execute(
                select(
                    GuardianRelationship.id,
                    GuardianRelationship.student_person_id,
                    GuardianRelationship.valid_from,
                    GuardianRelationship.valid_until,
                )
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
                        GuardianRelationship.ended_at > self.occurred_at,
                    ),
                )
                .order_by(GuardianRelationship.id)
            )
        ]
        canonical = json.dumps(
            {
                "account_id": str(account.id),
                "actor_person_id": str(account.person_id),
                "self_baseline": "v1",
                "rules": sorted(rules),
                "relationships": relationships,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()
