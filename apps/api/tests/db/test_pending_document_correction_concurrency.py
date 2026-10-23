"""Country corrections serialize with normal pending creation, preserving housing."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.auth.service import AuthContext
from app.housing.models import HousingStay
from app.housing.occupancy_service import HousingOccupancyService
from app.housing.resident_readiness import HousingResidentReadinessPolicy
from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import authenticated_approval_facts
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
    PersonnelIntakeRevision,
)
from app.personnel_intake.schemas import PendingIntakeInput, PendingProfileCorrection
from app.rbac.models import UserRole
from tests.db.test_housing_stay_lifecycle import _allow, _input, _room
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.fixture
def committed_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


@pytest.mark.parametrize("first", ["correction", "create"])
@pytest.mark.parametrize(
    "old_country,new_country,create_country,correction_first,create_first",
    [
        ("US", "BR", "BR", (True, False), (False, True)),
        ("US", None, "BR", (True, False), (False, True)),
        (None, "BR", "US", (True, True), (True, False)),
        ("US", "BR", None, (True, False), (True, False)),
    ],
)
def test_pending_country_correction_races_with_same_bare_passport_create(
    committed_db,
    postgres_engine,
    first,
    old_country,
    new_country,
    create_country,
    correction_first,
    create_first,
):
    # Removing either service's bare-document lock must fail the blocked-worker
    # assertion; skipping conflict checks must fail the literal outcome matrix.
    db = committed_db
    users = _platform_users.__wrapped__(db)
    who = actor(users, "admin")
    actor_id = who.account.id
    number = "RACE-" + uuid4().hex.upper()
    original = PendingIntakeInput(
        kind="staff",
        full_name="原住宿护照人员",
        identity_number=number,
        document_type="passport",
        document_issuing_country=old_country,
        sex="male",
        date_of_birth=date(1990, 1, 1),
        team_allocation_status="not_required",
    )
    service = approvals(db)
    target = service.direct_create_pending(who, original, uuid4())
    room = _room(db)
    when = datetime.now(UTC)
    stay = HousingOccupancyService(
        db,
        resident_readiness=HousingResidentReadinessPolicy(
            db, cipher=service.cipher, lookup=service.lookup
        ),
    ).create_stay(
        room.id,
        _input(target.person_id, start=when),
        actor_user_id=actor_id,
        occurred_at=when,
        authorize_room=_allow,
    )
    db.commit()
    stay_id = stay.id
    original_approval = db.get(AssistedApproval, target.approval_id)
    request_id = original_approval.request_id
    old_identity_digest = db.get(Person, target.person_id).identity_digest
    bare_digest = db.get(Person, target.person_id).passport_number_digest
    original_person = dict(
        db.execute(select(Person.__table__).where(Person.id == target.person_id)).mappings().one()
    )
    original_stay = dict(
        db.execute(select(HousingStay.__table__).where(HousingStay.id == stay_id)).mappings().one()
    )
    models = (Person, PersonnelIntakeRequest, PersonnelIntakeRevision, AssistedApproval)
    before = {model: db.scalar(select(func.count()).select_from(model)) for model in models}
    account_models = (UserAccount, AccountLoginIdentifier, UserRole)
    before_accounts = {
        model: db.scalar(select(func.count()).select_from(model)) for model in account_models
    }
    db.rollback()

    correction = PendingProfileCorrection(
        **original.model_dump(
            exclude={"kind", "coach_person_ids", "team_id", "team_allocation_status"}
        ),
        expected_version=1,
        reason="本人确认护照签发国家",
        idempotency_key=uuid4(),
    )
    correction.document_issuing_country = new_country
    competitor = original.model_copy(
        update={"full_name": "竞争建档护照人员", "document_issuing_country": create_country}
    )
    document_lock = int.from_bytes(
        hashlib.sha256(("passport-number:" + bare_digest).encode()).digest()[:8],
        "big",
        signed=True,
    )
    acquired, attempted, release = Event(), Event(), Event()
    pids = {}
    traces = {"correction": [], "create": []}

    def perform(kind):
        with Session(postgres_engine) as worker:
            connection = worker.connection()
            connection.execute(text("SET LOCAL statement_timeout = '10s'"))
            connection.execute(text("SET LOCAL lock_timeout = '8s'"))
            pids[kind] = connection.scalar(text("SELECT pg_backend_pid()"))
            account = worker.get(UserAccount, actor_id)
            context = AuthContext(
                account=account, person=worker.get(Person, account.person_id), session=None
            )
            paused = False

            def is_document_lock(statement, parameters):
                values = parameters.values() if isinstance(parameters, dict) else parameters
                return "pg_advisory_xact_lock" in statement and document_lock in values

            def before_execute(conn, cursor, statement, parameters, context, executemany):
                traces[kind].append((statement, is_document_lock(statement, parameters)))
                if kind != first and is_document_lock(statement, parameters):
                    attempted.set()

            def after_execute(conn, cursor, statement, parameters, context, executemany):
                nonlocal paused
                if kind == first and not paused and is_document_lock(statement, parameters):
                    paused = True
                    acquired.set()
                    assert release.wait(timeout=8), "controller did not release document lock"

            event.listen(connection, "before_cursor_execute", before_execute)
            event.listen(connection, "after_cursor_execute", after_execute)
            try:
                writer = approvals(worker)
                try:
                    if kind == "correction":
                        result = writer.correct_pending_profile(
                            context, target.person_id, correction
                        )
                    else:
                        result = writer.direct_create_pending(context, competitor, uuid4())
                    return result
                except PersonnelIntakeError as error:
                    assert error.status == 409
                    assert not worker.in_transaction(), "conflict left a partial transaction"
                    return None
            finally:
                event.remove(connection, "before_cursor_execute", before_execute)
                event.remove(connection, "after_cursor_execute", after_execute)

    second = "create" if first == "correction" else "correction"
    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(perform, first)
        try:
            assert acquired.wait(timeout=8), "leader never acquired the bare passport lock"
            follower = pool.submit(perform, second)
            assert attempted.wait(timeout=8), "follower bypassed bare passport serialization"
            deadline = monotonic() + 5
            blocked = False
            with postgres_engine.connect() as observer:
                while monotonic() < deadline:
                    blocked = observer.scalar(
                        text("SELECT :leader = ANY(pg_blocking_pids(:follower))"),
                        {"leader": pids[first], "follower": pids[second]},
                    )
                    if blocked:
                        break
                    sleep(0.01)
            assert blocked, "independent connection was not blocked by the document lock"
        finally:
            release.set()
        results = {first: leader.result(timeout=12), second: follower.result(timeout=12)}

    expected = correction_first if first == "correction" else create_first
    assert (results["correction"] is not None, results["create"] is not None) == expected
    for trace in traces.values():
        first_document = next(index for index, (_, document) in enumerate(trace) if document)
        # The sole allowed predecessor is the independent idempotency key lock;
        # all merged lifecycle/credential locks and row locks follow the bare key.
        assert sum("pg_advisory_xact_lock" in sql for sql, _ in trace[:first_document]) == 1
        assert all("FOR UPDATE" not in sql.upper() for sql, _ in trace[:first_document])

    db.expire_all()
    correction_won, create_won = expected
    person = db.get(Person, target.person_id)
    assert person.document_issuing_country == (new_country if correction_won else old_country)
    assert person.passport_number_digest == bare_digest
    assert (
        service.cipher.decrypt(person.identity_ciphertext, field_name="identity_number") == number
    )
    if not correction_won:
        assert person.identity_digest == old_identity_digest
        assert (
            dict(
                db.execute(select(Person.__table__).where(Person.id == target.person_id))
                .mappings()
                .one()
            )
            == original_person
        )
    people = list(db.scalars(select(Person).where(Person.passport_number_digest == bare_digest)))
    assert {p.id for p in people} == {
        target.person_id,
        *([results["create"].person_id] if create_won else []),
    }
    assert len(people) == 1 + int(create_won)
    if create_won:
        assert results["create"].person_id != target.person_id
        assert len({p.document_issuing_country for p in people}) == 2
        assert None not in {p.document_issuing_country for p in people}
    assert (
        dict(
            db.execute(select(HousingStay.__table__).where(HousingStay.id == stay_id))
            .mappings()
            .one()
        )
        == original_stay
    )
    assert list(
        db.scalars(select(HousingStay.id).where(HousingStay.person_id == target.person_id))
    ) == [stay_id]
    profile = db.get(AssistedPersonProfile, target.person_id)
    assert profile.version == 1 + int(correction_won)
    assert profile.current_approval_id == (
        results["correction"].approval_id if correction_won else target.approval_id
    )
    assert db.get(PersonnelIntakeRequest, request_id).version == profile.version
    assert list(
        db.scalars(
            select(PersonnelIntakeRevision.version)
            .where(PersonnelIntakeRevision.request_id == request_id)
            .order_by(PersonnelIntakeRevision.version)
        )
    ) == ([1, 2] if correction_won else [1])
    for current in people:
        current_profile = db.get(AssistedPersonProfile, current.id)
        current_approval = db.get(AssistedApproval, current_profile.current_approval_id)
        facts, guardians = authenticated_approval_facts(
            current_approval, cipher=service.cipher, lookup=service.lookup
        )
        assert facts["identity_number"] == number and guardians == []
        assert facts["document_issuing_country"] == current.document_issuing_country
        assert current_approval.person_id == current.id
        revision = db.scalar(
            select(PersonnelIntakeRevision).where(
                PersonnelIntakeRevision.request_id == current_approval.request_id,
                PersonnelIntakeRevision.version == current_approval.revision_version,
            )
        )
        assert revision.identity_digest == current.identity_digest
        assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == current.id)) is None
    increments = (int(create_won), int(create_won), sum(expected), sum(expected))
    for model, increment in zip(models, increments):
        assert db.scalar(select(func.count()).select_from(model)) == before[model] + increment
    for model in account_models:
        assert db.scalar(select(func.count()).select_from(model)) == before_accounts[model]
