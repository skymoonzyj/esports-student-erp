from __future__ import annotations

from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import Engine, text

from tests.db.test_migrations import _alembic_config, _delete_test_account, _insert_test_account

PREVIOUS = "0083_housing_property_changes"
CURRENT = "0084_team_staff_roles"
ASSISTANT_ID = UUID("10000000-0000-4000-8000-000000000015")


def test_team_staff_upgrade_refuses_preexisting_coach_grant_without_any_change(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, PREVIOUS)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO role_permissions(role_id,permission_id,scope_kind) "
                "SELECT r.id,p.id,'assigned_team' FROM roles r CROSS JOIN permissions p "
                "WHERE r.key='coach' AND p.key='team_memberships.read'"
            ))
            grants = set(connection.execute(text("SELECT role_id,permission_id,scope_kind FROM role_permissions")))
            constraint = connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_staff_profiles_staff_type'"))
        with pytest.raises(RuntimeError, match="pre-existing coach roster grant"):
            command.upgrade(config, CURRENT)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS
            assert connection.scalar(text("SELECT count(*) FROM roles WHERE key='assistant_coach'")) == 0
            assert set(connection.execute(text("SELECT role_id,permission_id,scope_kind FROM role_permissions"))) == grants
            assert connection.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ck_staff_profiles_staff_type'")) == constraint
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text(
                "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE key='coach') "
                "AND permission_id IN (SELECT id FROM permissions WHERE key='team_memberships.read') "
                "AND scope_kind='assigned_team'"
            ))
        command.upgrade(config, "head")


def test_team_staff_upgrade_preserves_people_and_only_adds_read_grants(postgres_engine: Engine) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, PREVIOUS)
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            connection.execute(text("INSERT INTO staff_profiles(person_id,staff_type) VALUES (:id,'coach')"), {"id": person_id})
            before = connection.execute(text("SELECT * FROM people WHERE id=:id"), {"id": person_id}).one()
            grants = set(connection.execute(text("SELECT role_id,permission_id,scope_kind FROM role_permissions")))
            assigned_roles = set(connection.execute(text("SELECT role_id FROM user_roles WHERE user_id=:id"), {"id": account_id}))
        command.upgrade(config, CURRENT)
        with postgres_engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM people WHERE id=:id"), {"id": person_id}).one() == before
            assert connection.scalar(text("SELECT staff_type FROM staff_profiles WHERE person_id=:id"), {"id": person_id}) == "coach"
            assert set(connection.execute(text("SELECT role_id FROM user_roles WHERE user_id=:id"), {"id": account_id})) == assigned_roles
            assert connection.execute(text("SELECT id,name FROM roles WHERE key='assistant_coach'")).one() == (ASSISTANT_ID, "助教")
            additions = set(connection.execute(text("SELECT role_id,permission_id,scope_kind FROM role_permissions"))) - grants
            assert len(additions) == 3
            assert set(connection.execute(text("SELECT p.key,rp.scope_kind FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE role_id=:id"), {"id": ASSISTANT_ID})) == {
                ("team_memberships.read", "assigned_team"),
                ("personnel_intake.housing.read", "global"),
            }
            assert connection.scalar(text("SELECT EXISTS(SELECT 1 FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE r.key='coach' AND p.key='team_memberships.read' AND rp.scope_kind='assigned_team')"))
        command.downgrade(config, PREVIOUS)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM roles WHERE key='assistant_coach'")) == 0
            assert set(connection.execute(text("SELECT role_id,permission_id,scope_kind FROM role_permissions"))) == grants
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM staff_profiles WHERE person_id=:id"), {"id": person_id})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)
        command.upgrade(config, "head")


@pytest.mark.parametrize("kind", ["team_manager", "assistant_coach", "role_assignment", "custom_grant"])
def test_team_staff_downgrade_refuses_to_discard_new_data(postgres_engine: Engine, kind: str) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, CURRENT)
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.begin() as connection:
            if kind in {"team_manager", "assistant_coach"}:
                connection.execute(text("INSERT INTO staff_profiles(person_id,staff_type) VALUES (:id,:kind)"), {"id": person_id, "kind": kind})
            elif kind == "role_assignment":
                connection.execute(text("INSERT INTO user_roles(user_id,role_id) VALUES (:user,:role)"), {"user": account_id, "role": ASSISTANT_ID})
            else:
                connection.execute(text("INSERT INTO role_permissions(role_id,permission_id,scope_kind) SELECT :role,id,'global' FROM permissions WHERE key='people.list'"), {"role": ASSISTANT_ID})
        with pytest.raises(RuntimeError, match="cannot discard"):
            command.downgrade(config, PREVIOUS)
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT
            assert connection.scalar(text("SELECT count(*) FROM roles WHERE id=:id"), {"id": ASSISTANT_ID}) == 1
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM staff_profiles WHERE person_id=:id"), {"id": person_id})
            connection.execute(text("DELETE FROM user_roles WHERE user_id=:id"), {"id": account_id})
            connection.execute(text("DELETE FROM role_permissions WHERE role_id=:id AND permission_id IN (SELECT id FROM permissions WHERE key='people.list')"), {"id": ASSISTANT_ID})
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)
        command.upgrade(config, "head")
