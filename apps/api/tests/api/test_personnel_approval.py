from uuid import uuid4

import pytest


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", f"/requests/{uuid4()}/approve"),
        ("post", f"/requests/{uuid4()}/return"),
        ("post", "/direct-create"),
        ("post", f"/people/{uuid4()}/corrections"),
        ("put", f"/people/{uuid4()}/coaches"),
    ],
)
def test_approval_mutations_require_auth_and_private_errors(client, method, path):
    response = getattr(client, method)("/api/v1/personnel-intake" + path)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store, private"


def test_approve_api_enforces_csrf_and_keeps_actor_separation(db, db_client, platform_users):
    from tests.api.test_people import sign_in
    from tests.services.test_personnel_intake import actor, intake, service

    pending = service(db).submit(actor(platform_users), intake(platform_users))
    csrf = sign_in(db_client, platform_users, "admin")
    url = f"/api/v1/personnel-intake/requests/{pending.id}/approve"
    payload = {"expected_version": 1}
    assert db_client.post(url, json=payload).status_code == 403
    response = db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert set(response.json()) == {"request_id", "person_id", "approval_id"}
    assert response.headers["Cache-Control"] == "no-store, private"
    assert (
        db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf}).json() == response.json()
    )


@pytest.mark.parametrize("decision", ["approve", "return"])
def test_hidden_review_target_and_absent_id_have_identical_private_response(
    db, db_client, platform_users, decision
):
    from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.api.test_people import sign_in
    from tests.services.test_personnel_intake import actor, intake, service
    from tests.services.test_team_access import teams

    submitter = actor(platform_users, "admin")
    first, hidden = teams(db, submitter.account.id)
    pending = service(db).submit(
        submitter, intake(platform_users, kind="player", coach_person_ids=[], team_id=hidden.id)
    )
    reviewer = actor(platform_users, "coach")
    db.add(UserRole(user_id=reviewer.account.id, role_id=ROLES["admin"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=reviewer.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="allow",
            scope_kind="assigned_team",
            scope_id=first.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "coach")
    responses = [
        db_client.post(
            f"/api/v1/personnel-intake/requests/{request_id}/{decision}",
            json={
                "expected_version": 1,
                **({"reason": "资料需要复核"} if decision == "return" else {}),
            },
            headers={"X-CSRF-Token": csrf},
        )
        for request_id in (pending.id, uuid4())
    ]
    assert [response.status_code for response in responses] == [404, 404]
    assert {key: value for key, value in responses[0].json().items() if key != "request_id"} == {
        key: value for key, value in responses[1].json().items() if key != "request_id"
    }
    assert all(response.headers["Cache-Control"] == "no-store, private" for response in responses)


@pytest.mark.parametrize(
    "reason,expected", [(" " + "调" * 200 + " ", 204), ("调" * 201, 422), ("联系13900000123", 422)]
)
def test_coach_reason_api_trims_limits_and_rejects_sensitive_input(
    db, db_client, platform_users, reason, expected
):
    from sqlalchemy import select

    from app.audit.models import AuditEvent
    from tests.api.test_people import sign_in
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    result = approvals(db).direct_create(
        actor(platform_users, "admin"), intake(platform_users), uuid4()
    )
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.put(
        f"/api/v1/personnel-intake/people/{result.person_id}/coaches",
        json={"coach_person_ids": [], "expected_version": 1, "reason": reason},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == expected
    assert response.headers["Cache-Control"] == "no-store, private"
    if expected == 204:
        assert (
            db.scalar(
                select(AuditEvent.purpose).where(
                    AuditEvent.action == "personnel_intake.coaches_replace"
                )
            )
            == reason.strip()
        )
    else:
        assert reason not in response.text
