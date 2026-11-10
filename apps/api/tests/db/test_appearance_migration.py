import subprocess
from pathlib import Path
from uuid import uuid4

from alembic import command
from sqlalchemy import inspect, text

from tests.db.test_migrations import CURRENT_HEAD, _alembic_config


def test_appearance_migration_seeds_singleton_and_preserves_existing_people(postgres_engine):
    config = _alembic_config(postgres_engine)
    person = uuid4()
    command.downgrade(config, "0074_person_profile_assignments")
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id,full_name,identity_ciphertext,identity_digest,sex,date_of_birth) VALUES (:id,'外观迁移测试','encrypted',:digest,'male','2000-01-01')"
                ),
                {"id": person, "digest": uuid4().hex + uuid4().hex},
            )
        command.upgrade(config, "head")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == CURRENT_HEAD
            )
            assert connection.execute(
                text("SELECT id,theme,revision FROM global_appearance")
            ).one() == (1, "clear", 1)
            assert connection.execute(
                text(
                    "SELECT r.key FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE p.key='operations.appearance.manage'"
                )
            ).scalars().all() == ["super_admin"]
        command.downgrade(config, "0074_person_profile_assignments")
        assert "global_appearance" not in inspect(postgres_engine).get_table_names()
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT full_name FROM people WHERE id=:id"), {"id": person})
                == "外观迁移测试"
            )
        command.upgrade(config, "head")
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM people WHERE id=:id"), {"id": person})


def test_appearance_manifest_includes_head_and_both_backup_table_sets(postgres_engine):
    manifest = Path(__file__).resolve().parents[4] / "scripts/db-schema-manifest.sh"
    result = subprocess.check_output(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n" "$EXPECTED_ALEMBIC_HEAD"; [[ " ${REQUIRED_TABLES[*]} " == *" global_appearance "* ]] && [[ " ${ROW_COUNT_TABLES[*]} " == *" global_appearance "* ]]',
            "manifest",
            str(manifest),
        ],
        text=True,
    )
    assert result.strip() == CURRENT_HEAD
