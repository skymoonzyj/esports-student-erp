"""Full payload privacy and person-scoped review entry are separate capabilities."""

from uuid import uuid4

import pytest

from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.service import PersonnelIntakeService
from app.rbac.models import RolePermission, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.team_management.models import TeamManagerAssignment
from tests.api.test_people import sign_in
from tests.services.test_personnel_intake import actor, intake, service
from tests.services.test_personnel_queue_filters import override, queue_row
from tests.services.test_team_access import teams

URL = "/api/v1/personnel-intake"


@pytest.mark.parametrize("role", ["team_manager", "administration", "admin"])
def test_submit_only_actor_cannot_list_or_decrypt_another_submitters_request(
    db, db_client, platform_users, monkeypatch, role
):
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    for key in ("coach", "customer_service"):
        account_id = platform_users["accounts"][key].id
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == account_id))
        db.add(UserRole(user_id=account_id, role_id=ROLES[role].id))
        if role == "team_manager":
            db.add(
                TeamManagerAssignment(
                    account_id=account_id,
                    team_id=first.id,
                    created_by_user_id=platform_users["accounts"]["admin"].id,
                )
            )
    if role == "admin":
        db.execute(
            RolePermission.__table__.delete().where(
                RolePermission.role_id == ROLES["admin"].id,
                RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
            )
        )
    db.flush()
    s = service(db)
    payload = intake(platform_users, kind="player", coach_person_ids=[], team_id=first.id)
    other = s.submit(actor(platform_users, "coach"), payload)
    reader = actor(platform_users, "customer_service")
    own = s.submit(
        reader,
        intake(
            platform_users,
            kind="player",
            coach_person_ids=[],
            team_id=first.id,
            identity_number="110105199001021238",
            date_of_birth="1990-01-02",
            email="own-final@example.com",
        ),
    )
    assert s.detail(reader, own.id).scoped_payload is not None
    sign_in(db_client, platform_users, "customer_service")

    def no_payload_decryption(*args, **kwargs):
        pytest.fail("unauthorized full detail must be rejected before decrypting payload")

    monkeypatch.setattr(PersonnelIntakeService, "_payload", no_payload_decryption)
    for view in (None, "mine"):
        page = s.list_requests(reader, view=view)
        assert (page.total, [row.id for row in page.items]) == (1, [own.id])
        response = db_client.get(URL + "/requests", params={"view": view} if view else {})
        assert response.status_code == 200
        assert (response.json()["total"], [row["id"] for row in response.json()["items"]]) == (
            1,
            [str(own.id)],
        )
    with pytest.raises(PersonnelIntakeError) as denied:
        s.detail(reader, other.id)
    assert denied.value.status == 404
    denied_response = db_client.get(f"{URL}/requests/{other.id}")
    absent_response = db_client.get(f"{URL}/requests/{uuid4()}")
    assert denied_response.status_code == absent_response.status_code == 404
    assert denied_response.json()["detail"] == absent_response.json()["detail"]
    assert denied_response.headers["Cache-Control"] == "no-store, private"


def test_person_scoped_review_context_queue_and_denial_do_not_grant_direct_create(
    db, db_client, platform_users
):
    target = platform_users["people"]["own_student"].id
    db.add(UserRole(user_id=platform_users["accounts"]["coach"].id, role_id=ROLES["admin"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    override(db, platform_users, "coach", "submit", "deny")
    override(db, platform_users, "coach", "review", "allow", "person", target)
    wanted = queue_row(db, platform_users, person_id=target)
    queue_row(db, platform_users)
    sign_in(db_client, platform_users, "coach")
    context = db_client.get(URL + "/context")
    assert context.status_code == 200
    assert context.json() == {
        "can_submit": False,
        "can_review": True,
        "can_direct_create": False,
        "can_direct_create_pending": False,
        "can_read_managed": True,
        "allowed_team_ids": [],
        "eligible_coaches": [],
        "eligible_teams": [],
    }
    page = db_client.get(URL + "/requests", params={"view": "review"})
    assert page.status_code == 200
    assert (page.json()["total"], [row["id"] for row in page.json()["items"]]) == (
        1,
        [str(wanted.id)],
    )
    override(db, platform_users, "coach", "review", "deny", "person", target)
    denied_context = db_client.get(URL + "/context").json()
    assert denied_context["can_review"] is False
    assert denied_context["can_direct_create"] is False
    assert db_client.get(URL + "/requests", params={"view": "review"}).status_code == 403
