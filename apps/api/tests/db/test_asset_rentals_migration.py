from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from tests.db.test_migrations import CURRENT_HEAD, _alembic_config
from tests.departure_helpers import committed_scene


def test_rental_migration_backfills_only_linked_procurement_and_blocks_data_loss(postgres_engine):
    config = _alembic_config(postgres_engine)
    center_id, purchase_id, linked_id, legacy_id = [uuid4() for _ in range(4)]
    with committed_scene(postgres_engine) as (db, context, _subject, _stay):
        db.commit()
        try:
            command.downgrade(config, "0072_operations_request_errors")
            with postgres_engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO procurement_settlement_centers (id,name,created_by_user_id) VALUES (:id,:name,:actor)"
                    ),
                    {"id": center_id, "name": f"租赁迁移{center_id}", "actor": context.account.id},
                )
                connection.execute(
                    text("""INSERT INTO daily_purchase_records
                    (id,purchase_date,item_name,purchase_channel,quantity,unit_price_cents,total_price_cents,settlement_center_id,created_by_user_id)
                    VALUES (:id,'2026-01-01','迁移采购','online',1,100,100,:center,:actor)"""),
                    {"id": purchase_id, "center": center_id, "actor": context.account.id},
                )
                for asset_id, source in [(linked_id, purchase_id), (legacy_id, None)]:
                    connection.execute(
                        text(
                            "INSERT INTO assets (id,asset_code,name,source_purchase_id,warranty_channel) VALUES (:id,:code,'历史资产',:source,'offline')"
                        ),
                        {"id": asset_id, "code": f"MIG-{asset_id.hex[:12]}", "source": source},
                    )
            command.upgrade(config, CURRENT_HEAD)
            with postgres_engine.connect() as connection:
                linked = connection.execute(
                    text(
                        "SELECT ownership_type,acquisition_method,acquisition_channel,warranty_channel,rental_revision FROM assets WHERE id=:id"
                    ),
                    {"id": linked_id},
                ).one()
                assert tuple(linked) == ("owned", "purchase", "online", "offline", 0)
                assert (
                    connection.scalar(
                        text("SELECT ownership_type FROM assets WHERE id=:id"), {"id": legacy_id}
                    )
                    is None
                )
                assert "uq_asset_assignments_active_asset" in {
                    row["name"] for row in inspect(connection).get_indexes("asset_assignments")
                }
            with pytest.raises(RuntimeError, match="retained asset ownership"):
                command.downgrade(config, "0072_operations_request_errors")
            with postgres_engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == CURRENT_HEAD
                )
                assert (
                    connection.scalar(
                        text("SELECT acquisition_channel FROM assets WHERE id=:id"),
                        {"id": linked_id},
                    )
                    == "online"
                )
        finally:
            with postgres_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM assets WHERE id IN (:linked,:legacy)"),
                    {"linked": linked_id, "legacy": legacy_id},
                )
                connection.execute(
                    text("DELETE FROM daily_purchase_records WHERE id=:id"), {"id": purchase_id}
                )
                connection.execute(
                    text("DELETE FROM procurement_settlement_centers WHERE id=:id"),
                    {"id": center_id},
                )
            command.upgrade(config, CURRENT_HEAD)


@pytest.mark.parametrize(
    "facts",
    [
        "ownership_type='owned'",
        "ownership_type='owned',acquisition_method='purchase'",
        "ownership_type='owned',acquisition_method='purchase',acquisition_note='note'",
        "ownership_type='rented',rental_started_on='2026-01-01',rental_expires_on='10000-01-01'",
        "ownership_type='mystery'",
        "ownership_type='rented'",
        "rental_started_on='2026-01-01'",
        "rental_revision=-1",
    ],
)
def test_database_rejects_incomplete_or_unknown_ownership(db, facts):
    asset_id = uuid4()
    db.execute(
        text("INSERT INTO assets (id,asset_code,name) VALUES (:id,:code,'约束测试')"),
        {"id": asset_id, "code": asset_id.hex},
    )
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(text(f"UPDATE assets SET {facts} WHERE id=:id"), {"id": asset_id})
