from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.housing.seed_inventory import INITIAL_ROOMS
from app.personnel_intake.models import CoachPersonResponsibility
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.api.test_housing import sign_in


def test_owner_scope_applies_to_counts_pages_and_old_housing_routes(db, db_client, platform_users):
    coach = platform_users["accounts"]["coach"]
    target = platform_users["people"]["self_student"]
    db.add(
        CoachPersonResponsibility(
            coach_person_id=coach.person_id, person_id=target.id, created_by_user_id=coach.id
        )
    )
    for permission in ("housing.read", "housing.stays.manage", "housing.reports.export_sensitive"):
        db.add(
            UserPermissionOverride(
                user_id=coach.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.commit()
    csrf = sign_in(db_client, platform_users, "coach")
    response = db_client.get("/api/v1/personnel-intake/managed-people")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["total"] == 1
    assert {item["person_id"] for item in response.json()["items"]} == {str(target.id)}
    room = next(room for room in INITIAL_ROOMS if room.room_type == "single")
    for path in (
        "/housing/catalog",
        "/housing/overview",
        "/housing/report-batches",
        f"/housing/rooms/{room.id}",
        f"/housing/rooms/{room.id}/detail",
        f"/housing/rooms/{room.id}/resident-options?query=test",
        f"/housing/report-batches/{uuid4()}",
        f"/housing/report-batches/{uuid4()}/download?format=xlsx",
    ):
        denied = db_client.get("/api/v1" + path)
        assert denied.status_code in {403, 404}
        assert denied.headers["cache-control"] == "no-store, private"
    for path, body in (
        ("/housing/exports", {"format": "csv", "purpose": "隔离测试导出"}),
        (
            f"/housing/rooms/{room.id}/stays",
            {
                "person_id": str(target.id),
                "stay_purpose": "permanent",
                "duration_mode": "long_term",
                "planned_check_in_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            },
        ),
    ):
        denied = db_client.post("/api/v1" + path, json=body, headers={"X-CSRF-Token": csrf})
        assert denied.status_code in {403, 404}
        assert denied.headers["cache-control"] == "no-store, private"
    context = db_client.get("/api/v1/portal/context")
    assert context.status_code == 200
    assert context.json()["housing_management_available"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"page_size": 101},
        {"kind": "other"},
        {"housing_status": "unknown"},
        {"person_id": "private"},
        {"cursor": "broken"},
    ],
)
def test_invalid_managed_query_is_private_and_generic(db_client, platform_users, params):
    sign_in(db_client, platform_users, "coach")
    response = db_client.get("/api/v1/personnel-intake/managed-people", params=params)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store, private"
    assert "broken" not in response.text and '"private"' not in response.text


def test_unqualified_role_cannot_read_managed_people(db_client, platform_users):
    sign_in(db_client, platform_users, "customer_service")
    response = db_client.get("/api/v1/personnel-intake/managed-people")
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize(
    "role_key,staff_role",
    [("team_manager", "manager"), ("coach", "coach"), ("assistant_coach", "assistant_coach")],
)
def test_readonly_staff_managed_api_never_exposes_global_people_or_housing_writes(
    db, db_client, platform_users, role_key, staff_role
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.models import TeamMembership, TeamStaffAssignment
    from tests.services.test_team_access import teams

    user = platform_users["accounts"]["customer_service"]
    target = platform_users["people"]["self_student"]
    creator = platform_users["accounts"]["admin"].id
    for current in list(db.scalars(select(UserRole).where(UserRole.user_id == user.id))):
        db.delete(current)
    db.flush()
    db.add(UserRole(user_id=user.id, role_id=ROLES[role_key].id))
    team, _ = teams(db, creator)
    binding = TeamStaffAssignment(
        account_id=user.id, team_id=team.id, staff_role=staff_role, created_by_user_id=creator
    )
    db.add_all(
        [
            binding,
            TeamMembership(
                person_id=target.id,
                team_id=team.id,
                joined_on=datetime.now(UTC).date(),
                created_by_user_id=creator,
            ),
        ]
    )
    for permission in (
        "housing.read",
        "housing.stays.manage",
        "housing.transfers.manage",
        "housing.checkouts.manage",
        "personnel_intake.submit",
    ):
        db.add(
            UserPermissionOverride(
                user_id=user.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    result = db_client.get("/api/v1/personnel-intake/managed-people")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store, private"
    assert result.json()["total"] == 1
    assert [row["person_id"] for row in result.json()["items"]] == [str(target.id)]
    assert set(result.json()["items"][0]) == {
        "person_id",
        "full_name",
        "nickname",
        "kinds",
        "authorized_affiliations",
        "housing",
    }
    for path in ("/api/v1/people", "/api/v1/housing/catalog", "/api/v1/housing/overview"):
        denied = db_client.get(path)
        assert denied.status_code in {403, 404}
    room = INITIAL_ROOMS[0]
    denied_write = db_client.post(
        f"/api/v1/housing/rooms/{room.id}/stays",
        headers={"X-CSRF-Token": csrf},
        json={
            "person_id": str(target.id),
            "stay_purpose": "permanent",
            "duration_mode": "long_term",
            "planned_check_in_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert denied_write.status_code in {403, 404}
    context = db_client.get("/api/v1/personnel-intake/context")
    assert context.status_code == 200
    assert context.json()["can_submit"] is False
    binding.ended_at, binding.end_kind = datetime.now(UTC), "unassigned"
    db.commit()
    revoked = db_client.get("/api/v1/personnel-intake/managed-people")
    assert revoked.status_code == 200 and revoked.json()["total"] == 0
