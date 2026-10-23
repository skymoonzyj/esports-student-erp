from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AccountService
from app.identity.models import Person
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.errors import TeachingServiceError
from app.teaching.models import GameProject
from app.teaching.schemas import ProfessionalTeamUpdate, ProjectCreate, ProjectUpdate
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.errors import TeamHasActiveRelationships
from app.team_management.lifecycle import TeamLifecycleService
from app.team_management.membership_service import TeamMembershipService
from app.team_management.models import TeamManagerAssignment, TeamMembership, TeamOperation
from app.team_management.schemas import AssignManagerInput, LeaveTeamInput
from tests.services.test_team_memberships import join, make_world


@pytest.fixture
def world(db):
    return make_world(db)


@pytest.fixture
def accounts(db):
    return AccountService(
        db,
        identity_service=IdentityService(
            db,
            cipher=EnvelopeCipher(bytes(range(32))),
            lookup=LookupDigest(bytes(range(32))),
        ),
        passwords=PasswordService(),
    )


def bind(db, admin, manager, team):
    result = TeamAssignmentService(db).assign(
        admin,
        team.id,
        AssignManagerInput(account_id=manager.account.id, idempotency_key=uuid4()),
    )
    return db.get(TeamManagerAssignment, result.assignment_id)


def remove_role(db, admin, member, role, request_id="remove-role"):
    return RbacService(db).remove_role(
        actor_user_id=admin.account.id,
        account_id=member.account.id,
        role_id=ROLES[role].id,
        request_id=request_id,
    )


def test_disable_delete_and_role_removal_have_distinct_effects(db, world, accounts):
    actors, teams = world
    admin, member = actors["admin"], actors["member"]
    RbacService(db).assign_role(account_id=member.account.id, role_id=ROLES["team_manager"].id)
    membership_id = join(db, admin, teams[0], member).membership_id
    assignment = bind(db, admin, member, teams[1])
    login_session = AuthSession(
        user_id=member.account.id,
        token_digest=uuid4().hex + uuid4().hex,
        csrf_digest=uuid4().hex + uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(login_session)
    db.commit()
    accounts.disable_account(
        member.account.id, actor_user_id=admin.account.id, request_id="disable"
    )
    assert db.get(TeamMembership, membership_id).ended_at is None
    assert assignment.ended_at is None
    db.refresh(login_session)
    assert login_session.revoked_at is not None
    accounts.enable_account(member.account.id, actor_user_id=admin.account.id)
    assert db.get(TeamMembership, membership_id).ended_at is None
    assert assignment.ended_at is None
    ledger_count = db.scalar(select(func.count()).select_from(TeamOperation))
    accounts.delete_account(member.account.id, actor_user_id=admin.account.id, request_id="delete")
    old = db.get(TeamMembership, membership_id)
    db.refresh(assignment)
    assert old.end_kind == assignment.end_kind == "account_deleted"
    assert old.ended_at == assignment.ended_at == member.person.deleted_at
    assert old.left_on is not None
    assert old.close_operation_id is assignment.close_operation_id is None
    assert old.ended_by_user_id == assignment.ended_by_user_id == admin.account.id
    assert db.scalar(select(func.count()).select_from(TeamOperation)) == ledger_count
    event = db.scalar(select(AuditEvent).where(AuditEvent.request_id == "delete"))
    assert event.action == "account.delete" and event.resource_id == member.account.id
    assert event.event_metadata == {}


def test_member_role_removal_blocks_current_membership_but_keeps_student_role(db, world):
    actors, teams = world
    admin, member = actors["admin"], actors["member"]
    RbacService(db).assign_role(account_id=member.account.id, role_id=ROLES["student"].id)
    membership_id = join(db, admin, teams[0], member).membership_id
    with pytest.raises(TeamHasActiveRelationships):
        remove_role(db, admin, member, "team_member")
    assert db.get(UserRole, (member.account.id, ROLES["team_member"].id)) is not None
    assert db.get(TeamMembership, membership_id).ended_at is None
    TeamMembershipService(db).leave(admin, membership_id, LeaveTeamInput(idempotency_key=uuid4()))
    remove_role(db, admin, member, "team_member")
    assert db.get(UserRole, (member.account.id, ROLES["team_member"].id)) is None
    assert db.get(UserRole, (member.account.id, ROLES["student"].id)) is not None


def test_manager_role_removal_closes_all_bindings_and_noop_still_audits(db, world):
    actors, teams = world
    admin, manager = actors["admin"], actors["manager"]
    assignments = [bind(db, admin, manager, team) for team in teams]
    remove_role(db, admin, manager, "team_manager", "manager-remove")
    for assignment in assignments:
        db.refresh(assignment)
        assert assignment.end_kind == "role_removed" and assignment.close_operation_id is None
    remove_role(db, admin, manager, "team_manager", "manager-noop")
    events = list(
        db.scalars(
            select(AuditEvent).where(AuditEvent.request_id.in_(["manager-remove", "manager-noop"]))
        )
    )
    assert len(events) == 2
    assert all(event.action == "account.role.remove" for event in events)
    RbacService(db).assign_role(account_id=manager.account.id, role_id=ROLES["team_manager"].id)
    assert (
        db.scalar(
            select(func.count())
            .select_from(TeamManagerAssignment)
            .where(TeamManagerAssignment.ended_at.is_(None))
        )
        == 0
    )


@pytest.mark.parametrize("operation", ["delete", "role"])
def test_lifecycle_service_audit_failure_rolls_back_all_changes(
    db, world, accounts, monkeypatch, operation
):
    actors, teams = world
    admin, manager = actors["admin"], actors["manager"]
    RbacService(db).assign_role(account_id=manager.account.id, role_id=ROLES["team_member"].id)
    membership_id = join(db, admin, teams[0], manager).membership_id
    assignments = [bind(db, admin, manager, team) for team in teams]
    account_id, person_id = manager.account.id, manager.person.id
    db.commit()

    def fail_audit(*args, **kwargs):
        raise RuntimeError("lifecycle audit unavailable")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="lifecycle audit unavailable"):
        if operation == "delete":
            accounts.delete_account(
                account_id, actor_user_id=admin.account.id, request_id="failure"
            )
        else:
            remove_role(db, admin, manager, "team_manager", "failure")
    db.expire_all()
    assert db.get(UserAccount, account_id).status == "active"
    assert db.get(Person, person_id).deleted_at is None
    assert db.get(UserRole, (account_id, ROLES["team_manager"].id)) is not None
    assert db.get(TeamMembership, membership_id).ended_at is None
    assert all(assignment.ended_at is None for assignment in assignments)
    assert (
        db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.request_id == "failure")
        )
        == 0
    )


@pytest.mark.parametrize("relation", ["member", "manager"])
def test_legacy_catalog_cannot_bypass_roster_guard(db, world, relation):
    actors, teams = world
    admin, team = actors["admin"], teams[0]
    if relation == "member":
        join(db, admin, team, actors["member"])
    else:
        bind(db, admin, actors["manager"], team)
    catalog = TeachingCatalogService(db)
    for action in ("team", "project", "division"):
        with pytest.raises(TeamHasActiveRelationships):
            if action == "team":
                catalog.update_team(
                    team.id, ProfessionalTeamUpdate(is_active=False), actor_user_id=admin.account.id
                )
            else:
                catalog.update_project(
                    team.project_id,
                    ProjectUpdate(
                        **{"is_active" if action == "project" else "is_team_division": False}
                    ),
                    actor_user_id=admin.account.id,
                )
        db.refresh(team)
        project = db.get(GameProject, team.project_id)
        db.refresh(project)
        assert team.is_active and project.is_active and project.is_team_division
    with pytest.raises(TeachingServiceError) as exc:
        catalog.update_team(
            team.id,
            ProfessionalTeamUpdate(project_id=teams[1].project_id),
            actor_user_id=admin.account.id,
        )
    assert exc.value.code == "team_project_immutable"


def test_division_flag_changes_require_actual_superadmin_and_respect_deny(db, world):
    actors, teams = world
    catalog = TeachingCatalogService(db)
    admin, ordinary = actors["admin"], actors["member2"]
    RbacService(db).assign_role(account_id=ordinary.account.id, role_id=ROLES["admin"].id)
    created = catalog.create_project(
        ProjectCreate(name=f"普通项目{uuid4().hex}"), actor_user_id=ordinary.account.id
    )
    assert created.is_team_division is False
    project_id = teams[0].project_id
    for payload in ({"sort_order": 50}, {"is_team_division": True}):
        project = catalog.update_project(
            project_id, ProjectUpdate(**payload), actor_user_id=ordinary.account.id
        )
        assert project.is_team_division
    for value in (False,):
        with pytest.raises(TeachingServiceError) as exc:
            catalog.update_project(
                project_id, ProjectUpdate(is_team_division=value), actor_user_id=ordinary.account.id
            )
        assert exc.value.code == "team_division_access_denied"
    with pytest.raises(TeachingServiceError):
        catalog.create_project(
            ProjectCreate(name=f"普通分部{uuid4().hex}", is_team_division=True),
            actor_user_id=ordinary.account.id,
        )
    db.add(
        UserPermissionOverride(
            user_id=admin.account.id,
            permission_id=PERMISSIONS["projects.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.commit()
    with pytest.raises(TeachingServiceError):
        catalog.update_project(
            project_id, ProjectUpdate(is_team_division=False), actor_user_id=admin.account.id
        )
    assert db.get(GameProject, project_id).is_team_division


def test_lifecycle_closure_uses_supplied_business_timestamp_without_operation(db, world):
    actors, teams = world
    admin, member = actors["admin"], actors["member"]
    created_at = datetime(2026, 7, 1, tzinfo=UTC)
    relation = TeamMembership(
        person_id=member.person.id,
        team_id=teams[0].id,
        joined_on=date(2026, 7, 1),
        created_at=created_at,
        created_by_user_id=admin.account.id,
    )
    db.add(relation)
    db.flush()
    # This internal helper joins a transaction; the caller owns identity/directory locks.
    from app.team_management.write_support import lock_directory, lock_participants

    lock_participants(db, [member.person.id], [member.account.id])
    lock_directory(db, [teams[0].id])
    TeamLifecycleService(db).close_for_deleted_account(
        member.account.id, None, datetime(2026, 7, 26, 16, 0, tzinfo=UTC)
    )
    assert relation.left_on == date(2026, 7, 27)
    assert relation.ended_by_user_id is None
    assert relation.close_operation_id is None


def test_internal_delete_preserves_history_and_allows_cleared_catalog(db, world, accounts):
    actors, teams = world
    admin, member = actors["admin"], actors["member"]
    membership_id = join(db, admin, teams[0], member).membership_id
    accounts.delete_account(member.account.id, actor_user_id=None, request_id="internal-delete")
    relation = db.get(TeamMembership, membership_id)
    assert relation.end_kind == "account_deleted" and relation.ended_by_user_id is None
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == "internal-delete"))
    assert audit.actor_user_id is None and audit.action == "account.delete"
    catalog = TeachingCatalogService(db)
    catalog.update_team(
        teams[0].id, ProfessionalTeamUpdate(is_active=False), actor_user_id=admin.account.id
    )
    project = catalog.update_project(
        teams[0].project_id,
        ProjectUpdate(is_active=False, is_team_division=False),
        actor_user_id=admin.account.id,
    )
    assert project.is_active is False and project.is_team_division is False
    assert db.get(TeamMembership, membership_id) is not None
    created = catalog.create_project(
        ProjectCreate(name=f"新分部{uuid4().hex}", is_team_division=True),
        actor_user_id=admin.account.id,
    )
    assert created.is_team_division is True
