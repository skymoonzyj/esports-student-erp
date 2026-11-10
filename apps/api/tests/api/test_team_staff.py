from uuid import uuid4

import pytest

from tests.api.test_people import sign_in
from tests.services.test_team_access import teams


@pytest.mark.parametrize(
    "staff_role,role_key",
    [("manager", "team_manager"), ("coach", "coach"), ("assistant_coach", "assistant_coach")],
)
def test_staff_candidate_and_assignment_wire_contract(
    db, db_client, platform_users, staff_role, role_key
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    admin = platform_users["accounts"]["admin"]
    coach = platform_users["accounts"]["coach"]
    if role_key != "coach":
        db.add(UserRole(user_id=coach.id, role_id=ROLES[role_key].id))
    first, _ = teams(db, admin.id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    base = f"/api/v1/team-management/teams/{first.id}"
    candidates = db_client.get(
        f"{base}/staff-candidates",
        params={"staff_role": staff_role, "query": platform_users["people"]["coach"].full_name},
    )
    assert candidates.status_code == 200
    assert [item["account_id"] for item in candidates.json()["items"]] == [str(coach.id)]
    assert set(candidates.json()["items"][0]) == {
        "account_id",
        "person_id",
        "full_name",
        "nickname",
        "age",
        "is_minor",
    }
    payload = {
        "account_id": str(coach.id),
        "staff_role": staff_role,
        "idempotency_key": str(uuid4()),
    }
    assert db_client.post(f"{base}/staff-assignments", json=payload).status_code == 403
    response = db_client.post(
        f"{base}/staff-assignments", json=payload, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    assert (
        db_client.post(
            f"{base}/staff-assignments", json=payload, headers={"X-CSRF-Token": csrf}
        ).json()
        == response.json()
    )
    listed = db_client.get(f"{base}/staff-assignments").json()
    assert listed["total"] == 1
    assert set(listed["items"][0]) == {
        "id", "account_id", "full_name", "nickname", "account_status",
        "started_on", "ended_on", "staff_role",
    }
    assert listed["items"][0]["staff_role"] == staff_role
    assignment_id = response.json()["assignment_id"]
    ended = db_client.post(
        f"/api/v1/team-management/staff-assignments/{assignment_id}/end",
        json={"idempotency_key": str(uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert ended.status_code == 200
    assert db_client.get(f"{base}/staff-assignments").json()["total"] == 0
    history = db_client.get(f"{base}/staff-assignments", params={"status": "ended"}).json()
    assert history["total"] == 1 and history["items"][0]["ended_on"] is not None
    assert db_client.get(f"{base}/manager-assignments").json()["total"] == 0
    for query in ("", "   "):
        assert (
            db_client.get(
                f"{base}/staff-candidates", params={"staff_role": staff_role, "query": query}
            ).status_code
            == 422
        )


def test_staff_management_remains_actual_superadmin_only(db, db_client, platform_users):
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    sign_in(db_client, platform_users, "coach")
    response = db_client.get(f"/api/v1/team-management/teams/{first.id}/staff-assignments")
    assert response.status_code in {403, 404}
