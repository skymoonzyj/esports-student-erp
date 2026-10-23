"""Read-only snapshot/isolated verification helper, executed inside the API image.

Its JSON output may contain recovery secrets. The host runner captures it directly
into a private encrypted staging area; never invoke this helper in a user terminal.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys

import psycopg
from psycopg import sql

CHECK_ERRORS = {
    "backup_container_check_failed",
    "recovery_payload_invalid",
    "field_key_canary_failed",
    "lookup_key_canary_failed",
    "restore_row_counts_mismatch",
    "restore_alembic_revision_mismatch",
    "restore_schema_columns_mismatch",
    "restore_schema_constraints_mismatch",
    "restore_schema_indexes_mismatch",
    "restore_schema_sequences_mismatch",
    "restore_sequence_evidence_invalid",
    "restore_sequence_state_mismatch",
    "restore_sequence_next_identifier_unsafe",
    "restored_identity_decryption_failed",
    "restored_phone_decryption_failed",
    "restored_email_decryption_failed",
    "restored_mail_username_decryption_failed",
    "restored_mail_password_decryption_failed",
    "restored_mail_sender_decryption_failed",
    "restored_mail_reply_to_decryption_failed",
    "snapshot_controller_closed",
}


class CheckFailure(RuntimeError):
    pass


def database_manifest(connection):
    tables = connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    counts = {}
    for (table,) in tables:
        counts[table] = connection.execute(
            sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))
        ).fetchone()[0]
    schema = {}
    queries = {
        "columns": "SELECT table_name,column_name,data_type,is_nullable,column_default "
        "FROM information_schema.columns WHERE table_schema='public' "
        "ORDER BY table_name,ordinal_position",
        "constraints": "SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "ORDER BY c.relname,k.conname",
        "indexes": "SELECT tablename,indexname,indexdef FROM pg_indexes "
        "WHERE schemaname='public' ORDER BY tablename,indexname",
        "sequences": "SELECT sequencename,data_type::text,start_value,min_value,max_value,"
        "increment_by,cycle FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
    }
    for key, query in queries.items():
        schema[key] = [list(row) for row in connection.execute(query).fetchall()]
    return {
        "row_counts": counts,
        "schema": schema,
        "alembic_revision": connection.execute(
            "SELECT version_num FROM alembic_version ORDER BY version_num"
        ).fetchall(),
    }


def verify_sequence_state(connection, evidence, definitions, columns):
    """Compare restored positions to the dump, without nextval or setval calls."""
    if not isinstance(evidence, list):
        raise CheckFailure("restore_sequence_evidence_invalid")
    expected = {}
    for row in evidence:
        if (
            not isinstance(row, list) or len(row) != 3
            or not isinstance(row[0], str) or type(row[1]) is not int
            or type(row[2]) is not bool
        ):
            raise CheckFailure("restore_sequence_evidence_invalid")
        # Let PostgreSQL parse quoted regclass names. Never interpolate dump SQL.
        try:
            name = connection.execute(
                "SELECT n.nspname,c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.oid=to_regclass(%s) AND c.relkind='S'", (row[0],)
            ).fetchone()
        except psycopg.Error:
            raise CheckFailure("restore_sequence_evidence_invalid") from None
        if not name or name[0] != "public" or name[1] in expected:
            raise CheckFailure("restore_sequence_evidence_invalid")
        expected[name[1]] = (row[1], row[2])
    if set(expected) != {row[0] for row in definitions}:
        raise CheckFailure("restore_sequence_evidence_invalid")
    available = {(row[0], row[1]) for row in columns}
    identifier_rules = {
        "admission_order_number_seq": ("admission_cases", "order_number", r"^BM[0-9]{8}([0-9]{6,19})$"),
        "registration_student_number_seq": ("student_profiles", "student_number", r"^REG-[0-9]{4}([0-9]{8,19})$"),
    }
    for name, _, _, minimum, maximum, increment, cycle in definitions:
        actual = connection.execute(
            sql.SQL("SELECT last_value,is_called FROM {}").format(sql.Identifier("public", name))
        ).fetchone()
        if actual != expected[name]:
            raise CheckFailure("restore_sequence_state_mismatch")
        if name not in identifier_rules:
            continue
        table, column, pattern = identifier_rules[name]
        if (table, column) not in available:
            continue  # Older archives may predate these identifier columns.
        next_value = actual[0] + increment if actual[1] else actual[0]
        largest = connection.execute(
            sql.SQL("SELECT max(substring({} from %s)::numeric) FROM {}.{}").format(
                sql.Identifier(column), sql.Identifier("public"), sql.Identifier(table)
            ), (pattern,),
        ).fetchone()[0]
        if (cycle or increment <= 0 or not minimum <= next_value <= maximum
                or (largest is not None and next_value <= largest)):
            raise CheckFailure("restore_sequence_next_identifier_unsafe")


def cipher_for(config):
    from app.security.encryption import EnvelopeCipher

    return EnvelopeCipher(
        base64.urlsafe_b64decode(config["field_encryption_master_key"])
    )


def verify_constraints(connection, expected, actual):
    """Compare expression constraints through PostgreSQL's own restore-time parser.

    pg_dump/restore can re-express array casts and Boolean grouping without
    changing a CHECK or EXCLUDE predicate. Reparse the source definition exactly once, as pg_restore
    does, in an empty TEMP table in the disposable database. Never strip casts,
    parentheses, predicates or skip constraints. All other constraint types
    remain exact comparisons, including their referenced keys and flags.
    """
    expected = sorted(expected, key=lambda row: (row[0], row[1]))
    actual = sorted(actual, key=lambda row: (row[0], row[1]))
    if [row[:2] for row in expected] != [row[:2] for row in actual]:
        raise CheckFailure("restore_schema_constraints_mismatch")
    temporary_tables = {}
    for source, restored in zip(expected, actual, strict=True):
        source_definition = source[2]
        restored_definition = restored[2]
        if source_definition == restored_definition:
            continue
        if not any(
            source_definition.startswith(prefix)
            and restored_definition.startswith(prefix)
            for prefix in ("CHECK (", "EXCLUDE USING ")
        ):
            raise CheckFailure("restore_schema_constraints_mismatch")
        table = source[0]
        if table not in temporary_tables:
            temporary_tables[table] = "erp_backup_check_" + secrets.token_hex(8)
            connection.execute(
                sql.SQL("CREATE TEMP TABLE {} (LIKE public.{}) ON COMMIT DROP").format(
                    sql.Identifier(temporary_tables[table]), sql.Identifier(table)
                )
            )
        temporary = temporary_tables[table]
        check_name = "erp_backup_expected"
        connection.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} {}").format(
                sql.Identifier(temporary),
                sql.Identifier(check_name),
                sql.SQL(source_definition),
            )
        )
        normalized = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid=%s::regclass AND conname=%s",
            ("pg_temp." + temporary, check_name),
        ).fetchone()[0]
        connection.execute(
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                sql.Identifier(temporary), sql.Identifier(check_name)
            )
        )
        if normalized != restored_definition:
            raise CheckFailure("restore_schema_constraints_mismatch")


def index_parts(connection, row):
    table, name, definition = row
    quoted_name, quoted_table = connection.execute(
        "SELECT quote_ident(%s),quote_ident(%s)", (name, table)
    ).fetchone()
    unique = definition.startswith("CREATE UNIQUE INDEX ")
    prefix = (
        "CREATE "
        + ("UNIQUE " if unique else "")
        + "INDEX "
        + quoted_name
        + " ON public."
        + quoted_table
        + " "
    )
    if not definition.startswith(prefix + "USING "):
        raise CheckFailure("restore_schema_indexes_mismatch")
    return unique, definition[len(prefix) :]


def verify_indexes(connection, expected, actual):
    """Reparse changed expression/partial indexes without weakening their predicates."""
    expected = sorted(expected, key=lambda row: (row[0], row[1]))
    actual = sorted(actual, key=lambda row: (row[0], row[1]))
    if [row[:2] for row in expected] != [row[:2] for row in actual]:
        raise CheckFailure("restore_schema_indexes_mismatch")
    tables = {}
    for source, restored in zip(expected, actual, strict=True):
        if source == restored:
            continue
        unique, source_body = index_parts(connection, source)
        restored_unique, restored_body = index_parts(connection, restored)
        if unique != restored_unique:
            raise CheckFailure("restore_schema_indexes_mismatch")
        table = source[0]
        if table not in tables:
            tables[table] = "erp_backup_index_table_" + secrets.token_hex(8)
            connection.execute(
                sql.SQL("CREATE TEMP TABLE {} (LIKE public.{}) ON COMMIT DROP").format(
                    sql.Identifier(tables[table]), sql.Identifier(table)
                )
            )
        name = "erp_backup_index_" + secrets.token_hex(8)
        connection.execute(
            sql.SQL("CREATE {}INDEX {} ON {} {}").format(
                sql.SQL("UNIQUE " if unique else ""),
                sql.Identifier(name),
                sql.Identifier(tables[table]),
                sql.SQL(source_body),
            )
        )
        normalized = connection.execute(
            "SELECT pg_get_indexdef(%s::regclass)", ("pg_temp." + name,)
        ).fetchone()[0]
        connection.execute(sql.SQL("DROP INDEX {}").format(sql.Identifier(name)))
        normalized_body = "USING " + normalized.split(" USING ", 1)[1]
        if normalized_body != restored_body:
            raise CheckFailure("restore_schema_indexes_mismatch")


def snapshot():
    from app.core.config import get_settings

    settings = get_settings()
    config = {}
    for field in type(settings).model_fields:
        value = getattr(settings, field)
        if hasattr(value, "get_secret_value"):
            value = value.get_secret_value()
        config[field] = value
    with psycopg.connect(
        settings.database_url.replace("postgresql+psycopg:", "postgresql:")
    ) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        # A dropped controller cannot retain this transaction indefinitely.
        db.execute("SET LOCAL idle_in_transaction_session_timeout='2h'")
        exported = db.execute("SELECT pg_export_snapshot()").fetchone()[0]
        metadata = database_manifest(db)
        media = [
            dict(
                zip(("storage_key", "preview_storage_key", "sha256", "size_bytes"), row)
            )
            for row in db.execute(
                "SELECT storage_key,preview_storage_key,sha256,size_bytes "
                "FROM evaluation_media_blobs WHERE storage_status='available' ORDER BY storage_key"
            ).fetchall()
        ]
        metadata.update(
            {
                "snapshot": exported,
                "media": media,
                "runtime_config": config,
                "key_canary": cipher_for(config).encrypt(
                    "erp-backup-recovery-v1", field_name="backup"
                ),
                "lookup_canary": hashlib.sha256(
                    ("erp-backup-lookup-v1:" + config["field_lookup_key"]).encode()
                ).hexdigest(),
            }
        )
        print(json.dumps(metadata, default=str), flush=True)
        if sys.stdin.readline().strip() != "release":
            raise CheckFailure("snapshot_controller_closed")
        db.rollback()


def verify():
    payload = json.load(sys.stdin)
    expected = payload["manifest"]
    config = payload["runtime_config"]
    cipher = cipher_for(config)
    try:
        if (
            cipher.decrypt(expected["key_canary"], field_name="backup")
            != "erp-backup-recovery-v1"
        ):
            raise CheckFailure("field_key_canary_failed")
    except ValueError:
        raise CheckFailure("field_key_canary_failed") from None
    if (
        hashlib.sha256(
            ("erp-backup-lookup-v1:" + config["field_lookup_key"]).encode()
        ).hexdigest()
        != expected["lookup_canary"]
    ):
        raise CheckFailure("lookup_key_canary_failed")
    # This DSN is deliberately fixed to the disposable container network namespace.
    with psycopg.connect("postgresql://erp_verify@127.0.0.1:5432/erp_verify") as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        actual = database_manifest(db)
        for key in ("row_counts", "alembic_revision"):
            if json.dumps(actual[key], sort_keys=True) != json.dumps(
                expected[key], sort_keys=True
            ):
                raise CheckFailure("restore_" + key + "_mismatch")
        for key in ("columns", "sequences"):
            if json.dumps(actual["schema"][key], sort_keys=True) != json.dumps(
                expected["schema"][key], sort_keys=True
            ):
                raise CheckFailure("restore_schema_" + key + "_mismatch")
        verify_sequence_state(
            db, payload.get("sequence_state"), actual["schema"]["sequences"],
            actual["schema"]["columns"],
        )
        # Only this disposable restore connection creates temporary parser
        # tables. The production snapshot connection always remains read-only.
        db.rollback()
        verify_constraints(
            db, expected["schema"]["constraints"], actual["schema"]["constraints"]
        )
        verify_indexes(db, expected["schema"]["indexes"], actual["schema"]["indexes"])
        samples = 0
        # Validate actual personal/mail ciphertext with the recovered encryption key.
        columns = [
            ("people", "identity_ciphertext", "identity_number", "identity"),
            ("people", "phone_ciphertext", "phone", "phone"),
            ("people", "email_ciphertext", "email", "email"),
            (
                "mail_delivery_settings",
                "username_ciphertext",
                "mail-settings-username",
                "mail_username",
            ),
            (
                "mail_delivery_settings",
                "password_ciphertext",
                "mail-settings-password",
                "mail_password",
            ),
            (
                "mail_delivery_settings",
                "sender_ciphertext",
                "mail-settings-sender",
                "mail_sender",
            ),
            (
                "mail_delivery_settings",
                "reply_to_ciphertext",
                "mail-settings-reply-to",
                "mail_reply_to",
            ),
        ]
        available = {(row[0], row[1]) for row in actual["schema"]["columns"]}
        for table, column, field, error_field in columns:
            if (table, column) not in available:
                continue
            query = sql.SQL("SELECT {} FROM {} WHERE {} IS NOT NULL LIMIT 5").format(
                sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
            )
            for (ciphertext,) in db.execute(query).fetchall():
                try:
                    cipher.decrypt(ciphertext, field_name=field)
                except ValueError:
                    raise CheckFailure(
                        "restored_" + error_field + "_decryption_failed"
                    ) from None
                samples += 1
    print(
        json.dumps(
            {"table_count": len(actual["row_counts"]), "encrypted_samples": samples}
        )
    )


if __name__ == "__main__":
    try:
        {"snapshot": snapshot, "verify": verify}[sys.argv[1]]()
    except Exception as exc:  # noqa: BLE001 — sanitized helper protocol, never expose recovery secrets
        # Never leak DSNs, ciphertext values, or decrypted identity/mail details.
        code = (
            str(exc)
            if isinstance(exc, CheckFailure) and str(exc) in CHECK_ERRORS
            else "backup_container_check_failed"
        )
        print("ERP_BACKUP_ERROR:" + code, file=sys.stderr)
        raise SystemExit(1) from None
