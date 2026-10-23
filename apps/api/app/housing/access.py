from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, not_, or_, select, true
from sqlalchemy.orm import Session

from app.housing.models import HousingBuilding, HousingFloor, HousingRoom
from app.rbac.models import Permission, Role, RolePermission, UserRole
from app.rbac.policy import PolicyRule, ScopeKind
from app.rbac.service import RbacDecisionContext, RbacService

BuildingAction = Literal["edit", "delete", "create_floor"]
FloorAction = Literal["edit", "delete", "create_room"]
_HOUSING_PERMISSIONS = frozenset(
    {
        "housing.read",
        "housing.rooms.manage",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "housing.issues.manage",
        "housing.reports.export_sensitive",
    }
)
REPORT_PERMISSION = "housing.reports.export_sensitive"


class HousingAccessService:
    def __init__(
        self,
        db: Session,
        account_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self.db = db
        self.rbac = RbacService(db)
        self.context: RbacDecisionContext = self.rbac.decision_context(
            account_id,
            occurred_at=occurred_at,
        )
        self.roles = frozenset(
            self.db.scalars(
                select(Role.key)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == account_id)
            )
        )
        # Keep the source of each role grant long enough to discard precisely
        # the retired coach/global grant, including mixed-role accounts.
        role_rules = [
            PolicyRule(key, "allow", cast(ScopeKind, scope))
            for role, key, scope in self.db.execute(
                select(Role.key, Permission.key, RolePermission.scope_kind)
                .join(UserRole, UserRole.role_id == Role.id)
                .join(RolePermission, RolePermission.role_id == Role.id)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(UserRole.user_id == account_id)
            )
            if (role, key, scope) != ("coach", "housing.read", "global")
        ]
        self.context = RbacDecisionContext(self.context.policy, role_rules, self.context.overrides)

    @property
    def owner_only(self) -> bool:
        return bool(self.roles & {"coach", "team_manager"}) and not bool(
            self.roles & {"administration", "admin", "super_admin"}
        )

    @staticmethod
    def building_tokens(building_id: UUID) -> set[str]:
        return {"global", f"building:{building_id}"}

    @staticmethod
    def floor_tokens(building_id: UUID, floor_id: UUID) -> set[str]:
        return {
            "global",
            f"building:{building_id}",
            f"floor:{floor_id}",
        }

    @classmethod
    def room_tokens(cls, room: HousingRoom) -> set[str]:
        return cls.floor_tokens(room.building_id, room.floor_id)

    def can(self, permission: str, resource_tokens: set[str]) -> bool:
        if self.owner_only and permission in _HOUSING_PERMISSIONS:
            if permission != "housing.read":
                return False
            # Own/family authority is independent of owner-only summaries.
            # Preserve every deny, but never borrow a global/person allow.
            return self.context.policy.decide(
                permission,
                resource_tokens,
                [r for r in self.context.role_rules if r.scope_kind in {"self", "family"}],
                [
                    r
                    for r in self.context.overrides
                    if r.effect == "deny" or r.scope_kind in {"self", "family"}
                ],
            ).allowed
        return self.context.decide(permission, resource_tokens).allowed

    @staticmethod
    def _any_sql(conditions: list[ColumnElement[bool]]) -> ColumnElement[bool]:
        return or_(*conditions) if conditions else false()

    @staticmethod
    def _room_scope_criteria(rule: PolicyRule) -> ColumnElement[bool]:
        if rule.scope_kind == "global":
            return true()
        if rule.scope_kind == "building":
            if rule.scope_id is None:
                return HousingRoom.building_id.is_not(None)
            return HousingRoom.building_id == rule.scope_id
        if rule.scope_kind == "floor":
            if rule.scope_id is None:
                return HousingRoom.floor_id.is_not(None)
            return HousingRoom.floor_id == rule.scope_id
        return false()

    def _room_rules_criteria(
        self,
        rules: Iterable[PolicyRule],
    ) -> ColumnElement[bool]:
        return self._any_sql([self._room_scope_criteria(rule) for rule in rules])

    @staticmethod
    def _floor_scope_criteria(rule: PolicyRule) -> ColumnElement[bool]:
        if rule.scope_kind == "global":
            return true()
        if rule.scope_kind == "building":
            if rule.scope_id is None:
                return HousingFloor.building_id.is_not(None)
            return HousingFloor.building_id == rule.scope_id
        if rule.scope_kind == "floor":
            if rule.scope_id is None:
                return HousingFloor.id.is_not(None)
            return HousingFloor.id == rule.scope_id
        return false()

    def _floor_rules_criteria(
        self,
        rules: Iterable[PolicyRule],
    ) -> ColumnElement[bool]:
        return self._any_sql([self._floor_scope_criteria(rule) for rule in rules])

    def room_permission_criteria(self, permission: str) -> ColumnElement[bool]:
        if self.owner_only and permission in _HOUSING_PERMISSIONS:
            return false()
        matching_overrides = [
            rule for rule in self.context.overrides if rule.permission == permission
        ]
        explicit_deny = self._room_rules_criteria(
            rule for rule in matching_overrides if rule.effect == "deny"
        )
        account_allow = self._room_rules_criteria(
            rule for rule in matching_overrides if rule.effect == "allow"
        )
        role_allow = self._room_rules_criteria(
            rule
            for rule in self.context.role_rules
            if rule.permission == permission and rule.effect == "allow"
        )
        return and_(not_(explicit_deny), or_(account_allow, role_allow))

    def room_permissions_criteria(
        self,
        *permissions: str,
    ) -> ColumnElement[bool]:
        return and_(
            *(self.room_permission_criteria(permission) for permission in permissions)
        )

    def floor_permission_criteria(self, permission: str) -> ColumnElement[bool]:
        if self.owner_only and permission in _HOUSING_PERMISSIONS:
            return false()
        matching_overrides = [
            rule for rule in self.context.overrides if rule.permission == permission
        ]
        explicit_deny = self._floor_rules_criteria(
            rule for rule in matching_overrides if rule.effect == "deny"
        )
        account_allow = self._floor_rules_criteria(
            rule for rule in matching_overrides if rule.effect == "allow"
        )
        role_allow = self._floor_rules_criteria(
            rule
            for rule in self.context.role_rules
            if rule.permission == permission and rule.effect == "allow"
        )
        return and_(not_(explicit_deny), or_(account_allow, role_allow))

    def floor_permissions_criteria(
        self,
        *permissions: str,
    ) -> ColumnElement[bool]:
        return and_(
            *(self.floor_permission_criteria(permission) for permission in permissions)
        )

    @property
    def access_revision(self) -> str:
        rules = [
            (
                source,
                rule.permission,
                rule.effect,
                rule.scope_kind,
                str(rule.scope_id) if rule.scope_id is not None else "",
            )
            for source, source_rules in (
                ("role", self.context.role_rules),
                ("override", self.context.overrides),
            )
            for rule in source_rules
            if rule.permission in _HOUSING_PERMISSIONS
        ]
        canonical = json.dumps(
            sorted(rules)
            + ([("boundary", "owner_only", "deny", "global", "")] if self.owner_only else []),
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def can_read_floor(self, floor: HousingFloor) -> bool:
        return self.can(
            "housing.read",
            self.floor_tokens(floor.building_id, floor.id),
        )

    def can_read_room(self, room: HousingRoom) -> bool:
        return self.can("housing.read", self.room_tokens(room))

    def can_manage_building(self, building: HousingBuilding) -> bool:
        return self.can(
            "housing.rooms.manage",
            self.building_tokens(building.id),
        )

    def can_manage_floor(self, floor: HousingFloor) -> bool:
        return self.can(
            "housing.rooms.manage",
            self.floor_tokens(floor.building_id, floor.id),
        )

    def can_manage_room(self, room: HousingRoom) -> bool:
        return self.can("housing.rooms.manage", self.room_tokens(room))

    def can_manage_stays(self, room: HousingRoom) -> bool:
        return self.can("housing.stays.manage", self.room_tokens(room))

    def can_manage_transfers(self, room: HousingRoom) -> bool:
        return self.can("housing.transfers.manage", self.room_tokens(room))

    def can_manage_checkouts(self, room: HousingRoom) -> bool:
        return self.can("housing.checkouts.manage", self.room_tokens(room))

    def can_manage_issues(self, room: HousingRoom) -> bool:
        return self.can("housing.issues.manage", self.room_tokens(room))

    def can_export_report_floor(self, floor: HousingFloor) -> bool:
        return self.can(REPORT_PERMISSION, self.floor_tokens(floor.building_id, floor.id))

    def report_floor_criteria(self) -> ColumnElement[bool]:
        return self.floor_permission_criteria(REPORT_PERMISSION)

    def can_read_person(
        self,
        actor_person_id: UUID,
        target_person_id: UUID,
        reference_date: date,
    ) -> bool:
        tokens = self.rbac.person_resource_tokens(
            actor_person_id=actor_person_id,
            target_person_id=target_person_id,
            reference_date=reference_date,
        )
        return self.can("housing.read", tokens)

    def can_read_building(
        self,
        building: HousingBuilding,
        floors: Iterable[HousingFloor] | None = None,
    ) -> bool:
        if self.can("housing.read", self.building_tokens(building.id)):
            return True
        candidate_floors = floors
        if candidate_floors is None:
            candidate_floors = self.db.scalars(
                select(HousingFloor)
                .where(HousingFloor.building_id == building.id)
                .order_by(
                    HousingFloor.display_order,
                    HousingFloor.number,
                    HousingFloor.id,
                )
            )
        return any(self.can_read_floor(floor) for floor in candidate_floors)

    def filter_buildings(
        self,
        buildings: Sequence[HousingBuilding],
        floors: Sequence[HousingFloor] | None = None,
    ) -> list[HousingBuilding]:
        floors_by_building: dict[UUID, list[HousingFloor]] = {}
        if floors is not None:
            for floor in floors:
                floors_by_building.setdefault(floor.building_id, []).append(floor)
        return [
            building
            for building in buildings
            if self.can_read_building(
                building,
                floors_by_building.get(building.id, ())
                if floors is not None
                else None,
            )
        ]

    def filter_floors(
        self,
        floors: Sequence[HousingFloor],
    ) -> list[HousingFloor]:
        return [floor for floor in floors if self.can_read_floor(floor)]

    def filter_rooms(
        self,
        rooms: Sequence[HousingRoom],
    ) -> list[HousingRoom]:
        return [room for room in rooms if self.can_read_room(room)]

    def building_actions(self, building: HousingBuilding) -> list[BuildingAction]:
        if not self.can_manage_building(building):
            return []
        return ["edit", "delete", "create_floor"]

    def floor_actions(self, floor: HousingFloor) -> list[FloorAction]:
        if not self.can_manage_floor(floor):
            return []
        return ["edit", "delete", "create_room"]

    @property
    def can_create_building(self) -> bool:
        return self.can("housing.rooms.manage", {"global"})
