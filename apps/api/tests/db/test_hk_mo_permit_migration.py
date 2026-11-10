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


def test_permit_migration_preserves_legacy_rows_and_refuses_lossy_downgrade(postgres_engine):
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0077_hk_identity_documents")
    person, account = _insert_test_account(postgres_engine)
    passport, hk, permit = uuid4(), uuid4(), uuid4()
    try:
        with postgres_engine.begin() as connection:
            for identifier, kind in ((passport, "passport"), (hk, "hk_id")):
                connection.execute(
                    text(
                        "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode,passport_number_digest) "
                        "VALUES (:id,'Synthetic legacy','unchanged encrypted',:digest,:kind,'pending',:bare)"
                    ),
                    {
                        "id": identifier,
                        "kind": kind,
                        "digest": uuid4().hex * 2,
                        "bare": uuid4().hex * 2 if kind == "passport" else None,
                    },
                )
            query = text("SELECT * FROM people WHERE id IN (:cn,:p,:hk) ORDER BY id")
            params = {"cn": person, "p": passport, "hk": hk}
            before = connection.execute(query, params).all()
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            assert connection.execute(query, params).all() == before
            connection.execute(
                text(
                    "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode) "
                    "VALUES (:id,'Synthetic permit','encrypted',:digest,'hk_mo_permit','pending')"
                ),
                {"id": permit, "digest": uuid4().hex * 2},
            )
        with pytest.raises(RuntimeError, match="permit downgrade"):
            command.downgrade(config, "0077_hk_identity_documents")
        with postgres_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == CURRENT_HEAD
            )
            assert (
                connection.scalar(
                    text("SELECT document_type FROM people WHERE id=:id"), {"id": permit}
                )
                == "hk_mo_permit"
            )
        with postgres_engine.begin() as connection:
            connection.execute(text("DELETE FROM people WHERE id=:id"), {"id": permit})
        command.downgrade(config, "0077_hk_identity_documents")
        with postgres_engine.connect() as connection:
            assert connection.execute(query, params).all() == before
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO people(id,full_name,identity_ciphertext,identity_digest,document_type,intake_mode) "
                    "VALUES (:id,'Synthetic refused','encrypted',:digest,'hk_mo_permit','pending')"
                ),
                {"id": permit, "digest": uuid4().hex * 2},
            )
    finally:
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM people WHERE id IN (:p,:hk,:permit)"),
                {"p": passport, "hk": hk, "permit": permit},
            )
        _delete_test_account(postgres_engine, person_id=person, account_id=account)


def test_permit_database_facts_keys_and_account_guard(postgres_engine):
    person, account = _insert_test_account(postgres_engine)
    try:
        for assignment in (
            "document_type='hk_mo_permit',intake_mode='pending',sex=NULL,date_of_birth=NULL",
            "document_type='hk_mo_permit',document_issuing_country='HK'",
            "document_type='hk_mo_permit',passport_number_digest='" + "a" * 64 + "'",
        ):
            with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
                connection.execute(
                    text(f"UPDATE people SET {assignment} WHERE id=:id"), {"id": person}
                )
        with postgres_engine.begin() as connection:
            connection.execute(
                text("UPDATE people SET document_type='hk_mo_permit' WHERE id=:id"), {"id": person}
            )
    finally:
        _delete_test_account(postgres_engine, person_id=person, account_id=account)
