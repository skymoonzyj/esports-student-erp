from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from tests.db.test_team_concurrency import committed_world as base_world
from tests.db.test_team_concurrency import race, reload_actor


@pytest.fixture
def committed_world(postgres_engine):
    world = base_world.__wrapped__(postgres_engine)
    ids, team_ids = next(world)
    yield ids, team_ids
    from app.team_management.models import TeamStaffAssignment

    with Session(postgres_engine) as db:
        db.execute(delete(TeamStaffAssignment).where(TeamStaffAssignment.team_id.in_(team_ids)))
        db.commit()
    with pytest.raises(StopIteration):
        next(world)


@pytest.mark.parametrize("same_key", [False, True])
def test_parallel_staff_assignment_replay_and_unique_role(
    postgres_engine, committed_world, record_property, same_key
):
    from app.team_management.models import TeamStaffAssignment
    from app.team_management.schemas import AssignStaffInput
    from app.team_management.staff_service import TeamStaffService

    ids, teams = committed_world
    keys = [uuid4(), uuid4()]
    if same_key:
        keys[1] = keys[0]
    results = race(
        postgres_engine,
        [ids["admin"], ids["admin"]],
        [
            lambda db, who, index=index: TeamStaffService(db).assign(
                who,
                teams[0],
                AssignStaffInput(
                    account_id=ids["manager"][0], staff_role="manager", idempotency_key=keys[index]
                ),
            )
            for index in range(2)
        ],
        record_property,
    )
    assert sorted(row[1] for row in results) == ([200, 200] if same_key else [200, 409])
    if same_key:
        assert results[0][2] == results[1][2]
    with Session(postgres_engine) as db:
        assert (
            len(
                list(
                    db.scalars(
                        select(TeamStaffAssignment).where(TeamStaffAssignment.team_id == teams[0])
                    )
                )
            )
            == 1
        )


def test_staff_assignment_rechecks_role_after_account_lock(postgres_engine, committed_world):
    from app.auth.models import UserAccount
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES
    from app.team_management.errors import TeamCandidateIneligible
    from app.team_management.models import TeamStaffAssignment
    from app.team_management.schemas import AssignStaffInput
    from app.team_management.staff_service import TeamStaffService

    ids, teams = committed_world
    # Hold the target account lock before starting the command; role loss commits
    # while it waits. The command must re-read eligibility, not its initial DTO.
    from threading import Event

    from sqlalchemy import event

    waiting = Event()

    def observe(conn, cursor, statement, parameters, context, executemany):
        if "user_accounts" in statement and "FOR UPDATE" in statement:
            waiting.set()

    with Session(postgres_engine) as blocker:
        blocker.scalar(
            select(UserAccount).where(UserAccount.id == ids["manager"][0]).with_for_update()
        )

        def assign():
            with Session(postgres_engine) as db:
                who = reload_actor(db, ids["admin"])
                return TeamStaffService(db).assign(
                    who,
                    teams[0],
                    AssignStaffInput(
                        account_id=ids["manager"][0], staff_role="manager", idempotency_key=uuid4()
                    ),
                )

        event.listen(postgres_engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(assign)
                assert waiting.wait(10)
                blocker.execute(
                    delete(UserRole).where(
                        UserRole.user_id == ids["manager"][0],
                        UserRole.role_id == ROLES["team_manager"].id,
                    )
                )
                blocker.commit()
                with pytest.raises(TeamCandidateIneligible):
                    future.result(20)
        finally:
            event.remove(postgres_engine, "before_cursor_execute", observe)
    with Session(postgres_engine) as db:
        assert (
            list(
                db.scalars(
                    select(TeamStaffAssignment).where(TeamStaffAssignment.team_id == teams[0])
                )
            )
            == []
        )
