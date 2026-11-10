import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text

from tests.db.test_migrations import CURRENT_HEAD, _alembic_config

TABLES = {"person_assignment_states", "person_project_assignments", "person_assignment_operations"}


def test_assignment_manifest_matches_actual_migration_head(postgres_engine):
    root = Path(__file__).resolve().parents[4]
    output = subprocess.check_output(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n" "$EXPECTED_ALEMBIC_HEAD" "${REQUIRED_TABLES[@]}"',
            "manifest",
            str(root / "scripts/db-schema-manifest.sh"),
        ],
        text=True,
    ).splitlines()
    with postgres_engine.connect() as connection:
        actual = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert output[0] == actual == CURRENT_HEAD
    assert TABLES <= set(output[1:])


def test_assignment_migration_empty_roundtrip_and_retained_history_guard(postgres_engine):
    config = _alembic_config(postgres_engine)
    person, account = uuid4(), uuid4()
    command.downgrade(config, "0073_asset_ownership_rentals")
    assert not TABLES.intersection(inspect(postgres_engine).get_table_names())
    command.upgrade(config, "head")
    assert TABLES <= set(inspect(postgres_engine).get_table_names())
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO people (id,full_name,identity_ciphertext,identity_digest,sex,date_of_birth) VALUES (:id,'迁移测试','encrypted',:digest,'male','2000-01-01')"
            ),
            {"id": person, "digest": uuid4().hex + uuid4().hex},
        )
        connection.execute(
            text(
                "INSERT INTO user_accounts (id,person_id,password_hash,status,force_password_change,failed_login_count) VALUES (:id,:person,'unused','active',false,0)"
            ),
            {"id": account, "person": person},
        )
        connection.execute(
            text(
                "INSERT INTO person_assignment_states (person_id,is_player,version,updated_by_user_id) VALUES (:person,true,1,:actor)"
            ),
            {"person": person, "actor": account},
        )
    try:
        with pytest.raises(RuntimeError, match="retained person assignment history"):
            command.downgrade(config, "0073_asset_ownership_rentals")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text("SELECT is_player FROM person_assignment_states WHERE person_id=:id"),
                    {"id": person},
                )
                is True
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM person_assignment_states WHERE person_id=:id"), {"id": person}
            )
            connection.execute(text("DELETE FROM user_accounts WHERE id=:id"), {"id": account})
            connection.execute(text("DELETE FROM people WHERE id=:id"), {"id": person})
