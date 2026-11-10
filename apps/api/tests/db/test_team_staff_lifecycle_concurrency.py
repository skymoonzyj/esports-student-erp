"""Real PostgreSQL lifecycle races across multiple staff positions/divisions."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, event, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile
from app.rbac.models import UserRole
from app.rbac.schemas import JobRoleChangeRequest
from app.rbac.seed import ROLES
from app.rbac.service import RbacService
from app.teaching.models import ProfessionalTeam
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.models import TeamManagerAssignment, TeamStaffAssignment
from app.team_management.schemas import AssignManagerInput, AssignStaffInput, UnassignStaffInput
from app.team_management.staff_service import TeamStaffService
from tests.db.test_team_concurrency import (
    account_service,
    reload_actor,
    wait_for_service_then_commit,
)
from tests.db.test_team_staff_concurrency import committed_world as _staff_world


@pytest.fixture
def staff_world(postgres_engine):
    fixture = _staff_world.__wrapped__(postgres_engine)
    world = next(fixture)
    try:
        yield world
    finally:
        # Job replacement creates this target's primary staff profile. The
        # delegated fixture owns accounts/people, so remove our added dependent
        # first; its RESTRICT FK must never be disabled for test cleanup.
        with Session(postgres_engine) as db:
            db.execute(
                delete(StaffProfile).where(
                    StaffProfile.person_id == world[0]["manager"][1],
                )
            )
            db.commit()
        with pytest.raises(StopIteration):
            next(fixture)


def _prepare(engine, ids, teams):
    with Session(engine) as db:
        for key in ("coach", "assistant_coach"):
            db.add(UserRole(user_id=ids["manager"][0], role_id=ROLES[key].id))
        db.commit()
        admin = reload_actor(db, ids["admin"])
        staff = TeamStaffService(db)
        assignments = {}
        for position, team_index in (
            ("manager", 0),
            ("coach", 1),
            ("assistant_coach", 0),
            ("assistant_coach", 1),
        ):
            result = staff.assign(
                admin,
                teams[team_index],
                AssignStaffInput(
                    account_id=ids["manager"][0],
                    staff_role=position,
                    idempotency_key=uuid4(),
                ),
            )
            assignments[position, team_index] = result.assignment_id
        legacy = TeamAssignmentService(db).assign(
            admin,
            teams[1],
            AssignManagerInput(
                account_id=ids["manager"][0],
                idempotency_key=uuid4(),
            ),
        )
        current_roles = list(
            db.scalars(
                select(UserRole.role_id).where(
                    UserRole.user_id == ids["manager"][0],
                )
            )
        )
        project_ids = set(
            db.scalars(
                select(ProfessionalTeam.project_id).where(
                    ProfessionalTeam.id.in_(teams),
                )
            )
        )
        assert len(project_ids) == 2
        return assignments, legacy.assignment_id, current_roles, project_ids


def _uuid_parameters(value):
    if isinstance(value, UUID):
        return {value}
    if isinstance(value, dict):
        return set().union(*(_uuid_parameters(item) for item in value.values()), set())
    if isinstance(value, (tuple, list)):
        return set().union(*(_uuid_parameters(item) for item in value), set())
    return set()


@pytest.mark.parametrize("change", ["replace_jobs", "delete_account"])
@pytest.mark.parametrize("writer", ["bind", "unbind"])
@pytest.mark.parametrize("lifecycle_first", [True, False])
def test_multidivision_lifecycle_and_staff_writer_have_serial_result(
    postgres_engine,
    staff_world,
    record_property,
    change,
    writer,
    lifecycle_first,
):
    ids, teams = staff_world
    assignments, legacy_id, role_snapshot, project_ids = _prepare(postgres_engine, ids, teams)
    target_id = ids["manager"][0]
    ended_id = assignments["coach", 1]
    lifecycle_locks = []

    def observe(connection, cursor, statement, parameters, context, executemany):
        if not connection.info.get("staff_lifecycle_race") or "FOR UPDATE" not in statement:
            return
        sql = " ".join(statement.split())
        for table in ("game_projects", "professional_teams", "team_staff_assignments"):
            if f"FROM {table} " in sql:
                lifecycle_locks.append((table, _uuid_parameters(parameters)))

    def lifecycle(db):
        connection = db.connection()
        connection_info = connection.info
        connection_info["staff_lifecycle_race"] = True
        try:
            if change == "replace_jobs":
                RbacService(db).change_job_role(
                    actor_user_id=ids["admin"][0],
                    account_id=target_id,
                    input=JobRoleChangeRequest(
                        from_role_id=ROLES["team_manager"].id,
                        to_role_id=ROLES["life_teacher"].id,
                        keep_concurrent_roles=False,
                        expected_role_ids=role_snapshot,
                    ),
                    request_id="multidivision-staff-job-race",
                )
            else:
                account_service(db).delete_account(
                    target_id,
                    actor_user_id=ids["admin"][0],
                    request_id="multidivision-staff-delete-race",
                )
        finally:
            connection_info.pop("staff_lifecycle_race", None)

    def write(db):
        # When lifecycle is uncommitted this reads the old eligibility/row;
        # the later command must wait and refresh before it can mutate.
        target = db.get(UserAccount, target_id)
        assert target.status == "active"
        assert db.get(UserRole, (target_id, ROLES["team_manager"].id)) is not None
        service = TeamStaffService(db)
        actor = reload_actor(db, ids["admin2"])
        if writer == "bind":
            service.assign(
                actor,
                teams[1],
                AssignStaffInput(
                    account_id=target_id,
                    staff_role="manager",
                    idempotency_key=uuid4(),
                ),
            )
        else:
            assert db.get(TeamStaffAssignment, ended_id).ended_at is None
            service.unassign(actor, ended_id, UnassignStaffInput(idempotency_key=uuid4()))

    event.listen(postgres_engine, "before_cursor_execute", observe)
    try:
        result = wait_for_service_then_commit(
            postgres_engine,
            lifecycle if lifecycle_first else write,
            write if lifecycle_first else lifecycle,
            record_property,
        )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observe)
    if lifecycle_first:
        assert result == (
            409,
            "team_member_ineligible" if writer == "bind" else "team_assignment_stale",
        )
    else:
        assert result == (200, "success")

    # Observe actual SQL emitted on the lifecycle backend, not mocked helper
    # calls: exactly one batched project lock and one batched team lock must
    # cover both divisions before any staff row locks, even for three removed
    # roles. A per-role discover/lock loop fails this assertion.
    project_locks = [values for table, values in lifecycle_locks if table == "game_projects"]
    team_locks = [values for table, values in lifecycle_locks if table == "professional_teams"]
    assert project_locks == [project_ids]
    assert team_locks == [set(teams)]
    stages = [table for table, _ in lifecycle_locks]
    assert stages[:2] == ["game_projects", "professional_teams"]
    assert "team_staff_assignments" in stages[2:]
    expected_kind = "role_removed" if change == "replace_jobs" else "account_deleted"
    with Session(postgres_engine) as db:
        rows = list(
            db.scalars(
                select(TeamStaffAssignment).where(
                    TeamStaffAssignment.account_id == target_id,
                )
            )
        )
        assert len(rows) == 4 + int(writer == "bind" and not lifecycle_first)
        assert all(row.ended_at is not None for row in rows)
        for row in rows:
            assert row.end_kind == (
                "unassigned"
                if writer == "unbind" and not lifecycle_first and row.id == ended_id
                else expected_kind
            )
        assert db.get(TeamManagerAssignment, legacy_id).end_kind == expected_kind
        role_keys = set(db.scalars(select(UserRole.role_id).where(UserRole.user_id == target_id)))
        if change == "replace_jobs":
            assert role_keys == {ROLES["life_teacher"].id}
        else:
            assert db.get(UserAccount, target_id).status == "disabled"
            assert db.get(Person, ids["manager"][1]).deleted_at is not None
