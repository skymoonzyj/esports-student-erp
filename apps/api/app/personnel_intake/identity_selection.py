"""Management-only identity intent; never account or team authorization by itself."""

from __future__ import annotations

import json
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.security.encryption import LookupDigest

IdentityKey = Literal[
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
]
StaffRole = Literal["manager", "coach", "assistant_coach"]
STAFF_IDENTITIES = frozenset(
    {
        "team_manager",
        "coach",
        "assistant_coach",
        "administration",
        "customer_service",
        "life_teacher",
        "property",
        "finance",
        "staff",
    }
)
STAFF_ROLE_IDENTITIES: dict[StaffRole, str] = {
    "manager": "team_manager",
    "coach": "coach",
    "assistant_coach": "assistant_coach",
}


class ManagedIdentityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary_identity: IdentityKey
    concurrent_identities: list[IdentityKey] = Field(default_factory=list)
    primary_staff_identity: IdentityKey | None = None

    @model_validator(mode="after")
    def consistent_selection(self) -> Self:
        selected = [self.primary_identity, *self.concurrent_identities]
        if len(set(selected)) != len(selected):
            raise ValueError("主要身份和兼任身份不得重复")
        staff = set(selected) & STAFF_IDENTITIES
        chosen = self.primary_staff_identity
        if chosen is not None and chosen not in staff:
            raise ValueError("主要工作人员身份必须属于所选工作人员岗位")
        if self.primary_identity in STAFF_IDENTITIES:
            if chosen is not None and chosen != self.primary_identity:
                raise ValueError("工作人员主要身份必须作为主要工作人员分类")
        elif len(staff) > 1 and chosen is None:
            raise ValueError("兼任多个工作人员岗位时须明确主要工作人员身份")
        return self


class StaffTeamSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    staff_role: StaffRole
    team_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_teams(self) -> Self:
        if len(set(self.team_ids)) != len(self.team_ids):
            raise ValueError("同一岗位的队伍不得重复")
        return self


class ResolvedIntakeIdentity(BaseModel):
    selection: ManagedIdentityInput
    kind: Literal["student", "player", "both", "staff"]
    staff_type: str | None
    selected_role_keys: list[str]
    activation_role_keys: list[str]


def resolve_managed_identity(value: ManagedIdentityInput) -> ResolvedIntakeIdentity:
    from app.personnel_intake.activation_roles import activation_role_keys

    # Revalidate even model_construct/mutated input at this security boundary.
    value = ManagedIdentityInput.model_validate(value.model_dump())
    selected = {value.primary_identity, *value.concurrent_identities}
    student, player = "student" in selected, "team_member" in selected
    kind: Literal["student", "player", "both", "staff"] = (
        "both" if student and player else "student" if student else "player" if player else "staff"
    )
    staff = selected & STAFF_IDENTITIES
    primary = (
        value.primary_identity
        if value.primary_identity in staff
        else (value.primary_staff_identity or (next(iter(staff)) if len(staff) == 1 else None))
    )
    staff_type = None
    if primary is not None:
        staff_type = primary if primary not in {"property", "finance", "staff"} else "other"
    elif kind == "staff":
        staff_type = "other"
    return ResolvedIntakeIdentity(
        selection=value.model_copy(
            update={"concurrent_identities": sorted(value.concurrent_identities)}
        ),
        kind=kind,
        staff_type=staff_type,
        selected_role_keys=sorted(selected),
        activation_role_keys=sorted(selected - activation_role_keys(kind)),
    )


def normalize_staff_team_selections(
    identity: ManagedIdentityInput,
    groups: list[StaffTeamSelection],
) -> list[StaffTeamSelection]:
    selected = resolve_managed_identity(identity).selected_role_keys
    seen: set[StaffRole] = set()
    result = []
    for raw in groups:
        group = StaffTeamSelection.model_validate(raw.model_dump())
        if group.staff_role in seen:
            raise ValueError("每个工作人员岗位只能提供一组队伍")
        seen.add(group.staff_role)
        if STAFF_ROLE_IDENTITIES[group.staff_role] not in selected:
            raise ValueError("只能为已选工作人员身份分配队伍")
        if group.team_ids:
            result.append(group.model_copy(update={"team_ids": sorted(group.team_ids)}))
    return sorted(result, key=lambda item: item.staff_role)


def canonical_identity_selection(
    identity: ManagedIdentityInput,
    staff_teams: list[StaffTeamSelection],
) -> str:
    resolved = resolve_managed_identity(identity)
    payload = {
        "identity_selection": resolved.selection.model_dump(mode="json"),
        "staff_teams": [
            group.model_dump(mode="json")
            for group in normalize_staff_team_selections(identity, staff_teams)
        ],
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def identity_selection_fingerprint(
    lookup: LookupDigest,
    identity: ManagedIdentityInput,
    staff_teams: list[StaffTeamSelection],
) -> str:
    return lookup.digest_domain(
        "intake-identity-selection",
        canonical_identity_selection(identity, staff_teams),
    )
