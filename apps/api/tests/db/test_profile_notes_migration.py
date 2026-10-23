from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_migrations import _alembic_config, _delete_test_account, _insert_test_account


def test_profile_notes_upgrade_preserves_existing_identity_and_checks_note_limits(
    postgres_engine: Engine,
) -> None:
    config = _alembic_config(postgres_engine)
    command.downgrade(config, "0066_assisted_personnel")
    # Preserve the complete old schema projection across newer additive heads.
    legacy_people_columns = ", ".join(
        postgres_engine.dialect.identifier_preparer.quote(column["name"])
        for column in inspect(postgres_engine).get_columns("people")
    )
    legacy_people = text(f"SELECT {legacy_people_columns} FROM people WHERE id=:id")
    person_id, account_id = _insert_test_account(postgres_engine)
    try:
        with postgres_engine.connect() as connection:
            original = dict(connection.execute(legacy_people, {"id": person_id}).mappings().one())
        command.upgrade(config, "head")
        with postgres_engine.begin() as connection:
            upgraded = dict(connection.execute(legacy_people, {"id": person_id}).mappings().one())
            assert upgraded == original
            assert connection.execute(
                text(
                    "SELECT profile_notes,document_type,document_issuing_country,"
                    "passport_number_digest,intake_mode,team_allocation_status,"
                    "team_allocation_version FROM people WHERE id=:id"
                ),
                {"id": person_id},
            ).one() == (None, "cn_id", None, None, "strict", "untracked", 1)
            connection.execute(
                text("UPDATE people SET profile_notes=:notes WHERE id=:id"),
                {"id": person_id, "notes": "测" * 2000},
            )
            assert (
                connection.scalar(
                    text("SELECT length(profile_notes) FROM people WHERE id=:id"), {"id": person_id}
                )
                == 2000
            )
            for invalid in (" ", "测" * 2001):
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(
                        text("UPDATE people SET profile_notes=:notes WHERE id=:id"),
                        {"id": person_id, "notes": invalid},
                    )
        command.downgrade(config, "0066_assisted_personnel")
        assert "profile_notes" not in {
            column["name"] for column in inspect(postgres_engine).get_columns("people")
        }
        with postgres_engine.connect() as connection:
            after = dict(connection.execute(legacy_people, {"id": person_id}).mappings().one())
            assert after == original
    finally:
        _delete_test_account(postgres_engine, person_id=person_id, account_id=account_id)
        command.upgrade(config, "head")
