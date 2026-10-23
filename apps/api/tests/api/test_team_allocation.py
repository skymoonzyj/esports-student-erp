from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.identity.models import Person
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.api.test_people import sign_in


def test_allocation_counts_and_filter_share_authorization(db, db_client, platform_users):
    target = db.get(Person, platform_users["people"]["self_student"].id)
    target.team_allocation_status = "pending"
    db.flush()
    sign_in(db_client, platform_users, "admin")
    page = db_client.get("/api/v1/people", params={"team_allocation_status": "pending"}).json()
    assert page["total"] == 1
    assert page["items"][0]["team_allocation"] == {
        "visibility": "allowed",
        "status": "pending",
        "version": 1,
        "can_update": True,
    }
    assert "team_allocation_status" not in page["items"][0]
    assert db_client.get("/api/v1/people/counts").json()["pending_team_allocation"] == 1
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    filtered = db_client.get("/api/v1/people", params={"team_allocation_status": "pending"})
    assert filtered.status_code == 403
    counts = db_client.get("/api/v1/people/counts").json()
    assert counts["pending_team_allocation"] is None
    assert counts["assigned_team_allocation"] is None
    detail = db_client.get(f"/api/v1/people/{target.id}").json()
    assert detail["team_allocation"] == {
        "visibility": "denied",
        "status": None,
        "version": None,
        "can_update": False,
    }


def test_allocation_patch_is_private_csrf_versioned_and_replay_safe(db_client, platform_users):
    target = platform_users["people"]["self_student"]
    csrf = sign_in(db_client, platform_users, "admin")
    url = f"/api/v1/people/{target.id}/team-allocation"
    body = {
        "status": "not_required",
        "expected_version": 1,
        "idempotency_key": str(uuid4()),
        "reason": "暂无分队需要",
    }
    assert db_client.patch(url, json=body).status_code == 403
    response = db_client.patch(url, json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json() == {
        "visibility": "allowed",
        "status": "not_required",
        "version": 2,
        "can_update": True,
    }
    assert "no-store" in response.headers["Cache-Control"]
    assert db_client.patch(url, json=body, headers={"X-CSRF-Token": csrf}).json() == response.json()
    body["idempotency_key"] = str(uuid4())
    assert db_client.patch(url, json=body, headers={"X-CSRF-Token": csrf}).status_code == 409
    assert (
        db_client.patch(
            f"/api/v1/people/{uuid4()}/team-allocation", json=body, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 404
    )


def test_ordinary_admin_without_team_read_qualification_has_no_allocation_edit(
    db, db_client, platform_users
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    admin = platform_users["accounts"]["admin"]
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == admin.id))
    db.add(UserRole(user_id=admin.id, role_id=ROLES["admin"].id))
    db.flush()
    csrf = sign_in(db_client, platform_users, "admin")
    person_id = platform_users["people"]["self_student"].id
    detail = db_client.get(f"/api/v1/people/{person_id}")
    assert detail.status_code == 200
    assert detail.json()["team_allocation"] == {
        "visibility": "denied",
        "status": None,
        "version": None,
        "can_update": False,
    }
    response = db_client.patch(
        f"/api/v1/people/{person_id}/team-allocation",
        json={
            "status": "pending",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
            "reason": "当前无查看资格",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404


def test_scoped_counts_and_filtered_total_exclude_hidden_assigned_people(
    db, db_client, platform_users
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.assignment_service import TeamAssignmentService
    from app.team_management.membership_service import TeamMembershipService
    from app.team_management.schemas import AssignManagerInput, JoinTeamInput
    from tests.services.test_personnel_intake import actor
    from tests.services.test_team_access import teams

    admin = actor(platform_users, "admin")
    first, second = teams(db, admin.account.id)
    members = [platform_users["accounts"][key] for key in ("self_student", "minor_student")]
    for member, team in zip(members, (first, second), strict=True):
        db.add(UserRole(user_id=member.id, role_id=ROLES["team_member"].id))
        db.flush()
        TeamMembershipService(db).join(
            admin, team.id, JoinTeamInput(person_id=member.person_id, idempotency_key=uuid4())
        )
    db.add(UserRole(user_id=admin.account.id, role_id=ROLES["team_manager"].id))
    db.flush()
    TeamAssignmentService(db).assign(
        admin, first.id, AssignManagerInput(account_id=admin.account.id, idempotency_key=uuid4())
    )
    db.execute(
        UserRole.__table__.delete().where(
            UserRole.user_id == admin.account.id, UserRole.role_id == ROLES["super_admin"].id
        )
    )
    for permission in ("people.list", "people.read"):
        db.add(
            UserPermissionOverride(
                user_id=admin.account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.flush()
    sign_in(db_client, platform_users, "admin")
    page = db_client.get(
        "/api/v1/people", params={"team_allocation_status": "assigned", "page_size": 1}
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert db_client.get("/api/v1/people/counts").json()["assigned_team_allocation"] == 2
    detail = db_client.get(f"/api/v1/people/{members[1].person_id}").json()
    assert detail["team_allocation"] == {
        "visibility": "denied",
        "status": None,
        "version": None,
        "can_update": False,
    }


def test_pending_staff_response_accepts_unknown_facts_without_exposing_raw_allocation():
    from datetime import UTC, datetime

    from app.identity.router import _summary_response
    from app.identity.schemas import PersonSummary

    person = PersonSummary(
        id=uuid4(),
        full_name="待补工作人员",
        nickname=None,
        person_types=["staff"],
        assisted_kind="staff",
        account_status="not_activated",
        masked_identity="P*****T",
        masked_phone=None,
        email=None,
        sex=None,
        date_of_birth=None,
        document_type="passport",
        document_issuing_country=None,
        intake_mode="pending",
        team_allocation_status="pending",
        team_allocation_version=7,
        staff_type="other",
        created_at=datetime.now(UTC),
        age_status="unknown",
        guardian_status="not_applicable",
    )
    data = _summary_response(person).model_dump(mode="json")
    assert (data["assisted_kind"], data["sex"], data["date_of_birth"]) == ("staff", None, None)
    assert data["team_allocation"]["status"] is None
    assert "team_allocation_status" not in data and "team_allocation_version" not in data


def test_allocation_input_only_accepts_explicit_states_and_bounded_reason():
    from app.team_management.schemas import TeamAllocationInput

    body = {
        "status": "pending",
        "expected_version": 1,
        "idempotency_key": uuid4(),
        "reason": "  组织划分  ",
    }
    assert TeamAllocationInput.model_validate(body).reason == "组织划分"
    for changes in (
        {"status": "assigned"},
        {"status": "untracked"},
        {"reason": "   "},
        {"reason": "a" * 201},
        {"expected_version": 0},
        {"role": "team_member"},
    ):
        with pytest.raises(ValidationError):
            TeamAllocationInput.model_validate({**body, **changes})


def test_target_update_deny_keeps_readable_status_but_removes_mutation_capability(
    db, db_client, platform_users
):
    target = platform_users["people"]["self_student"]
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["people.update"].id,
            effect="deny",
            scope_kind="person",
            scope_id=target.id,
        )
    )
    db.flush()
    csrf = sign_in(db_client, platform_users, "admin")
    detail = db_client.get(f"/api/v1/people/{target.id}").json()
    assert detail["team_allocation"] == {
        "visibility": "allowed",
        "status": "untracked",
        "version": 1,
        "can_update": False,
    }
    response = db_client.patch(
        f"/api/v1/people/{target.id}/team-allocation",
        json={
            "status": "pending",
            "expected_version": 1,
            "idempotency_key": str(uuid4()),
            "reason": "目标权限已撤销",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 404


def test_internal_detail_preserves_allocation_facts_without_serializing_them(db, platform_users):
    person = db.get(Person, platform_users["people"]["self_student"].id)
    person.team_allocation_status = "pending"
    person.team_allocation_version = 7
    db.flush()
    detail = platform_users["identity_service"].get_person(person.id)
    assert (detail.team_allocation_status, detail.team_allocation_version) == ("pending", 7)
    assert "team_allocation_status" not in detail.model_dump()
