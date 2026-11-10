"""Administration team grants are additive; unknown ownership is never revoked."""

from uuid import uuid4

from alembic import command
from sqlalchemy import Connection, Engine, inspect, text

from tests.db.test_team_staff_migration import config_for

PREVIOUS = "0089_quick_intake_housing"
REVISION = "0090_administration_team_mgmt"
GRANTS = {
    ("administration", "professional_teams.manage", "global"),
    ("administration", "team_memberships.read", "global"),
    ("administration", "team_memberships.manage", "global"),
    ("administration", "team_memberships.transfer", "global"),
    ("administration", "team_manager_assignments.manage", "global"),
}


def _grants(connection: Connection) -> set[tuple[str, str, str]]:
    return set(connection.execute(text(
        "SELECT r.key,p.key,rp.scope_kind FROM role_permissions rp "
        "JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id"
    )).tuples())


def test_administration_upgrade_adds_only_missing_global_grants_and_preserves_denies(
    postgres_engine: Engine,
) -> None:
    config = config_for(postgres_engine)
    person_id, account_id, deny_id = [uuid4() for _ in range(3)]
    original: set[tuple[str, str, str]] = set()
    try:
        command.downgrade(config, PREVIOUS)
        tables = set(inspect(postgres_engine).get_table_names())
        with postgres_engine.begin() as connection:
            original = _grants(connection)
            connection.execute(text(
                "DELETE FROM role_permissions rp USING roles r, permissions p "
                "WHERE rp.role_id=r.id AND rp.permission_id=p.id "
                "AND r.key='administration' AND rp.scope_kind='global' "
                "AND p.key IN ('professional_teams.manage','team_memberships.manage',"
                "'team_memberships.transfer','team_manager_assignments.manage')"
            ))
            # An identical existing global grant and a scoped custom grant survive.
            for permission, scope in (
                ("team_memberships.read", "global"),
                ("team_memberships.manage", "assigned_team"),
            ):
                connection.execute(text(
                    "INSERT INTO role_permissions(role_id,permission_id,scope_kind) "
                    "SELECT r.id,p.id,:scope FROM roles r CROSS JOIN permissions p "
                    "WHERE r.key='administration' AND p.key=:permission ON CONFLICT DO NOTHING"
                ), {"scope": scope, "permission": permission})
            connection.execute(text(
                "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,sex,date_of_birth) "
                "VALUES (:id,'Migration synthetic administrator','cipher',:digest,'female','2000-01-01')"
            ), {"id": person_id, "digest": person_id.hex})
            connection.execute(text(
                "INSERT INTO user_accounts(id,person_id,password_hash,status,force_password_change,failed_login_count) "
                "VALUES (:id,:person,'hash','active',false,0)"
            ), {"id": account_id, "person": person_id})
            connection.execute(text(
                "INSERT INTO user_roles(user_id,role_id) SELECT :id,id FROM roles "
                "WHERE key='administration'"
            ), {"id": account_id})
            connection.execute(text(
                "INSERT INTO user_permission_overrides(id,user_id,permission_id,effect,scope_kind,grant_reason) "
                "SELECT :id,:user,id,'deny','global','Existing financial restriction' "
                "FROM permissions WHERE key='revenue_share.finance'"
            ), {"id": deny_id, "user": account_id})
            before = _grants(connection)
            overrides = connection.execute(text(
                "SELECT * FROM user_permission_overrides ORDER BY id"
            )).all()
            users = connection.execute(text("SELECT * FROM user_roles ORDER BY user_id,role_id")).all()

        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert _grants(connection) == before | GRANTS
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert connection.execute(text(
                "SELECT * FROM user_permission_overrides ORDER BY id"
            )).all() == overrides
            assert connection.execute(text(
                "SELECT * FROM user_roles ORDER BY user_id,role_id"
            )).all() == users
        assert set(inspect(postgres_engine).get_table_names()) == tables

        command.downgrade(config, PREVIOUS)
        with postgres_engine.connect() as connection:
            # No grant provenance exists: downgrade must not guess ownership.
            assert _grants(connection) == before | GRANTS
            assert connection.execute(text(
                "SELECT * FROM user_permission_overrides ORDER BY id"
            )).all() == overrides
        command.upgrade(config, REVISION)
        with postgres_engine.connect() as connection:
            assert _grants(connection) == before | GRANTS
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM user_permission_overrides WHERE id=:id"), {"id": deny_id})
            connection.execute(text("DELETE FROM user_roles WHERE user_id=:id"), {"id": account_id})
            connection.execute(text("DELETE FROM user_accounts WHERE id=:id"), {"id": account_id})
            connection.execute(text("DELETE FROM people WHERE id=:id"), {"id": person_id})
            if original:
                custom = ("administration", "team_memberships.manage", "assigned_team")
                if custom not in original:
                    connection.execute(text(
                        "DELETE FROM role_permissions rp USING roles r,permissions p "
                        "WHERE rp.role_id=r.id AND rp.permission_id=p.id "
                        "AND r.key='administration' AND p.key='team_memberships.manage' "
                        "AND rp.scope_kind='assigned_team'"
                    ))
        command.upgrade(config, "head")
