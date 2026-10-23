"""Cross-entry lock graphs, with observed PostgreSQL waits instead of sleeps."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Event
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.housing.models import HousingStay
from app.identity.models import GuardianRelationship, Person
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.service import IdentityService
from app.personnel_intake import approval_service as approval_module
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.personnel_intake.schemas import AssistedProfileCorrection
from app.registration.finalization_service import (
    RegistrationConflictPendingReview,
    RegistrationFinalizationService,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.matching_service import RegistrationMatchingService
from app.registration.models import RegistrationApplication
from tests.api.conftest import make_cn_id
from tests.db.test_assisted_activation_concurrency import activation_world as _activation_world
from tests.db.test_personnel_approval_concurrency import approval_world as _approval_world
from tests.db.test_personnel_approval_concurrency import committed_world as _committed_world
from tests.db.test_team_concurrency import reload_actor
from tests.services.test_personnel_approval import approvals


@pytest.fixture
def committed_world(postgres_engine):
    yield from _committed_world.__wrapped__(postgres_engine)


@pytest.fixture
def approval_world(postgres_engine, committed_world):
    yield from _approval_world.__wrapped__(postgres_engine, committed_world)


@pytest.fixture
def activation_world(postgres_engine, approval_world, request):
    yield from _activation_world.__wrapped__(postgres_engine, approval_world, request)


def run_behind_assisted_fences(
    engine,
    monkeypatch,
    first,
    second,
    observe_rows,
    record_property,
    *,
    hold_person_rows=False,
    guardian_ending=False,
):
    """Hold the real assisted fence set, then prove the legacy caller waits on it.

    Under the old registration order the waiter already owns Person rows. Releasing
    assisted then closes the actual DB cycle. Correct order waits before those rows.
    """
    held, release, legacy_started, rows_locked = Event(), Event(), Event(), Event()
    pids = {}
    original = approval_module.lock_participants

    def pause_writer():
        held.set()
        assert release.wait(10), "controller never released writer"

    def pause(db, person_ids, account_ids):
        if db.info.get("final_race") == "assisted" and not hold_person_rows:
            pause_writer()
        result = original(db, person_ids, account_ids)
        if db.info.get("final_race") == "assisted" and hold_person_rows:
            pause_writer()
        return result

    monkeypatch.setattr(approval_module, "lock_participants", pause)
    observe_rows(rows_locked)
    if guardian_ending:
        original_guardian_rows = GuardianReadinessPolicy.lock_students

        def guardian_rows(policy, person_ids):
            result = original_guardian_rows(policy, person_ids)
            if policy.db.info.get("final_race") == "assisted" and not held.is_set():
                pause_writer()
            return result

        monkeypatch.setattr(GuardianReadinessPolicy, "lock_students", guardian_rows)

    def run(name, operation):
        with Session(engine) as db:
            db.info["final_race"] = name
            db.execute(text("SET LOCAL lock_timeout = '8s'"))
            db.execute(text("SET LOCAL statement_timeout = '12s'"))
            pids[name] = db.scalar(select(func.pg_backend_pid()))
            if name == "legacy":
                legacy_started.set()
            try:
                return operation(db)
            except OperationalError as error:
                db.rollback()
                return error.orig.sqlstate

    with ThreadPoolExecutor(2) as pool:
        assisted = pool.submit(run, "assisted", first)
        try:
            assert held.wait(10)
            legacy = pool.submit(run, "legacy", second)
            assert legacy_started.wait(10)
            assert pids["assisted"] != pids["legacy"]
            with engine.connect() as monitor:
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    blockers = monitor.scalar(select(func.pg_blocking_pids(pids["legacy"])))
                    if pids["assisted"] in blockers:
                        break
                    if legacy.done():
                        pytest.fail(
                            f"legacy exited before the expected fence wait: {legacy.result()}"
                        )
                else:
                    pytest.fail("did not observe the named PostgreSQL fence wait")
            legacy_owned_rows_while_waiting = rows_locked.is_set()
        finally:
            release.set()
        outcomes = [assisted.result(20), legacy.result(20)]
    record_property("postgres_backend_pids", str(pids))
    record_property("legacy_owned_rows_while_waiting", legacy_owned_rows_while_waiting)
    assert outcomes == ["ok", "controlled"], outcomes
    assert not legacy_owned_rows_while_waiting, "legacy acquired Person before its shared fence"


def test_registration_contact_candidate_waits_before_person_rows(
    postgres_engine, approval_world, monkeypatch, record_property
):
    ids, _teams, data = approval_world
    phone = data.phone.get_secret_value()
    with Session(postgres_engine) as setup:
        s = approvals(setup)
        approving_actor = setup.get(Person, ids["admin2"][1])
        approving_actor.phone_ciphertext = s.cipher.encrypt(phone, field_name="phone")
        approving_actor.phone_digest = s.lookup.digest(phone)
        identifier = normalize_registration_identifier("phone", phone)
        identity = make_cn_id("20010102", 911)
        application = RegistrationApplication(
            primary_purpose="student",
            state="ready_to_finalize",
            verified_identifier_kind="phone",
            verified_identifier_digest=registration_identifier_digest(s.lookup, identifier),
            verified_identifier_ciphertext=s.cipher.encrypt(
                phone, field_name="registration_identifier"
            ),
            identity_ciphertext=s.cipher.encrypt(identity, field_name="registration_identity"),
            identity_digest=s.lookup.digest(identity),
            full_name="并发注册人员",
            sex="male",
            date_of_birth=date(2001, 1, 2),
            password_hash="unused-conflict-only",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        setup.add(application)
        setup.commit()
        app_id = application.id
        accounts_before = list(
            setup.execute(select(*UserAccount.__table__.columns).order_by(UserAccount.id))
        )
    original_rows = RegistrationMatchingService.lock_candidate_people_in_transaction

    def observe(flag):
        def rows(service, person_ids):
            result = original_rows(service, person_ids)
            if service.db.info.get("final_race") == "legacy":
                flag.set()
            return result

        monkeypatch.setattr(
            RegistrationMatchingService, "lock_candidate_people_in_transaction", rows
        )

    def assisted(db):
        approvals(db).direct_create(reload_actor(db, ids["admin2"]), data, uuid4())
        return "ok"

    def registration(db):
        s = approvals(db)
        try:
            RegistrationFinalizationService(db, cipher=s.cipher, lookup=s.lookup).finalize(
                app_id, request_id=str(uuid4())
            )
        except RegistrationConflictPendingReview:
            return "controlled"
        return "unexpected_success"

    try:
        run_behind_assisted_fences(
            postgres_engine, monkeypatch, assisted, registration, observe, record_property
        )
        with Session(postgres_engine) as check:
            assert check.get(RegistrationApplication, app_id).state == "conflict"
            assert (
                list(check.execute(select(*UserAccount.__table__.columns).order_by(UserAccount.id)))
                == accounts_before
            )
            assert check.scalar(select(func.count()).select_from(AssistedApproval)) == 1
            assert check.scalar(select(func.count()).select_from(GuardianRelationship)) == 0
            assert check.scalar(select(func.count()).select_from(HousingStay)) == 0
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationApplication).where(RegistrationApplication.id == app_id)
            )


@pytest.mark.parametrize("activation_world", ["student"], indirect=True)
def test_guardian_end_waits_before_rows_while_assisted_correction_holds_fence(
    postgres_engine, activation_world, monkeypatch, record_property
):
    approved, data, ids = activation_world
    with Session(postgres_engine) as setup:
        edge_id = setup.scalar(
            select(GuardianRelationship.id).where(
                GuardianRelationship.student_person_id == approved.person_id
            )
        )
        before = setup.get(AssistedApproval, approved.approval_id).snapshot_digest
    original_rows = GuardianReadinessPolicy.lock_students

    def observe(flag):
        def rows(policy, person_ids):
            result = original_rows(policy, person_ids)
            if policy.db.info.get("final_race") == "legacy":
                flag.set()
            return result

        monkeypatch.setattr(GuardianReadinessPolicy, "lock_students", rows)

    def correction(db):
        fields = {
            key: getattr(data, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(data).model_fields
        }
        approvals(db).correct_profile(
            reload_actor(db, ids["admin2"]),
            approved.person_id,
            AssistedProfileCorrection(
                **(fields | {"full_name": "更正后未成年"}),
                expected_version=1,
                reason="并发更正测试",
                idempotency_key=uuid4(),
            ),
        )
        return "ok"

    def ending(db):
        s = approvals(db)
        IdentityService(db, cipher=s.cipher, lookup=s.lookup).end_guardian_relationship(
            approved.person_id,
            edge_id,
            ended_by_user_id=ids["admin2"][0],
            reason="并发结束关系",
            ended_at=datetime.now(UTC),
            request_id=str(uuid4()),
        )
        return "controlled"

    run_behind_assisted_fences(
        postgres_engine, monkeypatch, correction, ending, observe, record_property
    )
    with Session(postgres_engine) as check:
        assert check.get(AssistedPersonProfile, approved.person_id).version == 2
        assert check.get(AssistedApproval, approved.approval_id).snapshot_digest == before
        assert check.get(GuardianRelationship, edge_id).ended_at is not None
        assert check.scalar(select(func.count()).select_from(AssistedApproval)) == 2
        assert (
            check.scalar(select(UserAccount.id).where(UserAccount.person_id == approved.person_id))
            is None
        )
        assert check.scalar(select(func.count()).select_from(HousingStay)) == 0


@pytest.mark.parametrize("activation_world", ["student"], indirect=True)
@pytest.mark.parametrize("writer", ["correction", "guardian_end"])
def test_allocation_revalidates_after_correction_or_guardian_invalidation(
    postgres_engine, activation_world, monkeypatch, record_property, request, writer
):
    from functools import partial

    from app.housing.errors import MinorGuardianRequired
    from app.housing.occupancy_service import HousingOccupancyService
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.housing.schemas import StayCreate
    from tests.db.test_housing_concurrency import _cleanup_committed_race_rows, _rooms
    from tests.invited_housing_helpers import stay

    approved, data, ids = activation_world
    now = datetime.now(UTC)
    with Session(postgres_engine) as setup:
        rooms = _rooms(setup, 1, capacity=2)
        room_id = rooms[0].id
        edge_id = setup.scalar(
            select(GuardianRelationship.id).where(
                GuardianRelationship.student_person_id == approved.person_id
            )
        )
        # A prior real occupancy is never erased or rewritten by readiness changes.
        historical = stay(
            setup,
            setup.get(UserAccount, ids["admin2"][0]),
            setup.get(Person, approved.person_id),
            status="checked_out",
            start=now - timedelta(days=3),
            actual_end=now - timedelta(days=1),
        )
        history_id = historical.id
        before = tuple(getattr(historical, column.key) for column in HousingStay.__table__.columns)
        all_room_ids = (room_id, historical.room_id)
        setup.commit()
    request.addfinalizer(
        partial(_cleanup_committed_race_rows, postgres_engine, room_ids=all_room_ids, person_ids=())
    )
    original_rows = HousingOccupancyService.lock_selectable_residents

    def observe(flag):
        def rows(service, person_ids):
            result = original_rows(service, person_ids)
            if service.db.info.get("final_race") == "legacy":
                flag.set()
            return result

        monkeypatch.setattr(HousingOccupancyService, "lock_selectable_residents", rows)

    def mutate(db):
        s = approvals(db)
        if writer == "guardian_end":
            IdentityService(db, cipher=s.cipher, lookup=s.lookup).end_guardian_relationship(
                approved.person_id,
                edge_id,
                ended_by_user_id=ids["admin2"][0],
                reason="并发结束关系",
                ended_at=now,
                request_id=str(uuid4()),
            )
        else:
            fields = {
                key: getattr(data, key)
                for key in AssistedProfileCorrection.model_fields
                if key in type(data).model_fields
            }
            s.correct_profile(
                reload_actor(db, ids["admin2"]),
                approved.person_id,
                AssistedProfileCorrection(
                    **(fields | {"full_name": "入住前更正人员"}),
                    expected_version=1,
                    reason="入住前核实资料",
                    idempotency_key=uuid4(),
                ),
            )
        return "ok"

    def allocate(db):
        s = approvals(db)
        occupancy = HousingOccupancyService(
            db,
            resident_readiness=HousingResidentReadinessPolicy(db, cipher=s.cipher, lookup=s.lookup),
        )
        if writer == "guardian_end":
            with pytest.raises(MinorGuardianRequired):
                occupancy.create_stay(
                    room_id,
                    StayCreate(
                        person_id=approved.person_id,
                        stay_purpose="permanent",
                        duration_mode="long_term",
                        planned_check_in_at=now,
                    ),
                    actor_user_id=ids["admin2"][0],
                    occurred_at=now,
                    authorize_room=lambda room: None,
                )
        else:
            occupancy.create_stay(
                room_id,
                StayCreate(
                    person_id=approved.person_id,
                    stay_purpose="permanent",
                    duration_mode="long_term",
                    planned_check_in_at=now,
                ),
                actor_user_id=ids["admin2"][0],
                occurred_at=now,
                authorize_room=lambda room: None,
            )
        return "controlled"

    run_behind_assisted_fences(
        postgres_engine,
        monkeypatch,
        mutate,
        allocate,
        observe,
        record_property,
        hold_person_rows=True,
        guardian_ending=writer == "guardian_end",
    )
    with Session(postgres_engine) as check:
        history = check.get(HousingStay, history_id)
        assert (
            tuple(getattr(history, column.key) for column in HousingStay.__table__.columns)
            == before
        )
        assert check.scalar(
            select(func.count()).select_from(HousingStay).where(HousingStay.room_id == room_id)
        ) == (writer == "correction")
        assert check.get(AssistedPersonProfile, approved.person_id).version == (
            2 if writer == "correction" else 1
        )
        assert (check.get(GuardianRelationship, edge_id).ended_at is not None) == (
            writer == "guardian_end"
        )
        assert (
            check.scalar(select(UserAccount.id).where(UserAccount.person_id == approved.person_id))
            is None
        )
