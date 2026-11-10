"""New role-intent storage is additive and cannot silently erase evidence."""

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_team_staff_migration import config_for
from tests.services.test_intake_activation_roles import committed_db as _committed_db
from tests.services.test_intake_role_intent_activation import prepare


@pytest.fixture
def committed_db(postgres_engine):
    yield from _committed_db.__wrapped__(postgres_engine)


def test_empty_intent_migration_roundtrip_preserves_existing_authority(committed_db, postgres_engine):
    config = config_for(postgres_engine)
    committed_db.commit()
    command.downgrade(config, "0087_intake_identity_teams")
    before = set(inspect(postgres_engine).get_table_names())
    command.upgrade(config, "0088_intake_role_intents")
    assert set(inspect(postgres_engine).get_table_names()) - before == {"intake_role_intents", "intake_role_decisions"}
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0088_intake_role_intents"
    command.upgrade(config, "head")


def test_pending_intent_is_immutable_and_blocks_lossy_downgrade(committed_db, postgres_engine):
    db = committed_db
    prepare(db)
    for mutation in ("UPDATE intake_role_intents SET role_keys=ARRAY['finance']", "DELETE FROM intake_role_intents"):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(text(mutation))
    db.commit()
    with pytest.raises(RuntimeError, match="已有历史"):
        command.downgrade(config_for(postgres_engine), "0087_intake_identity_teams")
    assert "intake_role_intents" in inspect(postgres_engine).get_table_names()
