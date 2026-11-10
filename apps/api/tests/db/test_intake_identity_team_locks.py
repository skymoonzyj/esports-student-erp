"""Real independent PostgreSQL transactions, fenced at deterministic checkpoints."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from time import monotonic
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.errors import ProblemError
from app.personnel_intake.role_grants import ActivationRoleService
from app.personnel_intake.schemas import ActivationRoleUpdate
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.rbac.service import RbacService
from app.registration.models import RegistrationVerificationChallenge
from app.team_management.errors import TeamServiceError
from app.team_management.models import TeamStaffAssignment
from app.team_management.person_staff_models import PersonTeamStaffPosition
from app.team_management.person_staff_schemas import PersonStaffEndInput, PersonStaffReconfirmInput
from app.team_management.person_staff_service import PersonStaffService
from tests.db.test_team_concurrency import reload_actor
from tests.services.test_assisted_activation import activation
from tests.services.test_intake_activation_roles import committed_db as _committed_db
from tests.services.test_intake_staff_activation import prepared_staff
from tests.services.test_personnel_approval import approvals


@pytest.fixture
def committed_db(postgres_engine):
    yield from _committed_db.__wrapped__(postgres_engine)


def limits(db):
    db.execute(text("SET lock_timeout = '8s'"))
    db.execute(text("SET statement_timeout = '12s'"))
    return db.scalar(text("SELECT pg_backend_pid()"))


def finish(engine, token, code, *, ready=None, verify_mutex=None, pids=None, name="activation"):
    with engine.connect() as connection, Session(connection) as db:
        connection.info["intake_staff_race"] = name
        try:
            pid = limits(db)
            if pids is not None:
                pids[name] = pid
            service = activation(db)[0]
            if ready is not None:
                original = service.verification.verify_unconsumed
                def verified(*args, **kwargs):
                    # Put both commands AFTER real successful guess accounting;
                    # NOWAIT's independent busy response is not this business race.
                    with verify_mutex:
                        result = original(*args, **kwargs)
                    ready.wait(timeout=8)
                    return result
                service.verification.verify_unconsumed = verified
            service.finish(token, code, SecretStr("SecurePass1"))
            return "completed"
        except (ProblemError, TeamServiceError) as exc:
            return exc.code
        finally:
            connection.info.pop("intake_staff_race", None)


def change(db, mode, ids, person_id, position_id, team_id, *, key=None):
    who = reload_actor(db, ids["admin"])
    if mode in {"end", "reconfirm"}:
        position = db.get(PersonTeamStaffPosition, position_id)
        model = PersonStaffEndInput if mode == "end" else PersonStaffReconfirmInput
        return getattr(PersonStaffService(db, lookup=approvals(db).lookup), mode)(
            who, position_id, model(expected_version=position.version,
                reason="合成并发岗位核验", idempotency_key=key or uuid4()))
    if mode == "resign":
        return ActivationRoleService(db, lookup=approvals(db).lookup).update(who, person_id,
            ActivationRoleUpdate(role_keys=["coach", "team_manager", "guardian"],
                                 expected_version=1, idempotency_key=key or uuid4()))
    if mode == "revoke":
        return RbacService(db).remove_role(actor_user_id=ids["coach"][0],
            account_id=ids["admin"][0], role_id=ROLES["super_admin"].id,
            request_id="synthetic-activation-revoke-race")
    from app.teaching.catalog_service import TeachingCatalogService
    from app.teaching.schemas import ProfessionalTeamUpdate
    return TeachingCatalogService(db).update_team(team_id, ProfessionalTeamUpdate(is_active=False),
                                                 actor_user_id=ids["admin"][0])


@pytest.mark.parametrize("mode", ["end", "reconfirm", "resign", "revoke", "disable_team"])
def test_activation_rejects_discovery_drift_without_consuming_otp(committed_db, postgres_engine, mode):
    db = committed_db
    users, result, first, _, _, token, code, challenge = prepared_staff(db)
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    ids = {key: (users["accounts"][key].id, users["people"][key].id) for key in ("admin", "coach")}
    person_id, team_id = result.person_id, first.id
    position_id = db.scalar(select(PersonTeamStaffPosition.id).order_by(PersonTeamStaffPosition.id))
    db.rollback()
    discovered, release = Event(), Event()
    pids = {}

    def snapshot(connection, cursor, statement, parameters, context, executemany):
        if (connection.info.get("intake_staff_race") == "activation"
                and "FROM person_team_staff_positions" in statement
                and "FOR UPDATE" not in statement and not discovered.is_set()):
            discovered.set()
            assert release.wait(8), "discovery barrier was not released"

    event.listen(postgres_engine, "after_cursor_execute", snapshot)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(finish, postgres_engine, token, code, pids=pids)
            try:
                assert discovered.wait(8)
                with Session(postgres_engine) as writer:
                    pids["writer"] = limits(writer)
                    assert pids["writer"] != pids["activation"]
                    if mode == "disable_team":
                        with pytest.raises(TeamServiceError):
                            change(writer, mode, ids, person_id, position_id, team_id)
                    else:
                        change(writer, mode, ids, person_id, position_id, team_id)
            finally:
                release.set()
            outcome = future.result(timeout=15)
    finally:
        event.remove(postgres_engine, "after_cursor_execute", snapshot)
    if mode == "disable_team":
        assert outcome == "completed"
        assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 3
        return
    assert outcome != "completed"
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 0
    assert db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at is None
    db.rollback()
    if mode != "revoke":
        assert finish(postgres_engine, token, code) == "completed"
        assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == (2 if mode == "end" else 3)


def test_two_concurrent_finish_commands_create_exactly_one_account_and_links(committed_db, postgres_engine):
    db = committed_db
    _, result, _, _, _, token, code, challenge = prepared_staff(db)
    person_id = result.person_id
    db.rollback()
    ready, verify_mutex, pids = Barrier(2), Lock(), {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish, postgres_engine, token, code, ready=ready, verify_mutex=verify_mutex, pids=pids,
                               name=name) for name in ("one", "two")]
        outcomes = [f.result(timeout=20) for f in futures]
    assert len(set(pids.values())) == 2
    assert outcomes == ["completed", "completed"]
    assert db.scalar(select(func.count()).select_from(UserAccount).where(UserAccount.person_id == person_id)) == 1
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 3
    assert db.get(RegistrationVerificationChallenge, challenge, populate_existing=True).consumed_at is not None


@pytest.mark.parametrize("mode", ["end", "reconfirm", "resign", "revoke", "disable_team"])
def test_activation_winner_serializes_staff_lifecycle_after_atomic_realization(
    committed_db, postgres_engine, mode
):
    db = committed_db
    users, result, first, _, _, token, code, _ = prepared_staff(db)
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    ids = {key: (users["accounts"][key].id, users["people"][key].id) for key in ("admin", "coach")}
    person_id, team_id = result.person_id, first.id
    position_id = db.scalar(select(PersonTeamStaffPosition.id).order_by(PersonTeamStaffPosition.id))
    db.rollback()
    inserted, release, writer_started = Event(), Event(), Event()
    pids = {}
    command_key = uuid4()

    def checkpoint(connection, cursor, statement, parameters, context, executemany):
        if (connection.info.get("intake_staff_race") == "activation"
                and statement.startswith("INSERT INTO team_staff_assignments") and not inserted.is_set()):
            inserted.set()
            assert release.wait(8), "realization barrier was not released"

    def writer():
        with Session(postgres_engine) as session:
            pids["writer"] = limits(session)
            writer_started.set()
            try:
                change(session, mode, ids, person_id, position_id, team_id, key=command_key)
                return "changed"
            except (ProblemError, TeamServiceError) as exc:
                return exc.code

    event.listen(postgres_engine, "after_cursor_execute", checkpoint)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            activation_future = pool.submit(finish, postgres_engine, token, code, pids=pids)
            try:
                assert inserted.wait(8)
                writer_future = pool.submit(writer)
                assert writer_started.wait(5)
                assert pids["writer"] != pids["activation"]
                deadline, blocked = monotonic() + 5, False
                with postgres_engine.connect() as observer:
                    while monotonic() < deadline:
                        blocked = observer.scalar(text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"),
                            {"pid": pids["writer"]})
                        observer.commit()
                        if blocked:
                            break
                        assert not writer_future.done(), "writer escaped the owned business locks"
                        Event().wait(0.01)
                assert blocked, "writer did not reach an observed PostgreSQL lock wait"
            finally:
                release.set()
            assert activation_future.result(timeout=15) == "completed"
            outcome = writer_future.result(timeout=15)
    finally:
        event.remove(postgres_engine, "after_cursor_execute", checkpoint)
    expected = {"end": "team_assignment_stale", "reconfirm": "team_assignment_stale", "revoke": "changed",
                "resign": "activation_roles_retry", "disable_team": "team_has_active_relationships"}
    assert outcome == expected[mode]
    assert db.scalar(select(func.count()).select_from(UserAccount).where(UserAccount.person_id == person_id)) == 1
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)) == 3
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)
                     .where(TeamStaffAssignment.ended_at.is_(None))) == 3
    assert all(p.version == 1 and p.ended_at is None for p in db.scalars(select(PersonTeamStaffPosition)))
    db.rollback()
    # New account/link is an authoritative snapshot change. Re-read and retry the
    # same command key; the rejected stale attempt must have recorded nothing.
    if mode in {"end", "reconfirm", "resign"}:
        with Session(postgres_engine) as retry:
            if mode == "resign":
                with pytest.raises(ProblemError) as error:
                    change(retry, mode, ids, person_id, position_id, team_id, key=command_key)
                assert error.value.code == "activation_roles_activated"
            else:
                change(retry, mode, ids, person_id, position_id, team_id, key=command_key)
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment)
                     .where(TeamStaffAssignment.ended_at.is_(None))) == (2 if mode == "end" else 3)
    position = db.get(PersonTeamStaffPosition, position_id)
    assert (position.ended_at is not None) is (mode == "end")
    assert position.version == (2 if mode in {"end", "reconfirm"} else 1)


def test_multiteam_creation_in_reverse_order_has_no_deadlock(committed_db, postgres_engine):
    from datetime import date

    from app.personnel_intake.identity_selection import ManagedIdentityInput, StaffTeamSelection
    from app.personnel_intake.schemas import DirectPersonInput
    from tests.api.conftest import make_cn_id
    db = committed_db
    users, _, first, second, _, _, _, _ = prepared_staff(db)
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    ids = [(users["accounts"][k].id, users["people"][k].id) for k in ("admin", "coach")]
    team_ids = [first.id, second.id]
    db.rollback()
    gate, pids = Barrier(2), []

    def create(index):
        with Session(postgres_engine) as session:
            pids.append(limits(session))
            who = reload_actor(session, ids[index])
            gate.wait(timeout=8)
            sequence = 331 + index * 2
            return approvals(session).direct_create(who, DirectPersonInput(kind="staff",
                full_name="合成反序建档", identity_number=make_cn_id("20000102", sequence),
                sex="male", date_of_birth=date(2000, 1, 2), phone=f"13900001{sequence}",
                email=f"reverse-{sequence}@example.com"), uuid4(),
                identity_selection=ManagedIdentityInput(primary_identity="coach"),
                staff_teams=[StaffTeamSelection(staff_role="coach",
                    team_ids=team_ids if index == 0 else list(reversed(team_ids)))])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create, index) for index in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert len(set(pids)) == 2
    for result in results:
        assert set(db.scalars(select(PersonTeamStaffPosition.team_id).where(
            PersonTeamStaffPosition.person_id == result.person_id))) == set(team_ids)
        assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
