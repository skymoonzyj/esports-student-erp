from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Barrier, Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AccountService, AuthContext
from app.identity.models import Person
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.schemas import PermissionOverrideCreate
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.catalog_service import TeachingCatalogService
from app.teaching.models import GameProject, ProfessionalTeam
from app.teaching.schemas import ProfessionalTeamCreate, ProfessionalTeamUpdate, ProjectUpdate
from app.team_management.access import TeamAccessService
from app.team_management.assignment_service import TeamAssignmentService
from app.team_management.errors import TeamServiceError
from app.team_management.membership_service import TeamMembershipService
from app.team_management.models import TeamManagerAssignment, TeamMembership, TeamOperation
from app.team_management.schemas import (
    AssignManagerInput,
    JoinTeamInput,
    LeaveTeamInput,
    TransferTeamInput,
    UnassignManagerInput,
)
from tests.services.test_team_memberships import make_world


@pytest.mark.parametrize("body_fails", [False, True])
def test_committed_world_cleanup_preserves_shared_projects(postgres_engine, body_fails):
    with Session(postgres_engine) as db:
        before = list(
            db.execute(
                select(
                    GameProject.id, GameProject.is_active, GameProject.is_team_division
                ).order_by(GameProject.id)
            )
        )
    fixture = committed_world.__wrapped__(postgres_engine)
    _, team_ids = next(fixture)
    with (
        pytest.raises(RuntimeError, match="simulated test failure") if body_fails else nullcontext()
    ):
        try:
            with Session(postgres_engine) as db:
                project_ids = list(
                    db.scalars(
                        select(ProfessionalTeam.project_id).where(ProfessionalTeam.id.in_(team_ids))
                    )
                )
                for project in db.scalars(
                    select(GameProject).where(GameProject.id.in_(project_ids))
                ):
                    project.is_active = False
                db.commit()
            if body_fails:
                raise RuntimeError("simulated test failure")
        finally:
            # Match pytest's yield-fixture finalization even when its test body fails.
            with pytest.raises(StopIteration):
                next(fixture)
    with Session(postgres_engine) as db:
        after = list(
            db.execute(
                select(
                    GameProject.id, GameProject.is_active, GameProject.is_team_division
                ).order_by(GameProject.id)
            )
        )
        assert after == before
        assert list(db.scalars(select(GameProject.id).where(GameProject.id.in_(project_ids)))) == []


@pytest.fixture
def committed_world(postgres_engine):
    with Session(postgres_engine, expire_on_commit=False) as db:
        actors, teams = make_world(db)
        projects = [
            GameProject(
                name=f"并发测试分部{uuid4().hex}",
                sort_order=index,
                is_active=True,
                is_team_division=True,
                created_by_user_id=actors["admin"].account.id,
            )
            for index in range(len(teams))
        ]
        db.add_all(projects)
        db.flush()
        for team, project in zip(teams, projects, strict=True):
            team.project_id = project.id
        project_ids = [project.id for project in projects]
        ids = {key: (actor.account.id, actor.person.id) for key, actor in actors.items()}
        team_ids = [team.id for team in teams]
        db.commit()
    yield ids, team_ids
    with Session(postgres_engine) as db:
        account_ids = [value[0] for value in ids.values()]
        person_ids = [value[1] for value in ids.values()]
        # Same transactional, isolated-test-only cleanup as talent_helpers.
        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(account_ids)))
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        db.execute(delete(TeamMembership).where(TeamMembership.person_id.in_(person_ids)))
        db.execute(
            delete(TeamManagerAssignment).where(TeamManagerAssignment.account_id.in_(account_ids))
        )
        db.execute(delete(TeamOperation).where(TeamOperation.actor_account_id.in_(account_ids)))
        db.execute(delete(ProfessionalTeam).where(ProfessionalTeam.id.in_(team_ids)))
        db.execute(delete(GameProject).where(GameProject.id.in_(project_ids)))
        db.execute(
            delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(account_ids))
        )
        db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
        db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
        db.execute(delete(Person).where(Person.id.in_(person_ids)))
        db.commit()


def reload_actor(db, ids):
    return AuthContext(
        account=db.get(UserAccount, ids[0]), person=db.get(Person, ids[1]), session=None
    )


def race(engine, ids, callbacks, record_property):
    barrier = Barrier(2)

    def run(index):
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pid = db.scalar(select(func.pg_backend_pid()))
            actor = reload_actor(db, ids[index])
            barrier.wait(10)
            try:
                result = callbacks[index](db, actor)
                status = 200
            except TeamServiceError as exc:
                result, status = exc.code, exc.status
            return pid, status, result

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        results = [future.result(20) for future in futures]
    assert results[0][0] != results[1][0]
    pids = [row[0] for row in results]
    record_property("postgres_backend_pids", str(pids))
    print(f"Independent PostgreSQL backend PIDs: {pids}")
    return results


@pytest.mark.parametrize("same_key", [False, True])
def test_parallel_joins_and_identical_replays(
    postgres_engine, committed_world, record_property, same_key
):
    ids, teams = committed_world
    keys = [uuid4(), uuid4()]
    if same_key:
        keys[1] = keys[0]
    callbacks = [
        lambda db, actor, index=index: TeamMembershipService(db).join(
            actor,
            teams[0 if same_key else index],
            JoinTeamInput(person_id=ids["member"][1], idempotency_key=keys[index]),
        )
        for index in range(2)
    ]
    actors = [ids["admin"], ids["admin"] if same_key else ids["admin2"]]
    results = race(postgres_engine, actors, callbacks, record_property)
    assert sorted(row[1] for row in results) == ([200, 200] if same_key else [200, 409])
    if same_key:
        assert results[0][2] == results[1][2]
    with Session(postgres_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TeamMembership)
                .where(
                    TeamMembership.person_id == ids["member"][1], TeamMembership.ended_at.is_(None)
                )
            )
            == 1
        )
        assert db.scalar(select(func.count()).select_from(TeamOperation)) == 1
        operation = db.scalar(select(TeamOperation))
        membership = db.get(TeamMembership, operation.result_ids["membership_id"])
        winner = next(index for index, row in enumerate(results) if row[1] == 200)
        assert operation.actor_account_id == membership.created_by_user_id == actors[winner][0]
        assert membership.person_id == ids["member"][1]
        assert membership.open_operation_id == operation.id
        audits = list(db.scalars(select(AuditEvent)))
        assert Counter(
            (row.action, row.resource_type, row.resource_id, row.actor_user_id) for row in audits
        ) == Counter(
            [
                ("people.team_allocation.update", "person", ids["member"][1], actors[winner][0]),
                ("team_memberships.join", "team_membership", membership.id, actors[winner][0]),
            ]
        )
        allocation = next(row for row in audits if row.action == "people.team_allocation.update")
        assert allocation.event_metadata == {
            "person_id": str(ids["member"][1]),
            "allocation_old_status": "untracked",
            "allocation_new_status": "assigned",
            "old_version": 1,
            "new_version": 2,
        }
        joined = next(row for row in audits if row.action == "team_memberships.join")
        assert joined.request_id == str(operation.idempotency_key)
        assert joined.event_metadata == {
            "operation_key": str(operation.idempotency_key),
            "professional_team_id": str(membership.team_id),
            **{key: value for key, value in operation.result_ids.items() if value is not None},
            "person_id": str(ids["member"][1]),
        }
        person = db.get(Person, ids["member"][1])
        assert (person.team_allocation_status, person.team_allocation_version) == ("assigned", 2)


def test_opposite_transfers_do_not_deadlock(postgres_engine, committed_world, record_property):
    ids, teams = committed_world
    memberships = []
    with Session(postgres_engine) as db:
        for index, member in enumerate(("member", "member2")):
            memberships.append(
                TeamMembershipService(db)
                .join(
                    reload_actor(db, ids["admin"]),
                    teams[index],
                    JoinTeamInput(person_id=ids[member][1], idempotency_key=uuid4()),
                )
                .membership_id
            )
    callbacks = [
        lambda db, actor, index=index: TeamMembershipService(db).transfer(
            actor,
            memberships[index],
            TransferTeamInput(
                target_team_id=teams[1 - index], reason="反向调动", idempotency_key=uuid4()
            ),
        )
        for index in range(2)
    ]
    results = race(postgres_engine, [ids["admin"], ids["admin2"]], callbacks, record_property)
    assert [row[1] for row in results] == [200, 200]
    with Session(postgres_engine) as db:
        for index, member in enumerate(("member", "member2")):
            current = db.scalar(
                select(TeamMembership).where(
                    TeamMembership.person_id == ids[member][1], TeamMembership.ended_at.is_(None)
                )
            )
            assert current.team_id == teams[1 - index]


def test_transfer_competes_with_leave_one_old_change(
    postgres_engine, committed_world, record_property
):
    ids, teams = committed_world
    with Session(postgres_engine) as db:
        old = (
            TeamMembershipService(db)
            .join(
                reload_actor(db, ids["admin"]),
                teams[0],
                JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4()),
            )
            .membership_id
        )
    callbacks = [
        lambda db, actor: TeamMembershipService(db).transfer(
            actor,
            old,
            TransferTeamInput(target_team_id=teams[1], reason="竞态调队", idempotency_key=uuid4()),
        ),
        lambda db, actor: TeamMembershipService(db).leave(
            actor, old, LeaveTeamInput(idempotency_key=uuid4())
        ),
    ]
    results = race(postgres_engine, [ids["admin"], ids["admin2"]], callbacks, record_property)
    assert sorted(row[1] for row in results) == [200, 409]
    with Session(postgres_engine) as db:
        assert db.get(TeamMembership, old).ended_at is not None
        assert db.scalar(select(func.count()).select_from(TeamOperation)) == 2
        winner = next(index for index, row in enumerate(results) if row[1] == 200)
        response = results[winner][2]
        winner_actor = ids["admin" if winner == 0 else "admin2"][0]
        old_membership = db.get(TeamMembership, old)
        joined_operation = db.get(TeamOperation, old_membership.open_operation_id)
        winning_operation = db.get(TeamOperation, response.operation_id)
        winning_action = "team_memberships.transfer" if winner == 0 else "team_memberships.leave"
        assert winning_operation.action == winning_action
        assert winning_operation.actor_account_id == old_membership.ended_by_user_id == winner_actor
        assert old_membership.close_operation_id == response.operation_id
        assert winning_operation.result_ids == response.model_dump(mode="json")
        assert joined_operation.action == "team_memberships.join"
        assert joined_operation.actor_account_id == ids["admin"][0]
        expected = [
            ("people.team_allocation.update", "person", ids["member"][1], ids["admin"][0]),
            ("team_memberships.join", "team_membership", old, ids["admin"][0]),
            (
                winning_action,
                "team_membership",
                response.membership_id if winner == 0 else old,
                winner_actor,
            ),
        ]
        if winner == 1:
            expected.append(
                ("people.team_allocation.update", "person", ids["member"][1], winner_actor)
            )
        audits = list(db.scalars(select(AuditEvent)))
        assert Counter(
            (row.action, row.resource_type, row.resource_id, row.actor_user_id) for row in audits
        ) == Counter(expected)
        allocations = sorted(
            (row for row in audits if row.action == "people.team_allocation.update"),
            key=lambda row: row.event_metadata["old_version"],
        )
        transitions = [("untracked", "assigned", 1, 2)]
        if winner == 1:
            transitions.append(("assigned", "pending", 2, 3))
        assert len(allocations) == len(transitions)
        for audit, (before, after, old_version, new_version) in zip(
            allocations, transitions, strict=True
        ):
            assert audit.event_metadata == {
                "person_id": str(ids["member"][1]),
                "allocation_old_status": before,
                "allocation_new_status": after,
                "old_version": old_version,
                "new_version": new_version,
            }
        for operation in (joined_operation, winning_operation):
            audit = next(row for row in audits if row.action == operation.action)
            expected_metadata = {
                "operation_key": str(operation.idempotency_key),
                "professional_team_id": str(
                    teams[1] if operation.action == "team_memberships.transfer" else teams[0]
                ),
                **{key: value for key, value in operation.result_ids.items() if value is not None},
                "person_id": str(ids["member"][1]),
            }
            if operation.action == "team_memberships.transfer":
                expected_metadata.update(source_team_id=str(teams[0]), target_team_id=str(teams[1]))
            assert audit.request_id == str(operation.idempotency_key)
            assert audit.event_metadata == expected_metadata
        person = db.get(Person, ids["member"][1])
        assert (person.team_allocation_status, person.team_allocation_version) == (
            ("assigned", 2) if winner == 0 else ("pending", 3)
        )
        assert list(
            db.scalars(
                select(TeamMembership.id).where(
                    TeamMembership.person_id == ids["member"][1], TeamMembership.ended_at.is_(None)
                )
            )
        ) == ([response.membership_id] if winner == 0 else [])
        record_property("audit_winner_action", winning_action)


@pytest.mark.parametrize("action", ["join", "leave", "transfer", "assign", "unassign"])
def test_audit_failure_commits_neither_business_nor_operation(
    postgres_engine, committed_world, monkeypatch, action
):
    from app.team_management.assignment_service import TeamAssignmentService
    from app.team_management.schemas import AssignManagerInput, UnassignManagerInput

    ids, teams = committed_world
    membership_id = assignment_id = None
    with Session(postgres_engine) as db:
        actor = reload_actor(db, ids["admin"])
        if action in {"leave", "transfer"}:
            membership_id = (
                TeamMembershipService(db)
                .join(
                    actor,
                    teams[0],
                    JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4()),
                )
                .membership_id
            )
        if action == "unassign":
            assignment_id = (
                TeamAssignmentService(db)
                .assign(
                    actor,
                    teams[0],
                    AssignManagerInput(account_id=ids["manager"][0], idempotency_key=uuid4()),
                )
                .assignment_id
            )
        before = tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (TeamMembership, TeamManagerAssignment, TeamOperation, AuditEvent)
        )

    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditService, "append", fail)
    with Session(postgres_engine) as db, pytest.raises(RuntimeError, match="audit unavailable"):
        actor = reload_actor(db, ids["admin"])
        if action == "join":
            TeamMembershipService(db).join(
                actor, teams[0], JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4())
            )
        elif action == "leave":
            TeamMembershipService(db).leave(
                actor, membership_id, LeaveTeamInput(idempotency_key=uuid4())
            )
        elif action == "transfer":
            TeamMembershipService(db).transfer(
                actor,
                membership_id,
                TransferTeamInput(
                    target_team_id=teams[1], reason="审计失败", idempotency_key=uuid4()
                ),
            )
        elif action == "assign":
            TeamAssignmentService(db).assign(
                actor,
                teams[0],
                AssignManagerInput(account_id=ids["manager"][0], idempotency_key=uuid4()),
            )
        else:
            TeamAssignmentService(db).unassign(
                actor, assignment_id, UnassignManagerInput(idempotency_key=uuid4())
            )
    with Session(postgres_engine) as observer:
        after = tuple(
            observer.scalar(select(func.count()).select_from(model))
            for model in (TeamMembership, TeamManagerAssignment, TeamOperation, AuditEvent)
        )
        assert after == before
        if membership_id:
            assert observer.get(TeamMembership, membership_id).ended_at is None
        if assignment_id:
            assert observer.get(TeamManagerAssignment, assignment_id).ended_at is None


@pytest.mark.parametrize("change", ["actor", "candidate", "project"])
def test_waiter_refreshes_stale_authorization_and_eligibility(
    postgres_engine, committed_world, record_property, change
):
    ids, teams = committed_world
    key = uuid4()
    if change == "actor":
        with Session(postgres_engine) as db:
            TeamMembershipService(db).join(
                reload_actor(db, ids["admin"]),
                teams[0],
                JoinTeamInput(person_id=ids["member"][1], idempotency_key=key),
            )
    ready, worker_pids = Event(), []

    def contender():
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            actor = reload_actor(db, ids["admin"])
            member = db.get(UserAccount, ids["member"][0])
            team = db.get(ProfessionalTeam, teams[0])
            project = db.get(GameProject, team.project_id)
            assert actor.account.status == member.status == "active"
            assert project.is_active
            worker_pids.append(db.scalar(select(func.pg_backend_pid())))
            ready.set()
            try:
                TeamMembershipService(db).join(
                    actor, teams[0], JoinTeamInput(person_id=ids["member"][1], idempotency_key=key)
                )
            except TeamServiceError as exc:
                return exc.status, exc.code
            return 200, "success"

    with Session(postgres_engine) as holder:
        if change == "project":
            project_id = holder.scalar(
                select(ProfessionalTeam.project_id).where(ProfessionalTeam.id == teams[0])
            )
            project = holder.scalar(
                select(GameProject).where(GameProject.id == project_id).with_for_update()
            )
            project.is_active = False
        else:
            target_ids = ids["admin" if change == "actor" else "member"]
            holder.scalar(select(Person).where(Person.id == target_ids[1]).with_for_update())
            account = holder.scalar(
                select(UserAccount).where(UserAccount.id == target_ids[0]).with_for_update()
            )
            account.status = "disabled"
        holder.flush()
        holder_pid = holder.scalar(select(func.pg_backend_pid()))
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(contender)
            try:
                assert ready.wait(10)
                with Session(postgres_engine) as observer:
                    observer_pid = observer.scalar(select(func.pg_backend_pid()))
                    deadline, blocked = monotonic() + 5, False
                    while monotonic() < deadline:
                        if holder_pid in observer.scalar(
                            select(func.pg_blocking_pids(worker_pids[0]))
                        ):
                            blocked = True
                            break
                        Event().wait(0.01)
                    assert blocked, "write never waited on the expected participant/catalog mutex"
                    assert len({holder_pid, worker_pids[0], observer_pid}) == 3
                holder.commit()
            finally:
                holder.rollback()
            expected = {
                "actor": (404, "team_not_found"),
                "candidate": (409, "team_member_ineligible"),
                "project": (409, "team_inactive"),
            }[change]
            assert future.result(15) == expected
        pids = [holder_pid, worker_pids[0], observer_pid]
        record_property("postgres_backend_pids", str(pids))
        print(f"Observed blocking PostgreSQL backend PIDs: {pids}")
    with Session(postgres_engine) as observer:
        assert observer.scalar(select(func.count()).select_from(TeamOperation)) == int(
            change == "actor"
        )


def account_service(db):
    return AccountService(
        db,
        identity_service=IdentityService(
            db,
            cipher=EnvelopeCipher(bytes(range(32))),
            lookup=LookupDigest(bytes(range(32))),
        ),
        passwords=PasswordService(),
    )


def bind_manager(engine, ids, team_id):
    with Session(engine) as db:
        return (
            TeamAssignmentService(db)
            .assign(
                reload_actor(db, ids["admin"]),
                team_id,
                AssignManagerInput(account_id=ids["manager"][0], idempotency_key=uuid4()),
            )
            .assignment_id
        )


def lifecycle_change(db, ids, team_id, assignment_id, change):
    if change == "role":
        RbacService(db).remove_role(
            actor_user_id=ids["admin"][0],
            account_id=ids["manager"][0],
            role_id=ROLES["team_manager"].id,
            request_id="concurrent-role-remove",
        )
    elif change == "deny":
        RbacService(db).add_override(
            actor_user_id=ids["admin"][0],
            account_id=ids["manager"][0],
            input=PermissionOverrideCreate(
                permission_id=PERMISSIONS["team_memberships.manage"].id,
                effect="deny",
                scope_kind="assigned_team",
                scope_id=team_id,
            ),
        )
    elif change == "unassign":
        TeamAssignmentService(db).unassign(
            reload_actor(db, ids["admin"]),
            assignment_id,
            UnassignManagerInput(idempotency_key=uuid4()),
        )
    elif change == "disable":
        account_service(db).disable_account(
            ids["manager"][0],
            actor_user_id=ids["admin"][0],
            request_id="concurrent-disable",
        )
    elif change == "delete":
        account_service(db).delete_account(
            ids["member"][0],
            actor_user_id=ids["admin"][0],
            request_id="concurrent-delete",
        )
    elif change == "team":
        TeachingCatalogService(db).update_team(
            team_id,
            ProfessionalTeamUpdate(is_active=False),
            actor_user_id=ids["admin"][0],
        )
    else:
        project_id = db.scalar(
            select(ProfessionalTeam.project_id).where(ProfessionalTeam.id == team_id)
        )
        TeachingCatalogService(db).update_project(
            project_id,
            ProjectUpdate(**{"is_active" if change == "project" else "is_team_division": False}),
            actor_user_id=ids["admin"][0],
        )


def wait_for_service_then_commit(
    engine,
    hold,
    wait,
    record_property,
    *,
    expected_error_detail=None,
):
    """Keep the first real service transaction open and prove the second blocks."""
    ready, worker_pids = Event(), []

    def contender():
        with Session(engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            worker_pids.append(db.scalar(select(func.pg_backend_pid())))
            ready.set()
            try:
                wait(db)
            except TeamServiceError as exc:
                if expected_error_detail is not None:
                    assert str(exc) == expected_error_detail
                return exc.status, exc.code
            return 200, "success"

    with Session(engine) as holder:
        # Service-owned atomic writes receive a savepoint, never commit this holder.
        holder.begin()
        holder.execute(text("SET LOCAL lock_timeout = '8s'"))
        holder.execute(text("SET LOCAL statement_timeout = '12s'"))
        holder_pid = holder.scalar(select(func.pg_backend_pid()))
        hold(holder)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(contender)
            try:
                assert ready.wait(10)
                with Session(engine) as observer:
                    observer_pid = observer.scalar(select(func.pg_backend_pid()))
                    deadline, blocked = monotonic() + 5, False
                    while monotonic() < deadline:
                        if holder_pid in observer.scalar(
                            select(func.pg_blocking_pids(worker_pids[0]))
                        ):
                            blocked = True
                            break
                        if future.done():
                            break
                        Event().wait(0.01)
                    assert blocked, "lifecycle competitor never blocked on the service transaction"
                    assert len({holder_pid, worker_pids[0], observer_pid}) == 3
                holder.commit()
            finally:
                holder.rollback()
            result = future.result(15)
    pids = [holder_pid, worker_pids[0], observer_pid]
    record_property("postgres_backend_pids", str(pids))
    print(f"Lifecycle blocking PostgreSQL backend PIDs: {pids}")
    return result


@pytest.mark.parametrize("change", ["role", "deny", "unassign", "disable", "delete"])
def test_lifecycle_revocation_wins_against_stale_manager_join(
    postgres_engine,
    committed_world,
    record_property,
    change,
):
    ids, teams = committed_world
    assignment_id = bind_manager(postgres_engine, ids, teams[0])

    def stale_join(db):
        actor = reload_actor(db, ids["manager"])
        candidate = db.get(UserAccount, ids["member"][0])
        binding = db.get(TeamManagerAssignment, assignment_id)
        role = db.get(UserRole, (actor.account.id, ROLES["team_manager"].id))
        assert actor.account.status == candidate.status == "active"
        assert binding.ended_at is None and role is not None
        TeamAccessService(db).require_team(actor, teams[0], "manage")
        TeamMembershipService(db).join(
            actor,
            teams[0],
            JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4()),
        )

    result = wait_for_service_then_commit(
        postgres_engine,
        lambda db: lifecycle_change(db, ids, teams[0], assignment_id, change),
        stale_join,
        record_property,
    )
    assert result == (
        (409, "team_member_ineligible") if change == "delete" else (404, "team_not_found")
    )
    with Session(postgres_engine) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(TeamMembership)
                .where(TeamMembership.person_id == ids["member"][1])
            )
            == 0
        )
        binding = db.get(TeamManagerAssignment, assignment_id)
        if change in {"disable", "deny", "delete"}:
            assert binding.ended_at is None
        else:
            assert binding.end_kind == ("role_removed" if change == "role" else "unassigned")


@pytest.mark.parametrize("change", ["role", "deny", "unassign", "disable", "delete"])
def test_join_wins_then_lifecycle_change_has_serial_result(
    postgres_engine,
    committed_world,
    record_property,
    change,
):
    ids, teams = committed_world
    assignment_id = bind_manager(postgres_engine, ids, teams[0])

    def join_first(db):
        TeamMembershipService(db).join(
            reload_actor(db, ids["manager"]),
            teams[0],
            JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4()),
        )

    result = wait_for_service_then_commit(
        postgres_engine,
        join_first,
        lambda db: lifecycle_change(db, ids, teams[0], assignment_id, change),
        record_property,
    )
    assert result == (200, "success")
    with Session(postgres_engine) as db:
        membership = db.scalar(
            select(TeamMembership).where(TeamMembership.person_id == ids["member"][1])
        )
        assert membership is not None
        if change == "delete":
            assert membership.end_kind == "account_deleted" and membership.ended_at is not None
        else:
            assert membership.ended_at is None


@pytest.mark.parametrize("change", ["team", "project", "division"])
@pytest.mark.parametrize("catalog_first", [False, True])
def test_catalog_stop_vs_join_is_serial_without_identity_lock_inversion(
    postgres_engine,
    committed_world,
    record_property,
    change,
    catalog_first,
):
    ids, teams = committed_world

    # No binding: a live binding itself correctly prevents catalog stop.
    def join_team(db):
        actor = reload_actor(db, ids["admin2"])
        team = db.get(ProfessionalTeam, teams[0])
        project = db.get(GameProject, team.project_id)
        assert team.is_active and project.is_active and project.is_team_division
        TeamMembershipService(db).join(
            actor,
            teams[0],
            JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4()),
        )

    def stop_catalog(db):
        lifecycle_change(db, ids, teams[0], None, change)

    result = wait_for_service_then_commit(
        postgres_engine,
        stop_catalog if catalog_first else join_team,
        join_team if catalog_first else stop_catalog,
        record_property,
        expected_error_detail=(
            "队伍不存在或无查看权限" if catalog_first and change == "division" else None
        ),
    )
    if catalog_first and change == "division":
        # A cleared non-division team with no roster history is no longer visible.
        assert result == (404, "team_not_found")
    else:
        assert result == (
            409,
            "team_inactive" if catalog_first else "team_has_active_relationships",
        )
    with Session(postgres_engine) as db:
        membership = db.scalar(
            select(TeamMembership).where(TeamMembership.person_id == ids["member"][1])
        )
        assert (membership is None) == catalog_first
        team = db.get(ProfessionalTeam, teams[0])
        project = db.get(GameProject, team.project_id)
        assert (team.is_active and project.is_active and project.is_team_division) == (
            not catalog_first
        )


@pytest.mark.parametrize("mutation", ["update_team", "create_team", "update_project"])
def test_same_actor_catalog_and_join_avoid_account_project_deadlock(
    postgres_engine,
    committed_world,
    record_property,
    mutation,
):
    ids, teams = committed_world
    directory_locked, continue_catalog, join_started = Event(), Event(), Event()
    pids = {}
    created_team_ids = []
    with Session(postgres_engine) as db:
        project_id = db.get(ProfessionalTeam, teams[0]).project_id
        assert db.get(ProfessionalTeam, teams[0]).updated_by_user_id is None
        assert db.get(GameProject, project_id).updated_by_user_id is None

    def catalog_writer():
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pids["catalog"] = db.scalar(select(func.pg_backend_pid()))

            def pause_after_directory_lock(conn, cursor, statement, parameters, context, many):
                if (
                    not directory_locked.is_set()
                    and "FROM game_projects" in statement
                    and "FOR UPDATE" in statement
                ):
                    directory_locked.set()
                    assert continue_catalog.wait(10), "observer did not release catalog writer"

            connection = db.connection()
            event.listen(connection, "after_cursor_execute", pause_after_directory_lock)
            try:
                catalog = TeachingCatalogService(db)
                if mutation == "update_team":
                    catalog.update_team(
                        teams[0],
                        ProfessionalTeamUpdate(notes="同操作人更新"),
                        actor_user_id=ids["admin"][0],
                    )
                elif mutation == "create_team":
                    created = catalog.create_team(
                        ProfessionalTeamCreate(
                            project_id=project_id, name=f"同操作人建队{uuid4().hex}"
                        ),
                        actor_user_id=ids["admin"][0],
                    )
                    created_team_ids.append(created.id)
                else:
                    # Omitted division flag is the legacy catalog path, not a superadmin-only toggle.
                    catalog.update_project(
                        project_id, ProjectUpdate(sort_order=42), actor_user_id=ids["admin"][0]
                    )
            finally:
                event.remove(connection, "after_cursor_execute", pause_after_directory_lock)

    def join_writer():
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pids["join"] = db.scalar(select(func.pg_backend_pid()))
            actor = reload_actor(db, ids["admin"])
            join_started.set()
            return TeamMembershipService(db).join(
                actor, teams[0], JoinTeamInput(person_id=ids["member"][1], idempotency_key=uuid4())
            )

    try:
        with ThreadPoolExecutor(2) as pool:
            catalog_future = pool.submit(catalog_writer)
            try:
                assert directory_locked.wait(10), "catalog writer never held its project mutex"
                join_future = pool.submit(join_writer)
                assert join_started.wait(10)
                with Session(postgres_engine) as observer:
                    pids["observer"] = observer.scalar(select(func.pg_backend_pid()))
                    deadline, blocked = monotonic() + 5, False
                    while monotonic() < deadline:
                        if pids["catalog"] in observer.scalar(
                            select(func.pg_blocking_pids(pids["join"]))
                        ):
                            blocked = True
                            break
                        Event().wait(0.01)
                    assert blocked, "same-actor join did not wait on the catalog transaction"
                    assert len(set(pids.values())) == 3
                continue_catalog.set()
                # Neither future may deadlock, time out, or use an application retry.
                catalog_future.result(15)
                joined = join_future.result(15)
            finally:
                continue_catalog.set()
        with Session(postgres_engine) as db:
            assert db.get(TeamMembership, joined.membership_id).ended_at is None
            if mutation == "update_team":
                assert db.get(ProfessionalTeam, teams[0]).notes == "同操作人更新"
            elif mutation == "create_team":
                assert len(created_team_ids) == 1
                assert (
                    db.get(ProfessionalTeam, created_team_ids[0]).created_by_user_id
                    == ids["admin"][0]
                )
            else:
                assert db.get(GameProject, project_id).sort_order == 42
        record_property("postgres_backend_pids", str(pids))
        print(f"Same-actor catalog blocking PostgreSQL backend PIDs: {pids}")
    finally:
        if created_team_ids:
            with Session(postgres_engine) as db:
                db.execute(
                    delete(ProfessionalTeam).where(ProfessionalTeam.id.in_(created_team_ids))
                )
                db.commit()
