from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

API_ROOT = Path(__file__).resolve().parents[2]
NEW_HEAD = "0060_esports_assessments"
PREVIOUS_HEAD = "0059_talent_dynamic_vision"
TAKE_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000080")
READ_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000081")
PERMISSION_IDS = {
    "esports_assessments.take": TAKE_PERMISSION_ID,
    "esports_assessments.read": READ_PERMISSION_ID,
}
DEFAULT_GRANTS = {
    ("super_admin", "esports_assessments.take", "self"),
    ("super_admin", "esports_assessments.read", "global"),
    ("student", "esports_assessments.take", "self"),
    ("student", "esports_assessments.read", "self"),
    ("guardian", "esports_assessments.read", "family"),
    ("coach", "esports_assessments.read", "assigned_class"),
}
NEW_TABLES = {
    "esports_assessment_sessions",
    "esports_assessment_attempts",
    "esports_assessment_results",
    "esports_assessment_reports",
    "esports_assessment_practice_runs",
}


def _config(engine: Engine) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    database_url = engine.url.render_as_string(hide_password=False)
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    return config


def _insert_account(engine: Engine) -> tuple[UUID, UUID]:
    person_id = uuid4()
    account_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO people ("
                "id, full_name, identity_ciphertext, identity_digest, sex, date_of_birth"
                ") VALUES (:id, '电竞迁移测试', 'ciphertext', :digest, 'female', :birth_date)"
            ),
            {
                "id": person_id,
                "digest": person_id.hex,
                "birth_date": date(2012, 1, 2),
            },
        )
        connection.execute(
            text(
                "INSERT INTO user_accounts ("
                "id, person_id, password_hash, status, force_password_change, "
                "failed_login_count) VALUES ("
                ":id, :person_id, 'not-used', 'active', false, 0)"
            ),
            {"id": account_id, "person_id": person_id},
        )
    return person_id, account_id


def _delete_account(engine: Engine, person_id: UUID, account_id: UUID) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM user_permission_overrides WHERE user_id = :id"),
            {"id": account_id},
        )
        connection.execute(
            text("DELETE FROM user_roles WHERE user_id = :id"),
            {"id": account_id},
        )
        connection.execute(text("DELETE FROM user_accounts WHERE id = :id"), {"id": account_id})
        connection.execute(text("DELETE FROM people WHERE id = :id"), {"id": person_id})


def _permission_grants(engine: Engine) -> set[tuple[str, str, str]]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT r.key, p.key, rp.scope_kind FROM role_permissions rp "
                    "JOIN roles r ON r.id = rp.role_id "
                    "JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE p.key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": sorted(PERMISSION_IDS)},
            ).tuples()
        )


def _insert_legacy_talent_rows(
    engine: Engine,
    *,
    person_id: UUID,
    account_id: UUID,
) -> dict[str, object]:
    session_ids = [uuid4(), uuid4()]
    result_ids = [uuid4(), uuid4()]
    with engine.begin() as connection:
        for index, (session_id, catalog_version, status) in enumerate(
            (
                (session_ids[0], "talent-training-v1", "completed"),
                (session_ids[1], "talent-training-v2", "completed"),
            )
        ):
            connection.execute(
                text(
                    "INSERT INTO talent_test_sessions ("
                    "id, person_id, created_by_user_id, creation_key, "
                    "creation_request_digest, catalog_version, scoring_version, "
                    "report_version, rules_snapshot, status, completed_at"
                    ") VALUES ("
                    ":id, :person_id, :account_id, :creation_key, :digest, "
                    ":catalog_version, :scoring_version, :report_version, "
                    "CAST(:rules AS jsonb), :status, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": session_id,
                    "person_id": person_id,
                    "account_id": account_id,
                    "creation_key": uuid4(),
                    "digest": f"{index + 1}" * 64,
                    "catalog_version": catalog_version,
                    "scoring_version": f"talent-score-v{index + 1}",
                    "report_version": f"talent-report-v{index + 1}",
                    "rules": f'{{"catalog_version":"{catalog_version}"}}',
                    "status": status,
                },
            )
        for result_id, session_id, test_id in (
            (result_ids[0], session_ids[0], "reactiontime"),
            (result_ids[1], session_ids[1], "dynamic-vision"),
        ):
            connection.execute(
                text(
                    "INSERT INTO talent_test_results ("
                    "id, session_id, test_id, engine_version, locale, raw_result, "
                    "device_snapshot, normalized_result, score, quality_flags, "
                    "duration_ms, background_interrupted, submission_id, submission_digest"
                    ") VALUES ("
                    ":id, :session_id, :test_id, 'legacy-engine', 'zh-CN', "
                    "CAST(:raw AS jsonb), CAST('{}' AS jsonb), CAST('{}' AS jsonb), "
                    "50, CAST('[]' AS jsonb), 60000, false, :submission_id, :digest)"
                ),
                {
                    "id": result_id,
                    "session_id": session_id,
                    "test_id": test_id,
                    "raw": f'{{"sentinel":"{test_id}"}}',
                    "submission_id": uuid4(),
                    "digest": "d" * 64,
                },
            )
        return {
            "sessions": connection.execute(
                text(
                    "SELECT id, catalog_version, rules_snapshot "
                    "FROM talent_test_sessions WHERE id = ANY(CAST(:ids AS uuid[])) "
                    "ORDER BY id"
                ),
                {"ids": session_ids},
            ).all(),
            "results": connection.execute(
                text(
                    "SELECT id, test_id, raw_result FROM talent_test_results "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": result_ids},
            ).all(),
            "session_ids": session_ids,
        }


def _delete_legacy_talent_rows(engine: Engine, session_ids: list[UUID]) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "DELETE FROM talent_test_results "
                "WHERE session_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": session_ids},
        )
        connection.execute(
            text("DELETE FROM talent_test_sessions WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": session_ids},
        )


def test_0060_upgrade_preserves_legacy_rows_and_seeds_exact_grants(
    postgres_engine: Engine,
) -> None:
    config = _config(postgres_engine)
    person_id, account_id = _insert_account(postgres_engine)
    legacy: dict[str, object] | None = None
    try:
        command.downgrade(config, PREVIOUS_HEAD)
        old_grants = _permission_grants(postgres_engine)
        legacy = _insert_legacy_talent_rows(
            postgres_engine,
            person_id=person_id,
            account_id=account_id,
        )

        command.upgrade(config, NEW_HEAD)

        assert NEW_TABLES <= set(inspect(postgres_engine).get_table_names())
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == NEW_HEAD
            assert connection.execute(
                text(
                    "SELECT id, catalog_version, rules_snapshot "
                    "FROM talent_test_sessions WHERE id = ANY(CAST(:ids AS uuid[])) "
                    "ORDER BY id"
                ),
                {"ids": legacy["session_ids"]},
            ).all() == legacy["sessions"]
            assert connection.execute(
                text(
                    "SELECT id, test_id, raw_result FROM talent_test_results "
                    "WHERE session_id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
                ),
                {"ids": legacy["session_ids"]},
            ).all() == legacy["results"]
            assert connection.execute(
                text(
                    "SELECT key, id, sensitive FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[])) ORDER BY key"
                ),
                {"keys": sorted(PERMISSION_IDS)},
            ).all() == [
                (key, PERMISSION_IDS[key], key == "esports_assessments.read")
                for key in sorted(PERMISSION_IDS)
            ]
        assert old_grants == set()
        assert _permission_grants(postgres_engine) == DEFAULT_GRANTS
    finally:
        command.upgrade(config, "head")
        if legacy is not None:
            _delete_legacy_talent_rows(postgres_engine, legacy["session_ids"])
        _delete_account(postgres_engine, person_id, account_id)


def _insert_new_session(engine: Engine, person_id: UUID, account_id: UUID) -> UUID:
    session_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO esports_assessment_sessions ("
                "id, person_id, created_by_user_id, suite_id, creation_key, "
                "creation_request_digest, catalog_snapshot, scoring_snapshot, "
                "report_rules_snapshot, status"
                ") VALUES ("
                ":id, :person_id, :account_id, 'pc-fps', :key, :digest, "
                "CAST('{}' AS jsonb), CAST('{}' AS jsonb), CAST('{}' AS jsonb), "
                "'in_progress')"
            ),
            {
                "id": session_id,
                "person_id": person_id,
                "account_id": account_id,
                "key": uuid4(),
                "digest": "e" * 64,
            },
        )
    return session_id


def test_0060_downgrade_rejects_business_data_without_deleting(
    postgres_engine: Engine,
) -> None:
    config = _config(postgres_engine)
    person_id, account_id = _insert_account(postgres_engine)
    session_id: UUID | None = None
    try:
        command.downgrade(config, NEW_HEAD)
        session_id = _insert_new_session(postgres_engine, person_id, account_id)

        with pytest.raises(RuntimeError, match="存在天赋评测 2 业务数据，拒绝降级"):
            command.downgrade(config, PREVIOUS_HEAD)

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == NEW_HEAD
            assert connection.scalar(
                text("SELECT count(*) FROM esports_assessment_sessions WHERE id = :id"),
                {"id": session_id},
            ) == 1
    finally:
        command.upgrade(config, "head")
        if session_id is not None:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM esports_assessment_sessions WHERE id = :id"),
                    {"id": session_id},
                )
        _delete_account(postgres_engine, person_id, account_id)


@pytest.mark.parametrize("customization", ["override", "non_default_role_grant"])
def test_0060_downgrade_refuses_custom_permission_data(
    postgres_engine: Engine,
    customization: str,
) -> None:
    config = _config(postgres_engine)
    person_id, account_id = _insert_account(postgres_engine)
    override_id = uuid4()
    try:
        command.downgrade(config, NEW_HEAD)
        with postgres_engine.begin() as connection:
            if customization == "override":
                connection.execute(
                    text(
                        "INSERT INTO user_permission_overrides ("
                        "id, user_id, permission_id, effect, scope_kind, created_by_user_id, "
                        "grant_reason) VALUES ("
                        ":id, :user_id, :permission_id, 'deny', 'self', :user_id, "
                        "'迁移保留授权')"
                    ),
                    {
                        "id": override_id,
                        "user_id": account_id,
                        "permission_id": READ_PERMISSION_ID,
                    },
                )
            else:
                connection.execute(
                    text(
                        "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                        "SELECT id, :permission_id, 'self' FROM roles "
                        "WHERE key = 'registered_user'"
                    ),
                    {"permission_id": READ_PERMISSION_ID},
                )

        expected = (
            "存在天赋评测 2 用户授权"
            if customization == "override"
            else "存在天赋评测 2 非默认角色授权"
        )
        with pytest.raises(RuntimeError, match=expected):
            command.downgrade(config, PREVIOUS_HEAD)

        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == NEW_HEAD
            if customization == "override":
                assert connection.scalar(
                    text("SELECT count(*) FROM user_permission_overrides WHERE id = :id"),
                    {"id": override_id},
                ) == 1
            else:
                assert (
                    "registered_user",
                    "esports_assessments.read",
                    "self",
                ) in _permission_grants(postgres_engine)
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_permission_overrides WHERE id = :id"),
                {"id": override_id},
            )
            connection.execute(
                text(
                    "DELETE FROM role_permissions WHERE permission_id = :permission_id "
                    "AND role_id = (SELECT id FROM roles WHERE key = 'registered_user')"
                ),
                {"permission_id": READ_PERMISSION_ID},
            )
        _delete_account(postgres_engine, person_id, account_id)


def test_0060_empty_downgrade_removes_only_owned_permissions_and_tables(
    postgres_engine: Engine,
) -> None:
    config = _config(postgres_engine)
    sentinel_permission_id = uuid4()
    sentinel_key = f"migration.sentinel.{sentinel_permission_id.hex}"
    try:
        command.downgrade(config, NEW_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO permissions (id, key, name, sensitive) "
                    "VALUES (:id, :key, '保留权限', false)"
                ),
                {"id": sentinel_permission_id, "key": sentinel_key},
            )
            connection.execute(
                text(
                    "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                    "SELECT id, :permission_id, 'self' FROM roles "
                    "WHERE key = 'registered_user'"
                ),
                {"permission_id": sentinel_permission_id},
            )

        command.downgrade(config, PREVIOUS_HEAD)

        assert NEW_TABLES.isdisjoint(set(inspect(postgres_engine).get_table_names()))
        with postgres_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                PREVIOUS_HEAD
            )
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM permissions "
                    "WHERE key = ANY(CAST(:keys AS text[]))"
                ),
                {"keys": sorted(PERMISSION_IDS)},
            ) == 0
            assert connection.execute(
                text("SELECT key, name FROM permissions WHERE id = :id"),
                {"id": sentinel_permission_id},
            ).one() == (sentinel_key, "保留权限")
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM role_permissions WHERE permission_id = :id"),
                {"id": sentinel_permission_id},
            )
            connection.execute(
                text("DELETE FROM permissions WHERE id = :id"),
                {"id": sentinel_permission_id},
            )
