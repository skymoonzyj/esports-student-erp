from __future__ import annotations

import importlib
import importlib.util
from itertools import product
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.housing.models import HousingStay
from tests.cli.test_operations_docs import _shell_array, _text
from tests.db.test_housing_stay_lifecycle import _room
from tests.db.test_migrations import (
    CURRENT_HEAD,
    _alembic_config,
    _delete_test_account,
    _insert_test_account,
    _unrelated_rbac_snapshot,
)

HEAD = "0070_housing_history_void"
PREVIOUS = "0069_pending_profile_intake"
VOID_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000090")
VOID_PERMISSION_KEY = "housing.history.void"


@pytest.fixture(autouse=True)
def restore_current_schema(postgres_engine):
    """Historical roundtrips must not leave later tests on an obsolete schema."""
    config = _alembic_config(postgres_engine)
    command.upgrade(config, "head")
    try:
        yield
    finally:
        command.upgrade(config, "head")


def void_permission(conn):
    return conn.execute(
        text("SELECT id,key,name,sensitive FROM permissions WHERE key=:key"),
        {"key": VOID_PERMISSION_KEY},
    ).one_or_none()


def test_void_permission_seed_roundtrip_is_super_admin_only_and_preserves_other_rbac(
    postgres_engine,
):
    engine = postgres_engine
    config = require_revision(engine)
    with engine.connect() as conn:
        assert void_permission(conn) == (
            VOID_PERMISSION_ID,
            VOID_PERMISSION_KEY,
            "作废历史入住记录",
            True,
        )
    try:
        command.downgrade(config, PREVIOUS)
        with engine.connect() as conn:
            assert void_permission(conn) is None
            before = _unrelated_rbac_snapshot(conn, {VOID_PERMISSION_KEY})
        command.upgrade(config, HEAD)
        with engine.connect() as conn:
            assert void_permission(conn) == (
                VOID_PERMISSION_ID,
                VOID_PERMISSION_KEY,
                "作废历史入住记录",
                True,
            )
            assert conn.execute(
                text(
                    "SELECT r.key,rp.scope_kind FROM role_permissions rp JOIN roles r ON r.id=rp.role_id WHERE rp.permission_id=:id"
                ),
                {"id": VOID_PERMISSION_ID},
            ).all() == [("super_admin", "global")]
            assert _unrelated_rbac_snapshot(conn, {VOID_PERMISSION_KEY}) == before
        command.downgrade(config, PREVIOUS)
        with engine.connect() as conn:
            assert void_permission(conn) is None
            assert _unrelated_rbac_snapshot(conn, {VOID_PERMISSION_KEY}) == before
    finally:
        command.upgrade(config, HEAD)


def test_void_permission_custom_reference_blocks_downgrade_without_losing_seed_or_schema(
    postgres_engine,
):
    engine = postgres_engine
    config = require_revision(engine)
    person, actor = _insert_test_account(engine)
    override_id = uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO user_permission_overrides (id,user_id,permission_id,effect,scope_kind) VALUES (:id,:actor,:permission,'deny','global')"
                ),
                {"id": override_id, "actor": actor, "permission": VOID_PERMISSION_ID},
            )
            before = _unrelated_rbac_snapshot(conn, set())
        with pytest.raises(IntegrityError):
            command.downgrade(config, PREVIOUS)
        with engine.connect() as conn:
            assert _unrelated_rbac_snapshot(conn, set()) == before
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM user_permission_overrides WHERE id=:id"),
                    {"id": override_id},
                )
                == 1
            )
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_housing_stays_void_fact_immutable','trg_housing_stay_void_operations_immutable') AND NOT tgisinternal"
                    )
                )
                == 2
            )
        assert "housing_stay_void_operations" in inspect(engine).get_table_names()
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM user_permission_overrides WHERE id=:id"), {"id": override_id}
            )
        _delete_test_account(engine, person_id=person, account_id=actor)


def require_revision(engine):
    config = _alembic_config(engine)
    assert HEAD in {r.revision for r in ScriptDirectory.from_config(config).walk_revisions()}, (
        "historical housing void migration is missing"
    )
    return config


def seed(engine):
    person, actor = _insert_test_account(engine)
    # This fixture deliberately runs on pre-0070 schemas too. Avoid current ORM
    # defaults for later room columns while testing a historical upgrade.
    room_id, floor_id, building_id = uuid4(), uuid4(), uuid4()
    suffix = uuid4().hex[:10].upper()
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO housing_buildings (id,code,name,display_order,is_active) "
            "VALUES (:id,:code,:name,0,true)"
        ), {"id": building_id, "code": f"L{suffix}", "name": f"生命周期楼{suffix}"})
        conn.execute(text(
            "INSERT INTO housing_floors (id,building_id,number,name,display_order,is_active) "
            "VALUES (:id,:building,2,:name,0,true)"
        ), {"id": floor_id, "building": building_id, "name": f"二楼{suffix}"})
        conn.execute(text(
            "INSERT INTO housing_rooms (id,building_id,floor_id,room_number,normalized_key,"
            "room_type,capacity,verification_status,service_status,version) "
            "VALUES (:id,:building,:floor,:number,:number,'custom',2,'verified','available',1)"
        ), {"id": room_id, "building": building_id, "floor": floor_id, "number": f"{suffix}-201"})
    ids = [uuid4(), uuid4()]
    with engine.begin() as conn:
        for identifier, status in zip(ids, ("checked_out", "planned"), strict=True):
            conn.execute(
                text("""
                INSERT INTO housing_stays
                    (id,person_id,room_id,stay_purpose,duration_mode,planned_check_in_at,
                     actual_check_in_at,actual_check_out_at,status,room_name_snapshot,
                     floor_name_snapshot,building_name_snapshot,created_by_user_id,
                     checked_in_by_user_id,checked_out_by_user_id,version)
                VALUES (:id,:person,:room,'permanent','long_term','2026-01-01T00:00:00Z',
                        CASE WHEN :status='checked_out' THEN '2026-01-01T00:00:00Z'::timestamptz END,
                        CASE WHEN :status='checked_out' THEN '2026-01-02T00:00:00Z'::timestamptz END,
                        :status,'synthetic room','synthetic floor','synthetic building',:actor,
                        CASE WHEN :status='checked_out' THEN :actor END,
                        CASE WHEN :status='checked_out' THEN :actor END,1)
            """),
                {
                    "id": identifier,
                    "person": person,
                    "room": room_id,
                    "actor": actor,
                    "status": status,
                },
            )
    return person, actor, ids, room_id, floor_id, building_id


def cleanup(engine, data):
    person, actor, stays, room, floor, building = data
    with engine.begin() as conn:
        if "housing_stay_void_operations" in inspect(conn).get_table_names():
            conn.execute(
                text(
                    "ALTER TABLE housing_stay_void_operations DISABLE TRIGGER trg_housing_stay_void_operations_immutable"
                )
            )
            conn.execute(
                text("DELETE FROM housing_stay_void_operations WHERE stay_id IN (:a,:b)"),
                {"a": stays[0], "b": stays[1]},
            )
            conn.execute(
                text(
                    "ALTER TABLE housing_stay_void_operations ENABLE TRIGGER trg_housing_stay_void_operations_immutable"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE housing_stays DISABLE TRIGGER trg_housing_stays_void_fact_immutable"
                )
            )
        for identifier in stays:
            conn.execute(text("DELETE FROM housing_stays WHERE id=:id"), {"id": identifier})
        if "housing_stay_void_operations" in inspect(conn).get_table_names():
            conn.execute(
                text(
                    "ALTER TABLE housing_stays ENABLE TRIGGER trg_housing_stays_void_fact_immutable"
                )
            )
        for table, identifier in (
            ("housing_rooms", room),
            ("housing_floors", floor),
            ("housing_buildings", building),
        ):
            conn.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": identifier})
    _delete_test_account(engine, person_id=person, account_id=actor)


def mark(conn, stay, actor):
    conn.execute(
        text(
            "UPDATE housing_stays SET voided_at='2026-10-21T00:00:00Z',voided_by_user_id=:actor,void_reason_ciphertext='synthetic-cipher' WHERE id=:stay"
        ),
        {"stay": stay, "actor": actor},
    )


def test_void_migration_preserves_existing_rows_and_constraints(postgres_engine: Engine):
    engine = postgres_engine
    config = require_revision(engine)
    command.downgrade(config, PREVIOUS)
    data = seed(engine)
    _, actor, stays, *_ = data
    columns = ",".join(f'"{c["name"]}"' for c in inspect(engine).get_columns("housing_stays"))
    query = text(f"SELECT {columns} FROM housing_stays ORDER BY id")
    try:
        with engine.connect() as conn:
            legacy_rows_before = conn.execute(query).all()
        command.upgrade(config, HEAD)
        with engine.begin() as conn:
            legacy_rows_after = conn.execute(query).all()
            assert legacy_rows_after == legacy_rows_before
            assert conn.execute(
                text(
                    "SELECT voided_at,voided_by_user_id,void_reason_ciphertext FROM housing_stays WHERE id=:id"
                ),
                {"id": stays[0]},
            ).one() == (None, None, None)
            for sql, params, constraint in [
                (
                    "UPDATE housing_stays SET voided_at=now() WHERE id=:id",
                    {"id": stays[0]},
                    "ck_housing_stays_void_fact",
                ),
                (
                    "UPDATE housing_stays SET voided_at=now(),voided_by_user_id=:actor,void_reason_ciphertext='cipher' WHERE id=:id",
                    {"id": stays[1], "actor": actor},
                    "ck_housing_stays_void_fact",
                ),
                (
                    "UPDATE housing_stays SET voided_at=now(),voided_by_user_id=:actor,void_reason_ciphertext='cipher' WHERE id=:id",
                    {"id": stays[0], "actor": uuid4()},
                    "fk_housing_stays_void_actor",
                ),
            ]:
                with pytest.raises(IntegrityError) as exc, conn.begin_nested():
                    conn.execute(text(sql), params)
                assert exc.value.orig.diag.constraint_name == constraint
            # Exercise actual INSERTs as well as updates: every partial combination
            # must fail, as must a complete fact on a non-checked-out stay or with
            # an unknown actor. Existing status/date constraints stay unchanged.
            insert_columns = columns + ",voided_at,voided_by_user_id,void_reason_ciphertext"
            copy_columns = ",".join(
                ":new_id" if c["name"] == "id" else f'"{c["name"]}"'
                for c in inspect(conn).get_columns("housing_stays")
                if c["name"] not in {"voided_at", "voided_by_user_id", "void_reason_ciphertext"}
            )
            insert = text(
                f"INSERT INTO housing_stays ({insert_columns}) SELECT {copy_columns},"
                ":voided_at,:void_actor,:cipher FROM housing_stays WHERE id=:source"
            )
            complete = {
                "new_id": uuid4(),
                "source": stays[0],
                "voided_at": "2026-10-21T00:00:00Z",
                "void_actor": actor,
                "cipher": "cipher",
            }
            for flags in product((False, True), repeat=3):
                if all(flags) or not any(flags):
                    continue
                params = complete | {
                    key: complete[key] if present else None
                    for key, present in zip(
                        ("voided_at", "void_actor", "cipher"), flags, strict=True
                    )
                }
                with pytest.raises(IntegrityError) as exc, conn.begin_nested():
                    conn.execute(insert, params)
                assert exc.value.orig.diag.constraint_name == "ck_housing_stays_void_fact"
            for overrides, constraint in (
                ({"source": stays[1]}, "ck_housing_stays_void_fact"),
                ({"void_actor": uuid4()}, "fk_housing_stays_void_actor"),
            ):
                with pytest.raises(IntegrityError) as exc, conn.begin_nested():
                    conn.execute(insert, complete | overrides)
                assert exc.value.orig.diag.constraint_name == constraint
            mark(conn, stays[0], actor)
            assert conn.execute(query).all() == legacy_rows_before
            for assignment in (
                "voided_at=NULL,voided_by_user_id=NULL,void_reason_ciphertext=NULL",
                "void_reason_ciphertext='rewritten'",
                "voided_at=now()",
                "voided_by_user_id=:other",
            ):
                with pytest.raises(IntegrityError), conn.begin_nested():
                    conn.execute(
                        text(f"UPDATE housing_stays SET {assignment} WHERE id=:id"),
                        {"id": stays[0], "other": uuid4()},
                    )
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text("DELETE FROM housing_stays WHERE id=:id"), {"id": stays[0]})
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(text("DELETE FROM user_accounts WHERE id=:id"), {"id": actor})
        # FK relationships and reporting protection survive the additive revision.
        fks = inspect(engine).get_foreign_keys("housing_transfers")
        assert {tuple(fk["constrained_columns"]) for fk in fks} >= {
            ("from_stay_id",),
            ("to_stay_id",),
        }
        void_actor_fk = next(
            fk
            for fk in inspect(engine).get_foreign_keys("housing_stays")
            if fk["constrained_columns"] == ["voided_by_user_id"]
        )
        assert void_actor_fk["referred_table"] == "user_accounts"
        assert void_actor_fk["options"]["ondelete"] == "RESTRICT"
        with engine.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_housing_report_batches_update','trg_housing_report_batch_scopes_immutable','trg_housing_report_batches_no_delete','trg_housing_report_batches_correction_predecessor') AND NOT tgisinternal"
                    )
                )
                == 4
            )
    finally:
        cleanup(engine, data)
        command.upgrade(config, "head")


def operation(conn, stay, actor, /, **overrides):
    values = {
        "id": uuid4(),
        "stay": stay,
        "actor": actor,
        "key": uuid4(),
        "fingerprint": "a" * 64,
        "version": 2,
    } | overrides
    conn.execute(
        text(
            "INSERT INTO housing_stay_void_operations (id,actor_account_id,idempotency_key,stay_id,request_fingerprint,result_version) VALUES (:id,:actor,:key,:stay,:fingerprint,:version)"
        ),
        values,
    )
    return values


def test_void_operation_is_unique_restrictive_and_immutable(postgres_engine):
    engine = postgres_engine
    require_revision(engine)
    data = seed(engine)
    _, actor, stays, *_ = data
    try:
        with engine.begin() as conn:
            values = operation(conn, stays[0], actor)
            for overrides in (
                {},
                {"stay": stays[1], "key": values["key"]},
                {"stay": uuid4()},
                {"stay": stays[1], "actor": uuid4()},
                {"stay": stays[1], "fingerprint": "invalid"},
                {"stay": stays[1], "version": 0},
            ):
                with pytest.raises(IntegrityError), conn.begin_nested():
                    operation(conn, stays[0], actor, **overrides)
            for sql in (
                "UPDATE housing_stay_void_operations SET result_version=3 WHERE id=:id",
                "DELETE FROM housing_stay_void_operations WHERE id=:id",
                "DELETE FROM housing_stays WHERE id=:stay",
            ):
                with pytest.raises(IntegrityError), conn.begin_nested():
                    conn.execute(text(sql), values)
    finally:
        cleanup(engine, data)


@pytest.mark.parametrize("evidence", ["fact", "operation"])
def test_void_evidence_refuses_downgrade_before_destructive_ddl(postgres_engine, evidence):
    engine = postgres_engine
    config = require_revision(engine)
    data = seed(engine)
    _, actor, stays, *_ = data
    try:
        with engine.begin() as conn:
            if evidence == "fact":
                mark(conn, stays[0], actor)
            else:
                operation(conn, stays[0], actor)
            permission_before = void_permission(conn)
            rbac_before = _unrelated_rbac_snapshot(conn, set())
        with pytest.raises(RuntimeError, match="void"):
            command.downgrade(config, PREVIOUS)
        assert "housing_stay_void_operations" in inspect(engine).get_table_names()
        assert "voided_at" in {c["name"] for c in inspect(engine).get_columns("housing_stays")}
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
            assert void_permission(conn) == permission_before
            assert _unrelated_rbac_snapshot(conn, set()) == rbac_before
    finally:
        cleanup(engine, data)


def test_empty_void_facts_upgrade_downgrade_roundtrip(postgres_engine):
    engine = postgres_engine
    config = require_revision(engine)
    command.downgrade(config, PREVIOUS)
    before = set(inspect(engine).get_table_names())
    command.upgrade(config, HEAD)
    command.downgrade(config, PREVIOUS)
    assert set(inspect(engine).get_table_names()) == before
    assert "voided_at" not in {c["name"] for c in inspect(engine).get_columns("housing_stays")}
    command.upgrade(config, HEAD)


def test_effective_stay_clause_only_filters_void_fact(postgres_engine):
    assert importlib.util.find_spec("app.housing.history_validity") is not None, (
        "effective stay predicate is missing"
    )
    clause = importlib.import_module("app.housing.history_validity").effective_stay_clause
    data = seed(postgres_engine)
    _, actor, stays, *_ = data
    try:
        with postgres_engine.begin() as conn:
            mark(conn, stays[0], actor)
        with Session(postgres_engine) as db:
            for model in (HousingStay, aliased(HousingStay)):
                assert db.scalars(
                    select(model.id).where(model.id.in_(stays), clause(model))
                ).all() == [stays[1]]
            assert db.scalars(
                select(HousingStay.id).where(HousingStay.id.in_(stays), clause())
            ).all() == [stays[1]]
    finally:
        cleanup(postgres_engine, data)


def test_void_fact_preserves_transfer_chain_and_other_stay(postgres_engine):
    engine = postgres_engine
    require_revision(engine)
    data = seed(engine)
    person, actor, stays, *_ = data
    with Session(engine) as db:
        destination = _room(db)
        destination_ids = destination.id, destination.floor_id, destination.building_id
        db.commit()
    transfer_id = uuid4()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE housing_stays SET room_id=:room,planned_check_in_at='2026-01-02T00:00:00Z' WHERE id=:id"
                ),
                {"room": destination_ids[0], "id": stays[1]},
            )
            conn.execute(
                text("""
                    INSERT INTO housing_transfers
                        (id,person_id,from_stay_id,to_stay_id,transferred_at,reason,transferred_by_user_id)
                    VALUES (:id,:person,:source,:target,'2026-01-02T00:00:00Z','synthetic transfer',:actor)
                """),
                {
                    "id": transfer_id,
                    "person": person,
                    "source": stays[0],
                    "target": stays[1],
                    "actor": actor,
                },
            )
            projection = text(
                "SELECT to_jsonb(s) - ARRAY['voided_at','voided_by_user_id','void_reason_ciphertext']::text[] FROM housing_stays s WHERE id IN (:source,:target) ORDER BY id"
            )
            params = {"source": stays[0], "target": stays[1]}
            before_stays = conn.execute(projection, params).all()
            before_transfer = conn.execute(
                text("SELECT * FROM housing_transfers WHERE id=:id"), {"id": transfer_id}
            ).one()
            mark(conn, stays[0], actor)
            assert conn.execute(projection, params).all() == before_stays
            assert (
                conn.execute(
                    text("SELECT * FROM housing_transfers WHERE id=:id"), {"id": transfer_id}
                ).one()
                == before_transfer
            )
            with pytest.raises(IntegrityError) as exc, conn.begin_nested():
                conn.execute(text("DELETE FROM housing_stays WHERE id=:id"), {"id": stays[1]})
            assert exc.value.orig.diag.constraint_name is not None
        transfer_fks = inspect(engine).get_foreign_keys("housing_transfers")
        assert all(
            fk["options"]["ondelete"] == "RESTRICT"
            for fk in transfer_fks
            if fk["constrained_columns"] in (["from_stay_id"], ["to_stay_id"])
        )
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM housing_transfers WHERE id=:id"), {"id": transfer_id})
        cleanup(engine, data)
        with engine.begin() as conn:
            for table, identifier in zip(
                ("housing_rooms", "housing_floors", "housing_buildings"),
                destination_ids,
                strict=True,
            ):
                conn.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": identifier})


def test_void_manifest_matches_complete_database_schema(postgres_engine):
    require_revision(postgres_engine)
    manifest = _text("scripts/db-schema-manifest.sh")
    inspector = inspect(postgres_engine)
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set(_shell_array(manifest, "REQUIRED_TABLES"))
    # The existing row-count manifest intentionally omits auth/session and
    # dependent profile tables; it must not name absent tables, and new void
    # evidence must participate in backup/restore count verification.
    row_count_tables = set(_shell_array(manifest, "ROW_COUNT_TABLES"))
    assert row_count_tables <= tables
    assert "housing_stay_void_operations" in row_count_tables
    with postgres_engine.connect() as conn:
        for array, sql in (
            ("REQUIRED_INDEXES", "SELECT indexname FROM pg_indexes WHERE schemaname='public'"),
            (
                "REQUIRED_CONSTRAINTS",
                "SELECT conname FROM pg_constraint WHERE connamespace='public'::regnamespace",
            ),
            (
                "REQUIRED_TRIGGERS",
                "SELECT tgname FROM pg_trigger JOIN pg_class ON tgrelid=pg_class.oid WHERE relnamespace='public'::regnamespace AND NOT tgisinternal",
            ),
        ):
            actual = set(conn.scalars(text(sql)))
            assert set(_shell_array(manifest, array)) <= actual
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_HEAD
    for array in (
        "REQUIRED_SCOPE_COLUMNS",
        "REQUIRED_ENCRYPTED_COLUMNS",
        "REQUIRED_NULLABLE_LEGACY_COLUMNS",
    ):
        for qualified in _shell_array(manifest, array):
            table, column = qualified.split(".")
            actual_columns = {c["name"]: c for c in inspector.get_columns(table)}
            assert column in actual_columns
            if array == "REQUIRED_NULLABLE_LEGACY_COLUMNS":
                assert actual_columns[column]["nullable"]
