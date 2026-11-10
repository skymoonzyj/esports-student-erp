from uuid import uuid4

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.team_management.person_staff_models import (
    PersonTeamStaffAuthorization,
    PersonTeamStaffPosition,
)
from tests.api.test_people import sign_in
from tests.services.test_person_staff import (
    managed_person,
    raw_position,
    staff_write_snapshot,
    unlinked_account_fixture,
)
from tests.services.test_team_access import teams


def test_unactivated_staff_visible_without_account_grant(db, db_client, platform_users):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    row = raw_position(db, platform_users, result, first.id)
    db.commit()
    sign_in(db_client, platform_users, "admin")
    response = db_client.get(f"/api/v1/team-management/teams/{first.id}/staff-positions")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    assert page["items"][0]["row_key"] == f"person:{row.id}"
    assert page["items"][0]["account_id"] is None
    assert page["items"][0]["account_status"] == "not_activated"
    assert page["items"][0]["account_assignment_id"] is None
    assert page["items"][0]["authorization_status"] == "valid"
    assert response.headers["cache-control"] == "no-store, private"
    assert set(page["items"][0]) == {
        "row_key",
        "source_kind",
        "person_position_id",
        "account_assignment_id",
        "person_id",
        "account_id",
        "full_name",
        "nickname",
        "staff_role",
        "account_status",
        "authorization_status",
        "started_on",
        "ended_on",
        "version",
        "allowed_actions",
    }


def test_new_person_assignment_requires_csrf_and_uses_role_plan(db, db_client, platform_users):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    url = f"/api/v1/team-management/teams/{first.id}/person-staff-positions"
    payload = {
        "person_id": str(result.person_id),
        "staff_role": "coach",
        "idempotency_key": str(uuid4()),
    }
    assert db_client.post(url, json=payload).status_code == 403
    response = db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert (
        db_client.post(url, json=payload, headers={"X-CSRF-Token": csrf}).json() == response.json()
    )
    assert response.headers["cache-control"] == "no-store, private"


def test_person_candidates_and_new_mutation_wire_keep_private_minimum(
    db, db_client, platform_users
):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    row = raw_position(db, platform_users, result, first.id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    base = "/api/v1/team-management"
    candidates = db_client.get(
        f"{base}/teams/{first.id}/person-staff-candidates",
        params={"staff_role": "assistant_coach", "query": "隔离"},
    )
    assert candidates.status_code == 200
    assert candidates.json()["items"] == [
        {
            "person_id": str(result.person_id),
            "account_id": None,
            "full_name": "隔离建档人员",
            "nickname": None,
        }
    ]
    for action, version, reason in (
        ("reconfirm", 1, "完整保留确认原因"),
        ("end", 2, "完整保留结束原因"),
    ):
        key = uuid4()
        payload = {"expected_version": version, "reason": reason, "idempotency_key": str(key)}
        response = db_client.post(
            f"{base}/person-staff-positions/{row.id}/{action}",
            json=payload,
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store, private"
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == f"person_staff.{action}", AuditEvent.request_id == str(key)
            )
        )
        assert audit is not None and audit.purpose == reason
    history = db_client.get(
        f"{base}/teams/{first.id}/staff-positions", params={"status": "ended"}
    ).json()
    assert history["total"] == 1 and history["items"][0]["allowed_actions"] == []


@pytest.mark.parametrize("action", ["end", "reconfirm"])
@pytest.mark.parametrize("reason", [" ", "确认" * 101, "联系电话13900000001"])
def test_invalid_or_private_reason_returns_safe_422_and_no_partial_write(
    db, db_client, platform_users, action, reason
):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    row = raw_position(db, platform_users, result, first.id)
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.post(
        f"/api/v1/team-management/person-staff-positions/{row.id}/{action}",
        json={"expected_version": 1, "reason": reason, "idempotency_key": str(uuid4())},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 422, response.text
    assert "13900000001" not in response.text
    db.expire_all()
    assert db.get(PersonTeamStaffPosition, row.id).version == 1
    assert db.get(PersonTeamStaffPosition, row.id).ended_at is None
    assert (
        len(
            list(
                db.scalars(
                    select(PersonTeamStaffAuthorization).where(
                        PersonTeamStaffAuthorization.position_id == row.id
                    )
                )
            )
        )
        == 1
    )


@pytest.mark.parametrize("path", ["staff-positions", "person-staff-candidates"])
def test_new_roster_and_candidates_do_not_expand_staff_management_access(
    db, db_client, platform_users, path
):
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    db.commit()
    sign_in(db_client, platform_users, "coach")
    response = db_client.get(
        f"/api/v1/team-management/teams/{first.id}/{path}",
        params={"staff_role": "coach", "query": "隔离"},
    )
    assert response.status_code in {403, 404}
    assert response.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize("state", ["active_roleless", "disabled", "active_with_role"])
def test_reconfirm_null_link_is_source_conflict_and_preserves_all_state(
    db, db_client, platform_users, state
):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    position = raw_position(db, platform_users, result, first.id)
    unlinked_account_fixture(db, result.person_id, state)
    csrf = sign_in(db_client, platform_users, "admin")
    before = staff_write_snapshot(db, position.id)
    response = db_client.post(
        f"/api/v1/team-management/person-staff-positions/{position.id}/reconfirm",
        json={
            "expected_version": 1,
            "reason": "不可补造账号兑现关联",
            "idempotency_key": str(uuid4()),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "person_staff_source_invalid"
    assert response.headers["cache-control"] == "no-store, private"
    db.expire_all()
    assert staff_write_snapshot(db, position.id) == before
