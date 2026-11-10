import pytest
from alembic import command
from sqlalchemy import inspect, text

from tests.db.test_team_staff_migration import config_for


@pytest.fixture
def committed_db(postgres_engine):
    from tests.services.test_profile_claims import claim_db

    yield from claim_db.__wrapped__(postgres_engine)


def test_role_plan_migration_empty_roundtrip_and_schema(postgres_engine):
    config = config_for(postgres_engine)
    try:
        command.downgrade(config, "0085_team_staff_assignments")
        assert "intake_activation_role_plans" not in inspect(postgres_engine).get_table_names()
        command.upgrade(config, "head")
        schema = inspect(postgres_engine)
        constraints = schema.get_unique_constraints("intake_activation_role_plans")
        assert {"person_id", "version"} in [set(c["column_names"]) for c in constraints]
        assert {"authorizer_user_id", "operation_key"} in [
            set(c["column_names"]) for c in constraints
        ]
        with postgres_engine.connect() as db:
            assert db.scalar(text("SELECT count(*) FROM intake_activation_role_plans")) == 0
    finally:
        command.upgrade(config, "head")


def test_role_plan_downgrade_refuses_to_discard_authorization_history(
    postgres_engine, committed_db
):
    from uuid import uuid4

    from tests.api.conftest import platform_users
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import actor, intake

    db = committed_db
    users = platform_users.__wrapped__(db)
    result = approvals(db).direct_create(
        actor(users, "admin"), intake(users), uuid4(), activation_role_keys=["coach"]
    )
    db.commit()
    config = config_for(postgres_engine)
    with postgres_engine.connect() as connection:
        current_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(RuntimeError, match="激活角色授权已有历史"):
        command.downgrade(config, "0085_team_staff_assignments")
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM intake_activation_role_plans WHERE person_id=:person_id"
                ),
                {"person_id": result.person_id},
            )
            == 1
        )
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == current_head
        )


def test_role_plan_downgrade_fences_writes_between_guard_and_ddl(postgres_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import Engine, event
    from sqlalchemy.exc import DBAPIError

    guard, release = Event(), Event()

    def pause(connection, cursor, statement, parameters, context, executemany):
        if statement == "SELECT EXISTS (SELECT 1 FROM intake_activation_role_plans)":
            guard.set()
            if not release.wait(10):
                raise TimeoutError("role plan downgrade guard not released")

    config = config_for(postgres_engine)
    event.listen(Engine, "after_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(command.downgrade, config, "0085_team_staff_assignments")
            try:
                assert guard.wait(5)
                with postgres_engine.connect() as writer, writer.begin():
                    writer.execute(text("SET LOCAL lock_timeout = '200ms'"))
                    with pytest.raises(DBAPIError, match="lock timeout"):
                        writer.execute(
                            text(
                                "UPDATE intake_activation_role_plans SET version=version WHERE false"
                            )
                        )
            finally:
                release.set()
            future.result(timeout=10)
    finally:
        event.remove(Engine, "after_cursor_execute", pause)
        command.upgrade(config, "head")
