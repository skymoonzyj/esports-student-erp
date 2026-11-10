"""The effective-access explanation must match live administrative team scope."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.team_management.access import TeamAccessService
from app.team_management.models import TeamManagerAssignment
from tests.api.test_people import sign_in
from tests.services.test_administration_team_management import administration
from tests.services.test_team_access import teams

TEAM_PERMISSIONS = {
    "team_memberships.read",
    "team_memberships.manage",
    "team_memberships.transfer",
    "team_manager_assignments.manage",
}
FINANCIAL_DENIES = (
    "payments.read",
    "payments.manage",
    "revenue_share.finance",
    "revenue_share.manage",
    "revenue_share.export",
    "procurement.read",
    "procurement.manage",
    "procurement.settle",
    "procurement.export",
)


def test_seeded_administration_role_projects_effective_team_permissions_and_auth_navigation(
    db, db_client, platform_users
):
    target = platform_users["accounts"]["customer_service"]
    db.execute(
        UserRole.__table__.delete().where(
            UserRole.user_id == target.id,
            UserRole.role_id == ROLES["customer_service"].id,
        )
    )
    db.add_all(
        [UserRole(user_id=target.id, role_id=ROLES[key].id) for key in ("admin", "administration")]
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=target.id,
                permission_id=PERMISSIONS[key].id,
                effect="deny",
                scope_kind="global",
            )
            for key in FINANCIAL_DENIES
        ]
    )
    db.commit()
    before_roles = set(db.execute(select(UserRole.user_id, UserRole.role_id)))
    before_denies = set(
        db.scalars(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == target.id,
                UserPermissionOverride.effect == "deny",
            )
        )
    )
    sign_in(db_client, platform_users, "customer_service")
    me = db_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert TEAM_PERMISSIONS <= set(me.json()["permissions"])
    assert me.json()["is_super_admin"] is False
    assert not set(FINANCIAL_DENIES) & set(me.json()["permissions"])
    assert db_client.get("/api/v1/team-management/context").json()["can_open_management"]
    sign_in(db_client, platform_users, "admin")
    access = db_client.get(f"/api/v1/accounts/{target.id}/access")
    assert access.status_code == 200
    effective = {item["key"]: item for item in access.json()["effective_permissions"]}
    assert TEAM_PERMISSIONS <= set(effective)
    assert TEAM_PERMISSIONS <= set(RbacService(db).available_permissions(user_id=target.id))
    for permission in TEAM_PERMISSIONS:
        assert effective[permission]["scope_kinds"] == ["global"]
        assert effective[permission]["sources"] == ["role"]
    assert not set(FINANCIAL_DENIES) & set(effective)
    assert set(db.execute(select(UserRole.user_id, UserRole.role_id))) == before_roles
    assert (
        set(
            db.scalars(
                select(UserPermissionOverride.id).where(
                    UserPermissionOverride.user_id == target.id,
                    UserPermissionOverride.effect == "deny",
                )
            )
        )
        == before_denies
    )


@pytest.mark.parametrize(
    "revoke", ["deny", "role", "disabled", "expired", "removed_scoped", "expired_scoped"]
)
def test_administration_effective_projection_requires_live_global_authority(
    db, platform_users, revoke
):
    who = administration(db, platform_users)
    service = RbacService(db)
    assert TEAM_PERMISSIONS <= {
        item.permission.key for item in service.account_effective_permissions(who.account.id)
    }
    if revoke == "deny":
        db.add_all(
            [
                UserPermissionOverride(
                    user_id=who.account.id,
                    permission_id=PERMISSIONS[key].id,
                    effect="deny",
                    scope_kind="global",
                )
                for key in TEAM_PERMISSIONS
            ]
        )
    elif revoke == "role":
        db.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == who.account.id,
                UserRole.role_id == ROLES["administration"].id,
            )
        )
    elif revoke == "disabled":
        who.account.status = "disabled"
    else:
        for grant in list(
            db.scalars(
                select(UserPermissionOverride).where(
                    UserPermissionOverride.user_id == who.account.id,
                )
            )
        ):
            if revoke == "removed_scoped":
                db.delete(grant)
            else:
                grant.valid_from = datetime.now(UTC) - timedelta(days=2)
                grant.valid_until = datetime.now(UTC) - timedelta(days=1)
        if revoke.endswith("_scoped"):
            first, _ = teams(db, platform_users["accounts"]["admin"].id)
            db.add_all(
                [
                    UserPermissionOverride(
                        user_id=who.account.id,
                        permission_id=PERMISSIONS[key].id,
                        effect="allow",
                        scope_kind="assigned_team",
                        scope_id=first.id,
                    )
                    for key in TEAM_PERMISSIONS
                ]
            )
    db.flush()
    assert not TEAM_PERMISSIONS & {
        item.permission.key for item in service.account_effective_permissions(who.account.id)
    }
    assert not TEAM_PERMISSIONS & set(service.available_permissions(user_id=who.account.id))


def test_coach_overrides_without_live_team_relationship_do_not_project_global_team_access(
    db, platform_users
):
    who = administration(db, platform_users, key="coach", role=False)
    service = RbacService(db)
    assert not TEAM_PERMISSIONS & {
        item.permission.key for item in service.account_effective_permissions(who.account.id)
    }
    assert not TEAM_PERMISSIONS & set(service.available_permissions(user_id=who.account.id))


def test_non_administration_manager_keeps_only_real_team_scope_in_projection(db, platform_users):
    who = administration(db, platform_users, key="coach", role=False)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    db.add(
        TeamManagerAssignment(
            account_id=who.account.id,
            team_id=first.id,
            created_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )
    db.flush()
    service = RbacService(db)
    expected = {"team_memberships.read", "team_memberships.manage"}
    assert (
        TEAM_PERMISSIONS
        & {item.permission.key for item in service.account_effective_permissions(who.account.id)}
        == expected
    )
    assert TEAM_PERMISSIONS & set(service.available_permissions(user_id=who.account.id)) == expected
    for action in ("read", "manage"):
        assert set(db.scalars(TeamAccessService(db).visible_team_ids(who, action))) == {first.id}
        assert second.id not in set(db.scalars(TeamAccessService(db).visible_team_ids(who, action)))
