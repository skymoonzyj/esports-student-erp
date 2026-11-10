from importlib.util import find_spec

import pytest


@pytest.mark.parametrize(
    "roles,deny_submit,deny_direct,expected",
    [
        (("property",), False, False, True),
        (("property", "coach"), False, False, True),
        (("administration",), False, False, False),
        (("super_admin",), False, False, False),
        (("property", "administration"), False, False, False),
        (("property", "super_admin"), False, False, False),
        (("property", "admin"), False, False, False),
        (("property",), True, False, False),
        (("property", "administration"), False, True, False),
    ],
)
def test_new_property_request_capability_is_separate_from_legacy_submission_lifecycle(
    db, roles, deny_submit, deny_direct, expected
):
    from app.housing.access import HousingAccessService
    from app.housing.property_service import HousingPropertyService
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.invited_housing_helpers import NOW, account, room

    actor, target = account(db, roles[0]), room(db)
    for role in roles[1:]:
        db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    # No visible rooms may qualify, including unrelated seeded rooms.
    deny_read = UserPermissionOverride(
        user_id=actor.id,
        permission_id=PERMISSIONS["housing.read"].id,
        effect="deny",
        scope_kind="global",
    )
    db.add(deny_read)
    db.flush()
    access = HousingAccessService(db, actor.id, NOW)
    assert HousingPropertyService(db).capabilities(access).get("can_create_request") is False
    db.delete(deny_read)
    if deny_submit:
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS["housing.changes.submit"].id,
                effect="deny",
                scope_kind="floor",
                scope_id=target.floor_id,
            )
        )
    if deny_direct:
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS["housing.rooms.manage"].id,
                effect="deny",
                scope_kind="floor",
                scope_id=target.floor_id,
            )
        )
    db.flush()
    access = HousingAccessService(db, actor.id, NOW)
    result = HousingPropertyService(db).capabilities(access, room_id=target.id)
    assert result.get("can_create_request") is expected
    assert access.can_create_property_request(target) is expected
    if not deny_submit:
        assert HousingPropertyService(db).capabilities(access)["can_create_request"] is expected
    if "administration" in roles or "super_admin" in roles:
        assert result["can_submit"] is True


@pytest.mark.parametrize(
    "roles,deny_export,expected_export,expected_show",
    [
        (("property",), False, True, True),
        (("property", "administration"), False, True, True),
        (("administration",), False, True, False),
        (("super_admin",), False, True, False),
        (("property",), True, False, False),
    ],
)
def test_basic_export_entry_requires_property_identity_and_effective_room_permission(
    db, roles, deny_export, expected_export, expected_show
):
    from uuid import uuid4

    from app.housing.access import HousingAccessService
    from app.housing.property_service import HousingPropertyService
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.invited_housing_helpers import NOW, account, room

    actor = account(db, roles[0])
    for role in roles[1:]:
        db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    target = room(db)
    if deny_export:
        db.add(
            UserPermissionOverride(
                user_id=actor.id,
                permission_id=PERMISSIONS["housing.exports.basic"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    db.flush()
    access = HousingAccessService(db, actor.id, NOW)
    service = HousingPropertyService(db)
    for room_id in (None, target.id):
        result = service.capabilities(access, room_id=room_id)
        assert result["can_export"] is expected_export
        assert result.get("show_basic_export") is expected_show
    missing = service.capabilities(access, room_id=uuid4())
    assert missing.get("show_basic_export") is False


def test_basic_export_entry_is_hidden_without_any_visible_room(db):
    from app.housing.access import HousingAccessService
    from app.housing.property_service import HousingPropertyService
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    from tests.invited_housing_helpers import NOW, account

    actor = account(db, "property")
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    result = HousingPropertyService(db).capabilities(HousingAccessService(db, actor.id, NOW))
    assert result["can_export"] is False
    assert result.get("show_basic_export") is False


def test_property_contract_rejects_unapproved_fields_and_empty_changes():
    assert find_spec("app.housing.property_schemas") is not None, "property proposals are missing"
    from pydantic import ValidationError

    from app.housing.property_schemas import PropertyChangeCreate

    with pytest.raises(ValidationError):
        PropertyChangeCreate(expected_version=1, changes={"floor_id": "x"}, reason="维修检查")
    with pytest.raises(ValidationError):
        PropertyChangeCreate(expected_version=1, changes={}, reason="维修检查")
    proposal = PropertyChangeCreate(
        expected_version=1, changes={"notes": None}, reason="  清理备注  "
    )
    assert proposal.changes.model_dump(exclude_unset=True) == {"notes": None}
    assert proposal.reason == "清理备注"


def test_admin_does_not_implicitly_receive_new_property_permissions():
    from app.rbac.seed import ROLE_PERMISSION_GRANTS

    assert ("property", "housing.read", "global") in ROLE_PERMISSION_GRANTS
    assert ("administration", "housing.changes.review", "global") in ROLE_PERMISSION_GRANTS
    assert not any(
        role == "admin"
        and permission
        in {
            "housing.changes.review",
            "housing.keycards.manage",
            "housing.changes.submit",
            "housing.exports.basic",
        }
        for role, permission, _ in ROLE_PERMISSION_GRANTS
    )


def test_property_export_formula_cells_are_protected_in_both_formats():
    assert find_spec("app.housing.property_export_service") is not None, "basic exports are missing"
    from datetime import UTC, datetime
    from io import BytesIO

    from openpyxl import load_workbook

    from app.housing.property_export_service import render_property_export

    for format in ("csv", "xlsx"):
        content, _, _ = render_property_export(
            ["姓名"], [["=1+1"]], format=format, occurred_at=datetime.now(UTC)
        )
        if format == "csv":
            assert "'=1+1" in content.decode("utf-8-sig")
        else:
            cell = load_workbook(BytesIO(content)).active["A2"]
            assert cell.data_type != "f"
            assert cell.value == "'=1+1"


def test_problem_notes_do_not_export_embedded_phone_email_or_credentials():
    import app.housing.property_export_service as exports

    assert hasattr(exports, "public_note"), "free text privacy filter is missing"
    assert exports.public_note("漏水，联系13800138000") == "[含敏感资料，已隐藏]"
    assert exports.public_note("报修人 test@example.com") == "[含敏感资料，已隐藏]"
    assert exports.public_note("密码：Secret2026") == "[含敏感资料，已隐藏]"
    assert exports.public_note("空调待维修") == "空调待维修"


def test_basic_export_refuses_a_reused_non_snapshot_transaction():
    from datetime import UTC, datetime
    from uuid import uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.errors import ProblemError
    from app.housing.property_export_service import HousingPropertyExportService
    from app.housing.property_schemas import PropertyExportRequest

    with Session(create_engine("sqlite://")) as db, db.begin():
        with pytest.raises(ProblemError) as caught:
            HousingPropertyExportService(db, uuid4()).rows(
                PropertyExportRequest(kind="problems", purpose="事务检查"), as_of=datetime.now(UTC)
            )
        assert caught.value.status == 503


@pytest.mark.parametrize(
    "roles,allowed",
    [
        ({"admin"}, False),
        ({"staff"}, False),
        ({"coach"}, False),
        ({"property"}, True),
        ({"property", "coach"}, True),
        ({"administration"}, True),
    ],
)
def test_room_card_permission_requires_actual_editor_role(roles, allowed):
    from uuid import uuid4

    from app.housing.access import HousingAccessService
    from app.housing.models import HousingRoom
    from app.rbac.policy import PolicyRule, RbacPolicy
    from app.rbac.service import RbacDecisionContext

    access = HousingAccessService.__new__(HousingAccessService)
    access.roles = frozenset(roles)
    access.context = RbacDecisionContext(
        RbacPolicy(),
        [
            PolicyRule("housing.read", "allow", "global"),
            PolicyRule("housing.keycards.manage", "allow", "global"),
        ],
        [],
    )
    target = HousingRoom(building_id=uuid4(), floor_id=uuid4())
    assert access.can_manage_keycards(target) is allowed
    access.context = RbacDecisionContext(
        RbacPolicy(),
        list(access.context.role_rules),
        [PolicyRule("housing.keycards.manage", "deny", "floor", target.floor_id)],
    )
    assert access.can_manage_keycards(target) is False
