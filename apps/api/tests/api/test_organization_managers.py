from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.revenue_share.models import RevenueShareOrganization


@pytest.fixture
def organization(db, platform_users):
    row = RevenueShareOrganization(
        name=f"API负责人机构-{uuid4().hex}",
        created_by_user_id=platform_users["accounts"]["admin"].id,
    )
    db.add(row)
    db.commit()
    return row


def test_superadmin_can_grant_list_revoke_and_candidates_are_minimal(
    db_client,
    db,
    platform_users,
    login_as_admin,
    organization,
):
    target = platform_users["accounts"]["self_student"]
    headers = {"X-CSRF-Token": login_as_admin.csrf_token}
    base = f"/api/v1/revenue-share/organizations/{organization.id}/managers"
    for _ in range(2):
        assigned = db_client.put(f"{base}/{target.id}", headers=headers)
        assert assigned.status_code == 200
        assert set(assigned.json()) == {
            "account_id",
            "organization_id",
            "full_name",
            "nickname",
            "granted_at",
        }
    listing = db_client.get(base)
    assert listing.status_code == 200
    assert listing.json() == [assigned.json()]
    candidates = db_client.get(
        "/api/v1/revenue-share/organization-manager-candidates", params={"q": "自助账号"}
    )
    assert candidates.status_code == 200
    assert candidates.json()["total"] == 1
    assert candidates.json()["items"] == [
        {
            "account_id": str(target.id),
            "full_name": "自助账号学员",
            "nickname": None,
        }
    ]
    assert candidates.headers["cache-control"] == "no-store, private"
    for _ in range(2):
        assert db_client.delete(f"{base}/{target.id}", headers=headers).status_code == 204
    assert db_client.get(base).json() == []


@pytest.mark.parametrize("role", ["admin", "finance", "student"])
def test_real_admin_finance_and_manager_cannot_call_management_routes(
    db_client,
    db,
    platform_users,
    login_as_admin,
    organization,
    role,
):
    actor = platform_users["accounts"]["admin"]
    target = platform_users["accounts"]["self_student"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES[role].id))
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["revenue_share.manage"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.commit()
    headers = {"X-CSRF-Token": login_as_admin.csrf_token}
    base = f"/api/v1/revenue-share/organizations/{organization.id}/managers"
    for response in (
        db_client.get(base),
        db_client.get("/api/v1/revenue-share/organization-manager-candidates"),
        db_client.put(f"{base}/{target.id}", headers=headers),
        db_client.delete(f"{base}/{target.id}", headers=headers),
    ):
        assert response.status_code == 403


def test_management_writes_require_csrf_and_pagination_is_bounded(
    db_client, platform_users, login_as_admin, organization
):
    target = platform_users["accounts"]["self_student"]
    base = f"/api/v1/revenue-share/organizations/{organization.id}/managers/{target.id}"
    assert db_client.put(base).status_code == 403
    assert db_client.delete(base).status_code == 403
    candidates = "/api/v1/revenue-share/organization-manager-candidates"
    assert db_client.get(candidates, params={"page_size": 101}).status_code == 422
    assert db_client.get(candidates, params={"page": 0}).status_code == 422


@pytest.mark.parametrize("endpoint", ["candidates", "managers"])
@pytest.mark.parametrize("status", [200, 401, 403, 422])
def test_manager_reads_are_private_before_auth_and_validation(
    db_client, db, platform_users, organization, request, endpoint, status
):
    if status != 401:
        request.getfixturevalue("login_as_admin")
    if status == 403:
        actor = platform_users["accounts"]["admin"]
        db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
        db.add(UserRole(user_id=actor.id, role_id=ROLES["admin"].id))
        db.commit()
    if endpoint == "candidates":
        path = "/api/v1/revenue-share/organization-manager-candidates"
        params = {"page": 0} if status == 422 else {}
    else:
        organization_id = "invalid-uuid" if status == 422 else str(organization.id)
        path = f"/api/v1/revenue-share/organizations/{organization_id}/managers"
        params = {}
    response = db_client.get(path, params=params)
    assert response.status_code == status
    assert response.headers.get("cache-control") == "no-store, private"
    assert response.headers.get("pragma") == "no-cache"
    assert response.headers.get("x-content-type-options") == "nosniff"
