from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from alembic import command
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.exc import DBAPIError

from app.personnel_intake.quick_import_service import QuickIntakeService
from tests.db.test_team_staff_migration import config_for
from tests.services.test_personnel_approval import approvals
from tests.services.test_profile_claims import claim_db as _claim_db
from tests.services.test_quick_intake_transactions import context, prepare, request


@pytest.fixture
def committed_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def test_quick_intake_empty_migration_roundtrip(committed_db, postgres_engine):
    config = config_for(postgres_engine)
    command.downgrade(config, "0088_intake_role_intents")
    assert "quick_intake_receipts" not in inspect(postgres_engine).get_table_names()
    command.upgrade(config, "head")
    assert "quick_intake_receipts" in inspect(postgres_engine).get_table_names()


def test_nonempty_receipts_cannot_be_silently_downgraded(committed_db, postgres_engine):
    uid, sid, body = prepare(committed_db)
    QuickIntakeService(approvals(committed_db)).commit(context(committed_db, uid, sid), body, request())
    with pytest.raises(RuntimeError, match="导入回执"):
        command.downgrade(config_for(postgres_engine), "0088_intake_role_intents")
    assert "quick_intake_receipts" in inspect(postgres_engine).get_table_names()


def test_downgrade_blocks_receipt_insert_after_empty_guard(committed_db, postgres_engine):
    uid, sid, body = prepare(committed_db)
    QuickIntakeService(approvals(committed_db)).commit(context(committed_db, uid, sid), body, request())
    receipt = dict(committed_db.execute(text("SELECT * FROM quick_intake_receipts")).mappings().one())
    committed_db.execute(text("DELETE FROM quick_intake_receipts"))
    committed_db.commit()
    checked, release = Event(), Event()
    config = config_for(postgres_engine)

    def pause_after_guard(connection, cursor, statement, parameters, context, executemany):
        if statement == "SELECT EXISTS (SELECT 1 FROM quick_intake_receipts)":
            checked.set()
            if not release.wait(timeout=15):
                raise TimeoutError("receipt downgrade guard was not released")

    event.listen(Engine, "after_cursor_execute", pause_after_guard)
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(command.downgrade, config, "0088_intake_role_intents")
    try:
        assert checked.wait(timeout=10)
        with postgres_engine.connect() as writer:
            transaction = writer.begin()
            try:
                writer.execute(text("SET LOCAL lock_timeout = '200ms'"))
                with pytest.raises(DBAPIError, match="lock timeout"):
                    writer.execute(text(
                        "INSERT INTO quick_intake_receipts "
                        "(actor_id,operation_key,fingerprint,person_id,stay_id,room_id,stay_status,role_pending,created_at) "
                        "VALUES (:actor_id,:operation_key,:fingerprint,:person_id,:stay_id,:room_id,:stay_status,:role_pending,:created_at)"
                    ), receipt)
            finally:
                transaction.rollback()
        release.set()
        future.result(timeout=15)
    finally:
        release.set()
        try:
            future.result(timeout=15)
        finally:
            pool.shutdown(wait=True)
            event.remove(Engine, "after_cursor_execute", pause_after_guard)
            command.upgrade(config, "head")
