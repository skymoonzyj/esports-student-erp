"""Managed identity derivation: no implicit privilege and canonical team intent."""

import importlib
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.security.encryption import LookupDigest


def contracts():
    name = "app.personnel_intake.identity_selection"
    assert importlib.util.find_spec(name) is not None, (
        "managed identity contract is not implemented"
    )
    return importlib.import_module(name)


def test_student_coach_has_two_business_profiles_contract():
    api = contracts()
    value = api.resolve_managed_identity(
        api.ManagedIdentityInput(primary_identity="student", concurrent_identities=["coach"])
    )
    assert value.kind == "student"
    assert value.staff_type == "coach"
    assert value.activation_role_keys == ["coach"]


@pytest.mark.parametrize(
    ("primary", "concurrent", "kind", "staff", "extra"),
    [
        ("student", ["team_member"], "both", None, []),
        ("team_member", [], "player", None, []),
        ("coach", ["student", "assistant_coach"], "student", "coach", ["assistant_coach", "coach"]),
        ("registered_user", [], "staff", "other", []),
        ("guardian", [], "staff", "other", ["guardian"]),
        ("property", [], "staff", "other", ["property"]),
    ],
)
def test_business_kind_and_minimum_roles_are_independent(primary, concurrent, kind, staff, extra):
    api = contracts()
    value = api.resolve_managed_identity(
        api.ManagedIdentityInput(primary_identity=primary, concurrent_identities=concurrent)
    )
    assert (value.kind, value.staff_type, value.activation_role_keys) == (kind, staff, extra)


@pytest.mark.parametrize(
    "key",
    [
        "student",
        "team_member",
        "team_manager",
        "coach",
        "assistant_coach",
        "administration",
        "customer_service",
        "life_teacher",
        "property",
        "finance",
        "guardian",
        "staff",
        "registered_user",
    ],
)
def test_every_non_admin_identity_remains_selectable(key):
    api = contracts()
    assert api.resolve_managed_identity(
        api.ManagedIdentityInput(primary_identity=key, concurrent_identities=[])
    ).selected_role_keys == [key]


@pytest.mark.parametrize(
    "payload",
    [
        {"primary_identity": "admin"},
        {"primary_identity": "root"},
        {"primary_identity": "student", "concurrent_identities": ["coach", "coach"]},
        {"primary_identity": "student", "concurrent_identities": ["student"]},
        {"primary_identity": "student", "concurrent_identities": ["coach", "assistant_coach"]},
        {"primary_identity": "student", "primary_staff_identity": "coach"},
        {
            "primary_identity": "coach",
            "concurrent_identities": ["assistant_coach"],
            "primary_staff_identity": "assistant_coach",
        },
        {
            "primary_identity": "student",
            "concurrent_identities": ["coach"],
            "primary_staff_identity": "student",
        },
    ],
)
def test_unknown_duplicate_or_ambiguous_identity_is_rejected(payload):
    api = contracts()
    with pytest.raises((ValidationError, ValueError)):
        api.resolve_managed_identity(api.ManagedIdentityInput(**payload))


def test_multiple_concurrent_staff_require_explicit_selected_primary():
    api = contracts()
    value = api.resolve_managed_identity(
        api.ManagedIdentityInput(
            primary_identity="student",
            concurrent_identities=["coach", "assistant_coach"],
            primary_staff_identity="assistant_coach",
        )
    )
    assert value.staff_type == "assistant_coach"


def test_team_group_order_and_empty_groups_do_not_change_keyed_fingerprint():
    api = contracts()
    identity = api.ManagedIdentityInput(
        primary_identity="coach", concurrent_identities=["team_manager"]
    )
    first, second = UUID(int=1), UUID(int=2)
    left = [
        api.StaffTeamSelection(staff_role="coach", team_ids=[second, first]),
        api.StaffTeamSelection(staff_role="manager", team_ids=[]),
    ]
    right = [api.StaffTeamSelection(staff_role="coach", team_ids=[first, second])]
    normalized = api.normalize_staff_team_selections(identity, left)
    assert [item.model_dump(mode="json") for item in normalized] == [
        {"staff_role": "coach", "team_ids": [str(first), str(second)]}
    ]
    lookup = LookupDigest(bytes(range(32)))
    digest = api.identity_selection_fingerprint(lookup, identity, left)
    assert digest == lookup.digest_domain(
        "intake-identity-selection", api.canonical_identity_selection(identity, right)
    )
    assert digest == api.identity_selection_fingerprint(lookup, identity, right)
    assert digest != api.identity_selection_fingerprint(
        LookupDigest(bytes(reversed(range(32)))), identity, right
    )
    assert digest != lookup.digest_domain(
        "person-team-staff-authorization", api.canonical_identity_selection(identity, right)
    )


@pytest.mark.parametrize(
    "groups",
    [
        [{"staff_role": "coach", "team_ids": [str(UUID(int=1)), str(UUID(int=1))]}],
        [{"staff_role": "coach", "team_ids": []}, {"staff_role": "coach", "team_ids": []}],
        [{"staff_role": "manager", "team_ids": [str(UUID(int=1))]}],
    ],
)
def test_duplicate_or_unselected_staff_team_groups_are_rejected(groups):
    api = contracts()
    with pytest.raises((ValidationError, ValueError)):
        api.normalize_staff_team_selections(
            api.ManagedIdentityInput(primary_identity="coach"),
            [api.StaffTeamSelection(**item) for item in groups],
        )


def authorization_evidence(**changes):
    module = importlib.import_module("app.team_management.person_staff_schemas")
    assert hasattr(module, "StaffAuthorizationEvidence"), (
        "staff authorization evidence contract is not implemented"
    )
    values = {
        "position_id": UUID(int=1),
        "person_id": UUID(int=2),
        "team_id": UUID(int=3),
        "origin_approval_id": UUID(int=4),
        "staff_role": "coach",
        "version": 1,
        "authorizer_user_id": UUID(int=5),
        "authorized_role_key": "coach",
        "role_plan_id": UUID(int=6),
        "operation_id": UUID(int=7),
        **changes,
    }
    return module, module.StaffAuthorizationEvidence(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"person_id": UUID(int=20)},
        {"team_id": UUID(int=30)},
        {"staff_role": "manager", "authorized_role_key": "team_manager"},
        {"authorizer_user_id": UUID(int=50)},
        {"origin_approval_id": UUID(int=40)},
        {"version": 2},
        {"position_id": UUID(int=10)},
        {"role_plan_id": None},
        {"operation_id": UUID(int=70)},
    ],
)
def test_staff_proof_binds_every_authorization_fact_and_provenance(changes):
    module, original = authorization_evidence()
    _, changed = authorization_evidence(**changes)
    lookup = LookupDigest(bytes(range(32)))
    assert module.staff_authorization_digest(lookup, original) != module.staff_authorization_digest(
        lookup, changed
    )


def test_staff_proof_uses_keyed_separate_domain():
    module, evidence = authorization_evidence()
    lookup = LookupDigest(bytes(range(32)))
    digest = module.staff_authorization_digest(lookup, evidence)
    assert digest == lookup.digest_domain(
        "person-team-staff-authorization", module.canonical_staff_authorization(evidence)
    )
    assert digest != module.staff_authorization_digest(
        LookupDigest(bytes(reversed(range(32)))), evidence
    )
    assert digest != lookup.digest_domain(
        "intake-identity-selection", module.canonical_staff_authorization(evidence)
    )


@pytest.mark.parametrize(
    "changes", [{"staff_role": "manager"}, {"version": 0}, {"authorized_role_key": "admin"}]
)
def test_staff_proof_rejects_wrong_role_or_version(changes):
    with pytest.raises(ValidationError):
        authorization_evidence(**changes)
