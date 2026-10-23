from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.identity.models import Person
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.models import TeamManagerAssignment, TeamMembership
from tests.api.test_accounts import sign_in


def roster_fixture(db, platform_users, kind):
    admin = platform_users["accounts"]["admin"]
    account = platform_users["accounts"]["self_student"]
    project = db.scalar(select(GameProject).where(GameProject.is_team_division).limit(1))
    team = ProfessionalTeam(
        project_id=project.id, name=f"生命周期{uuid4().hex}", created_by_user_id=admin.id
    )
    db.add(team)
    db.flush()
    role = "team_member" if kind == "member" else "team_manager"
    db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
    if kind == "member":
        relation = TeamMembership(
            person_id=account.person_id,
            team_id=team.id,
            joined_on=datetime.now(UTC).date(),
            created_by_user_id=admin.id,
        )
    else:
        relation = TeamManagerAssignment(
            account_id=account.id, team_id=team.id, created_by_user_id=admin.id
        )
    db.add(relation)
    db.commit()
    return account, project, team, relation


@pytest.mark.parametrize("kind", ["member", "manager"])
def test_legacy_catalog_http_roster_guards(db_client, platform_users, db, kind):
    _account, project, team, relation = roster_fixture(db, platform_users, kind)
    csrf = sign_in(db_client, platform_users, "admin")
    for path, payload in (
        (f"/api/v1/professional-teams/{team.id}", {"is_active": False}),
        (f"/api/v1/projects/{project.id}", {"is_active": False}),
        (f"/api/v1/projects/{project.id}", {"is_team_division": False}),
    ):
        response = db_client.patch(path, json=payload, headers={"X-CSRF-Token": csrf})
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "team_has_active_relationships"
    db.refresh(project)
    db.refresh(team)
    db.refresh(relation)
    assert team.is_active and project.is_active and project.is_team_division
    assert relation.ended_at is None


def test_member_role_http_conflict_and_noop_audit(db_client, platform_users, db):
    account, _, _, relation = roster_fixture(db, platform_users, "member")
    csrf = sign_in(db_client, platform_users, "admin")
    response = db_client.delete(
        f"/api/v1/accounts/{account.id}/roles/{ROLES['team_member'].id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "team_has_active_relationships"
    assert db.get(UserRole, (account.id, ROLES["team_member"].id)) is not None
    assert db.get(UserRole, (account.id, ROLES["student"].id)) is not None
    noop = db_client.delete(
        f"/api/v1/accounts/{account.id}/roles/{ROLES['team_manager'].id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "team-role-noop"},
    )
    assert noop.status_code == 204
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.request_id == "team-role-noop",
                AuditEvent.action == "account.role.remove",
            )
        )
        == 1
    )
    db.refresh(relation)
    assert relation.ended_at is None


@pytest.mark.parametrize("operation", ["delete", "role"])
def test_lifecycle_http_audit_failure_rolls_back(
    db_client, platform_users, db, monkeypatch, operation
):
    account, _, _, relation = roster_fixture(db, platform_users, "manager")
    csrf = sign_in(db_client, platform_users, "admin")
    account_id, person_id, relation_id = account.id, account.person_id, relation.id

    def fail_audit(*args, **kwargs):
        raise RuntimeError("lifecycle audit failure")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    path = f"/api/v1/accounts/{account_id}"
    if operation == "role":
        path += f"/roles/{ROLES['team_manager'].id}"
    with pytest.raises(RuntimeError, match="lifecycle audit failure"):
        db_client.delete(path, headers={"X-CSRF-Token": csrf})
    db.expire_all()
    assert db.get(UserAccount, account_id).status == "active"
    assert db.get(Person, person_id).deleted_at is None
    assert db.get(UserRole, (account_id, ROLES["team_manager"].id)) is not None
    assert db.get(TeamManagerAssignment, relation_id).ended_at is None


@pytest.mark.parametrize("operation", ["delete", "role"])
def test_lifecycle_http_records_one_success_audit(db_client, platform_users, db, operation):
    account, _, _, relation = roster_fixture(db, platform_users, "manager")
    csrf = sign_in(db_client, platform_users, "admin")
    path = f"/api/v1/accounts/{account.id}"
    if operation == "role":
        path += f"/roles/{ROLES['team_manager'].id}"
    response = db_client.delete(
        path, headers={"X-CSRF-Token": csrf, "X-Request-ID": "lifecycle-success"}
    )
    assert response.status_code == 204, response.text
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.request_id == "lifecycle-success"))
    )
    assert len(events) == 1
    assert events[0].action == (
        "account.delete" if operation == "delete" else "account.role.remove"
    )
    db.refresh(relation)
    assert relation.end_kind == ("account_deleted" if operation == "delete" else "role_removed")


def test_division_flag_http_preserves_legacy_admin_permissions(db_client, platform_users, db):
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/projects", json={"name": "生命周期普通目录"}, headers={"X-CSRF-Token": csrf}
    )
    assert created.status_code == 201, created.text
    assert created.json()["is_team_division"] is False
    project_id = created.json()["id"]
    admin = platform_users["accounts"]["admin"]
    db.delete(db.get(UserRole, (admin.id, ROLES["super_admin"].id)))
    db.add(UserRole(user_id=admin.id, role_id=ROLES["admin"].id))
    db.commit()
    for payload in ({"sort_order": 20}, {"is_team_division": False}):
        response = db_client.patch(
            f"/api/v1/projects/{project_id}", json=payload, headers={"X-CSRF-Token": csrf}
        )
        assert response.status_code == 200, response.text
        assert response.json()["is_team_division"] is False
    denied = db_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"is_team_division": True},
        headers={"X-CSRF-Token": csrf},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "team_division_access_denied"


def test_division_change_audit_failure_rolls_back_flag(db_client, platform_users, db, monkeypatch):
    csrf = sign_in(db_client, platform_users, "admin")
    project = db.scalar(select(GameProject).where(GameProject.is_team_division).limit(1))
    project_id = project.id

    def fail_audit(*args, **kwargs):
        raise AuditMetadataRejected("unavailable")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    response = db_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"is_team_division": False, "is_active": False},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 500
    assert response.json()["code"] == "teaching_audit_failed"
    db.refresh(project)
    assert project.is_team_division and project.is_active
