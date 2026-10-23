from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.errors import TeamServiceError
from app.team_management.membership_service import TeamMembershipService
from app.team_management.schemas import AssignManagerInput, LeaveTeamInput, UnassignManagerInput
from tests.services.test_team_memberships import join, make_world


def allocation_service(db):
    from app.team_management.allocation import TeamAllocationService

    return TeamAllocationService(db)


def change(status="pending", version=1, key=None):
    from app.team_management.schemas import TeamAllocationInput

    return TeamAllocationInput(
        status=status, expected_version=version, idempotency_key=key or uuid4(), reason="组织划分"
    )


def test_allocation_is_explicit_not_inferred_from_missing_team(db):
    from app.team_management.allocation import project_team_allocations

    actors, _ = make_world(db)
    old = actors["member"].person
    imported = actors["member2"].person
    imported.team_allocation_status = "pending"
    db.flush()
    projected = project_team_allocations(db, actors["admin"], [old.id, imported.id])
    assert projected[old.id].status == "untracked"
    assert projected[imported.id].status == "pending"
    invisible = project_team_allocations(db, actors["manager"], [imported.id])[imported.id]
    assert invisible.model_dump() == {
        "visibility": "denied",
        "status": None,
        "version": None,
        "can_update": False,
    }
    assert old.team_allocation_status == "untracked"


def test_explicit_update_versions_replay_and_revocation(db):
    actors, _ = make_world(db)
    svc = allocation_service(db)
    target = actors["member"].person
    request = change("not_required")
    result = svc.update(actors["admin"], target.id, request)
    assert (result.status, result.version) == ("not_required", 2)
    event = db.scalar(
        select(AuditEvent).where(AuditEvent.request_id == str(request.idempotency_key))
    )
    assert event.event_metadata == {
        "person_id": str(target.id),
        "allocation_old_status": "untracked",
        "allocation_new_status": "not_required",
        "old_version": 1,
        "new_version": 2,
    }
    events = db.scalar(select(func.count()).select_from(AuditEvent))
    assert svc.update(actors["admin"], target.id, request) == result
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == events
    with pytest.raises(TeamServiceError) as stale:
        svc.update(actors["admin2"], target.id, change())
    assert stale.value.status == 409
    db.add(
        UserPermissionOverride(
            user_id=actors["admin"].account.id,
            permission_id=PERMISSIONS["people.update"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    with pytest.raises(TeamServiceError) as denied:
        svc.update(actors["admin"], target.id, request)
    assert denied.value.status == 404


def test_membership_and_manager_changes_sync_real_relationships(db):
    actors, teams = make_world(db)
    member = actors["member"].person
    member.team_allocation_status = "pending"
    db.flush()
    joined = join(db, actors["admin"], teams[0], actors["member"])
    assert member.team_allocation_status == "assigned"
    with pytest.raises(TeamServiceError) as conflict:
        allocation_service(db).update(
            actors["admin"], member.id, change(version=member.team_allocation_version)
        )
    assert conflict.value.status == 409
    TeamMembershipService(db).leave(
        actors["admin"], joined.membership_id, LeaveTeamInput(idempotency_key=uuid4())
    )
    assert member.team_allocation_status == "pending"
    manager = actors["manager"]
    first = TeamAssignmentService(db).assign(
        actors["admin"],
        teams[0].id,
        AssignManagerInput(account_id=manager.account.id, idempotency_key=uuid4()),
    )
    second = TeamAssignmentService(db).assign(
        actors["admin"],
        teams[1].id,
        AssignManagerInput(account_id=manager.account.id, idempotency_key=uuid4()),
    )
    assert manager.person.team_allocation_status == "assigned"
    TeamAssignmentService(db).unassign(
        actors["admin"], first.assignment_id, UnassignManagerInput(idempotency_key=uuid4())
    )
    assert manager.person.team_allocation_status == "assigned"
    TeamAssignmentService(db).unassign(
        actors["admin"], second.assignment_id, UnassignManagerInput(idempotency_key=uuid4())
    )
    assert manager.person.team_allocation_status == "pending"


def test_scoped_manager_cannot_explicitly_mark_unassigned_people(db):
    actors, _ = make_world(db)
    with pytest.raises(TeamServiceError) as denied:
        allocation_service(db).update(actors["manager"], actors["member"].person.id, change())
    assert denied.value.status == 404


def test_relationship_failure_rolls_allocation_back(db, monkeypatch):
    from app.audit.service import AuditService

    actors, teams = make_world(db)
    target = actors["member"].person
    target.team_allocation_status = "pending"
    db.commit()

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditService, "append", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        join(db, actors["admin"], teams[0], actors["member"])
    db.refresh(target)
    assert (target.team_allocation_status, target.team_allocation_version) == ("pending", 1)


def test_removed_admin_role_denies_old_context(db):
    actors, _ = make_world(db)
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == actors["admin"].account.id))
    db.flush()
    with pytest.raises(TeamServiceError) as denied:
        allocation_service(db).update(actors["admin"], actors["member"].person.id, change())
    assert denied.value.status == 404


def test_local_projection_does_not_expose_hidden_relationship(db):
    from app.team_management.allocation import project_team_allocations
    from app.team_management.models import TeamManagerAssignment

    actors, teams = make_world(db)
    visible = actors["member"].person
    hidden = actors["member2"].person
    join(db, actors["admin"], teams[0], actors["member"])
    join(db, actors["admin"], teams[1], actors["member2"])
    db.add(
        TeamManagerAssignment(
            account_id=actors["manager"].account.id,
            team_id=teams[0].id,
            created_by_user_id=actors["admin"].account.id,
        )
    )
    db.flush()
    projection = project_team_allocations(db, actors["manager"], [visible.id, hidden.id])
    assert projection[visible.id].status == "assigned"
    assert projection[hidden.id].status is None


def test_explicit_current_team_deny_does_not_become_zero_count_permission(db):
    from app.team_management.allocation import allocation_read_scope, project_team_allocations

    actors, teams = make_world(db)
    member = actors["member"]
    join(db, actors["admin"], teams[0], member)
    db.add(
        UserPermissionOverride(
            user_id=member.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=teams[0].id,
        )
    )
    db.flush()
    assert project_team_allocations(db, member, [member.person.id])[member.person.id].status is None
    _, enabled = allocation_read_scope(db, member)
    assert enabled is False


def test_superadmin_global_scope_cannot_bypass_explicit_self_projection_deny(db):
    from app.team_management.allocation import project_team_allocations
    from app.team_management.projection import project_person_teams

    actors, _ = make_world(db)
    admin = actors["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="self",
        )
    )
    db.flush()
    assert (
        project_person_teams(db, admin, [admin.person.id])[admin.person.id].visibility == "denied"
    )
    assert (
        project_team_allocations(db, admin, [admin.person.id])[admin.person.id].visibility
        == "denied"
    )


def test_other_staff_allocation_never_grants_membership_role(db):
    from app.identity.models import StaffProfile

    actors, teams = make_world(db)
    target = actors["member"]
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == target.account.id))
    db.add(StaffProfile(person_id=target.person.id, staff_type="other"))
    db.flush()
    allocation_service(db).update(actors["admin"], target.person.id, change())
    with pytest.raises(TeamServiceError) as ineligible:
        join(db, actors["admin"], teams[0], target)
    assert ineligible.value.code == "team_member_ineligible"
    assert (
        db.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.user_id == target.account.id)
        )
        == 0
    )
    assert target.person.team_allocation_status == "pending"


def test_account_deletion_closes_last_relationship_and_returns_to_pending(db):
    from datetime import UTC, datetime

    from app.team_management.lifecycle import TeamLifecycleService

    actors, teams = make_world(db)
    join(db, actors["admin"], teams[0], actors["member"])
    TeamLifecycleService(db).close_for_deleted_account(
        actors["member"].account.id, actors["admin"].account.id, datetime.now(UTC)
    )
    assert actors["member"].person.team_allocation_status == "pending"


@pytest.fixture
def committed_allocation_world(postgres_engine):
    from tests.db.test_team_concurrency import committed_world

    yield from committed_world.__wrapped__(postgres_engine)


@pytest.mark.parametrize("same_key", [False, True])
def test_parallel_allocation_versions_and_identical_replay(
    postgres_engine, committed_allocation_world, record_property, same_key
):
    from sqlalchemy.orm import Session

    from tests.db.test_team_concurrency import race

    ids, _ = committed_allocation_world
    key = uuid4()
    requests = [
        change("not_required", key=key),
        change("not_required", key=key if same_key else uuid4()),
    ]
    callbacks = [
        lambda db, actor, index=index: allocation_service(db).update(
            actor, ids["member"][1], requests[index]
        )
        for index in range(2)
    ]
    results = race(
        postgres_engine,
        [ids["admin"], ids["admin"] if same_key else ids["admin2"]],
        callbacks,
        record_property,
    )
    assert sorted(row[1] for row in results) == ([200, 200] if same_key else [200, 409])
    with Session(postgres_engine) as db:
        from app.identity.models import Person

        person = db.get(Person, ids["member"][1])
        assert (person.team_allocation_status, person.team_allocation_version) == (
            "not_required",
            2,
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.resource_id == person.id,
                    AuditEvent.action == "people.team_allocation.update",
                )
            )
            == 1
        )
