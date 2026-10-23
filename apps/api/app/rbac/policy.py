from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

Effect = Literal["allow", "deny"]
ScopeKind = Literal[
    "global",
    "self",
    "family",
    "person",
    "assigned_class",
    "assigned_team",
    "cohort",
    "project",
    "enrollment",
    "building",
    "floor",
]


@dataclass(frozen=True)
class PolicyRule:
    permission: str
    effect: Effect
    scope_kind: ScopeKind
    scope_id: UUID | None = None


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str


class RbacPolicy:
    @staticmethod
    def _matches_scope(rule: PolicyRule, resource_tokens: set[str]) -> bool:
        if rule.scope_id is not None:
            return f"{rule.scope_kind}:{rule.scope_id}" in resource_tokens
        if rule.scope_kind == "assigned_class":
            return "assigned_class" in resource_tokens
        if rule.scope_kind in resource_tokens:
            return True
        prefix = f"{rule.scope_kind}:"
        return any(token.startswith(prefix) for token in resource_tokens)

    def _matches(
        self,
        rule: PolicyRule,
        permission: str,
        resource_tokens: set[str],
    ) -> bool:
        return rule.permission == permission and self._matches_scope(rule, resource_tokens)

    def decide(
        self,
        permission: str,
        resource_tokens: set[str],
        role_rules: list[PolicyRule],
        overrides: list[PolicyRule],
    ) -> PolicyDecision:
        if any(
            rule.effect == "deny" and self._matches(rule, permission, resource_tokens)
            for rule in overrides
        ):
            return PolicyDecision(allowed=False, reason="explicit_deny")
        if any(
            rule.effect == "allow" and self._matches(rule, permission, resource_tokens)
            for rule in overrides
        ):
            return PolicyDecision(allowed=True, reason="account_allow")
        if any(
            rule.effect == "allow" and self._matches(rule, permission, resource_tokens)
            for rule in role_rules
        ):
            return PolicyDecision(allowed=True, reason="role_allow")
        return PolicyDecision(allowed=False, reason="default_deny")
