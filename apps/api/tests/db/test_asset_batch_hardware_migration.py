from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_migrations import CURRENT_HEAD, _alembic_config


def test_hardware_migration_preserves_existing_computer_and_reversible_schema(postgres_engine):
    config = _alembic_config(postgres_engine)
    asset_id = uuid4()
    command.downgrade(config, "0075_global_appearance")
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO assets (id,asset_code,name,category,location) VALUES (:id,:code,'历史电脑','台式电脑','原位置')"
                ),
                {"id": asset_id, "code": f"MIG-{asset_id.hex[:12]}"},
            )
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.connect() as connection:
            assert tuple(
                connection.execute(
                    text(
                        "SELECT name,category,location,cpu_model,memory_capacity,gpu_model,disk_capacity FROM assets WHERE id=:id"
                    ),
                    {"id": asset_id},
                ).one()
            ) == ("历史电脑", "台式电脑", "原位置", None, None, None, None)
            columns = {row["name"]: row for row in inspect(connection).get_columns("assets")}
            assert all(
                columns[name]["nullable"]
                for name in ("cpu_model", "memory_capacity", "gpu_model", "disk_capacity")
            )
            assert "asset_batch_operations" in inspect(connection).get_table_names()
            assert set(
                inspect(connection).get_pk_constraint("asset_batch_operations")[
                    "constrained_columns"
                ]
            ) == {"actor_user_id", "operation_key"}
        command.downgrade(config, "0075_global_appearance")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT location FROM assets WHERE id=:id"), {"id": asset_id}
                )
                == "原位置"
            )
            assert "asset_batch_operations" not in inspect(connection).get_table_names()
            assert "cpu_model" not in {
                row["name"] for row in inspect(connection).get_columns("assets")
            }
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM assets WHERE id=:id"), {"id": asset_id})


@pytest.mark.parametrize(
    "values",
    ["'bad',3,'[]'::jsonb", "repeat('a',64),0,'[]'::jsonb", "repeat('a',64),3,'[]'::jsonb"],
)
def test_batch_operation_database_rejects_invalid_result_facts(db, values):
    actor, person = uuid4(), uuid4()
    db.execute(
        text(
            "INSERT INTO people (id,full_name,identity_ciphertext,identity_digest,sex,date_of_birth) VALUES (:id,'批量约束','encrypted',:digest,'male','1990-01-01')"
        ),
        {"id": person, "digest": uuid4().hex},
    )
    db.execute(
        text(
            "INSERT INTO user_accounts (id,person_id,email_normalized,password_hash,status,force_password_change,failed_login_count) VALUES (:id,:person,:email,'unused','active',false,0)"
        ),
        {"id": actor, "person": person, "email": f"{actor}@example.test"},
    )
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            text(
                f"INSERT INTO asset_batch_operations (actor_user_id,operation_key,payload_hash,quantity,asset_ids) VALUES (:actor,gen_random_uuid(),{values})"
            ),
            {"actor": actor},
        )


def test_downgrade_refuses_to_discard_recorded_computer_hardware(postgres_engine):
    config = _alembic_config(postgres_engine)
    asset_id = uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO assets (id,asset_code,name,cpu_model) VALUES (:id,:code,'有配置资产','Ryzen 7')"
                ),
                {"id": asset_id, "code": f"MIG-{asset_id.hex[:12]}"},
            )
        with pytest.raises(RuntimeError, match="retained asset batch or hardware"):
            command.downgrade(config, "0075_global_appearance")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT cpu_model FROM assets WHERE id=:id"), {"id": asset_id}
                )
                == "Ryzen 7"
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM assets WHERE id=:id"), {"id": asset_id})


def test_downgrade_refuses_to_discard_batch_retry_evidence(postgres_engine):
    config = _alembic_config(postgres_engine)
    actor, person = uuid4(), uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people (id,full_name,identity_ciphertext,identity_digest,sex,date_of_birth) VALUES (:id,'迁移批次','encrypted',:digest,'male','1990-01-01')"
                ),
                {"id": person, "digest": uuid4().hex},
            )
            connection.execute(
                text(
                    "INSERT INTO user_accounts (id,person_id,email_normalized,password_hash,status,force_password_change,failed_login_count) VALUES (:id,:person,:email,'unused','active',false,0)"
                ),
                {"id": actor, "person": person, "email": f"{actor}@example.test"},
            )
            connection.execute(
                text(
                    "INSERT INTO asset_batch_operations (actor_user_id,operation_key,payload_hash,quantity,asset_ids) VALUES (:actor,gen_random_uuid(),repeat('a',64),1,jsonb_build_array(gen_random_uuid()::text))"
                ),
                {"actor": actor},
            )
        with pytest.raises(RuntimeError, match="retained asset batch or hardware"):
            command.downgrade(config, "0075_global_appearance")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM asset_batch_operations WHERE actor_user_id=:actor"),
                    {"actor": actor},
                )
                == 1
            )
    finally:
        command.upgrade(config, CURRENT_HEAD)
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM asset_batch_operations WHERE actor_user_id=:actor"),
                {"actor": actor},
            )
            connection.execute(text("DELETE FROM user_accounts WHERE id=:actor"), {"actor": actor})
            connection.execute(text("DELETE FROM people WHERE id=:person"), {"person": person})
