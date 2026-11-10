from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.core.errors import ProblemError
from app.identity.assignments.models import PersonAssignmentOperation, PersonAssignmentState
from app.identity.assignments.schemas import AssignmentInput
from app.identity.assignments.service import PersonAssignmentService
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.db.test_team_concurrency import committed_world, reload_actor


def service(db):
    return PersonAssignmentService(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


@pytest.fixture
def assignment_world(postgres_engine):
    generator = committed_world.__wrapped__(postgres_engine)
    ids, _ = next(generator)
    try:
        yield ids
    finally:
        with Session(postgres_engine) as db, db.begin():
            db.execute(
                delete(PersonAssignmentOperation).where(
                    PersonAssignmentOperation.person_id.in_([value[1] for value in ids.values()])
                )
            )
            db.execute(
                delete(PersonAssignmentState).where(
                    PersonAssignmentState.person_id.in_([value[1] for value in ids.values()])
                )
            )
        with pytest.raises(StopIteration):
            next(generator)


@pytest.mark.parametrize("same_key", [False, True])
def test_parallel_assignment_version_and_replay_are_serialized(
    postgres_engine, assignment_world, same_key
):
    ids = assignment_world
    person_id = ids["member"][1]
    with Session(postgres_engine) as db:
        before = service(db).get(reload_actor(db, ids["admin"]), person_id)
    keys = [uuid4(), uuid4()]
    if same_key:
        keys[1] = keys[0]
    barrier = Barrier(2)

    def run(index):
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout='8s'"))
            actor = reload_actor(db, ids["admin"])
            pid = db.scalar(select(func.pg_backend_pid()))
            barrier.wait(10)
            try:
                result = service(db).replace(
                    actor,
                    person_id,
                    AssignmentInput(
                        expected_version=before.version,
                        idempotency_key=keys[index],
                        reason="并发归属调整",
                        business_kind="player",
                        student_project_ids=[],
                        coach_person_ids=[],
                    ),
                )
                return pid, 200, result.version
            except ProblemError as exc:
                return pid, exc.status, exc.code

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, index) for index in range(2)]
        results = [future.result(20) for future in futures]
    assert results[0][0] != results[1][0]
    assert sorted(row[1] for row in results) == ([200, 200] if same_key else [200, 409])
    if same_key:
        assert results[0][2] == results[1][2]
    else:
        assert next(row[2] for row in results if row[1] == 409) == "assignments_stale"
    with Session(postgres_engine) as db:
        assert db.get(PersonAssignmentState, person_id).version == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(PersonAssignmentOperation)
                .where(PersonAssignmentOperation.person_id == person_id)
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.resource_id == person_id,
                    AuditEvent.action == "person.assignments.update",
                )
            )
            == 1
        )
