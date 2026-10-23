from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from threading import Event
from time import monotonic, sleep

import pytest
from sqlalchemy import delete, event, or_, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.service import AuthContext
from app.housing.api_support import LockedHousingAuthorizationDenied
from app.housing.errors import HousingServiceError
from app.housing.history_void_models import HousingStayVoidOperation
from app.housing.models import HousingBuilding, HousingFloor, HousingRoom, HousingStay
from app.identity.models import Person, StaffProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import ROLES
from tests.db.test_housing_transfer_checkout import _account_person
from tests.services.test_housing_history_void import command, counts, service, setup_history

_CLEANUP_TRIGGERS = (
    ("audit_events", "audit_events_no_update"),
    ("housing_stay_void_operations", "trg_housing_stay_void_operations_immutable"),
    ("housing_stays", "trg_housing_stays_void_fact_immutable"),
)


def _trigger_states(db):
    return dict(
        db.execute(
            text(
                "SELECT tgname,tgenabled FROM pg_trigger WHERE NOT tgisinternal "
                "AND tgname IN ('audit_events_no_update','trg_housing_stay_void_operations_immutable',"
                "'trg_housing_stays_void_fact_immutable')"
            )
        ).all()
    )


def _setup_owned_history(db, owned):
    actor, room, stay = setup_history(db)
    # Record before the caller commits, so success, assertion failure and an
    # ambiguous commit outcome all have the same exact-ID finalizer.
    owned["people"].update((actor.person.id, stay.person_id))
    owned["stays"].add(stay.id)
    owned["rooms"].add(room.id)
    owned["floors"].add(room.floor_id)
    owned["buildings"].add(room.building_id)
    return actor, room, stay


def _cleanup_owned_history(engine, owned):
    if not any(owned.values()):
        return
    with Session(engine) as db, db.begin():
        name = db.scalar(text("SELECT current_database()"))
        assert isinstance(name, str) and (name == "erp_e2e" or name.endswith("_test"))
        assert _trigger_states(db) == {name: "O" for _, name in _CLEANUP_TRIGGERS}
        accounts = tuple(
            db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(owned["people"])))
        )
        # PostgreSQL transactional DDL restores all prior trigger states if any
        # guarded delete fails. On success explicitly re-enable before commit.
        for table, trigger in _CLEANUP_TRIGGERS:
            db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
        db.execute(
            delete(HousingStayVoidOperation).where(
                HousingStayVoidOperation.stay_id.in_(owned["stays"])
            )
        )
        db.execute(
            delete(AuditEvent).where(
                or_(
                    AuditEvent.actor_user_id.in_(accounts),
                    AuditEvent.resource_id.in_(owned["stays"]),
                )
            )
        )
        db.execute(delete(HousingStay).where(HousingStay.id.in_(owned["stays"])))
        db.execute(
            delete(UserPermissionOverride).where(UserPermissionOverride.user_id.in_(accounts))
        )
        db.execute(delete(UserRole).where(UserRole.user_id.in_(accounts)))
        db.execute(
            delete(AccountLoginIdentifier).where(AccountLoginIdentifier.account_id.in_(accounts))
        )
        db.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(owned["people"])))
        db.execute(delete(UserAccount).where(UserAccount.id.in_(accounts)))
        for model, key in (
            (HousingRoom, "rooms"),
            (HousingFloor, "floors"),
            (HousingBuilding, "buildings"),
            (Person, "people"),
        ):
            db.execute(delete(model).where(model.id.in_(owned[key])))
        for table, trigger in _CLEANUP_TRIGGERS:
            db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))


@contextmanager
def _committed_history_rows(engine):
    owned = {name: set() for name in ("people", "stays", "rooms", "floors", "buildings")}
    try:
        yield owned
    finally:
        _cleanup_owned_history(engine, owned)


@pytest.fixture
def owned_history_rows(postgres_engine):
    with _committed_history_rows(postgres_engine) as owned:
        yield owned


@pytest.mark.parametrize("same_key", [True, False])
def test_independent_connections_void_only_once(postgres_engine, owned_history_rows, same_key):
    with Session(postgres_engine) as db:
        actor, _, stay = _setup_owned_history(db, owned_history_rows)
        actor_id, person_id, stay_id = actor.account.id, actor.person.id, stay.id
        second_person_id, second_actor_id = person_id, actor_id
        if not same_key:
            other_person, second_actor_id = _account_person(db, name="并发历史修正超管")
            owned_history_rows["people"].add(other_person.id)
            second_person_id = other_person.id
            db.add(UserRole(user_id=second_actor_id, role_id=ROLES["super_admin"].id))
        db.commit()
    cmd = command()
    first_locked, contender_at_lock, release_first = Event(), Event(), Event()
    backend_pids = {}
    overlap_deadline = {}

    def run(command_, account_id, actor_person_id, *, first):
        with Session(postgres_engine) as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            backend_pids["first" if first else "second"] = connection.scalar(
                text("SELECT pg_backend_pid()")
            )
            current = AuthContext(
                account=connection.get(UserAccount, account_id),
                person=connection.get(Person, actor_person_id),
                session=AuthSession(),
            )
            sql_connection = connection.connection()

            def observe_lock(conn, cursor, statement, parameters, context, executemany):
                participant_lock = "FROM people" in statement and "FOR UPDATE" in statement
                if first and participant_lock:
                    # after_cursor_execute: the first transaction actually owns
                    # the shared resident lock, not merely a queued statement.
                    overlap_deadline["at"] = monotonic() + 3
                    first_locked.set()
                    assert release_first.wait(timeout=3), "first lock release handshake timed out"
                elif not first and (
                    (same_key and "pg_advisory_xact_lock" in statement)
                    or (not same_key and participant_lock)
                ):
                    # before_cursor_execute: same-key requests contend on the
                    # advisory lock; distinct keys contend on the resident row.
                    contender_at_lock.set()

            event_name = "after_cursor_execute" if first else "before_cursor_execute"
            event.listen(sql_connection, event_name, observe_lock)
            try:
                try:
                    return service(connection).void(current, stay_id, command_)
                except HousingServiceError as exc:
                    return exc.code
            finally:
                event.remove(sql_connection, event_name, observe_lock)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(run, cmd, actor_id, person_id, first=True)
        try:
            assert first_locked.wait(timeout=3), "first request did not acquire participant lock"
            two = pool.submit(
                run, cmd if same_key else command(), second_actor_id, second_person_id, first=False
            )
            assert contender_at_lock.wait(timeout=max(0, overlap_deadline["at"] - monotonic())), (
                "second request did not reach its contender lock"
            )
            blocked_by_first = False
            with Session(postgres_engine) as observer:
                while monotonic() < overlap_deadline["at"]:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": backend_pids["second"]}
                    )
                    if backend_pids["first"] in blockers:
                        blocked_by_first = True
                        break
                    sleep(0.01)
            assert blocked_by_first, "second request never blocked on the first transaction"
            assert not one.done() and not two.done(), "void requests did not overlap"
        finally:
            # A root-controlled release, not a two-party barrier after a shared
            # lock: the blocked contender never has to acquire it to make progress.
            release_first.set()
        outcomes = [one.result(timeout=10), two.result(timeout=10)]
    if same_key:
        assert outcomes[0] == outcomes[1]
    else:
        assert sum(isinstance(outcome, str) for outcome in outcomes) == 1
        assert "housing_stay_already_voided" in outcomes
    with Session(postgres_engine) as db:
        assert counts(db, stay_id) == (1, 1)


def test_role_revocation_wins_while_void_waits_for_participant_lock(
    postgres_engine, owned_history_rows
):
    with Session(postgres_engine) as db:
        actor, _, stay = _setup_owned_history(db, owned_history_rows)
        actor_id, person_id, stay_id = actor.account.id, actor.person.id, stay.id
        db.commit()
    waiting = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "FROM people" in statement and "FOR UPDATE" in statement:
            waiting.set()

    def run():
        with Session(postgres_engine) as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            actor = AuthContext(
                account=db.get(UserAccount, actor_id),
                person=db.get(Person, person_id),
                session=AuthSession(),
            )
            try:
                service(db).void(actor, stay_id, command())
            except LockedHousingAuthorizationDenied:
                return "denied"
            return "unexpected success"

    with Session(postgres_engine) as revoker:
        revoker.scalar(select(Person).where(Person.id == person_id).with_for_update())
        revoker.scalar(select(UserAccount).where(UserAccount.id == actor_id).with_for_update())
        event.listen(postgres_engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(run)
                assert waiting.wait(timeout=3), "void did not reach participant lock"
                revoker.execute(
                    delete(UserRole).where(
                        UserRole.user_id == actor_id, UserRole.role_id == ROLES["super_admin"].id
                    )
                )
                revoker.commit()
                assert pending.result(timeout=10) == "denied"
        finally:
            event.remove(postgres_engine, "before_cursor_execute", observe)
    with Session(postgres_engine) as db:
        assert counts(db, stay_id) == (0, 0)


def test_independent_observer_sees_no_partial_fact_after_audit_failure(
    postgres_engine, monkeypatch, owned_history_rows
):
    with Session(postgres_engine) as db:
        actor, _, stay = _setup_owned_history(db, owned_history_rows)
        stay_id, resident_id = stay.id, stay.person_id
        db.commit()

        def snapshot(observer):
            return tuple(
                observer.execute(text(sql), {"stay_id": stay_id, "person_id": resident_id})
                .scalars()
                .all()
                for sql in (
                    "SELECT row_to_json(p)::text FROM people p WHERE id=:person_id",
                    "SELECT row_to_json(a)::text FROM user_accounts a WHERE person_id=:person_id",
                    "SELECT row_to_json(s)::text FROM housing_stays s WHERE id=:stay_id",
                )
            )

        with Session(postgres_engine) as observer:
            state_before = snapshot(observer)

        def fail(*args, **kwargs):
            raise RuntimeError("injected audit failure")

        monkeypatch.setattr(AuditService, "append", fail)
        with pytest.raises(RuntimeError, match="injected audit failure"):
            service(db).void(actor, stay_id, command())
        with Session(postgres_engine) as observer:
            assert snapshot(observer) == state_before
            assert counts(observer, stay_id) == (0, 0)


@pytest.mark.parametrize("body_fails", [False, True], ids=["success", "assertion-failure"])
def test_committed_history_finalizer_cleans_exact_rows_on_success_or_failure(
    postgres_engine, body_fails
):
    with _committed_history_rows(postgres_engine) as unrelated:
        with Session(postgres_engine) as db:
            _, unrelated_room, unrelated_stay = _setup_owned_history(db, unrelated)
            unrelated_room_id, unrelated_stay_id = unrelated_room.id, unrelated_stay.id
            db.commit()
        expected_error = (
            pytest.raises(AssertionError, match="synthetic_body_failure")
            if body_fails
            else nullcontext()
        )
        with expected_error, _committed_history_rows(postgres_engine) as owned:
            with Session(postgres_engine) as db:
                actor, room, stay = _setup_owned_history(db, owned)
                actor_id, person_id, stay_id = actor.account.id, stay.person_id, stay.id
                room_id, floor_id, building_id = room.id, room.floor_id, room.building_id
                db.commit()
                service(db).void(actor, stay_id, command())
            if body_fails:
                raise AssertionError("synthetic_body_failure")
        with Session(postgres_engine) as observer:
            for model, identifier in (
                (HousingStay, stay_id),
                (Person, person_id),
                (UserAccount, actor_id),
                (HousingRoom, room_id),
                (HousingFloor, floor_id),
                (HousingBuilding, building_id),
            ):
                assert observer.get(model, identifier) is None
            assert counts(observer, stay_id) == (0, 0)
            assert observer.get(HousingStay, unrelated_stay_id) is not None
            assert observer.get(HousingRoom, unrelated_room_id) is not None
            assert _trigger_states(observer) == {name: "O" for _, name in _CLEANUP_TRIGGERS}


def test_history_cleanup_delete_failure_rolls_back_trigger_disables(postgres_engine):
    with _committed_history_rows(postgres_engine) as owned:
        with Session(postgres_engine) as db:
            actor, _, stay = _setup_owned_history(db, owned)
            stay_id = stay.id
            db.commit()
            service(db).void(actor, stay_id, command())

        def fail_delete(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("DELETE FROM housing_stays"):
                raise RuntimeError("synthetic_cleanup_delete_failure")

        event.listen(postgres_engine, "before_cursor_execute", fail_delete)
        try:
            with pytest.raises(RuntimeError, match="synthetic_cleanup_delete_failure"):
                _cleanup_owned_history(postgres_engine, owned)
        finally:
            event.remove(postgres_engine, "before_cursor_execute", fail_delete)
        with Session(postgres_engine) as observer:
            assert _trigger_states(observer) == {name: "O" for _, name in _CLEANUP_TRIGGERS}
            assert observer.get(HousingStay, stay_id) is not None
            assert counts(observer, stay_id) == (1, 1)
