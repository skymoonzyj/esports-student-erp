from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.identity.assignments.models import PersonProjectAssignment
from app.identity.models import StaffProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.models import TeamMembership


def _roles(db, account):
    return set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account.id)))


def _login(client, users, key="admin"):
    identifier, password, _ = users["credentials"][key]
    result = client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json()["csrf_token"]}


def _payload(db, account, old="customer_service", new="finance", keep=True):
    return {"from_role_id": str(ROLES[old].id) if old else None,
            "to_role_id": str(ROLES[new].id), "keep_concurrent_roles": keep,
            "expected_role_ids": [str(value) for value in _roles(db, account)]}


@pytest.mark.parametrize("keep,staff_type", [(True, "coach"), (False, "other")])
def test_job_change_preserves_identities_overrides_and_syncs_final_coach(
    db_client, platform_users, db: Session, keep, staff_type,
):
    target = platform_users["accounts"]["customer_service"]
    for key in ("coach", "student", "guardian", "registered_user", "team_member"):
        db.add(UserRole(user_id=target.id, role_id=ROLES[key].id))
    override = UserPermissionOverride(user_id=target.id,
        permission_id=PERMISSIONS["people.read"].id, effect="deny", scope_kind="global")
    db.add(override)
    project = db.scalar(select(GameProject).where(GameProject.is_team_division).limit(1))
    team = ProfessionalTeam(project_id=project.id, name=f"岗位保留归属{uuid4().hex}",
                            created_by_user_id=platform_users["accounts"]["admin"].id)
    db.add(team)
    db.flush()
    membership = TeamMembership(person_id=target.person_id, team_id=team.id,
                                joined_on=date(2026, 1, 1),
                                created_by_user_id=platform_users["accounts"]["admin"].id)
    project_assignment = PersonProjectAssignment(person_id=target.person_id, project_id=project.id,
                                created_by_user_id=platform_users["accounts"]["admin"].id)
    db.add_all([membership, project_assignment])
    db.commit()
    original = _roles(db, target)
    override_id = override.id
    headers = _login(db_client, platform_users)
    response = db_client.post(f"/api/v1/accounts/{target.id}/job-role-change",
                             json=_payload(db, target, keep=keep), headers=headers)
    assert response.status_code == 200, response.text
    removed = {ROLES["customer_service"].id}
    if not keep:
        removed.add(ROLES["coach"].id)
    expected = (original - removed) | {ROLES["finance"].id}
    db.expire_all()
    assert _roles(db, target) == expected
    assert db.get(UserPermissionOverride, override_id) is not None
    assert db.get(UserPermissionOverride, override_id).effect == "deny"
    db.refresh(membership)
    db.refresh(project_assignment)
    assert membership.ended_at is None and membership.team_id == team.id
    assert project_assignment.ended_at is None and project_assignment.project_id == project.id
    assert db.get(StaffProfile, target.person_id).staff_type == staff_type
    assert (CoachEligibility(db).ineligibility_reason(target.person_id) is None) is keep
    body = response.json()
    assert set(body["removed_role_ids"]) == {str(value) for value in removed}
    assert body["added_role_ids"] == [str(ROLES["finance"].id)]
    assert set(body["retained_role_ids"]) == {str(value) for value in original - removed}
    assert {role["id"] for role in body["roles"]} == {str(value) for value in expected}
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "account.job_role.change",
                                               AuditEvent.resource_id == target.id))
    assert event is not None
    assert event.event_metadata["keep_concurrent_roles"] is keep
    assert "customer_service" in event.event_metadata["old_role_keys"]
    assert "finance" in event.event_metadata["new_role_keys"]


def test_job_change_to_existing_role_merges_and_null_from_creates_staff_profile(
    db_client, platform_users, db,
):
    target = platform_users["accounts"]["self_student"]
    headers = _login(db_client, platform_users)
    response = db_client.post(f"/api/v1/accounts/{target.id}/job-role-change",
        json=_payload(db, target, old=None, new="coach", keep=False), headers=headers)
    assert response.status_code == 200, response.text
    assert db.get(StaffProfile, target.person_id).staff_type == "coach"
    db.add(UserRole(user_id=target.id, role_id=ROLES["finance"].id))
    db.commit()
    response = db_client.post(f"/api/v1/accounts/{target.id}/job-role-change",
        json=_payload(db, target, old="coach", new="finance", keep=False), headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["added_role_ids"] == []
    assert db.get(StaffProfile, target.person_id).staff_type == "other"


@pytest.mark.parametrize("kind,status", [
    ("stale", 409), ("nonstaff", 409), ("same", 409), ("null", 409),
    ("wrong_old", 409), ("missing_role", 404), ("missing_account", 404),
    ("unauthorized", 403), ("delegation", 403), ("self_super", 409),
    ("last_super", 409), ("target_deny", 403),
])
def test_job_change_rejects_invalid_or_unauthorized_without_effects(
    db_client, platform_users, db, kind, status,
):
    target = platform_users["accounts"]["customer_service"]
    actor_key = "admin"
    if kind == "unauthorized":
        actor_key = "coach"
    if kind == "delegation":
        actor_key = "coach"
        db.add(UserPermissionOverride(user_id=platform_users["accounts"][actor_key].id,
            permission_id=PERMISSIONS["permissions.manage"].id, effect="allow", scope_kind="global"))
    if kind in {"self_super", "last_super"}:
        target = platform_users["accounts"]["admin"]
    if kind == "last_super":
        actor_key = "customer_service"
        actor = platform_users["accounts"][actor_key]
        db.add(UserRole(user_id=actor.id, role_id=ROLES["super_admin"].id))
        db.add(UserPermissionOverride(user_id=actor.id,
            permission_id=PERMISSIONS["accounts.manage"].id, effect="deny", scope_kind="global"))
    if kind == "target_deny":
        db.add(UserPermissionOverride(user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["permissions.manage"].id, effect="deny",
            scope_kind="person", scope_id=target.person_id))
    db.commit()
    payload = _payload(db, target, old="super_admin" if kind in {"self_super", "last_super"}
                       else "customer_service", new="admin" if kind == "delegation" else "finance")
    if kind == "stale":
        payload["expected_role_ids"] = []
    if kind == "nonstaff":
        payload["to_role_id"] = str(ROLES["student"].id)
    if kind == "same":
        payload["to_role_id"] = payload["from_role_id"]
    if kind == "null":
        payload["from_role_id"] = None
    if kind == "wrong_old":
        payload["from_role_id"] = str(ROLES["coach"].id)
    if kind == "missing_role":
        payload["to_role_id"] = str(uuid4())
    original = _roles(db, target)
    profile = db.get(StaffProfile, target.person_id)
    previous_type = profile.staff_type
    headers = _login(db_client, platform_users, actor_key)
    account_id = uuid4() if kind == "missing_account" else target.id
    response = db_client.post(f"/api/v1/accounts/{account_id}/job-role-change", json=payload, headers=headers)
    assert response.status_code == status, response.text
    db.expire_all()
    assert _roles(db, target) == original
    assert db.get(StaffProfile, target.person_id).staff_type == previous_type
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.action == "account.job_role.change",
                                               AuditEvent.resource_id == target.id)) is None


def test_job_change_failure_rolls_back_roles_and_staff_profile(platform_users, db, monkeypatch):
    from app.rbac.schemas import JobRoleChangeRequest
    from app.rbac.service import RbacService

    target = platform_users["accounts"]["customer_service"]
    previous = _roles(db, target)
    original_append = AuditService.append

    def fail_audit(self, **kwargs):
        if kwargs["action"] == "account.job_role.change":
            raise RuntimeError("synthetic audit failure")
        return original_append(self, **kwargs)

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        RbacService(db).change_job_role(actor_user_id=platform_users["accounts"]["admin"].id,
            account_id=target.id, input=JobRoleChangeRequest(**_payload(db, target)), request_id="test")
    db.expire_all()
    assert _roles(db, target) == previous
    assert db.get(StaffProfile, target.person_id).staff_type == "customer_service"


@pytest.mark.parametrize("fail", [False, True])
def test_job_change_runs_team_manager_lifecycle_atomically(platform_users, db, monkeypatch, fail):
    from app.rbac.schemas import JobRoleChangeRequest
    from app.rbac.service import RbacService
    from tests.api.test_team_lifecycle import roster_fixture

    target, _, _, relation = roster_fixture(db, platform_users, "manager")
    original = _roles(db, target)
    original_append = AuditService.append

    def append(self, **kwargs):
        if fail and kwargs["action"] == "account.job_role.change":
            raise RuntimeError("synthetic lifecycle audit failure")
        return original_append(self, **kwargs)

    monkeypatch.setattr(AuditService, "append", append)

    def change():
        return RbacService(db).change_job_role(
            actor_user_id=platform_users["accounts"]["admin"].id, account_id=target.id,
            input=JobRoleChangeRequest(**_payload(db, target, old="team_manager")), request_id="test")

    if fail:
        with pytest.raises(RuntimeError, match="synthetic lifecycle audit failure"):
            change()
    else:
        change()
    db.refresh(relation)
    assert (relation.ended_at is None) is fail
    if fail:
        assert _roles(db, target) == original
        assert db.get(StaffProfile, target.person_id) is None
    else:
        assert relation.end_kind == "role_removed"
        assert _roles(db, target) == {ROLES["student"].id, ROLES["finance"].id}


def test_job_change_never_activates_disabled_account(db_client, platform_users, db):
    target = platform_users["accounts"]["customer_service"]
    target.status = "disabled"
    target.force_password_change = True
    db.commit()
    original_password = target.password_hash
    headers = _login(db_client, platform_users)
    response = db_client.post(f"/api/v1/accounts/{target.id}/job-role-change",
                             json=_payload(db, target), headers=headers)
    assert response.status_code == 200, response.text
    db.refresh(target)
    assert target.status == "disabled"
    assert target.force_password_change is True
    assert target.password_hash == original_password
