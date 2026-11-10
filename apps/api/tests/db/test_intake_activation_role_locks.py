"""Real PostgreSQL races with disjoint identity participants, not mocked grants."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.schemas import DirectPersonInput
from app.rbac.models import Role, RolePermission, UserPermissionOverride, UserRole
from app.rbac.schemas import JobRoleChangeRequest
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.registration.models import RegistrationVerificationChallenge
from tests.api.conftest import make_cn_id
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_assisted_activation import activation
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor


@pytest.fixture
def committed_db(postgres_engine):
    from tests.services.test_profile_claims import claim_db

    yield from claim_db.__wrapped__(postgres_engine)


def _prepared_activation(db, users, who, sequence, role_keys, *, kind="staff", team_id=None):
    email = f"role-lock-{sequence}@example.com"
    result = approvals(db).direct_create(
        actor(users, who),
        DirectPersonInput(
            kind=kind,
            team_id=team_id,
            full_name="合成角色并发人员",
            identity_number=make_cn_id("20000102", sequence),
            phone=f"13900001{sequence}",
            email=email,
            sex="male",
            date_of_birth=date(2000, 1, 2),
        ),
        uuid4(),
        activation_role_keys=role_keys,
    )
    db.commit()
    service, sender = activation(db)
    token = service.start(email, f"127.0.19.{sequence % 100}").session_token.get_secret_value()
    proof = db.scalar(
        select(AssistedActivationSession).where(
            AssistedActivationSession.person_id == result.person_id
        )
    )
    code = SecretStr(sender.code_for(proof.challenge_id))
    service.verify(token, code)
    return result.person_id, token, code, proof.challenge_id


def _finish(engine, prepared, name, pids, started):
    with engine.connect() as connection, Session(connection) as db:
        connection.info["role_lock_race"] = name
        try:
            db.execute(text("SET lock_timeout = '8s'"))
            db.execute(text("SET statement_timeout = '12s'"))
            pids[name] = db.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            activation(db)[0].finish(prepared[1], prepared[2], SecretStr("SecurePass1"))
            return "completed"
        except DBAPIError as error:
            return getattr(error.orig, "sqlstate", type(error.orig).__name__)
        finally:
            connection.info.pop("role_lock_race", None)


def _wait_for_lock_or_event(engine, pid, reached):
    deadline = monotonic() + 5
    with engine.connect() as observer:
        while monotonic() < deadline:
            if reached.is_set():
                return
            waiting = observer.scalar(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid=:pid"),
                {"pid": pid},
            )
            observer.commit()
            if waiting:
                return
            sleep(0.01)
    pytest.fail("second independent operation reached neither its write nor a real lock wait")


def _assert_completed(db, prepared, role_keys):
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == prepared[0]))
    assert account is not None
    assert (
        set(db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == account.id)))
        == role_keys
    )
    assert (
        db.get(RegistrationVerificationChallenge, prepared[3], populate_existing=True).consumed_at
        is not None
    )


def test_disjoint_activations_selecting_baseline_do_not_upgrade_fk_locks_into_deadlock(
    committed_db,
    postgres_engine,
):
    db = committed_db
    users = _platform_users.__wrapped__(db)
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id))
    db.commit()
    first = _prepared_activation(db, users, "admin", 901, ["registered_user"])
    second = _prepared_activation(db, users, "coach", 903, ["registered_user"])
    db.rollback()
    inserted, release = {"first": Event(), "second": Event()}, Event()
    pids = {}

    def after_insert(connection, cursor, statement, parameters, context, executemany):
        name = connection.info.get("role_lock_race")
        if (
            name in inserted
            and statement.startswith("INSERT INTO user_roles")
            and not inserted[name].is_set()
        ):
            inserted[name].set()
            if not release.wait(8):
                raise TimeoutError("baseline write checkpoint was not released")

    event.listen(postgres_engine, "after_cursor_execute", after_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(_finish, postgres_engine, first, "first", pids, Event())
            try:
                assert inserted["first"].wait(5)
                started = Event()
                b = pool.submit(_finish, postgres_engine, second, "second", pids, started)
                assert started.wait(5)
                # Old code reaches both FK writes; fixed code waits at the earlier Role lock.
                _wait_for_lock_or_event(postgres_engine, pids["second"], inserted["second"])
            finally:
                release.set()
            assert [a.result(timeout=15), b.result(timeout=15)] == ["completed", "completed"]
    finally:
        event.remove(postgres_engine, "after_cursor_execute", after_insert)
    _assert_completed(db, first, {"registered_user"})
    _assert_completed(db, second, {"registered_user"})


def test_activation_and_unrelated_job_change_share_canonical_role_lock_order(
    committed_db,
    postgres_engine,
):
    db = committed_db
    users = _platform_users.__wrapped__(db)
    grantor = users["accounts"]["coach"].id
    db.add(UserRole(user_id=grantor, role_id=ROLES["admin"].id))
    # Explicit synthetic current authority for the two real role bundles. The real
    # prospective and assign_role checks still run; this grantor is not a root and
    # therefore is not one of change_job_role's globally prelocked root accounts.
    # Use life_teacher rather than administration, whose new global team bundle
    # intentionally cannot be delegated by this non-root synthetic grantor.
    permission_ids = set(
        db.scalars(
            select(RolePermission.permission_id).where(
                RolePermission.role_id.in_(
                    [ROLES["life_teacher"].id, ROLES["customer_service"].id]
                )
            )
        )
    ) | {PERMISSIONS["permissions.manage"].id}
    db.add_all(
        UserPermissionOverride(
            user_id=grantor, permission_id=permission_id, effect="allow", scope_kind="global"
        )
        for permission_id in permission_ids
    )
    db.commit()
    prepared = _prepared_activation(db, users, "coach", 905, ["life_teacher", "customer_service"])
    root, target = users["accounts"]["admin"].id, users["accounts"]["customer_service"].id
    expected = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == target)))
    db.rollback()
    activation_locked, release = Event(), Event()
    pids = {}

    def after_role_lock(connection, cursor, statement, parameters, context, executemany):
        if (
            connection.info.get("role_lock_race") == "activation"
            and "FROM roles" in statement
            and "FOR UPDATE" in statement
            and not activation_locked.is_set()
        ):
            activation_locked.set()
            if not release.wait(8):
                raise TimeoutError("activation role-lock checkpoint was not released")

    def change(started):
        with postgres_engine.connect() as connection, Session(connection) as other:
            other.execute(text("SET lock_timeout = '8s'"))
            pids["job"] = other.scalar(text("SELECT pg_backend_pid()"))
            started.set()
            try:
                RbacService(other).change_job_role(
                    actor_user_id=root,
                    account_id=target,
                    input=JobRoleChangeRequest(
                        from_role_id=ROLES["customer_service"].id,
                        to_role_id=ROLES["life_teacher"].id,
                        keep_concurrent_roles=False,
                        expected_role_ids=expected,
                    ),
                    request_id=str(uuid4()),
                )
                return "completed"
            except DBAPIError as error:
                return getattr(error.orig, "sqlstate", type(error.orig).__name__)

    event.listen(postgres_engine, "after_cursor_execute", after_role_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(_finish, postgres_engine, prepared, "activation", pids, Event())
            try:
                assert activation_locked.wait(5)
                started = Event()
                b = pool.submit(change, started)
                assert started.wait(5)
                _wait_for_lock_or_event(postgres_engine, pids["job"], Event())
            finally:
                release.set()
            assert [a.result(timeout=15), b.result(timeout=15)] == ["completed", "completed"]
    finally:
        event.remove(postgres_engine, "after_cursor_execute", after_role_lock)
    _assert_completed(db, prepared, {"registered_user", "life_teacher", "customer_service"})
    assert set(db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == target))) == {
        "life_teacher",
    }


def test_waiting_for_activation_roles_holds_no_directory_or_otp_lock(committed_db, postgres_engine):
    from app.teaching.models import GameProject, ProfessionalTeam
    from tests.services.test_team_access import teams

    db = committed_db
    users = _platform_users.__wrapped__(db)
    team, _ = teams(db, users["accounts"]["admin"].id)
    team_id, project_id = team.id, team.project_id
    prepared = _prepared_activation(
        db, users, "admin", 907, ["team_member"], kind="player", team_id=team_id
    )
    db.rollback()
    pids, started = {}, Event()
    with Session(postgres_engine) as blocker:
        blocker.scalar(select(Role).where(Role.key == "team_member").with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_finish, postgres_engine, prepared, "activation", pids, started)
            try:
                assert started.wait(5)
                _wait_for_lock_or_event(postgres_engine, pids["activation"], Event())
                # These are real downstream rows, acquired with NOWAIT on a third
                # connection. Holding Role locks after any of them fails this test.
                with Session(postgres_engine) as inspector, inspector.begin():
                    for model, identifier in (
                        (GameProject, project_id),
                        (ProfessionalTeam, team_id),
                        (
                            AssistedActivationSession,
                            db.scalar(
                                select(AssistedActivationSession.id).where(
                                    AssistedActivationSession.person_id == prepared[0]
                                )
                            ),
                        ),
                        (RegistrationVerificationChallenge, prepared[3]),
                    ):
                        assert (
                            inspector.scalar(
                                select(model)
                                .where(model.id == identifier)
                                .with_for_update(nowait=True)
                            )
                            is not None
                        )
            finally:
                blocker.rollback()
            assert future.result(timeout=15) == "completed"
    _assert_completed(db, prepared, {"team_member"})
