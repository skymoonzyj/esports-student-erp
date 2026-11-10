from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

API_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS_HEAD = "0060_esports_assessments"
NEW_HEAD = "0061_team_management"
DIVISIONS = {"王者荣耀", "和平精英", "三角洲行动"}
NEW_TABLES = {"team_memberships", "team_manager_assignments", "team_operations"}
NEW_ROLE_KEYS = {"team_member", "team_manager"}
NEW_PERMISSION_KEYS = {
    "team_memberships.read",
    "team_memberships.manage",
    "team_memberships.transfer",
    "team_manager_assignments.manage",
}
NEW_GRANTS = {
    ("team_member", "team_memberships.read", "self"),
    ("team_manager", "team_memberships.read", "assigned_team"),
    ("team_manager", "team_memberships.manage", "assigned_team"),
    ("super_admin", "team_memberships.read", "global"),
    ("super_admin", "team_memberships.manage", "global"),
    ("super_admin", "team_memberships.transfer", "global"),
    ("super_admin", "team_manager_assignments.manage", "global"),
}


@pytest.fixture(autouse=True)
def prepare_historical_team_grants(postgres_engine: Engine) -> Iterator[None]:
    # 0090 conservatively retains grants on downgrade. These 0061 regressions
    # explicitly revoke only its five later defaults as fixture preparation;
    # the real nondefault-grant and business-history guards remain unchanged.
    clear_grants = text(
        "DELETE FROM role_permissions rp USING roles r,permissions p "
        "WHERE rp.role_id=r.id AND rp.permission_id=p.id "
        "AND r.key='administration' AND rp.scope_kind='global' "
        "AND p.key IN ('professional_teams.manage','team_memberships.read',"
        "'team_memberships.manage','team_memberships.transfer',"
        "'team_manager_assignments.manage') "
        "RETURNING rp.role_id,rp.permission_id,rp.scope_kind"
    )
    with postgres_engine.begin() as connection:
        initial_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        original_grants = [dict(row) for row in connection.execute(clear_grants).mappings()]
    try:
        yield
    finally:
        with postgres_engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == initial_head
            # Tests may recreate defaults via upgrade, or remain at head after
            # rejecting a downgrade. Restore exactly the prior subset in both cases.
            connection.execute(clear_grants)
            if original_grants:
                connection.execute(text(
                    "INSERT INTO role_permissions(role_id,permission_id,scope_kind) "
                    "VALUES (:role_id,:permission_id,:scope_kind)"
                ), original_grants)


@pytest.mark.parametrize(
    "missing_name,expected_id,expected_order",
    [
        ("和平精英", UUID("30000000-0000-4000-8000-000000000002"), 20),
        ("三角洲行动", UUID("30000000-0000-4000-8000-000000000003"), 30),
    ],
)
def test_upgrade_creates_each_missing_division_without_changing_other_projects(
    postgres_engine: Engine,
    missing_name: str,
    expected_id: UUID,
    expected_order: int,
) -> None:
    config = _config(postgres_engine)
    from sqlalchemy.orm import Session

    from app.teaching.seed import seed_initial_projects

    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM project_analysis_profiles WHERE project_id = :id"),
                {"id": expected_id},
            )
            connection.execute(
                text("DELETE FROM game_projects WHERE id = :id"), {"id": expected_id}
            )
            before_projects = connection.execute(
                text(
                    "SELECT id, name, sort_order, is_active, created_by_user_id "
                    "FROM game_projects ORDER BY id"
                )
            ).all()
            before_profiles = connection.execute(
                text(
                    "SELECT id, project_id, version, mode FROM project_analysis_profiles "
                    "ORDER BY id"
                )
            ).all()

        command.upgrade(config, NEW_HEAD)
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id, sort_order, is_active, created_by_user_id, is_team_division "
                    "FROM game_projects WHERE name = :name"
                ),
                {"name": missing_name},
            ).one() == (expected_id, expected_order, True, None, True)
            assert (
                connection.execute(
                    text(
                        "SELECT id, name, sort_order, is_active, created_by_user_id "
                        "FROM game_projects WHERE name NOT IN (:missing_name, '王者荣耀') "
                        "ORDER BY id"
                    ),
                    {"missing_name": missing_name},
                ).all()
                == before_projects
            )
            assert (
                connection.execute(
                    text(
                        "SELECT id, project_id, version, mode FROM project_analysis_profiles "
                        "ORDER BY id"
                    )
                ).all()
                == before_profiles
            )
            assert (
                set(
                    connection.scalars(
                        text("SELECT name FROM game_projects WHERE is_team_division")
                    )
                )
                == DIVISIONS
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection, Session(bind=connection) as db:
            seed_initial_projects(db)
            db.flush()


@pytest.mark.parametrize(
    "collision",
    [
        "role_key_wrong_id",
        "role_id_wrong_key",
        "permission_key_wrong_id",
        "permission_id_wrong_key",
    ],
)
def test_upgrade_rejects_conflicting_fixed_rbac_identity_without_mutation(
    postgres_engine: Engine, collision: str
) -> None:
    config = _config(postgres_engine)
    role_id = UUID("10000000-0000-4000-8000-000000000012")
    permission_id = UUID("20000000-0000-4000-8000-000000000082")
    inserted_id = (
        uuid4()
        if collision.endswith("wrong_id")
        else (role_id if collision.startswith("role") else permission_id)
    )
    table = "roles" if collision.startswith("role") else "permissions"
    key = (
        ("team_member" if table == "roles" else "team_memberships.read")
        if collision.endswith("wrong_id")
        else f"migration_collision_{collision}"
    )
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            if table == "roles":
                connection.execute(
                    text("INSERT INTO roles (id, key, name) VALUES (:id, :key, '原有角色')"),
                    {"id": inserted_id, "key": key},
                )
                original = connection.execute(
                    text("SELECT id, key, name FROM roles WHERE id = :id"),
                    {"id": inserted_id},
                ).one()
            else:
                connection.execute(
                    text(
                        "INSERT INTO permissions (id, key, name, sensitive) "
                        "VALUES (:id, :key, '原有能力', false)"
                    ),
                    {"id": inserted_id, "key": key},
                )
                original = connection.execute(
                    text("SELECT id, key, name, sensitive FROM permissions WHERE id = :id"),
                    {"id": inserted_id},
                ).one()

        with pytest.raises(RuntimeError, match="固定.*ID.*冲突"):
            command.upgrade(config, NEW_HEAD)

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                PREVIOUS_HEAD
            )
            assert "is_team_division" not in {
                column["name"] for column in inspect(connection).get_columns("game_projects")
            }
            assert NEW_TABLES.isdisjoint(set(inspect(connection).get_table_names()))
            columns = "id, key, name" if table == "roles" else "id, key, name, sensitive"
            assert (
                connection.execute(
                    text(f"SELECT {columns} FROM {table} WHERE id = :id"),
                    {"id": inserted_id},
                ).one()
                == original
            )
    finally:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": inserted_id})
        command.upgrade(config, "head")


def _config(engine: Engine) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    url = engine.url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["database_url"] = url
    return config


@pytest.mark.parametrize(
    "name,primary,fallback",
    [
        ("和平精英", 2, 6),
        ("三角洲行动", 3, 7),
        ("王者荣耀", 5, 8),
    ],
)
@pytest.mark.parametrize("block_fallback", [False, True])
def test_renamed_seed_identity_preserves_relations_and_uses_free_fallback(
    postgres_engine: Engine,
    name: str,
    primary: int,
    fallback: int,
    block_fallback: bool,
) -> None:
    from sqlalchemy.orm import Session

    from app.teaching.seed import seed_initial_projects

    config = _config(postgres_engine)
    primary_id = UUID(f"30000000-0000-4000-8000-{primary:012d}")
    fallback_id = UUID(f"30000000-0000-4000-8000-{fallback:012d}")
    person_id, account_id, team_id = uuid4(), uuid4(), uuid4()
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth) VALUES (:id, '迁移碰撞', 'cipher', :digest, 'female', '2000-01-01')"
                ),
                {"id": person_id, "digest": person_id.hex},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, force_password_change, failed_login_count) VALUES (:id, :person, 'hash', 'active', false, 0)"
                ),
                {"id": account_id, "person": person_id},
            )
            if primary == 5:
                connection.execute(
                    text(
                        "INSERT INTO game_projects (id, name, sort_order, is_active, created_by_user_id) VALUES (:id, '已改名项目', 99, false, :actor)"
                    ),
                    {"id": primary_id, "actor": account_id},
                )
            else:
                connection.execute(
                    text(
                        "UPDATE game_projects SET name = '已改名项目', sort_order = 99, is_active = false WHERE id = :id"
                    ),
                    {"id": primary_id},
                )
            if block_fallback:
                connection.execute(
                    text(
                        "INSERT INTO game_projects (id, name, sort_order, is_active, created_by_user_id) VALUES (:id, '备用已占用', 98, false, :actor)"
                    ),
                    {"id": fallback_id, "actor": account_id},
                )
            connection.execute(
                text(
                    "INSERT INTO professional_teams (id, project_id, name, is_active, created_by_user_id) VALUES (:id, :project, '保留关联队伍', true, :actor)"
                ),
                {"id": team_id, "project": primary_id, "actor": account_id},
            )
            before = connection.execute(
                text(
                    "SELECT id, name, sort_order, is_active, created_by_user_id FROM game_projects ORDER BY id"
                )
            ).all()
            profiles = connection.execute(
                text(
                    "SELECT id, project_id, version, mode FROM project_analysis_profiles ORDER BY id"
                )
            ).all()
        if block_fallback:
            with pytest.raises(RuntimeError, match="分部.*ID.*冲突"):
                command.upgrade(config, NEW_HEAD)
            with postgres_engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == PREVIOUS_HEAD
                )
                assert "is_team_division" not in {
                    column["name"] for column in inspect(connection).get_columns("game_projects")
                }
                assert (
                    connection.execute(
                        text(
                            "SELECT id, name, sort_order, is_active, created_by_user_id FROM game_projects ORDER BY id"
                        )
                    ).all()
                    == before
                )
        else:
            command.upgrade(config, NEW_HEAD)
            with postgres_engine.begin() as connection, Session(bind=connection) as db:
                seed_initial_projects(db)
                seed_initial_projects(db)
                db.flush()
            with postgres_engine.connect() as connection:
                assert (
                    connection.scalar(
                        text("SELECT id FROM game_projects WHERE name = :name"), {"name": name}
                    )
                    == fallback_id
                )
                assert (
                    set(
                        connection.scalars(
                            text("SELECT name FROM game_projects WHERE is_team_division")
                        )
                    )
                    == DIVISIONS
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT id, project_id, version, mode FROM project_analysis_profiles ORDER BY id"
                        )
                    ).all()
                    == profiles
                )
            referenced_team_id = uuid4()
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO professional_teams (id, project_id, name, is_active, created_by_user_id) "
                        "VALUES (:id, :project, '新分部引用', true, :actor)"
                    ),
                    {"id": referenced_team_id, "project": fallback_id, "actor": account_id},
                )
            try:
                with pytest.raises(IntegrityError):
                    command.downgrade(config, PREVIOUS_HEAD)
                with postgres_engine.connect() as connection:
                    assert (
                        connection.scalar(text("SELECT version_num FROM alembic_version"))
                        == NEW_HEAD
                    )
                    assert NEW_TABLES <= set(inspect(connection).get_table_names())
                    assert (
                        connection.scalar(
                            text("SELECT project_id FROM professional_teams WHERE id = :id"),
                            {"id": referenced_team_id},
                        )
                        == fallback_id
                    )
            finally:
                with postgres_engine.begin() as connection:
                    connection.execute(
                        text("DELETE FROM professional_teams WHERE id = :id"),
                        {"id": referenced_team_id},
                    )
            command.downgrade(config, PREVIOUS_HEAD)
            with postgres_engine.connect() as connection:
                assert (
                    connection.execute(
                        text(
                            "SELECT id, name, sort_order, is_active, created_by_user_id FROM game_projects ORDER BY id"
                        )
                    ).all()
                    == before
                )
                assert (
                    connection.scalar(
                        text("SELECT project_id FROM professional_teams WHERE id = :id"),
                        {"id": team_id},
                    )
                    == primary_id
                )
    finally:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM professional_teams WHERE id = :id"), {"id": team_id}
            )
            connection.execute(
                text("DELETE FROM game_projects WHERE id = :id"), {"id": fallback_id}
            )
            if primary == 5:
                connection.execute(
                    text("DELETE FROM game_projects WHERE id = :id"), {"id": primary_id}
                )
            else:
                connection.execute(
                    text(
                        "UPDATE game_projects SET name = :name, sort_order = :sort, is_active = true WHERE id = :id"
                    ),
                    {"id": primary_id, "name": name, "sort": primary * 10},
                )
            connection.execute(text("DELETE FROM user_accounts WHERE id = :id"), {"id": account_id})
            connection.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
        command.upgrade(config, "head")


def _new_grants(engine: Engine) -> set[tuple[str, str, str]]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT r.key, p.key, rp.scope_kind FROM role_permissions rp "
                    "JOIN roles r ON r.id = rp.role_id "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE p.key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": sorted(NEW_PERMISSION_KEYS)},
            ).tuples()
        )


def _legacy_grants(connection) -> set[tuple[object, object, str]]:
    return set(
        connection.execute(
            text(
                "SELECT rp.role_id, rp.permission_id, rp.scope_kind "
                "FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                "WHERE p.key <> ALL(CAST(:keys AS text[]))"
            ),
            {"keys": sorted(NEW_PERMISSION_KEYS)},
        ).tuples()
    )


def test_preserves_existing_catalog_and_profiles(postgres_engine: Engine) -> None:
    """Adding divisions must not replace catalog/profile rows or reactivate projects."""
    config = _config(postgres_engine)
    person_id, account_id, unrelated_project_id = uuid4(), uuid4(), uuid4()
    with postgres_engine.connect() as connection:
        initial_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        initial_tables = set(inspect(connection).get_table_names())
        initial_people = set(connection.scalars(text("SELECT id FROM people")))
        initial_accounts = set(connection.scalars(text("SELECT id FROM user_accounts")))
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES (:id, '迁移保护', 'cipher', :digest, 'female', :dob)"
                ),
                {"id": person_id, "digest": person_id.hex, "dob": date(2000, 1, 1)},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, "
                    "force_password_change, failed_login_count) "
                    "VALUES (:id, :person_id, 'hash', 'active', false, 0)"
                ),
                {"id": account_id, "person_id": person_id},
            )
            connection.execute(
                text(
                    "INSERT INTO game_projects (id, name, sort_order, is_active, created_by_user_id) "
                    "VALUES (:id, '迁移无关项目', 90, false, :account_id)"
                ),
                {"id": unrelated_project_id, "account_id": account_id},
            )
            connection.execute(
                text("UPDATE game_projects SET is_active = false WHERE name = '和平精英'")
            )
            before_ids = set(connection.scalars(text("SELECT id FROM game_projects")))
            original_rows = connection.execute(
                text(
                    "SELECT id, name, sort_order, is_active, created_by_user_id "
                    "FROM game_projects WHERE id = :id"
                ),
                {"id": unrelated_project_id},
            ).all()
            original_profiles = connection.execute(
                text(
                    "SELECT id, project_id, version, mode FROM project_analysis_profiles "
                    "ORDER BY id"
                )
            ).all()
            old_grants = _legacy_grants(connection)
            old_people = connection.execute(
                text("SELECT id, full_name, date_of_birth FROM people WHERE id = :id"),
                {"id": person_id},
            ).one()
            old_account = connection.execute(
                text("SELECT id, person_id, status FROM user_accounts WHERE id = :id"),
                {"id": account_id},
            ).one()

        command.upgrade(config, NEW_HEAD)
        assert NEW_TABLES <= set(inspect(postgres_engine).get_table_names())
        with postgres_engine.connect() as connection:
            after_ids = set(
                connection.scalars(text("SELECT id FROM game_projects WHERE name <> '王者荣耀'"))
            )
            inactive_project = connection.execute(
                text("SELECT is_active FROM game_projects WHERE name = '和平精英'")
            ).one()
            unrelated_rows = connection.execute(
                text(
                    "SELECT id, name, sort_order, is_active, created_by_user_id "
                    "FROM game_projects WHERE id = :id"
                ),
                {"id": unrelated_project_id},
            ).all()
            assert after_ids == before_ids
            assert inactive_project.is_active is False
            assert unrelated_rows == original_rows
            assert (
                set(
                    connection.scalars(
                        text("SELECT name FROM game_projects WHERE is_team_division")
                    )
                )
                == DIVISIONS
            )
            assert (
                connection.execute(
                    text(
                        "SELECT id, project_id, version, mode FROM project_analysis_profiles "
                        "ORDER BY id"
                    )
                ).all()
                == original_profiles
            )
            assert (
                connection.execute(
                    text("SELECT id, full_name, date_of_birth FROM people WHERE id = :id"),
                    {"id": person_id},
                ).one()
                == old_people
            )
            assert (
                connection.execute(
                    text("SELECT id, person_id, status FROM user_accounts WHERE id = :id"),
                    {"id": account_id},
                ).one()
                == old_account
            )
            assert _legacy_grants(connection) == old_grants
            assert all(
                connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
                for table in NEW_TABLES
            )
        assert _new_grants(postgres_engine) == NEW_GRANTS

        # Startup seeds must be idempotent and must honor a later administrator opt-out.
        # Current application seeds require current schema, not historical 0061.
        # Keep the pure 0061 grant-preservation checks above, then test startup.
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            startup_grants = _legacy_grants(connection)
        from sqlalchemy.orm import Session

        from app.rbac.seed import seed_roles_and_permissions
        from app.teaching.seed import seed_initial_projects

        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE game_projects SET is_team_division = false WHERE name = '和平精英'")
            )
            with Session(bind=connection) as db:
                seed_roles_and_permissions(db)
                seed_roles_and_permissions(db)
                seed_initial_projects(db)
                db.flush()
        with postgres_engine.connect() as connection:
            new_role_count = connection.scalar(
                text("SELECT count(*) FROM roles WHERE key = ANY(CAST(:keys AS text[]))"),
                {"keys": sorted(NEW_ROLE_KEYS)},
            )
            assert new_role_count == 2
            assert (
                connection.scalar(
                    text("SELECT is_team_division FROM game_projects WHERE name = '和平精英'")
                )
                is False
            )
            assert _legacy_grants(connection) == startup_grants
            assert dict(
                connection.execute(
                    text("SELECT key, id FROM roles WHERE key = ANY(CAST(:keys AS text[]))"),
                    {"keys": sorted(NEW_ROLE_KEYS)},
                )
                .tuples()
                .all()
            ) == {
                "team_member": UUID("10000000-0000-4000-8000-000000000012"),
                "team_manager": UUID("10000000-0000-4000-8000-000000000013"),
            }
            assert _new_grants(postgres_engine) == NEW_GRANTS | {
                ("coach", "team_memberships.read", "assigned_team"),
                ("assistant_coach", "team_memberships.read", "assigned_team"),
                ("administration", "team_memberships.read", "global"),
                ("administration", "team_memberships.manage", "global"),
                ("administration", "team_memberships.transfer", "global"),
                ("administration", "team_manager_assignments.manage", "global"),
            }
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM game_projects WHERE id = :id"), {"id": unrelated_project_id}
            )
            connection.execute(text("DELETE FROM user_accounts WHERE id = :id"), {"id": account_id})
            connection.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
            connection.execute(
                text(
                    "UPDATE game_projects SET is_active = true, is_team_division = true "
                    "WHERE name = '和平精英'"
                )
            )

    # This test commits migration fixtures: prove it cannot poison subsequent
    # account/cloud tests with leaked accounts or a partially restored schema.
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == initial_head
        assert set(inspect(connection).get_table_names()) == initial_tables
        assert set(connection.scalars(text("SELECT id FROM people"))) == initial_people
        assert set(connection.scalars(text("SELECT id FROM user_accounts"))) == initial_accounts


def test_empty_upgrade_downgrade_upgrade_preserves_old_data(postgres_engine: Engine) -> None:
    config = _config(postgres_engine)
    person_id, account_id = uuid4(), uuid4()
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES (:id, '往返迁移保护', 'cipher', :digest, 'female', :dob)"
                ),
                {"id": person_id, "digest": person_id.hex, "dob": date(2000, 1, 1)},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, "
                    "force_password_change, failed_login_count) "
                    "VALUES (:id, :person_id, 'hash', 'active', false, 0)"
                ),
                {"id": account_id, "person_id": person_id},
            )
            before = connection.execute(
                text("SELECT id, name, sort_order, is_active FROM game_projects ORDER BY id")
            ).all()
            old_account = connection.execute(
                text("SELECT id, person_id, status FROM user_accounts WHERE id = :id"),
                {"id": account_id},
            ).one()
        command.upgrade(config, NEW_HEAD)
        command.downgrade(config, PREVIOUS_HEAD)
        assert NEW_TABLES.isdisjoint(set(inspect(postgres_engine).get_table_names()))
        with postgres_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT id, name, sort_order, is_active FROM game_projects ORDER BY id")
                ).all()
                == before
            )
            assert (
                connection.execute(
                    text("SELECT id, person_id, status FROM user_accounts WHERE id = :id"),
                    {"id": account_id},
                ).one()
                == old_account
            )
        command.upgrade(config, NEW_HEAD)
        with postgres_engine.connect() as connection:
            assert (
                set(
                    connection.scalars(
                        text("SELECT name FROM game_projects WHERE is_team_division")
                    )
                )
                == DIVISIONS
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM user_accounts WHERE id = :id"), {"id": account_id})
            connection.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})


def test_upgrade_reuses_same_name_project_without_replacing_its_data(
    postgres_engine: Engine,
) -> None:
    config = _config(postgres_engine)
    person_id, account_id, existing_project_id = uuid4(), uuid4(), uuid4()
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES (:id, '原项目保护', 'cipher', :digest, 'female', :dob)"
                ),
                {"id": person_id, "digest": person_id.hex, "dob": date(2000, 1, 1)},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, "
                    "force_password_change, failed_login_count) "
                    "VALUES (:id, :person_id, 'hash', 'active', false, 0)"
                ),
                {"id": account_id, "person_id": person_id},
            )
            connection.execute(
                text(
                    "INSERT INTO game_projects "
                    "(id, name, sort_order, is_active, created_by_user_id) "
                    "VALUES (:id, '王者荣耀', 77, false, :account_id)"
                ),
                {"id": existing_project_id, "account_id": account_id},
            )
        command.upgrade(config, NEW_HEAD)
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id, sort_order, is_active, created_by_user_id, is_team_division "
                    "FROM game_projects WHERE name = '王者荣耀'"
                )
            ).one() == (existing_project_id, 77, False, account_id, True)
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM game_projects WHERE name = '王者荣耀'")
                )
                == 1
            )
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT id, sort_order, is_active, created_by_user_id "
                    "FROM game_projects WHERE name = '王者荣耀'"
                )
            ).one() == (existing_project_id, 77, False, account_id)
    finally:
        command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM game_projects WHERE id = :id"), {"id": existing_project_id}
            )
            connection.execute(text("DELETE FROM user_accounts WHERE id = :id"), {"id": account_id})
            connection.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
        command.upgrade(config, "head")


def test_downgrade_rejects_new_business_rows(postgres_engine: Engine) -> None:
    config = _config(postgres_engine)
    operation_id = uuid4()
    person_id, account_id = uuid4(), uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id, full_name, identity_ciphertext, identity_digest, "
                    "sex, date_of_birth) VALUES (:id, '降级保护', 'cipher', :digest, 'female', :dob)"
                ),
                {"id": person_id, "digest": person_id.hex, "dob": date(2000, 1, 1)},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id, person_id, password_hash, status, "
                    "force_password_change, failed_login_count) "
                    "VALUES (:id, :person_id, 'hash', 'active', false, 0)"
                ),
                {"id": account_id, "person_id": person_id},
            )
            connection.execute(
                text(
                    "INSERT INTO team_operations "
                    "(id, actor_account_id, idempotency_key, action, request_fingerprint, result_ids) "
                    "VALUES (:id, :actor, :key, 'join', :fingerprint, CAST('{}' AS jsonb))"
                ),
                {"id": operation_id, "actor": account_id, "key": uuid4(), "fingerprint": "a" * 64},
            )
        with pytest.raises(RuntimeError, match="队伍业务数据"):
            command.downgrade(config, PREVIOUS_HEAD)
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM team_operations WHERE id = :id"),
                    {"id": operation_id},
                )
                == 1
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM team_operations WHERE id = :id"), {"id": operation_id}
            )
            connection.execute(text("DELETE FROM user_accounts WHERE id = :id"), {"id": account_id})
            connection.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})
