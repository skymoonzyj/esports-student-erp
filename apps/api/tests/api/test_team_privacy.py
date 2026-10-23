from datetime import date
from uuid import uuid4

from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.team_management.models import TeamManagerAssignment, TeamMembership
from tests.api.test_people import sign_in
from tests.services.test_team_access import teams


def test_private_team_surfaces_and_projection(db, db_client, platform_users):
    admin = platform_users["accounts"]["admin"]
    coach = platform_users["accounts"]["coach"]
    first, second = teams(db, admin.id)
    db.add(UserRole(user_id=coach.id, role_id=ROLES["team_manager"].id))
    db.add(
        TeamManagerAssignment(account_id=coach.id, team_id=first.id, created_by_user_id=admin.id)
    )
    db.add(
        TeamMembership(
            person_id=platform_users["people"]["self_student"].id,
            team_id=first.id,
            joined_on=date(2026, 9, 29),
            created_by_user_id=admin.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "coach")
    roster = db_client.get(f"/api/v1/team-management/teams/{first.id}/memberships")
    assert roster.status_code == 200
    assert roster.headers["cache-control"] == "no-store, private"

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    forbidden = {
        "identity_number",
        "identity_ciphertext",
        "phone",
        "email",
        "date_of_birth",
        "password_hash",
        "guardian",
        "balance",
        "login_phone_digest",
    }
    assert forbidden.isdisjoint(keys(roster.json()))
    for suffix in (
        "memberships",
        "memberships?status=left",
        "candidates?query=张",
        "manager-candidates?query=张",
        "manager-assignments",
    ):
        hidden = db_client.get(f"/api/v1/team-management/teams/{second.id}/{suffix}")
        missing = db_client.get(f"/api/v1/team-management/teams/{uuid4()}/{suffix}")
        assert hidden.status_code == missing.status_code == 404
        left, right = hidden.json(), missing.json()
        left.pop("request_id", None)
        right.pop("request_id", None)
        assert left == right and left["code"] == "team_not_found"
    for route, payload in (
        ("memberships", {"person_id": str(platform_users["people"]["self_student"].id)}),
        ("manager-assignments", {"account_id": str(coach.id)}),
    ):
        bodies = []
        for target in (second.id, uuid4()):
            response = db_client.post(
                f"/api/v1/team-management/teams/{target}/{route}",
                json={**payload, "idempotency_key": str(uuid4())},
                headers={"X-CSRF-Token": csrf},
            )
            assert response.status_code == 404
            body = response.json()
            body.pop("request_id", None)
            bodies.append(body)
        assert bodies[0] == bodies[1]
    assert db_client.get("/api/v1/people").status_code == 403
