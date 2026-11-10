"""Seeding must fail before opening or resetting an unowned database."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, make_url
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

OWNED_URL = "postgresql+psycopg://erp_e2e:synthetic@127.0.0.1:32786/erp_departure_reminders_test"


class ApprovedResetReached(Exception):
    """The real ownership guard allowed progress to the DB reset boundary."""


@pytest.fixture
def seed_bindings(monkeypatch):
    module = seed_module()
    settings = SimpleNamespace(database_url=OWNED_URL, validate_runtime_secrets=lambda: None)
    monkeypatch.setattr(module, "require_departure_database", lambda value: None)
    monkeypatch.setattr(module.base, "_secret_bytes", lambda *args: bytes(range(32)))

    def reset(*args, **kwargs):
        raise ApprovedResetReached

    monkeypatch.setattr(module.base, "seed_e2e", reset)
    engines = []
    connections = []
    sessions = []

    def engine(url=OWNED_URL):
        bind = create_engine(url)
        engines.append(bind)

        @event.listens_for(bind, "do_connect")
        def no_network(*args):
            pytest.fail("session ownership must be checked without opening a DB connection")

        return bind

    def connection(bind):
        # Real SQLAlchemy Connection, external DBAPI replaced before any SQL.
        value = Connection(bind, connection=SimpleNamespace(close=lambda: None, is_valid=True))
        connections.append(value)
        return value

    def session(bind=None, *, cls=Session, **kwargs):
        value = cls(bind=bind, **kwargs)
        sessions.append(value)
        return value

    yield module, settings, engine, connection, session
    for value in sessions:
        value.close()
    for value in connections:
        value.close()
    for value in engines:
        value.dispose()


@pytest.mark.parametrize("kind", [
    "no_bind", "foreign_engine", "foreign_connection", "wrong_password",
    "multiple_binds", "mapper_only", "routing_subclass", "instance_routing", "unproved_bind",
])
def test_seed_refuses_unproved_session_before_reset_or_sql(seed_bindings, kind):
    module, settings, engine, connection, session = seed_bindings
    owned = engine()
    foreign = engine(OWNED_URL.replace("32786", "32787"))
    from app.identity.models import Person

    if kind == "no_bind":
        db = session()
    elif kind == "foreign_engine":
        db = session(foreign)
    elif kind == "foreign_connection":
        db = session(connection(foreign))
    elif kind == "wrong_password":
        db = session(engine(make_url(OWNED_URL).set(password="other-synthetic")))
    elif kind == "multiple_binds":
        db = session(owned, binds={Person: foreign})
    elif kind == "mapper_only":
        db = session(binds={Person: owned})
    elif kind == "routing_subclass":
        class RoutingSession(Session):
            def get_bind(self, *args, **kwargs):
                return foreign

        db = session(owned, cls=RoutingSession)
    elif kind == "instance_routing":
        db = session(owned)
        db.get_bind = lambda *args, **kwargs: foreign
    else:
        db = session(SimpleNamespace(url=make_url(OWNED_URL)))
    with pytest.raises(module.DepartureRuntimeRefused, match="exact task-owned"):
        module.seed_departure_reminders(db, settings, datetime.now(UTC))


@pytest.mark.parametrize("factory,bound_connection", [(False, False), (False, True), (True, False), (True, True)])
def test_seed_accepts_exact_engine_or_existing_connection_without_sql(seed_bindings, factory, bound_connection):
    module, settings, engine, connection, session = seed_bindings
    bind = engine()
    if bound_connection:
        bind = connection(bind)
    db = session(bind, cls=module.SessionLocal if factory else Session)
    with pytest.raises(ApprovedResetReached):
        module.seed_departure_reminders(db, settings, datetime.now(UTC))


def test_standalone_seed_registers_referral_foreign_key_metadata():
    result = subprocess.run(
        [sys.executable, "-c", "from app.cli.seed_departure_reminders_e2e import StudentReferralBinding; tuple(f.column for f in StudentReferralBinding.__table__.foreign_keys)"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, "standalone seed must resolve binding foreign keys without app.main"


def seed_module():
    try:
        return import_module("app.cli.seed_departure_reminders_e2e")
    except ModuleNotFoundError:
        pytest.fail("departure seed ownership guard is not implemented")


@pytest.mark.parametrize("ledger", [None, "missing", "mismatch"])
def test_unowned_seed_is_refused_before_database_access(tmp_path, monkeypatch, ledger):
    module = seed_module()
    if ledger is None:
        monkeypatch.delenv("E2E_DEPARTURE_RUNTIME_LEDGER", raising=False)
    else:
        path = tmp_path / "runtime.json"
        if ledger == "mismatch":
            path.write_text('{"task_label":"another-task"}')
        monkeypatch.setenv("E2E_DEPARTURE_RUNTIME_LEDGER", str(path))
    settings = SimpleNamespace(
        app_env="test",
        database_url="postgresql+psycopg://erp_e2e:synthetic@127.0.0.1:32786/erp_departure_reminders_test",
    )
    with pytest.raises(module.DepartureRuntimeRefused, match="exact task-owned"):
        module.seed_departure_reminders(None, settings, datetime.now(UTC))


def test_cli_refuses_before_session_or_base_reset(monkeypatch):
    module = seed_module()
    monkeypatch.delenv("E2E_DEPARTURE_RUNTIME_LEDGER", raising=False)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(
        app_env="test", database_url="postgresql+psycopg://erp:synthetic@127.0.0.1:55433/erp_test"
    ))
    monkeypatch.setattr(module, "SessionLocal", lambda: pytest.fail("unowned session opened"))
    monkeypatch.setattr(module.base, "seed_e2e", lambda *a, **kw: pytest.fail("unowned reset"))
    with pytest.raises(module.DepartureRuntimeRefused, match="exact task-owned"):
        module.main([])


def test_owned_seed_requires_explicit_owned_phase_before_db(monkeypatch):
    class DatabaseRequest:
        def getfixturevalue(self, name):
            pytest.fail("generic suite opened a database before owned classification")

    monkeypatch.delenv("E2E_DEPARTURE_OWNED_ACCEPTANCE", raising=False)
    with pytest.raises(pytest.skip.Exception, match="owned departure-reminders gate"):
        _owned_seed_database(DatabaseRequest())


def test_owned_seed_rejects_invalid_ledger_before_db(monkeypatch):
    class DatabaseRequest:
        def getfixturevalue(self, name):
            pytest.fail("unproved owned suite opened a database")

    monkeypatch.setenv("E2E_DEPARTURE_OWNED_ACCEPTANCE", "1")
    monkeypatch.delenv("E2E_DEPARTURE_RUNTIME_LEDGER", raising=False)
    with pytest.raises(seed_module().DepartureRuntimeRefused, match="exact task-owned"):
        _owned_seed_database(DatabaseRequest())


def test_owned_seed_fetches_db_after_verified_guard(monkeypatch):
    from app.cli import departure_e2e_runtime

    observed = []

    class DatabaseRequest:
        def getfixturevalue(self, name):
            observed.append(name)
            return "owned-db"

    monkeypatch.setenv("E2E_DEPARTURE_OWNED_ACCEPTANCE", "1")
    monkeypatch.setattr(departure_e2e_runtime, "require_departure_database", lambda settings: observed.append("guard"))
    assert _owned_seed_database(DatabaseRequest()) == "owned-db"
    assert observed == ["guard", "db"]


def _owned_seed_database(request):
    if os.environ.get("E2E_DEPARTURE_OWNED_ACCEPTANCE") != "1":
        pytest.skip("destructive seed acceptance requires the owned departure-reminders gate")
    from app.cli.departure_e2e_runtime import require_departure_database
    from app.core.config import get_settings

    require_departure_database(get_settings())
    return request.getfixturevalue("db")


def test_owned_seed_builds_current_synthetic_scenarios(request):
    """Wrong relative dates, missing roles or a no-op seed lose visible facts."""
    from sqlalchemy import select

    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.cli import seed_e2e as base
    from app.core.config import get_settings
    from app.identity.models import Person
    from app.portal.departure_access import DepartureAccess
    from app.portal.departure_query import DepartureQuery

    module = seed_module()
    db = _owned_seed_database(request)
    now = datetime.now(UTC)
    module.seed_departure_reminders(db, get_settings(), now)
    assert db.get(UserAccount, base.E2E_ADMIN_ACCOUNT_ID) is not None, "seed must rebuild synthetic actors"

    def result(identifier):
        account = db.get(UserAccount, identifier)
        actor = AuthContext(account=account, person=db.get(Person, account.person_id), session=None)
        return DepartureQuery(db, DepartureAccess(db, actor, now)).read_page(
            now=now, window="three_days", page=1, page_size=50
        )

    all_rows = result(base.E2E_ADMIN_ACCOUNT_ID)
    assert all_rows.total > 20
    assert len({row.person_id for row in all_rows.items}) == all_rows.total
    names = [row.full_name for row in all_rows.items]
    assert names.count("E2E同名提醒") == 2
    assert "E2E已退房不提醒" not in names
    events = {row.full_name: row.events for row in all_rows.items}
    assert len(events["E2E多来源提醒"]) == 3
    assert {event.kind for event in events["E2E未激活提醒"]} == {
        "planned_checkout", "enrollment_end"
    }
    assert [event.kind for event in events["E2E常住结营提醒"]] == ["enrollment_end"]
    assert events["E2E独立已完成提醒"][0].enrollment_status == "completed"
    scoped = result(base.E2E_CUSTOMER_SERVICE_ACCOUNT_ID)
    assert scoped.items
    assert all(event.kind == "planned_checkout" and event.building_name == "4幢"
               for row in scoped.items for event in row.events)
    assert "E2E教练关联提醒" in [row.full_name for row in result(base.E2E_COACH_ACCOUNT_ID).items]
    for identifier, name in (
        (base.E2E_READY_MALE_STUDENT_ACCOUNT_ID, "E2E队伍关联提醒"),
        (base.E2E_LIFE_TEACHER_ACCOUNT_ID, "E2E邀请关联提醒"),
    ):
        rows = result(identifier)
        assert [row.full_name for row in rows.items] == [name]
        assert all(event.kind == "planned_checkout" and event.detail_path is None
                   for row in rows.items for event in row.events)
    assert not result(base.E2E_OTHER_GUARDIAN_ACCOUNT_ID).available
    assert [row.full_name for row in result(base.E2E_SELF_STUDENT_ACCOUNT_ID).items] == ["E2E自助学员"]
    assert set(db.scalars(select(Person.full_name).where(Person.full_name.like("E2E撤权提醒%")))) == {
        "E2E撤权提醒1440", "E2E撤权提醒768", "E2E撤权提醒360"
    }
