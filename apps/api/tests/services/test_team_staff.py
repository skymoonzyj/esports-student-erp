from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.team_management import models, schemas
from tests.api.conftest import platform_users as _api_platform_users
from tests.services.test_team_access import actor, teams


@pytest.fixture(name="platform_users")
def staff_platform_users(db):
    return _api_platform_users.__wrapped__(db)


def test_staff_input_accepts_only_three_readonly_positions():
    input_type = getattr(schemas, "AssignStaffInput", None)
    assert input_type is not None, "read-only staff assignment input is missing"
    for role in ("manager", "coach", "assistant_coach"):
        assert (
            input_type(account_id=uuid4(), staff_role=role, idempotency_key=uuid4()).staff_role
            == role
        )
    with pytest.raises(ValidationError):
        input_type(account_id=uuid4(), staff_role="super_admin", idempotency_key=uuid4())


def test_new_staff_model_does_not_reuse_writable_manager_table():
    model = getattr(models, "TeamStaffAssignment", None)
    assert model is not None, "separate staff relationship is missing"
    assert model.__table__.name == "team_staff_assignments"
    assert model.__table__ is not models.TeamManagerAssignment.__table__


def test_staff_read_scope_is_live_deduplicated_and_never_manage(db, platform_users):
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from app.team_management.access import TeamAccessService
    from app.team_management.errors import TeamAccessDenied, TeamNotFound
    from app.team_management.staff_service import active_staff_team_ids

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    first, second = teams(db, admin.account.id)
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id))
    bindings = [
        models.TeamStaffAssignment(
            account_id=coach.account.id,
            team_id=t.id,
            staff_role=role,
            created_by_user_id=admin.account.id,
        )
        for t, role in ((first, "coach"), (first, "manager"), (second, "coach"))
    ]
    db.add_all(bindings)
    db.flush()
    service = TeamAccessService(db)
    assert set(db.scalars(service.visible_team_ids(coach, "read"))) == {first.id, second.id}
    assert active_staff_team_ids(db, coach.account.id, role_keys={"coach", "team_manager"}) == {
        first.id,
        second.id,
    }
    with pytest.raises(TeamAccessDenied):
        service.require_team(coach, first.id, "manage")
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        )
    )
    db.flush()
    with pytest.raises(TeamNotFound):
        service.require_team(coach, first.id, "read")
    second.is_active = False
    db.flush()
    assert active_staff_team_ids(db, coach.account.id, role_keys={"coach"}) == {first.id}
    coach.account.status = "disabled"
    db.flush()
    assert active_staff_team_ids(db, coach.account.id, role_keys={"coach"}) == set()


@pytest.mark.parametrize(
    "staff_role,role_key",
    [("manager", "team_manager"), ("coach", "coach"), ("assistant_coach", "assistant_coach")],
)
def test_assign_replay_end_and_lifecycle_are_position_specific(
    db, platform_users, staff_role, role_key
):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.errors import TeamAssignmentConflict, TeamOperationConflict
    from app.team_management.lifecycle import TeamLifecycleService
    from app.team_management.staff_service import TeamStaffService, staff_team_ids_for_account

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    if role_key != "coach":
        db.add(UserRole(user_id=coach.account.id, role_id=ROLES[role_key].id))
    first, second = teams(db, admin.account.id)
    db.flush()
    service = TeamStaffService(db)
    payload = schemas.AssignStaffInput(
        account_id=coach.account.id, staff_role=staff_role, idempotency_key=uuid4()
    )
    created = service.assign(admin, first.id, payload)
    from app.identity.models import Person

    assert db.get(Person, coach.person.id).team_allocation_status == "assigned"
    assert service.assign(admin, first.id, payload) == created
    with pytest.raises(TeamOperationConflict):
        service.assign(admin, second.id, payload)
    with pytest.raises(TeamAssignmentConflict):
        service.assign(admin, first.id, payload.model_copy(update={"idempotency_key": uuid4()}))
    other = service.assign(
        admin, second.id, payload.model_copy(update={"idempotency_key": uuid4()})
    )
    assert staff_team_ids_for_account(db, coach.account.id, role_keys={role_key}) == {
        first.id,
        second.id,
    }
    end = schemas.UnassignStaffInput(idempotency_key=uuid4())
    assert (
        service.unassign(admin, created.assignment_id, end).assignment_id == created.assignment_id
    )
    assert (
        service.unassign(admin, created.assignment_id, end).assignment_id == created.assignment_id
    )
    assert db.get(models.TeamStaffAssignment, other.assignment_id).ended_at is None
    TeamLifecycleService(db).before_role_removal(
        coach.account.id, role_key, admin.account.id, datetime.now(UTC)
    )
    assert db.get(models.TeamStaffAssignment, other.assignment_id).end_kind == "role_removed"
    assert staff_team_ids_for_account(db, coach.account.id) == set()


def test_staff_bindings_block_directory_changes(db, platform_users):
    from app.team_management.errors import TeamHasActiveRelationships
    from app.team_management.lifecycle import TeamLifecycleService

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    first, _ = teams(db, admin.account.id)
    db.add(
        models.TeamStaffAssignment(
            account_id=coach.account.id,
            team_id=first.id,
            staff_role="coach",
            created_by_user_id=admin.account.id,
        )
    )
    db.flush()
    with pytest.raises(TeamHasActiveRelationships):
        TeamLifecycleService(db).assert_team_clear(first.id)
    with pytest.raises(TeamHasActiveRelationships):
        TeamLifecycleService(db).assert_project_clear(first.project_id)


def test_operation_lifecycle_closes_only_removed_position_and_syncs_allocation(db, platform_users):
    from app.identity.models import Person
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.staff_service import (
        TeamStaffService,
        close_staff_assignments_for_roles,
    )

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["assistant_coach"].id))
    first, _ = teams(db, admin.account.id)
    db.flush()
    service = TeamStaffService(db)
    created = [
        service.assign(
            admin,
            first.id,
            schemas.AssignStaffInput(
                account_id=coach.account.id, staff_role=role, idempotency_key=uuid4()
            ),
        )
        for role in ("coach", "assistant_coach")
    ]
    close_staff_assignments_for_roles(
        db,
        coach.account.id,
        {"coach"},
        operation_id=created[0].operation_id,
        occurred_at=datetime.now(UTC),
    )
    assert db.get(models.TeamStaffAssignment, created[0].assignment_id).end_kind == "role_removed"
    assert db.get(models.TeamStaffAssignment, created[1].assignment_id).ended_at is None
    assert db.get(Person, coach.person.id).team_allocation_status == "assigned"
    close_staff_assignments_for_roles(
        db,
        coach.account.id,
        {"assistant_coach"},
        operation_id=created[1].operation_id,
        occurred_at=datetime.now(UTC),
    )
    assert db.get(Person, coach.person.id).team_allocation_status == "pending"


@pytest.mark.parametrize(
    "invalid", ["disabled", "password", "deleted", "team", "division", "role", "ended"]
)
def test_staff_scope_revokes_immediately_on_live_state_change(db, platform_users, invalid):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.teaching.models import GameProject
    from app.team_management.staff_service import active_staff_team_ids

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    first, _ = teams(db, admin.account.id)
    row = models.TeamStaffAssignment(
        account_id=coach.account.id,
        team_id=first.id,
        staff_role="coach",
        created_by_user_id=admin.account.id,
    )
    db.add(row)
    db.flush()
    assert active_staff_team_ids(db, coach.account.id, role_keys={"coach"}) == {first.id}
    if invalid == "disabled":
        coach.account.status = "disabled"
    elif invalid == "password":
        coach.account.force_password_change = True
    elif invalid == "deleted":
        from app.identity.models import Person

        db.get(Person, coach.person.id).deleted_at = datetime.now(UTC)
    elif invalid == "team":
        first.is_active = False
    elif invalid == "division":
        db.get(GameProject, first.project_id).is_team_division = False
    elif invalid == "role":
        db.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == coach.account.id, UserRole.role_id == ROLES["coach"].id
            )
        )
    else:
        row.ended_at, row.end_kind = datetime.now(UTC), "unassigned"
    db.flush()
    assert active_staff_team_ids(db, coach.account.id, role_keys={"coach"}) == set()


def test_staff_idempotent_replay_still_requires_live_actor_authority(db, platform_users):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    from app.team_management.errors import TeamNotFound
    from app.team_management.staff_service import TeamStaffService

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    first, _ = teams(db, admin.account.id)
    service = TeamStaffService(db)
    payload = schemas.AssignStaffInput(
        account_id=coach.account.id, staff_role="coach", idempotency_key=uuid4()
    )
    service.assign(admin, first.id, payload)
    db.add(
        UserPermissionOverride(
            user_id=admin.account.id,
            permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    with pytest.raises(TeamNotFound):
        service.assign(admin, first.id, payload)


def test_superadmin_can_read_ended_staff_history_after_division_cancelled(db, platform_users):
    from app.teaching.models import GameProject
    from app.team_management.query_service import TeamQueryService

    admin, coach = actor(platform_users, "admin"), actor(platform_users, "coach")
    first, _ = teams(db, admin.account.id)
    db.add(
        models.TeamStaffAssignment(
            account_id=coach.account.id,
            team_id=first.id,
            staff_role="coach",
            created_by_user_id=admin.account.id,
            created_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            end_kind="unassigned",
        )
    )
    db.get(GameProject, first.project_id).is_team_division = False
    db.flush()
    history = TeamQueryService(db).list_staff_assignments(
        admin, first.id, status="ended", page=1, page_size=20
    )
    assert history.total == 1
