from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.db.test_migrations import (
    CURRENT_HEAD,
    _alembic_config,
    _delete_test_account,
    _insert_test_account,
)


def test_hkid_migration_preserves_legacy_rows_and_refuses_lossy_downgrade(postgres_engine):
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0076_asset_batch_hardware")
    person, account = _insert_test_account(postgres_engine)
    passport, hk = uuid4(), uuid4()
    try:
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode,passport_number_digest) VALUES (:id,'Synthetic passport','encrypted',:digest,'passport','pending',:bare)"
                ),
                {"id": passport, "digest": uuid4().hex * 2, "bare": uuid4().hex * 2},
            )
            before = connection.execute(
                text("SELECT * FROM people WHERE id IN (:cn,:p) ORDER BY id"),
                {"cn": person, "p": passport},
            ).all()
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            assert (
                connection.execute(
                    text("SELECT * FROM people WHERE id IN (:cn,:p) ORDER BY id"),
                    {"cn": person, "p": passport},
                ).all()
                == before
            )
            connection.execute(
                text(
                    "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode) VALUES (:id,'Synthetic HKID','encrypted',:digest,'hk_id','pending')"
                ),
                {"id": hk, "digest": uuid4().hex * 2},
            )
        with pytest.raises(RuntimeError, match="HKID downgrade"):
            command.downgrade(config, "0076_asset_batch_hardware")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == CURRENT_HEAD
            )
            assert (
                connection.scalar(text("SELECT document_type FROM people WHERE id=:id"), {"id": hk})
                == "hk_id"
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM people WHERE id IN (:p,:hk)"), {"p": passport, "hk": hk}
            )
        _delete_test_account(postgres_engine, person_id=person, account_id=account)


def test_hkid_database_facts_and_account_guard(postgres_engine):
    person, account = _insert_test_account(postgres_engine)
    try:
        for assignment in (
            "document_type='hk_id',intake_mode='pending',sex=NULL,date_of_birth=NULL",
            "document_type='hk_id',document_issuing_country='HK'",
            "document_type='hk_id',passport_number_digest='" + "a" * 64 + "'",
        ):
            with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE people SET {assignment} WHERE id=:id"), {"id": person}
                )
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE people SET document_type='hk_id' WHERE id=:id"), {"id": person}
            )
    finally:
        _delete_test_account(postgres_engine, person_id=person, account_id=account)
