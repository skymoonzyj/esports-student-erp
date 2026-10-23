from uuid import uuid4

import pytest

from tests.api.test_people import sign_in
from tests.services.test_personnel_intake import intake


@pytest.mark.parametrize("who,allowed", [("admin", True), ("coach", False)])
def test_context_pending_create_capability_requires_actual_global_admin(
    db, db_client, platform_users, who, allowed
):
    if who == "coach":
        from app.rbac.models import UserPermissionOverride
        from app.rbac.seed import PERMISSIONS

        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"][who].id,
                permission_id=PERMISSIONS["personnel_intake.review"].id,
                effect="allow",
                scope_kind="global",
            )
        )
        db.commit()
    sign_in(db_client, platform_users, who)
    response = db_client.get("/api/v1/personnel-intake/context")
    assert response.status_code == 200
    assert response.json()["can_direct_create_pending"] is allowed


def test_context_pending_create_does_not_borrow_scoped_strict_capability(
    db, db_client, platform_users
):
    from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.services.test_team_access import teams

    team, _ = teams(db, platform_users["accounts"]["admin"].id)
    account_id = platform_users["accounts"]["coach"].id
    db.add(UserRole(user_id=account_id, role_id=ROLES["admin"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=account_id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="allow",
            scope_kind="assigned_team",
            scope_id=team.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "coach")
    response = db_client.get("/api/v1/personnel-intake/context")
    assert response.status_code == 200
    assert response.json()["can_direct_create"] is True
    assert response.json()["can_direct_create_pending"] is False


@pytest.mark.parametrize("revoke", ["role", "permission"])
def test_context_pending_create_capability_rechecks_live_revocation(db, platform_users, revoke):
    from app.personnel_intake.access import PersonnelIntakeAccess
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS
    from tests.services.test_personnel_intake import actor

    access = PersonnelIntakeAccess(db)
    who = actor(platform_users, "admin")
    assert access.context(who).can_direct_create_pending is True
    if revoke == "role":
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == who.account.id))
    else:
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["personnel_intake.review"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    db.flush()
    assert access.context(who).can_direct_create_pending is False


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/context"),
        ("get", "/requests"),
        ("get", f"/requests/{uuid4()}"),
        ("post", "/requests"),
    ],
)
def test_private_intake_errors_are_never_cached(client, method, path):
    response = getattr(client, method)("/api/v1/personnel-intake" + path)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store, private"


def test_context_choices_submit_and_safe_validation(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "coach")
    context = db_client.get("/api/v1/personnel-intake/context")
    assert context.status_code == 200
    assert context.json()["eligible_coaches"] == [
        {"id": str(platform_users["people"]["coach"].id), "label": "平台教练"}
    ]
    assert context.json()["can_review"] is False
    url = "/api/v1/personnel-intake/requests"
    value = intake(platform_users)
    payload = value.model_dump(mode="json")
    payload.update(
        identity_number=value.identity_number.get_secret_value(),
        phone=value.phone.get_secret_value(),
        email=value.email.get_secret_value(),
    )
    assert db_client.post(url, json=payload).status_code == 403
    submitted = db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf})
    assert submitted.status_code == 201
    assert submitted.headers["Cache-Control"] == "no-store, private"
    listed = db_client.get(url).json()
    assert listed["total"] == 1
    assert "scoped_payload" not in listed["items"][0]
    invalid = db_client.post(
        url,
        json={"identity_number": payload["identity_number"], "unexpected": payload["email"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid.status_code == 422
    assert payload["identity_number"] not in invalid.text and payload["email"] not in invalid.text
    assert invalid.headers["Cache-Control"] == "no-store, private"
    malicious_key = "sensitive-key@example.com"
    invalid_key = db_client.post(
        url, json={malicious_key: "ignored"}, headers={"X-CSRF-Token": csrf}
    )
    assert invalid_key.status_code == 422
    assert malicious_key not in invalid_key.text
