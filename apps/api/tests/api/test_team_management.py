from uuid import uuid4

from tests.api.test_people import sign_in
from tests.services.test_team_access import teams


def test_context_and_candidate_validation(db_client, platform_users):
    sign_in(db_client, platform_users, "customer_service")
    response = db_client.get("/api/v1/team-management/context")
    assert response.status_code == 200
    assert response.json() == dict.fromkeys(
        [
            "can_open_management",
            "can_read_self",
            "can_manage_catalog",
            "can_assign_managers",
            "can_transfer",
        ],
        False,
    )
    sign_in(db_client, platform_users, "admin")
    for query in ("", "   "):
        response = db_client.get(
            f"/api/v1/team-management/teams/{uuid4()}/candidates", params={"query": query}
        )
        assert response.status_code == 422


def test_team_mutation_csrf_replay_and_people_roles(db, db_client, platform_users):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    admin = platform_users["accounts"]["admin"]
    member = platform_users["accounts"]["self_student"]
    first, _ = teams(db, admin.id)
    db.add(UserRole(user_id=member.id, role_id=ROLES["team_member"].id))
    db.add(UserRole(user_id=member.id, role_id=ROLES["team_manager"].id))
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    url = f"/api/v1/team-management/teams/{first.id}/memberships"
    payload = {"person_id": str(member.person_id), "idempotency_key": str(uuid4())}
    assert db_client.post(url, json=payload).status_code == 403
    joined = db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf})
    assert joined.status_code == 200
    assert db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf}).json() == joined.json()
    people = db_client.get("/api/v1/people", params={"role": "team_member"}).json()
    assert people["total"] == 1
    assert {role["key"] for role in people["items"][0]["roles"]} >= {"team_member", "team_manager"}
    assert people["items"][0]["team"]["current"]["team_id"] == str(first.id)
    counts = db_client.get("/api/v1/people/counts").json()
    assert (counts["team_member"], counts["team_manager"], counts["in_team"]) == (1, 1, 1)


def test_accountless_approved_roster_and_candidate_wire_contract(db, db_client, platform_users):
    from sqlalchemy import select

    from app.team_management.membership_service import TeamMembershipService
    from app.team_management.models import TeamMembership
    from app.team_management.schemas import LeaveTeamInput
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    who = actor(platform_users, "admin")
    first, second = teams(db, who.account.id)
    result = approvals(db).direct_create(
        who, intake(platform_users, kind="player", coach_person_ids=[], team_id=first.id), uuid4()
    )
    csrf = sign_in(db_client, platform_users, "admin")
    roster = db_client.get(f"/api/v1/team-management/teams/{first.id}/memberships")
    assert roster.status_code == 200
    assert roster.json()["items"][0]["account_status"] == "not_activated"
    membership = db.scalar(
        select(TeamMembership).where(TeamMembership.person_id == result.person_id)
    )
    TeamMembershipService(db).leave(who, membership.id, LeaveTeamInput(idempotency_key=uuid4()))
    candidates = db_client.get(
        f"/api/v1/team-management/teams/{second.id}/candidates", params={"query": "隔离建档"}
    )
    assert candidates.status_code == 200 and candidates.json()["items"][0]["account_id"] is None
    joined = db_client.post(
        f"/api/v1/team-management/teams/{second.id}/memberships",
        json={"person_id": str(result.person_id), "idempotency_key": str(uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert joined.status_code == 200
