from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.errors import TeamServiceError
from app.team_management.models import TeamManagerAssignment, TeamMembership, TeamOperation
from app.team_management.schemas import AssignManagerInput, UnassignManagerInput
from tests.services.test_team_memberships import make_world


@pytest.fixture(params=[0, 3], ids=["baseline", "preexisting_unrelated_audits"])
def world(db, request):
    result = make_world(db)
    for _ in range(request.param):
        AuditService(db).append(
            actor_user_id=None,
            action="teaching.cohort.transition",
            resource_type="cohort",
            resource_id=uuid4(),
            purpose=None,
            outcome="allowed",
            request_id=str(uuid4()),
            metadata={},
        )
    db.commit()
    return result


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (TeamManagerAssignment, TeamMembership, TeamOperation, AuditEvent)
    )


def assert_counts_delta(db, before, expected):
    assert tuple(after - prior for after, prior in zip(counts(db), before, strict=True)) == expected


def success_audit(db, actor, result, request, action):
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == actor.account.id,
                AuditEvent.request_id == str(request.idempotency_key),
            )
        )
    )
    assert len(events) == 1
    event = events[0]
    assert event.action == action
    assert event.outcome == "allowed"
    assert event.resource_type == "team_manager_assignment"
    assert event.resource_id == result.assignment_id
    assert event.event_metadata["operation_id"] == str(result.operation_id)
    assert event.event_metadata["assignment_id"] == str(result.assignment_id)
    return event.id


def test_assign_unassign_replay_and_old_id_safety(db, world):
    actors, teams = world
    svc = TeamAssignmentService(db)
    request = AssignManagerInput(account_id=actors["manager"].account.id, idempotency_key=uuid4())
    before = counts(db)
    first = svc.assign(actors["admin"], teams[0].id, request)
    assert_counts_delta(db, before, (1, 0, 1, 1))
    first_audit = success_audit(db, actors["admin"], first, request, "team_managers.assign")
    after_first = counts(db)
    assert svc.assign(actors["admin"], teams[0].id, request) == first
    assert counts(db) == after_first
    assert first.assignment_id is not None and first.membership_id is None
    with pytest.raises(TeamServiceError) as error:
        svc.assign(actors["admin"], teams[1].id, request)
    assert error.value.code == "team_operation_conflict"
    assert counts(db) == after_first
    with pytest.raises(TeamServiceError) as error:
        svc.assign(
            actors["admin"], teams[0].id, request.model_copy(update={"idempotency_key": uuid4()})
        )
    assert error.value.status == 409
    assert counts(db) == after_first
    unassign = UnassignManagerInput(idempotency_key=uuid4())
    ended = svc.unassign(actors["admin"], first.assignment_id, unassign)
    assert_counts_delta(db, before, (1, 0, 2, 2))
    end_audit = success_audit(db, actors["admin"], ended, unassign, "team_managers.unassign")
    after_ended = counts(db)
    assert svc.unassign(actors["admin"], first.assignment_id, unassign) == ended
    assert counts(db) == after_ended
    assert svc.assign(actors["admin"], teams[0].id, request) == first
    assert counts(db) == after_ended
    second_request = request.model_copy(update={"idempotency_key": uuid4()})
    second = svc.assign(actors["admin"], teams[0].id, second_request)
    assert_counts_delta(db, before, (2, 0, 3, 3))
    second_audit = success_audit(
        db, actors["admin"], second, second_request, "team_managers.assign"
    )
    assert len({first_audit, end_audit, second_audit}) == 3
    after_second = counts(db)
    assert second.assignment_id != first.assignment_id
    with pytest.raises(TeamServiceError) as error:
        svc.unassign(
            actors["admin"], first.assignment_id, UnassignManagerInput(idempotency_key=uuid4())
        )
    assert error.value.status == 409
    assert counts(db) == after_second
    assert db.get(TeamManagerAssignment, second.assignment_id).ended_at is None


def test_assign_requires_superadmin_and_live_manager_role(db, world):
    actors, teams = world
    svc = TeamAssignmentService(db)
    for actor, target, status in (
        (actors["manager"], actors["manager"], 404),
        (actors["admin"], actors["member"], 409),
    ):
        before = counts(db)
        with pytest.raises(TeamServiceError) as error:
            svc.assign(
                actor,
                teams[0].id,
                AssignManagerInput(account_id=target.account.id, idempotency_key=uuid4()),
            )
        assert error.value.status == status
        assert counts(db) == before


def test_unassign_disabled_target_and_cross_action_key_conflict(db, world):
    actors, teams = world
    svc = TeamAssignmentService(db)
    request = AssignManagerInput(account_id=actors["manager"].account.id, idempotency_key=uuid4())
    before = counts(db)
    result = svc.assign(actors["admin"], teams[0].id, request)
    assert_counts_delta(db, before, (1, 0, 1, 1))
    after_assigned = counts(db)
    with pytest.raises(TeamServiceError) as error:
        svc.unassign(
            actors["admin"],
            result.assignment_id,
            UnassignManagerInput(idempotency_key=request.idempotency_key),
        )
    assert error.value.code == "team_operation_conflict"
    assert counts(db) == after_assigned
    actors["manager"].account.status = "disabled"
    teams[0].is_active = False
    db.flush()
    svc.unassign(
        actors["admin"], result.assignment_id, UnassignManagerInput(idempotency_key=uuid4())
    )
    assert_counts_delta(db, before, (1, 0, 2, 2))
    assert db.get(TeamManagerAssignment, result.assignment_id).end_kind == "unassigned"


def test_key_namespace_crosses_membership_and_assignment_services(db, world):
    from tests.services.test_team_memberships import join

    actors, teams = world
    key = uuid4()
    join(db, actors["admin"], teams[0], actors["member"], key)
    before = counts(db)
    with pytest.raises(TeamServiceError) as error:
        TeamAssignmentService(db).assign(
            actors["admin"],
            teams[0].id,
            AssignManagerInput(account_id=actors["manager"].account.id, idempotency_key=key),
        )
    assert error.value.code == "team_operation_conflict"
    assert counts(db) == before


@pytest.mark.parametrize("invalid", ["disabled", "password", "deleted", "inactive"])
def test_assign_rechecks_target_and_destination(db, world, invalid):
    from datetime import UTC, datetime

    actors, teams = world
    manager = actors["manager"]
    if invalid == "disabled":
        manager.account.status = "disabled"
    elif invalid == "password":
        manager.account.force_password_change = True
    elif invalid == "deleted":
        manager.person.deleted_at = datetime.now(UTC)
    else:
        teams[0].is_active = False
    db.commit()
    before = counts(db)
    with pytest.raises(TeamServiceError) as error:
        TeamAssignmentService(db).assign(
            actors["admin"],
            teams[0].id,
            AssignManagerInput(account_id=manager.account.id, idempotency_key=uuid4()),
        )
    assert error.value.status == 409
    assert counts(db) == before
