from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class RoleResponse(BaseModel):
    id: UUID
    key: str
    name: str
    permissions: list[RolePermissionResponse] = Field(default_factory=list)


class RolePermissionResponse(BaseModel):
    id: UUID
    key: str
    name: str
    sensitive: bool
    scope_kind: Literal[
        "global",
        "self",
        "family",
        "person",
        "assigned_class",
        "assigned_team",
        "building",
        "floor",
    ]


class PermissionResponse(BaseModel):
    id: UUID
    key: str
    name: str
    sensitive: bool


class RoleAssignmentRequest(BaseModel):
    role_id: UUID


class JobRoleChangeRequest(BaseModel):
    from_role_id: UUID | None
    to_role_id: UUID
    keep_concurrent_roles: Annotated[bool, Field(strict=True)]
    expected_role_ids: list[UUID]

    @model_validator(mode="after")
    def validate_snapshot(self) -> JobRoleChangeRequest:
        if len(self.expected_role_ids) != len(set(self.expected_role_ids)):
            raise ValueError("当前角色快照不能包含重复角色")
        return self


class JobRoleSummary(BaseModel):
    id: UUID
    key: str
    name: str


class JobRoleChangeResponse(BaseModel):
    account_id: UUID
    added_role_ids: list[UUID]
    removed_role_ids: list[UUID]
    retained_role_ids: list[UUID]
    roles: list[JobRoleSummary]


class PermissionOverrideCreate(BaseModel):
    permission_id: UUID
    effect: Literal["allow", "deny"]
    scope_kind: Literal[
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
    scope_id: UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    grant_reason: Annotated[str | None, Field(max_length=200)] = None

    @model_validator(mode="after")
    def validate_scope_id(self) -> PermissionOverrideCreate:
        object_scope = self.scope_kind in {
            "person",
            "assigned_class",
            "assigned_team",
            "cohort",
            "project",
            "enrollment",
            "building",
            "floor",
        }
        if object_scope != (self.scope_id is not None):
            raise ValueError("scope_id must be set exactly for object-specific scopes")
        for field_name, value in (
            ("valid_from", self.valid_from),
            ("valid_until", self.valid_until),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.valid_until is not None and (
            self.valid_from is None or self.valid_until <= self.valid_from
        ):
            raise ValueError("valid_until must be later than valid_from")
        if self.grant_reason is not None and not self.grant_reason.strip():
            raise ValueError("grant_reason must not be blank")
        return self


class PermissionOverrideResponse(PermissionOverrideCreate):
    id: UUID
    user_id: UUID
    scope_label: str | None = None


class EffectivePermissionResponse(BaseModel):
    id: UUID
    key: str
    name: str
    sensitive: bool
    scope_kinds: list[
        Literal[
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
    ]
    sources: list[Literal["role", "account_override"]]


class PermissionPreviewResponse(BaseModel):
    allowed: bool
    reason: str


class AccountAccessResponse(BaseModel):
    account_id: UUID
    roles: list[RoleResponse]
    permission_overrides: list[PermissionOverrideResponse]
    effective_permissions: list[EffectivePermissionResponse] = Field(default_factory=list)


class PermissionAccountDirectoryItem(BaseModel):
    id: UUID
    full_name: str
    status: Literal["active", "disabled"]


class PermissionPersonDirectoryItem(BaseModel):
    id: UUID
    full_name: str


class PermissionBuildingDirectoryItem(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    label: str


class PermissionBuildingDirectoryPage(BaseModel):
    items: list[PermissionBuildingDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionFloorDirectoryItem(BaseModel):
    id: UUID
    building_id: UUID
    building_name: str
    number: int
    name: str
    building_is_active: bool
    is_active: bool
    label: str


class PermissionFloorDirectoryPage(BaseModel):
    items: list[PermissionFloorDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionClassDirectoryItem(BaseModel):
    id: UUID
    class_name: str
    cohort_name: str
    project_name: str
    label: str
    is_active: bool


class PermissionClassDirectoryPage(BaseModel):
    items: list[PermissionClassDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionCohortDirectoryItem(BaseModel):
    id: UUID
    name: str
    status: Literal["draft", "open", "upcoming", "active", "completed", "archived"]
    label: str


class PermissionCohortDirectoryPage(BaseModel):
    items: list[PermissionCohortDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionProjectDirectoryItem(BaseModel):
    id: UUID
    name: str
    is_active: bool
    label: str


class PermissionProjectDirectoryPage(BaseModel):
    items: list[PermissionProjectDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionTeamDirectoryItem(BaseModel):
    id: UUID
    name: str
    project_id: UUID
    project_name: str
    is_active: bool
    label: str


class PermissionTeamDirectoryPage(BaseModel):
    items: list[PermissionTeamDirectoryItem]
    page: int
    page_size: int
    total: int


class PermissionEnrollmentDirectoryItem(BaseModel):
    id: UUID
    student_name: str
    cohort_name: str
    class_name: str
    project_name: str
    status: Literal["active", "cancelled", "completed"]
    label: str


class PermissionEnrollmentDirectoryPage(BaseModel):
    items: list[PermissionEnrollmentDirectoryItem]
    page: int
    page_size: int
    total: int
