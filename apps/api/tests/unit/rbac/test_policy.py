from uuid import UUID

import pytest

from app.rbac.policy import PolicyRule, RbacPolicy


def allow(permission: str, scope: str) -> PolicyRule:
    scope_kind, separator, raw_id = scope.partition(":")
    return PolicyRule(
        permission,
        "allow",
        scope_kind,  # type: ignore[arg-type]
        UUID(raw_id) if separator else None,
    )


def deny(permission: str, scope: str) -> PolicyRule:
    scope_kind, separator, raw_id = scope.partition(":")
    return PolicyRule(
        permission,
        "deny",
        scope_kind,  # type: ignore[arg-type]
        UUID(raw_id) if separator else None,
    )


def test_explicit_deny_beats_role_and_user_allow() -> None:
    policy = RbacPolicy()
    decision = policy.decide(
        permission="people.identity.reveal",
        resource_tokens={"global", "person:00000000-0000-0000-0000-000000000123"},
        role_rules=[allow("people.identity.reveal", "global")],
        overrides=[
            allow(
                "people.identity.reveal",
                "person:00000000-0000-0000-0000-000000000123",
            ),
            deny(
                "people.identity.reveal",
                "person:00000000-0000-0000-0000-000000000123",
            ),
        ],
    )

    assert decision.allowed is False
    assert decision.reason == "explicit_deny"


def test_role_allows_union_but_scope_must_match() -> None:
    policy = RbacPolicy()

    assert policy.decide(
        "people.read",
        {"family:00000000-0000-0000-0000-000000000001"},
        [allow("people.read", "family")],
        [],
    ).allowed
    assert not policy.decide(
        "people.read",
        {"person:00000000-0000-0000-0000-000000000002"},
        [allow("people.read", "family")],
        [],
    ).allowed


def test_account_allow_beats_default_deny() -> None:
    decision = RbacPolicy().decide(
        "people.read",
        {"person:00000000-0000-0000-0000-000000000002"},
        [],
        [allow("people.read", "person:00000000-0000-0000-0000-000000000002")],
    )

    assert decision.allowed is True
    assert decision.reason == "account_allow"


def test_default_is_deny_and_permission_must_match() -> None:
    policy = RbacPolicy()

    assert policy.decide("people.read", {"global"}, [], []).reason == "default_deny"
    assert not policy.decide(
        "people.read",
        {"global"},
        [allow("people.update", "global")],
        [],
    ).allowed


def test_specific_scope_id_must_match_exact_resource() -> None:
    policy = RbacPolicy()
    rule = allow("people.read", "person:00000000-0000-0000-0000-000000000001")

    assert policy.decide(
        "people.read",
        {"person:00000000-0000-0000-0000-000000000001"},
        [],
        [rule],
    ).allowed
    assert not policy.decide(
        "people.read",
        {"person:00000000-0000-0000-0000-000000000002"},
        [],
        [rule],
    ).allowed


def test_generic_assigned_class_rule_requires_the_membership_token() -> None:
    rule = allow("teaching.read", "assigned_class")
    specific_token = {
        "assigned_class:00000000-0000-0000-0000-000000000001",
    }

    assert (
        not RbacPolicy()
        .decide(
            "teaching.read",
            specific_token,
            [rule],
            [],
        )
        .allowed
    )
    assert (
        RbacPolicy()
        .decide(
            "teaching.read",
            {"assigned_class", *specific_token},
            [rule],
            [],
        )
        .allowed
    )


@pytest.mark.parametrize("scope_kind", ["cohort", "project", "enrollment"])
def test_evaluation_object_scope_ids_match_exactly(scope_kind: str) -> None:
    matching_id = "00000000-0000-4000-8000-000000000001"
    other_id = "00000000-0000-4000-8000-000000000002"
    rule = allow("evaluation_media.upload", f"{scope_kind}:{matching_id}")

    assert (
        RbacPolicy()
        .decide(
            "evaluation_media.upload",
            {f"{scope_kind}:{matching_id}"},
            [],
            [rule],
        )
        .allowed
    )
    assert (
        not RbacPolicy()
        .decide(
            "evaluation_media.upload",
            {f"{scope_kind}:{other_id}"},
            [],
            [rule],
        )
        .allowed
    )


def test_building_allow_and_floor_deny_follow_deny_first_hierarchy() -> None:
    building_id = UUID("52000000-0000-4000-8000-000000000010")
    floor_id = UUID("52000000-0000-4000-8000-000000000020")
    tokens = {"global", f"building:{building_id}", f"floor:{floor_id}"}
    policy = RbacPolicy()

    allowed = policy.decide(
        "housing.read",
        tokens,
        [],
        [allow("housing.read", f"building:{building_id}")],
    )
    denied = policy.decide(
        "housing.read",
        tokens,
        [],
        [
            allow("housing.read", f"building:{building_id}"),
            deny("housing.read", f"floor:{floor_id}"),
        ],
    )

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == "explicit_deny"
