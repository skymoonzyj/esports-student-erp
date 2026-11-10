from __future__ import annotations

from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_housing_history_void_migration import cleanup, seed
from tests.db.test_migrations import CURRENT_HEAD, _alembic_config

PREVIOUS = "0080_reactionx_foundation"
NEW_PERMISSIONS = {
    "housing.changes.submit", "housing.changes.review",
    "housing.keycards.manage", "housing.exports.basic",
}


@pytest.fixture
def migration_data(postgres_engine):
    config = _alembic_config(postgres_engine)
    command.upgrade(config, CURRENT_HEAD)
    data = seed(postgres_engine)
    try:
        yield data
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM housing_room_cards WHERE stay_id IN (:a,:b)"), {"a": data[2][0], "b": data[2][1]})
            connection.execute(text("DELETE FROM housing_property_changes WHERE room_id=:room"), {"room": data[3]})
            connection.execute(text("UPDATE housing_rooms SET cleaning_frequency='unspecified',daily_cleaning_enabled=false WHERE id=:room"), {"room": data[3]})
        cleanup(postgres_engine, data)


@pytest.mark.parametrize("legacy_daily", [False, True])
def test_upgrade_preserves_legacy_rooms_people_and_stays(postgres_engine, migration_data, legacy_daily):
    engine = postgres_engine
    config = _alembic_config(engine)
    person, actor, stays, room, *_ = migration_data
    command.downgrade(config, PREVIOUS)
    queries = {}
    for table, identifier in (("people", person), ("user_accounts", actor), ("housing_rooms", room), ("housing_stays", stays[0])):
        columns = ",".join(f'"{column["name"]}"' for column in inspect(engine).get_columns(table))
        queries[table] = (text(f"SELECT {columns} FROM {table} WHERE id=:id"), {"id": identifier})
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE housing_rooms SET daily_cleaning_enabled=:daily WHERE id=:room"), {"daily": legacy_daily, "room": room})
            before = {table: connection.execute(query, params).all() for table, (query, params) in queries.items()}
            roles_before = connection.execute(text("SELECT id,key,name FROM roles ORDER BY id")).all()
        command.upgrade(config, CURRENT_HEAD)
        with engine.connect() as connection:
            for table, (query, params) in queries.items():
                assert connection.execute(query, params).all() == before[table]
            roles_after = connection.execute(text("SELECT id,key,name FROM roles ORDER BY id")).all()
            original_role_ids = {role.id for role in roles_before}
            assert [role for role in roles_after if role.id in original_role_ids] == roles_before
            added_roles = [role for role in roles_after if role.id not in original_role_ids]
            assert len(added_roles) == 2
            assert {role.key for role in added_roles} == {"property", "assistant_coach"}
            assert connection.scalar(text("SELECT cleaning_frequency FROM housing_rooms WHERE id=:room"), {"room": room}) == ("daily" if legacy_daily else "unspecified")
            assert connection.scalar(text("SELECT count(*) FROM housing_room_cards")) == 0
            assert connection.scalar(text("SELECT count(*) FROM housing_property_changes")) == 0
            grants = connection.execute(text("SELECT r.key,p.key,rp.scope_kind FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE p.key IN ('housing.changes.submit','housing.changes.review','housing.keycards.manage','housing.exports.basic')")).all()
            by_role = {role: {key for r, key, scope in grants if r == role and scope == "global"} for role in ("property", "administration", "super_admin", "admin", "coach")}
            assert by_role["property"] == NEW_PERMISSIONS - {"housing.changes.review"}
            assert by_role["administration"] == NEW_PERMISSIONS - {"housing.changes.submit"}
            assert by_role["super_admin"] == NEW_PERMISSIONS
            assert by_role["admin"] == by_role["coach"] == set()
            assert connection.scalar(text("SELECT count(*) FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.key='property'")) == 0
        command.downgrade(config, PREVIOUS)
        with engine.connect() as connection:
            for table, (query, params) in queries.items():
                assert connection.execute(query, params).all() == before[table]
            assert connection.execute(text("SELECT id,key,name FROM roles ORDER BY id")).all() == roles_before
    finally:
        command.upgrade(config, CURRENT_HEAD)


def test_card_history_prevents_lossy_downgrade_atomically(postgres_engine, migration_data):
    _, actor, stays, *_ = migration_data
    with postgres_engine.begin() as connection:
        connection.execute(text("INSERT INTO housing_room_cards(stay_id,issued_on,expires_on,version,updated_by_user_id) VALUES (:stay,'2026-11-01','2026-11-05',1,:actor)"), {"stay": stays[0], "actor": actor})
    with pytest.raises(RuntimeError, match="房卡数据存在"):
        command.downgrade(_alembic_config(postgres_engine), PREVIOUS)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
        assert connection.scalar(text("SELECT count(*) FROM housing_room_cards WHERE stay_id=:stay"), {"stay": stays[0]}) == 1
        assert connection.scalar(text("SELECT count(*) FROM roles WHERE key='property'")) == 1


def test_proposal_history_prevents_lossy_downgrade(postgres_engine, migration_data):
    _, actor, _, room, *_ = migration_data
    with postgres_engine.begin() as connection:
        connection.execute(text("INSERT INTO housing_property_changes(id,room_id,base_version,changes,before,reason,status,submitted_by_user_id,submitted_at) VALUES (:id,:room,1,CAST(:changes AS jsonb),CAST(:before AS jsonb),'synthetic migration test','pending',:actor,now())"), {"id": uuid4(), "room": room, "actor": actor, "changes": '{"notes":"synthetic"}', "before": '{"notes":null}'})
    with pytest.raises(RuntimeError, match="approval history"):
        command.downgrade(_alembic_config(postgres_engine), PREVIOUS)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
        assert connection.scalar(text("SELECT count(*) FROM housing_property_changes WHERE room_id=:room"), {"room": room}) == 1


@pytest.mark.parametrize("issued,expires,version", [
    ("2026-11-01", None, 1), (None, "2026-11-05", 1),
    ("2026-11-06", "2026-11-05", 1), ("2026-11-01", "2026-11-05", 0),
])
def test_card_database_constraints_reject_invalid_pairs(postgres_engine, migration_data, issued, expires, version):
    _, actor, stays, *_ = migration_data
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text("INSERT INTO housing_room_cards(stay_id,issued_on,expires_on,version,updated_by_user_id) VALUES (:stay,:issued,:expires,:version,:actor)"), {"stay": stays[0], "actor": actor, "issued": issued, "expires": expires, "version": version})
