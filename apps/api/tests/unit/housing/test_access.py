import hashlib
from datetime import date
from typing import Any, Literal
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.housing.access import HousingAccessService
from app.housing.models import HousingFloor, HousingRoom
from app.housing.schemas import HousingCatalogResponse, HousingOverviewResponse
from app.rbac.policy import PolicyRule, RbacPolicy
from app.rbac.service import RbacDecisionContext


def _access(
    *,
    role_rules: list[PolicyRule],
    overrides: list[PolicyRule],
) -> HousingAccessService:
    access = object.__new__(HousingAccessService)
    access.context = RbacDecisionContext(RbacPolicy(), role_rules, overrides)
    return access


def test_access_revision_is_canonical_and_ignores_non_housing_rules() -> None:
    floor_id = UUID("52000000-0000-4000-8000-000000000001")
    housing_rules = [
        PolicyRule("housing.rooms.manage", "allow", "floor", floor_id),
        PolicyRule("housing.read", "allow", "global"),
    ]
    expected = _access(role_rules=housing_rules, overrides=[]).access_revision

    reordered_with_unrelated_rule = _access(
        role_rules=[
            PolicyRule("students.read", "allow", "global"),
            *reversed(housing_rules),
        ],
        overrides=[PolicyRule("audit.read", "deny", "global")],
    )

    assert reordered_with_unrelated_rule.access_revision == expected
    assert len(expected) == 64
    int(expected, 16)

    mixed_null_and_scoped_ids = _access(
        role_rules=[],
        overrides=[
            PolicyRule("housing.read", "allow", "building"),
            PolicyRule("housing.read", "allow", "building", floor_id),
        ],
    )
    assert len(mixed_null_and_scoped_ids.access_revision) == 64


def test_access_revision_changes_for_rule_source_effect_or_scope() -> None:
    floor_id = UUID("52000000-0000-4000-8000-000000000001")
    base_rule = PolicyRule("housing.read", "allow", "floor", floor_id)
    revisions = {
        _access(role_rules=[base_rule], overrides=[]).access_revision,
        _access(role_rules=[], overrides=[base_rule]).access_revision,
        _access(
            role_rules=[],
            overrides=[PolicyRule("housing.read", "deny", "floor", floor_id)],
        ).access_revision,
        _access(
            role_rules=[],
            overrides=[PolicyRule("housing.read", "allow", "building", floor_id)],
        ).access_revision,
        _access(
            role_rules=[],
            overrides=[PolicyRule("housing.rooms.manage", "allow", "floor", floor_id)],
        ).access_revision,
    }

    assert len(revisions) == 5


def test_access_revision_uses_an_all_string_canonical_representation() -> None:
    revision = _access(
        role_rules=[PolicyRule("housing.read", "allow", "global")],
        overrides=[],
    ).access_revision

    expected = hashlib.sha256(
        b'[["role","housing.read","allow","global",""]]'
    ).hexdigest()
    assert revision == expected


def test_access_revision_response_fields_require_lowercase_sha256_hex() -> None:
    for response_model in (HousingCatalogResponse, HousingOverviewResponse):
        revision_schema = response_model.model_json_schema()["properties"][
            "access_revision"
        ]
        assert revision_schema["pattern"] == "^[0-9a-f]{64}$"


@pytest.mark.parametrize(
    "permission",
    [
        "housing.read",
        "housing.rooms.manage",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "housing.issues.manage",
    ],
)
def test_access_revision_tracks_each_housing_permission(permission: str) -> None:
    baseline = _access(role_rules=[], overrides=[]).access_revision
    changed = _access(
        role_rules=[PolicyRule(permission, "allow", "global")],
        overrides=[],
    ).access_revision

    assert changed != baseline


def test_access_revision_tracks_sensitive_report_permission() -> None:
    baseline = _access(role_rules=[], overrides=[]).access_revision
    changed = _access(
        role_rules=[
            PolicyRule(
                "housing.reports.export_sensitive",
                "allow",
                "global",
            )
        ],
        overrides=[],
    ).access_revision

    assert changed != baseline


@pytest.mark.parametrize("scope_kind", ["global", "building", "floor"])
def test_report_floor_permission_uses_global_building_and_floor_scope(
    scope_kind: Literal["global", "building", "floor"],
) -> None:
    building_id = UUID("52000000-0000-4000-8000-000000000010")
    floor_id = UUID("52000000-0000-4000-8000-000000000001")
    floor = HousingFloor(id=floor_id, building_id=building_id, number=1, name="一层")
    scope_id = {
        "global": None,
        "building": building_id,
        "floor": floor_id,
    }[scope_kind]
    access = _access(
        role_rules=[
            PolicyRule(
                "housing.reports.export_sensitive",
                "allow",
                scope_kind,
                scope_id,
            )
        ],
        overrides=[],
    )

    can_export = getattr(access, "can_export_report_floor", None)
    assert can_export is not None, "can_export_report_floor is missing"
    assert can_export(floor) is True


def test_report_floor_explicit_deny_overrides_global_allow() -> None:
    building_id = UUID("52000000-0000-4000-8000-000000000010")
    floor_id = UUID("52000000-0000-4000-8000-000000000001")
    floor = HousingFloor(id=floor_id, building_id=building_id, number=1, name="一层")
    access = _access(
        role_rules=[
            PolicyRule("housing.reports.export_sensitive", "allow", "global")
        ],
        overrides=[
            PolicyRule(
                "housing.reports.export_sensitive",
                "deny",
                "floor",
                floor_id,
            )
        ],
    )

    assert access.can_export_report_floor(floor) is False


def test_report_floor_sql_criteria_respects_floor_deny_and_building_allow(
    db: Session,
) -> None:
    floors = db.execute(
        select(HousingFloor.id, HousingFloor.building_id).order_by(
            HousingFloor.building_id, HousingFloor.number, HousingFloor.id
        )
    ).all()
    allowed_floor = floors[0]
    denied_floor = next(
        row
        for row in floors
        if row.building_id == allowed_floor.building_id and row.id != allowed_floor.id
    )
    access = _access(
        role_rules=[
            PolicyRule(
                "housing.reports.export_sensitive",
                "allow",
                "building",
                allowed_floor.building_id,
            )
        ],
        overrides=[
            PolicyRule(
                "housing.reports.export_sensitive",
                "deny",
                "floor",
                denied_floor.id,
            )
        ],
    )
    criteria_builder = getattr(access, "report_floor_criteria", None)
    assert criteria_builder is not None, "report_floor_criteria is missing"
    allowed_ids = set(db.scalars(select(HousingFloor.id).where(criteria_builder())))
    assert allowed_floor.id in allowed_ids
    assert denied_floor.id not in allowed_ids


def test_report_floor_account_allow_works_and_unrelated_permission_does_not() -> None:
    building_id = UUID("52000000-0000-4000-8000-000000000010")
    floor_id = UUID("52000000-0000-4000-8000-000000000001")
    floor = HousingFloor(id=floor_id, building_id=building_id, number=1, name="一层")
    account_allowed = _access(
        role_rules=[],
        overrides=[
            PolicyRule(
                "housing.reports.export_sensitive",
                "allow",
                "floor",
                floor_id,
            )
        ],
    )
    unrelated = _access(
        role_rules=[PolicyRule("housing.read", "allow", "global")],
        overrides=[],
    )

    assert account_allowed.can_export_report_floor(floor) is True
    assert unrelated.can_export_report_floor(floor) is False


@pytest.mark.parametrize(
    ("method_name", "permission"),
    [
        ("can_manage_stays", "housing.stays.manage"),
        ("can_manage_transfers", "housing.transfers.manage"),
        ("can_manage_checkouts", "housing.checkouts.manage"),
        ("can_manage_issues", "housing.issues.manage"),
    ],
)
def test_lifecycle_management_uses_room_scope(
    method_name: str,
    permission: str,
) -> None:
    allowed_floor_id = UUID("52000000-0000-4000-8000-000000000001")
    other_floor_id = UUID("52000000-0000-4000-8000-000000000002")
    building_id = UUID("52000000-0000-4000-8000-000000000010")
    access = _access(
        role_rules=[PolicyRule(permission, "allow", "floor", allowed_floor_id)],
        overrides=[],
    )
    allowed_room = HousingRoom(building_id=building_id, floor_id=allowed_floor_id)
    other_room = HousingRoom(building_id=building_id, floor_id=other_floor_id)

    method = getattr(access, method_name, None)
    assert method is not None, f"{method_name} is missing"
    assert method(allowed_room) is True
    assert method(other_room) is False


class _PersonTokenRbac:
    def __init__(self, tokens_by_target: dict[UUID, set[str]]) -> None:
        self.tokens_by_target = tokens_by_target
        self.calls: list[dict[str, Any]] = []

    def person_resource_tokens(
        self,
        *,
        actor_person_id: UUID,
        target_person_id: UUID,
        reference_date: date,
    ) -> set[str]:
        self.calls.append(
            {
                "actor_person_id": actor_person_id,
                "target_person_id": target_person_id,
                "reference_date": reference_date,
            }
        )
        return self.tokens_by_target[target_person_id]


def _person_access(
    *,
    role_rules: list[PolicyRule],
    overrides: list[PolicyRule],
    tokens_by_target: dict[UUID, set[str]],
) -> tuple[HousingAccessService, _PersonTokenRbac]:
    access = _access(role_rules=role_rules, overrides=overrides)
    rbac = _PersonTokenRbac(tokens_by_target)
    access.rbac = rbac  # type: ignore[assignment]
    return access, rbac


def test_can_read_person_reuses_self_and_valid_adult_family_tokens() -> None:
    actor_id = UUID("51000000-0000-4000-8000-000000000001")
    adult_family_id = UUID("51000000-0000-4000-8000-000000000002")
    reference_date = date(2026, 8, 4)
    access, rbac = _person_access(
        role_rules=[
            PolicyRule("housing.read", "allow", "self"),
            PolicyRule("housing.read", "allow", "family"),
        ],
        overrides=[],
        tokens_by_target={
            actor_id: {
                "global",
                f"person:{actor_id}",
                f"self:{actor_id}",
            },
            adult_family_id: {
                "global",
                f"person:{adult_family_id}",
                f"family:{adult_family_id}",
            },
        },
    )

    can_read_person = getattr(access, "can_read_person", None)
    assert can_read_person is not None, "can_read_person is missing"
    assert can_read_person(actor_id, actor_id, reference_date) is True
    assert can_read_person(actor_id, adult_family_id, reference_date) is True
    assert rbac.calls[-1]["reference_date"] == reference_date


def test_can_read_person_rejects_ended_or_expired_family_relationships() -> None:
    actor_id = UUID("51000000-0000-4000-8000-000000000001")
    ended_id = UUID("51000000-0000-4000-8000-000000000002")
    expired_id = UUID("51000000-0000-4000-8000-000000000003")
    reference_date = date(2026, 8, 4)
    access, _ = _person_access(
        role_rules=[PolicyRule("housing.read", "allow", "family")],
        overrides=[],
        tokens_by_target={
            ended_id: {"global", f"person:{ended_id}"},
            expired_id: {"global", f"person:{expired_id}"},
        },
    )

    can_read_person = getattr(access, "can_read_person", None)
    assert can_read_person is not None, "can_read_person is missing"
    assert can_read_person(actor_id, ended_id, reference_date) is False
    assert can_read_person(actor_id, expired_id, reference_date) is False


def test_can_read_person_honors_explicit_deny_before_family_allow() -> None:
    actor_id = UUID("51000000-0000-4000-8000-000000000001")
    target_id = UUID("51000000-0000-4000-8000-000000000002")
    reference_date = date(2026, 8, 4)
    access, _ = _person_access(
        role_rules=[PolicyRule("housing.read", "allow", "family")],
        overrides=[PolicyRule("housing.read", "deny", "person", target_id)],
        tokens_by_target={
            target_id: {
                "global",
                f"person:{target_id}",
                f"family:{target_id}",
            }
        },
    )

    can_read_person = getattr(access, "can_read_person", None)
    assert can_read_person is not None, "can_read_person is missing"
    assert can_read_person(actor_id, target_id, reference_date) is False


def test_room_sql_permissions_match_policy_precedence_and_intersection(
    db: Session,
) -> None:
    room_rows = db.execute(
        select(
            HousingRoom.id,
            HousingRoom.building_id,
            HousingRoom.floor_id,
        ).order_by(HousingRoom.building_id, HousingRoom.floor_id, HousingRoom.id)
    ).all()
    allowed_room = room_rows[0]
    denied_room = next(
        room for room in room_rows if room.building_id != allowed_room.building_id
    )
    access = _access(
        role_rules=[PolicyRule("housing.read", "allow", "global")],
        overrides=[
            PolicyRule(
                "housing.transfers.manage",
                "allow",
                "building",
                allowed_room.building_id,
            ),
            PolicyRule(
                "housing.transfers.manage",
                "allow",
                "floor",
                denied_room.floor_id,
            ),
            PolicyRule(
                "housing.read",
                "allow",
                "building",
                denied_room.building_id,
            ),
            PolicyRule(
                "housing.read",
                "deny",
                "floor",
                denied_room.floor_id,
            ),
        ],
    )

    criteria_builder = getattr(access, "room_permissions_criteria", None)
    assert criteria_builder is not None, "room_permissions_criteria is missing"
    allowed_ids = set(
        db.scalars(
            select(HousingRoom.id).where(
                criteria_builder(
                    "housing.read",
                    "housing.transfers.manage",
                )
            )
        )
    )

    assert allowed_room.id in allowed_ids
    assert denied_room.id not in allowed_ids
    assert all(
        room.building_id == allowed_room.building_id
        for room in room_rows
        if room.id in allowed_ids
    )
