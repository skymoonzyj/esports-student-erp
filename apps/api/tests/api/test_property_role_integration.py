from sqlalchemy import select

from app.identity.models import StaffProfile
from app.rbac.models import UserRole
from app.rbac.seed import ROLES


def test_property_can_be_assigned_as_staff_job_without_administrative_privileges(
    db_client, platform_users, db,
):
    target = platform_users["accounts"]["customer_service"]
    identifier, password, _ = platform_users["credentials"]["admin"]
    login = db_client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
    assert login.status_code == 200
    expected = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == target.id)))
    response = db_client.post(
        f"/api/v1/accounts/{target.id}/job-role-change",
        json={"from_role_id": str(ROLES["customer_service"].id), "to_role_id": str(ROLES["property"].id),
              "keep_concurrent_roles": False, "expected_role_ids": [str(value) for value in expected]},
        headers={"X-CSRF-Token": login.json()["csrf_token"]},
    )
    assert response.status_code == 200, response.text
    assigned = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == target.id)))
    assert ROLES["property"].id in assigned
    assert ROLES["administration"].id not in assigned
    assert ROLES["customer_service"].id not in assigned
    assert db.get(StaffProfile, target.person_id).staff_type == "other"
    access = db_client.get(f"/api/v1/accounts/{target.id}/access")
    assert access.status_code == 200, access.text
    allowed = {item["key"] for item in access.json()["effective_permissions"]}
    assert "housing.keycards.manage" in allowed
    assert not allowed & {"housing.rooms.manage", "housing.stays.manage", "housing.reports.export_sensitive", "revenue_share.finance"}
