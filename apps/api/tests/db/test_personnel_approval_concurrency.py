from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier, BrokenBarrierError, Event
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.identity.models import GuardianProfile, GuardianRelationship, Person, StudentProfile
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    CoachPersonResponsibility,
    PersonnelCredentialReservation,
    PersonnelIntakeRequest,
    PersonnelIntakeRevision,
)
from app.personnel_intake.schemas import IntakeInput
from app.team_management.models import TeamMembership
from tests.api.conftest import make_cn_id
from tests.db.test_team_concurrency import committed_world as _committed_world
from tests.db.test_team_concurrency import reload_actor
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import service


@pytest.fixture
def committed_world(postgres_engine):
    yield from _committed_world.__wrapped__(postgres_engine)


@pytest.fixture
def approval_world(postgres_engine, committed_world):
    ids, teams = committed_world
    data = IntakeInput(
        kind="player",
        full_name="并发审批人员",
        identity_number=make_cn_id("20000102", 987),
        sex="male",
        date_of_birth=date(2000, 1, 2),
        phone="13900000987",
        email="approval-race@example.com",
        team_id=teams[0],
    )
    yield ids, teams, data
    with Session(postgres_engine) as db:
        requests = list(
            db.scalars(
                select(PersonnelIntakeRequest.id).where(
                    PersonnelIntakeRequest.submitted_by_user_id.in_([v[0] for v in ids.values()])
                )
            )
        )
        people = list(
            db.scalars(
                select(AssistedApproval.person_id).where(AssistedApproval.request_id.in_(requests))
            )
        )
        db.execute(
            delete(GuardianRelationship).where(GuardianRelationship.student_person_id.in_(people))
        )
        db.execute(delete(TeamMembership).where(TeamMembership.person_id.in_(people)))
        db.execute(
            delete(CoachPersonResponsibility).where(CoachPersonResponsibility.person_id.in_(people))
        )
        db.execute(delete(AssistedPersonProfile).where(AssistedPersonProfile.person_id.in_(people)))
        for model in (AssistedApproval, PersonnelIntakeRevision):
            table = model.__tablename__
            db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER trg_{table}_immutable"))
            db.execute(delete(model).where(model.request_id.in_(requests)))
            db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER trg_{table}_immutable"))
        db.execute(
            delete(PersonnelCredentialReservation).where(
                PersonnelCredentialReservation.request_id.in_(requests)
            )
        )
        db.execute(delete(PersonnelIntakeRequest).where(PersonnelIntakeRequest.id.in_(requests)))
        db.execute(delete(StudentProfile).where(StudentProfile.person_id.in_(people)))
        db.execute(delete(Person).where(Person.id.in_(people)))
        db.commit()


@pytest.fixture
def graph_world(postgres_engine, approval_world):
    from app.personnel_intake.codec import normalize_payload
    from app.personnel_intake.guardian_records import insert_person

    ids, teams, base = approval_world
    with Session(postgres_engine) as db:
        s = approvals(db)
        data = [
            IntakeInput.model_validate(
                {
                    **base.model_dump(),
                    "full_name": f"图节点{index}",
                    "identity_number": make_cn_id("20000102", 701 + 2 * index),
                    "phone": f"139000007{index:02d}",
                    "email": f"graph-{index}@example.com",
                    "team_id": teams[1 if index == 4 else 0],
                }
            )
            for index in range(8)
        ]
        people = [
            insert_person(db, normalize_payload(value), cipher=s.cipher, lookup=s.lookup).id
            for value in data
        ]
        db.add_all([GuardianProfile(person_id=person_id) for person_id in people])
        db.add_all(
            [
                StudentProfile(
                    person_id=people[index],
                    student_number=f"GRAPH-{uuid4().hex[:20]}",
                    status="draft",
                )
                for index in (0, 4)
            ]
        )
        db.add_all(
            [
                GuardianRelationship(
                    guardian_person_id=people[left],
                    student_person_id=people[right],
                    relationship="监护人",
                    is_primary=False,
                    valid_from=date(2020, 1, 1),
                )
                for left, right in ((0, 1), (1, 2), (2, 3), (4, 5), (5, 6), (6, 7))
            ]
        )
        db.commit()
        first = s.direct_create(reload_actor(db, ids["admin2"]), data[4], uuid4())
    yield ids, data, people, first
    # Approval cleanup runs next; keep its target E until its immutable lineage is removed.
    with Session(postgres_engine) as db:
        db.execute(
            delete(GuardianRelationship).where(GuardianRelationship.guardian_person_id.in_(people))
        )
        db.execute(delete(GuardianProfile).where(GuardianProfile.person_id.in_(people)))
        assisted = set(
            db.scalars(
                select(AssistedPersonProfile.person_id).where(
                    AssistedPersonProfile.person_id.in_(people)
                )
            )
        )
        removable = [person for person in people if person not in assisted]
        db.execute(delete(StudentProfile).where(StudentProfile.person_id.in_(removable)))
        db.execute(delete(Person).where(Person.id.in_(removable)))
        db.commit()


@pytest.mark.parametrize("other_writer", ["assisted", "legacy"])
def test_disjoint_edges_cannot_close_guardian_cycle(
    postgres_engine, graph_world, monkeypatch, other_writer
):
    from app.identity.service import GuardianCycle, IdentityService
    from app.personnel_intake.guardian_records import AssistedGuardianWriter
    from app.personnel_intake.schemas import AssistedGuardianInput, AssistedProfileCorrection

    ids, data, people, first = graph_world
    barrier = Barrier(2)
    original_assisted, original_legacy = (
        AssistedGuardianWriter._cycle,
        IdentityService._would_create_guardian_cycle,
    )

    def synchronize(result):
        # Before the fix both disjoint writers finish the cycle read before either writes.
        # Correct locking serializes them: the first times out, commits, second observes drift.
        try:
            barrier.wait(1)
        except BrokenBarrierError:
            pass
        return result

    monkeypatch.setattr(
        AssistedGuardianWriter,
        "_cycle",
        lambda self, a, b: synchronize(original_assisted(self, a, b)),
    )
    monkeypatch.setattr(
        IdentityService,
        "_would_create_guardian_cycle",
        lambda self, a, b: synchronize(original_legacy(self, a, b)),
    )

    def with_guardian(target, guardian):
        fields = {
            key: getattr(data[guardian], key)
            for key in AssistedGuardianInput.model_fields
            if key in IntakeInput.model_fields
        }
        return data[target].model_copy(
            update={
                "guardians": [
                    AssistedGuardianInput(**fields, relationship="监护人", is_primary=False)
                ]
            }
        )

    def run(index):
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pid = db.scalar(select(func.pg_backend_pid()))
            s = approvals(db)
            try:
                if index == 0:
                    value = with_guardian(4, 3)  # D -> E
                    correction = AssistedProfileCorrection(
                        **{
                            key: getattr(value, key)
                            for key in AssistedProfileCorrection.model_fields
                            if key in IntakeInput.model_fields
                        },
                        expected_version=1,
                        reason="并发图检查",
                        idempotency_key=uuid4(),
                    )
                    s.correct_profile(reload_actor(db, ids["admin2"]), first.person_id, correction)
                elif other_writer == "assisted":
                    s.direct_create(
                        reload_actor(db, ids["admin"]), with_guardian(0, 7), uuid4()
                    )  # H -> A
                else:
                    IdentityService(db, cipher=s.cipher, lookup=s.lookup).add_guardian(
                        people[0], people[7], "监护人", False, date(2020, 1, 1), None
                    )
                    db.commit()
                return pid, 200
            except (PersonnelIntakeError, GuardianCycle):
                return pid, 409

    with ThreadPoolExecutor(2) as pool:
        results = [future.result(20) for future in [pool.submit(run, index) for index in range(2)]]
    assert results[0][0] != results[1][0]
    assert sorted(result[1] for result in results) == [200, 409]


@pytest.mark.parametrize("mode", ["approval", "direct", "different_payload", "correction"])
def test_duplicate_approval_is_atomic(postgres_engine, approval_world, mode):
    ids, _teams, data = approval_world
    pending = None
    if mode == "approval":
        with Session(postgres_engine) as db:
            pending = service(db).submit(reload_actor(db, ids["admin"]), data).id
    correction_person_id = None
    if mode == "correction":
        with Session(postgres_engine) as db:
            correction_person_id = (
                approvals(db)
                .direct_create(reload_actor(db, ids["admin2"]), data, uuid4())
                .person_id
            )
    key, barrier = uuid4(), Barrier(2)

    def run(index):
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pid = db.scalar(select(func.pg_backend_pid()))
            who = reload_actor(db, ids["admin2"])
            barrier.wait(10)
            try:
                if correction_person_id:
                    from app.personnel_intake.schemas import AssistedProfileCorrection

                    correction = AssistedProfileCorrection(
                        **{
                            key: getattr(data, key)
                            for key in AssistedProfileCorrection.model_fields
                            if key in type(data).model_fields
                        },
                        expected_version=1,
                        reason="并发复核",
                        idempotency_key=key,
                    )
                    result = approvals(db).correct_profile(who, correction_person_id, correction)
                    return pid, 200, result
                result = (
                    approvals(db).approve(who, pending, 1)
                    if pending
                    else approvals(db).direct_create(
                        who,
                        data.model_copy(update={"full_name": "changed"})
                        if mode == "different_payload" and index
                        else data,
                        key,
                    )
                )
                return pid, 200, result
            except PersonnelIntakeError as exc:
                return pid, exc.status, None

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(run, i) for i in range(2)]
        results = [future.result(20) for future in futures]
    assert results[0][0] != results[1][0]
    assert sorted(row[1] for row in results) == (
        [200, 409] if mode == "different_payload" else [200, 200]
    )
    if mode != "different_payload":
        assert results[0][2] == results[1][2]
    with Session(postgres_engine) as db:
        assert db.scalar(select(func.count()).select_from(AssistedApproval)) == (
            2 if mode == "correction" else 1
        )
        assert db.scalar(select(func.count()).select_from(AssistedPersonProfile)) == 1
        assert db.scalar(select(func.count()).select_from(TeamMembership)) == 1


@pytest.mark.parametrize("mode", ["approval", "direct"])
def test_shared_phone_approval_and_legacy_update_have_no_lock_cycle(
    postgres_engine, approval_world, monkeypatch, mode
):
    from sqlalchemy.exc import OperationalError

    from app.identity.readiness import GuardianReadinessPolicy
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from app.personnel_intake import approval_service as module

    ids, _teams, data = approval_world
    target_id = ids["admin2"][1]
    with Session(postgres_engine) as db:
        s = approvals(db)
        target = db.get(Person, target_id)
        phone = "13900000501"
        identity = make_cn_id("19950101", 501)
        target.identity_ciphertext = s.cipher.encrypt(identity, field_name="identity_number")
        target.identity_digest = s.lookup.digest(identity)
        target.phone_ciphertext = s.cipher.encrypt(phone, field_name="phone")
        target.phone_digest = s.lookup.digest(phone)
        target.email_ciphertext = s.cipher.encrypt("lock-actor@example.com", field_name="email")
        target.email_digest = s.lookup.digest_domain("person-email", "lock-actor@example.com")
        db.commit()
        data = IntakeInput.model_validate({**data.model_dump(), "phone": phone})
        pending = (
            service(db).submit(reload_actor(db, ids["admin"]), data).id
            if mode == "approval"
            else None
        )
    row_locked, phone_locked, start = Event(), Event(), Barrier(2)
    original_rows = GuardianReadinessPolicy.lock_students
    original_memberships = module.lock_person_memberships

    def rows(policy, person_ids):
        result = original_rows(policy, person_ids)
        if target_id in {person.id for person in result}:
            row_locked.set()
            phone_locked.wait(1)
        return result

    def memberships(db, keys):
        original_memberships(db, keys)
        phone_locked.set()
        row_locked.wait(1)

    monkeypatch.setattr(GuardianReadinessPolicy, "lock_students", rows)
    monkeypatch.setattr(module, "lock_person_memberships", memberships)

    def run(update):
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pid = db.scalar(select(func.pg_backend_pid()))
            s = approvals(db)
            start.wait(5)
            try:
                if update:
                    IdentityService(db, cipher=s.cipher, lookup=s.lookup).update_person(
                        target_id,
                        PersonUpdate(full_name="并发更新管理员"),
                        actor_user_id=ids["admin2"][0],
                        request_id=str(uuid4()),
                    )
                elif pending:
                    s.approve(reload_actor(db, ids["admin2"]), pending, 1)
                else:
                    s.direct_create(reload_actor(db, ids["admin2"]), data, uuid4())
                return pid, "ok"
            except OperationalError as exc:
                return pid, exc.orig.sqlstate

    with ThreadPoolExecutor(2) as pool:
        outcomes = [
            future.result(20) for future in [pool.submit(run, update) for update in (True, False)]
        ]
    assert outcomes[0][0] != outcomes[1][0]
    assert [outcome[1] for outcome in outcomes] == ["ok", "ok"]
