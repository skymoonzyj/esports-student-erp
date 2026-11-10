"""Administration business authority, independent from system role delegation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import UserAccount
from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.team_management.access import TeamAccessService
from app.team_management.allocation import project_team_allocations
from app.team_management.errors import TeamAccessDenied, TeamNotFound, TeamServiceError
from app.team_management.membership_service import TeamMembershipService
from app.team_management.person_staff_models import PersonTeamStaffPosition
from app.team_management.person_staff_query import personnel_staff_states
from app.team_management.person_staff_schemas import PersonStaffAssignInput
from app.team_management.projection import project_person_teams
from app.team_management.schemas import JoinTeamInput, LeaveTeamInput, TransferTeamInput
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_person_staff import LOOKUP, managed_person, services
from tests.services.test_team_access import actor, teams

GRANTS = (
    "professional_teams.manage",
    "team_memberships.read",
    "team_memberships.manage",
    "team_memberships.transfer",
    "team_manager_assignments.manage",
)
ACTIONS = (
    ("read", "team_memberships.read"),
    ("manage", "team_memberships.manage"),
    ("transfer", "team_memberships.transfer"),
    ("assign_managers", "team_manager_assignments.manage"),
)


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def administration(db, users, *, key="customer_service", role=True):
    # Exercise explicit grants independently of the later seed/migration task.
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["administration"].id,
            RolePermission.permission_id.in_([PERMISSIONS[key].id for key in GRANTS]),
        )
    )
    who = actor(users, key)
    if role:
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == who.account.id))
        db.add(UserRole(user_id=who.account.id, role_id=ROLES["administration"].id))
    for permission in GRANTS:
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    db.flush()
    return who


@pytest.mark.parametrize("action,permission", ACTIONS)
def test_administration_global_actions_filter_each_team_and_global_deny(
    db, platform_users, action, permission
):
    who = administration(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    access = TeamAccessService(db)
    assert access.require_team(who, first.id, action).id == first.id
    assert set(db.scalars(access.visible_team_ids(who, action))) == {first.id, second.id}
    deny = UserPermissionOverride(
        user_id=who.account.id,
        permission_id=PERMISSIONS[permission].id,
        effect="deny",
        scope_kind="assigned_team",
        scope_id=first.id,
    )
    db.add(deny)
    db.flush()
    assert set(db.scalars(access.visible_team_ids(who, action))) == {second.id}
    with pytest.raises(TeamNotFound):
        access.require_team(who, first.id, action)
    deny.scope_kind, deny.scope_id = "global", None
    db.flush()
    assert list(db.scalars(access.visible_team_ids(who, action))) == []
    with pytest.raises(TeamNotFound):
        access.require_team(who, second.id, action)


@pytest.mark.parametrize("revoke", ["role", "disabled", "password", "expired"])
def test_administration_live_revocation_closes_context_and_all_actions(db, platform_users, revoke):
    who = administration(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    access = TeamAccessService(db)
    context = access.context(who)
    assert context.can_open_management and context.can_manage_catalog
    assert context.can_transfer and context.can_assign_managers
    if revoke == "role":
        db.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == who.account.id, UserRole.role_id == ROLES["administration"].id
            )
        )
    elif revoke == "disabled":
        who.account.status = "disabled"
    elif revoke == "password":
        who.account.force_password_change = True
    else:
        for grant in db.scalars(
            select(UserPermissionOverride).where(UserPermissionOverride.user_id == who.account.id)
        ):
            grant.valid_from = datetime.now(UTC) - timedelta(days=2)
            grant.valid_until = datetime.now(UTC) - timedelta(days=1)
    db.flush()
    context = access.context(who)
    assert not context.can_open_management
    assert (
        not context.can_manage_catalog
        and not context.can_transfer
        and not context.can_assign_managers
    )
    for action, _ in ACTIONS:
        assert list(db.scalars(access.visible_team_ids(who, action))) == []
        with pytest.raises(TeamServiceError):
            access.require_team(who, first.id, action)


def test_coach_global_overrides_never_become_administration_business_scope(db, platform_users):
    who = administration(db, platform_users, key="coach", role=False)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    access = TeamAccessService(db)
    assert not access.context(who).can_open_management
    for action, _ in ACTIONS:
        with pytest.raises(TeamAccessDenied):
            access.require_team(who, first.id, action)


@pytest.mark.parametrize("action,permission", ACTIONS[:2])
@pytest.mark.parametrize("grant", ["removed", "expired"])
def test_administration_manager_without_global_grant_keeps_real_assignment_scope(
    db, platform_users, action, permission, grant
):
    from app.team_management.models import TeamManagerAssignment

    who = administration(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    db.add(
        TeamManagerAssignment(
            account_id=who.account.id,
            team_id=first.id,
            created_by_user_id=platform_users["accounts"]["admin"].id,
        )
    )
    global_grant = db.scalar(
        select(UserPermissionOverride).where(
            UserPermissionOverride.user_id == who.account.id,
            UserPermissionOverride.permission_id == PERMISSIONS[permission].id,
        )
    )
    if grant == "removed":
        db.delete(global_grant)
    else:
        global_grant.valid_from = datetime.now(UTC) - timedelta(days=2)
        global_grant.valid_until = datetime.now(UTC) - timedelta(days=1)
    db.flush()
    access = TeamAccessService(db)
    assert access.require_team(who, first.id, action).id == first.id
    assert set(db.scalars(access.visible_team_ids(who, action))) == {first.id}
    with pytest.raises(TeamNotFound):
        access.require_team(who, second.id, action)


def test_administration_unassigned_and_assigned_projections_honor_read_denies(db, platform_users):
    who = administration(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    target = actor(platform_users, "self_student")
    db.add(UserRole(user_id=target.account.id, role_id=ROLES["team_member"].id))
    db.flush()
    assert (
        project_person_teams(db, who, [target.person.id])[target.person.id].visibility == "allowed"
    )
    assert (
        project_team_allocations(db, who, [target.person.id])[target.person.id].visibility
        == "allowed"
    )
    TeamMembershipService(db).join(
        who, first.id, JoinTeamInput(person_id=target.person.id, idempotency_key=uuid4())
    )
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        )
    )
    db.flush()
    assert (
        project_person_teams(db, who, [target.person.id])[target.person.id].visibility == "denied"
    )
    assert (
        project_team_allocations(db, who, [target.person.id])[target.person.id].visibility
        == "denied"
    )


@pytest.mark.parametrize("denied_end", ["source", "destination"])
def test_administration_membership_flow_and_transfer_checks_both_ends(
    db, platform_users, denied_end
):
    who = administration(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    target = actor(platform_users, "self_student")
    db.add(UserRole(user_id=target.account.id, role_id=ROLES["team_member"].id))
    db.flush()
    service = TeamMembershipService(db)
    joined = service.join(
        who, first.id, JoinTeamInput(person_id=target.person.id, idempotency_key=uuid4())
    )
    denied = first if denied_end == "source" else second
    deny = UserPermissionOverride(
        user_id=who.account.id,
        permission_id=PERMISSIONS["team_memberships.transfer"].id,
        effect="deny",
        scope_kind="assigned_team",
        scope_id=denied.id,
    )
    db.add(deny)
    db.flush()
    command = TransferTeamInput(
        target_team_id=second.id, reason="行政跨队安排", idempotency_key=uuid4()
    )
    db.commit()
    with pytest.raises(TeamNotFound):
        service.transfer(who, joined.membership_id, command)
    db.delete(deny)
    db.flush()
    moved = service.transfer(who, joined.membership_id, command)
    service.leave(who, moved.membership_id, LeaveTeamInput(idempotency_key=uuid4()))


@pytest.mark.parametrize("revoke", ["deny", "role"])
def test_administration_person_staff_preserves_approved_source_without_account_grants(
    db, platform_users, revoke
):
    result = managed_person(db, platform_users)
    who = administration(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    commands, query = services(db)
    before = set(db.execute(select(UserRole.user_id, UserRole.role_id)))
    options = query.staff_team_options(who)
    assert options.can_assign and first.id in {item.id for item in options.items}
    assigned = commands.assign_person(
        who,
        first.id,
        PersonStaffAssignInput(
            person_id=result.person_id, staff_role="coach", idempotency_key=uuid4()
        ),
    )
    position = db.get(PersonTeamStaffPosition, assigned.assignment_id)
    assert (
        personnel_staff_states(db, lookup=LOOKUP, positions=[position])[
            position.id
        ].authorization_status
        == "valid"
    )
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert set(db.execute(select(UserRole.user_id, UserRole.role_id))) == before
    with pytest.raises(TeamServiceError):
        commands.assign_person(
            who,
            first.id,
            PersonStaffAssignInput(
                person_id=result.person_id, staff_role="manager", idempotency_key=uuid4()
            ),
        )
    if revoke == "deny":
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
                effect="deny",
                scope_kind="assigned_team",
                scope_id=first.id,
            )
        )
    else:
        db.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == who.account.id, UserRole.role_id == ROLES["administration"].id
            )
        )
    db.flush()
    assert first.id not in {item.id for item in query.staff_team_options(who).items}
    assert (
        personnel_staff_states(db, lookup=LOOKUP, positions=[position])[
            position.id
        ].authorization_status
        == "reconfirmation_required"
    )
    with pytest.raises(TeamServiceError):
        query.list(who, first.id, status="current", page=1, page_size=20)


@pytest.mark.parametrize(
    "staff_role,role_key",
    [("manager", "team_manager"), ("coach", "coach"), ("assistant_coach", "assistant_coach")],
)
def test_administration_account_staff_requires_existing_qualifying_role(
    db, platform_users, staff_role, role_key
):
    from app.team_management.schemas import AssignStaffInput
    from app.team_management.staff_service import TeamStaffService

    who = administration(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    target = actor(platform_users, "self_student")
    command = AssignStaffInput(
        account_id=target.account.id, staff_role=staff_role, idempotency_key=uuid4()
    )
    db.commit()
    with pytest.raises(TeamServiceError):
        TeamStaffService(db).assign(who, first.id, command)
    assert (
        db.scalar(
            select(UserRole.user_id).where(
                UserRole.user_id == target.account.id, UserRole.role_id == ROLES[role_key].id
            )
        )
        is None
    )
    db.add(UserRole(user_id=target.account.id, role_id=ROLES[role_key].id))
    db.flush()
    before = set(db.execute(select(UserRole.user_id, UserRole.role_id)))
    assert TeamStaffService(db).assign(who, first.id, command).assignment_id is not None
    assert set(db.execute(select(UserRole.user_id, UserRole.role_id))) == before


@pytest.mark.parametrize("revoke", ["removed", "expired"])
def test_person_staff_evidence_requires_global_grant_despite_residual_team_allow(
    db, platform_users, revoke
):
    from app.rbac.prospective_batch import ProspectiveRoleBatch, ProspectiveRoleCheck
    from app.team_management.person_staff_service import require_staff_manager

    result = managed_person(db, platform_users)
    who = administration(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    assigned = services(db)[0].assign_person(
        who,
        first.id,
        PersonStaffAssignInput(
            person_id=result.person_id, staff_role="coach", idempotency_key=uuid4()
        ),
    )
    position = db.get(PersonTeamStaffPosition, assigned.assignment_id)
    check = ProspectiveRoleCheck(who.account.id, result.person_id, "coach")
    assert ProspectiveRoleBatch(db, [check]).can_assign_team(who.account.id, first.id)
    assert (
        personnel_staff_states(db, lookup=LOOKUP, positions=[position])[
            position.id
        ].authorization_status
        == "valid"
    )
    global_grant = db.scalar(
        select(UserPermissionOverride).where(
            UserPermissionOverride.user_id == who.account.id,
            UserPermissionOverride.permission_id
            == PERMISSIONS["team_manager_assignments.manage"].id,
            UserPermissionOverride.scope_kind == "global",
            UserPermissionOverride.effect == "allow",
        )
    )
    assert global_grant is not None
    if revoke == "removed":
        db.delete(global_grant)
    else:
        global_grant.valid_from = datetime.now(UTC) - timedelta(days=2)
        global_grant.valid_until = datetime.now(UTC) - timedelta(days=1)
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
            effect="allow",
            scope_kind="assigned_team",
            scope_id=first.id,
        )
    )
    db.flush()
    with pytest.raises(TeamNotFound):
        require_staff_manager(db, who, first.id)
    assert (
        personnel_staff_states(db, lookup=LOOKUP, positions=[position])[
            position.id
        ].authorization_status
        == "reconfirmation_required"
    )
    assert not ProspectiveRoleBatch(db, [check]).can_assign_team(who.account.id, first.id)


def test_administration_legacy_manager_assignment_preserves_qualification(db, platform_users):
    from app.team_management.assignment_service import TeamAssignmentService
    from app.team_management.schemas import AssignManagerInput

    who = administration(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    target = actor(platform_users, "coach")
    command = AssignManagerInput(account_id=target.account.id, idempotency_key=uuid4())
    db.commit()
    with pytest.raises(TeamServiceError):
        TeamAssignmentService(db).assign(who, first.id, command)
    db.add(UserRole(user_id=target.account.id, role_id=ROLES["team_manager"].id))
    db.flush()
    assert TeamAssignmentService(db).assign(who, first.id, command).assignment_id is not None
