from uuid import uuid4

import pytest
from sqlalchemy import select

from app.identity.models import StaffProfile
from app.rbac.models import UserRole
from app.rbac.schemas import JobRoleChangeRequest
from app.rbac.seed import ROLES
from app.rbac.service import RbacService
from app.team_management.models import TeamStaffAssignment
from app.team_management.schemas import AssignStaffInput
from app.team_management.staff_service import TeamStaffService
from tests.services.test_team_lifecycle import accounts as lifecycle_accounts
from tests.services.test_team_memberships import make_world


@pytest.fixture(name="world")
def team_world(db):
    return make_world(db)


@pytest.fixture(name="accounts")
def account_service(db):
    return lifecycle_accounts.__wrapped__(db)


def prepare(db, world):
    actors, teams = world
    admin, manager = actors["admin"], actors["manager"]
    for key in ("coach", "assistant_coach"):
        db.add(UserRole(user_id=manager.account.id, role_id=ROLES[key].id))
    db.flush()
    rows = {}
    service = TeamStaffService(db)
    for position, team in (("manager", teams[0]), ("coach", teams[1]),
                           ("assistant_coach", teams[0])):
        result = service.assign(admin, team.id, AssignStaffInput(
            account_id=manager.account.id, staff_role=position, idempotency_key=uuid4(),
        ))
        rows[position] = db.get(TeamStaffAssignment, result.assignment_id)
    return admin, manager, rows


def test_account_role_removal_closes_only_matching_staff_jobs(db, world):
    admin, manager, rows = prepare(db, world)
    RbacService(db).remove_role(
        actor_user_id=admin.account.id, account_id=manager.account.id,
        role_id=ROLES["coach"].id, request_id="staff-role-remove",
    )
    assert rows["coach"].end_kind == "role_removed"
    assert rows["manager"].ended_at is None
    assert rows["assistant_coach"].ended_at is None


def test_effective_permissions_include_staff_read_but_not_legacy_manage(db, world):
    _, manager, _ = prepare(db, world)
    keys = {detail.permission.key for detail in RbacService(db).account_effective_permissions(
        manager.account.id,
    )}
    assert "team_memberships.read" in keys
    assert "team_memberships.manage" not in keys


@pytest.mark.parametrize("keep", [True, False])
def test_job_change_preserves_only_explicit_concurrent_staff_jobs(db, world, keep):
    admin, manager, rows = prepare(db, world)
    current = list(db.scalars(select(UserRole.role_id).where(
        UserRole.user_id == manager.account.id,
    )))
    RbacService(db).change_job_role(
        actor_user_id=admin.account.id, account_id=manager.account.id,
        input=JobRoleChangeRequest(
            from_role_id=ROLES["coach"].id, to_role_id=ROLES["life_teacher"].id,
            keep_concurrent_roles=keep, expected_role_ids=current,
        ), request_id="staff-job-change",
    )
    assert rows["coach"].end_kind == "role_removed"
    for position in ("manager", "assistant_coach"):
        assert (rows[position].ended_at is None) is keep


def test_change_to_assistant_updates_main_staff_type(db, world):
    actors, _ = world
    admin, manager = actors["admin"], actors["manager"]
    current = list(db.scalars(select(UserRole.role_id).where(
        UserRole.user_id == manager.account.id,
    )))
    RbacService(db).change_job_role(
        actor_user_id=admin.account.id, account_id=manager.account.id,
        input=JobRoleChangeRequest(
            from_role_id=ROLES["team_manager"].id, to_role_id=ROLES["assistant_coach"].id,
            keep_concurrent_roles=False, expected_role_ids=current,
        ), request_id="assistant-job-change",
    )
    assert db.get(StaffProfile, manager.person.id).staff_type == "assistant_coach"


def test_account_delete_closes_all_staff_jobs_without_erasing_history(db, world, accounts):
    admin, manager, rows = prepare(db, world)
    accounts.delete_account(
        manager.account.id, actor_user_id=admin.account.id, request_id="staff-account-delete",
    )
    assert all(row.end_kind == "account_deleted" for row in rows.values())
    assert all(row.ended_by_user_id == admin.account.id for row in rows.values())
    assert all(db.get(TeamStaffAssignment, row.id) is not None for row in rows.values())
