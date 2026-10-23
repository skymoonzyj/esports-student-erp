"""Test-only historical void restore probe; importing never connects to a DB.

The controller injects this program into an immutable API image joined only to
the exact newly owned PostgreSQL network namespace. No ambient DSN is accepted.
"""

import base64
import csv
import hashlib
import io
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

HISTORY_REVISION = "0070_housing_history_void"
HISTORY_DSN = "postgresql+psycopg://erp_verify@127.0.0.1:5432/erp_verify"
HISTORY_REASON = "Synthetic duplicate historical import"
HISTORY_NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def history_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def history_sha256(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def verify_api_source(expected, root=Path("/app")):
    """Every production API/migration byte must match before any DB action."""
    if not expected:
        raise ValueError("api_image_source_mismatch")
    for name, digest in expected.items():
        path = root / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
            or history_sha256(path.read_bytes()) != digest
        ):
            raise ValueError("api_image_source_mismatch")


def validate_history_void_case(observed, expected) -> dict[str, bool]:
    """Fail closed with assertion labels only; never put restored values in errors."""
    checks = {
        "seed_proof_coherent": history_proof_coherent(expected)
        and history_proof_coherent(observed),
        "same_person": observed.get("person_sha256") == expected["person_sha256"],
        "immutability_trigger_functions_preserved": (
            observed.get("immutability_triggers_sha256") == expected["immutability_triggers_sha256"]
        ),
        "same_accounts": (
            observed.get("accounts_sha256") == expected["accounts_sha256"]
            and observed.get("account_count") == expected["account_count"] == 1
        ),
        "voided_source_preserved": observed.get("source") == expected["source"],
        "current_target_unchanged": observed.get("target") == expected["target"],
        "transfer_fk_preserved": observed.get("transfer") == expected["transfer"],
        "one_operation_preserved": (
            observed.get("operations") == expected["operations"]
            and len(observed.get("operations", [])) == 1
        ),
        "one_audit_preserved": (
            observed.get("audits") == expected["audits"] and len(observed.get("audits", [])) == 1
        ),
        "old_report_ciphertext_bytes_and_parsed_download_preserved": (
            observed.get("report") == expected["report"]
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AssertionError("history_void_verification_failed:" + ",".join(failed))
    return checks


def history_proof_coherent(case):
    """A pre-backup snapshot cannot bless missing or incorrectly linked evidence."""
    source, target, transfer = (case.get(key) or {} for key in ("source", "target", "transfer"))
    operations, audits = case.get("operations", []), case.get("audits", [])
    if len(operations) != 1 or len(audits) != 1:
        return False
    operation, audit = operations[0], audits[0]
    metadata = audit.get("metadata", {})
    return bool(
        source.get("id")
        and target.get("id")
        and source["id"] != target["id"]
        and source.get("status") == "checked_out"
        and source.get("voided_at")
        and source.get("voided_by_user_id")
        and source.get("version") == 2
        and source.get("reason_ciphertext_sha256")
        and source.get("reason_plaintext_sha256")
        and target.get("status") == "checked_in"
        and target.get("voided_at") is None
        and source.get("person_id") == target.get("person_id") == transfer.get("person_id")
        and transfer.get("from_stay_id") == source["id"]
        and transfer.get("to_stay_id") == target["id"]
        and operation.get("stay_id") == source["id"]
        and operation.get("actor_account_id") == source["voided_by_user_id"]
        and operation.get("result_version") == source["version"]
        and operation.get("created_at") == source["voided_at"]
        and audit.get("resource_id") == source["id"]
        and audit.get("actor_user_id") == source["voided_by_user_id"]
        and audit.get("action") == "housing.stay.void_history"
        and audit.get("outcome") == "allowed"
        and metadata
        == {
            "operation_id": operation.get("id"),
            "room_id": source.get("room_id"),
            "person_id": source.get("person_id"),
            "old_version": 1,
            "new_version": 2,
        }
        and sorted(case.get("report", {}).get("stay_ids", []))
        == sorted([source["id"], target["id"]])
    )


def history_keys():
    from app.security.encryption import EnvelopeCipher, LookupDigest

    return (
        EnvelopeCipher(base64.urlsafe_b64decode(os.environ["FIELD_ENCRYPTION_MASTER_KEY"])),
        LookupDigest(base64.urlsafe_b64decode(os.environ["FIELD_LOOKUP_KEY"])),
    )


def history_person_values(index, cipher, lookup):
    """Synthetic checksum-valid documents; no real people or secret fixtures."""
    birth = date(1988, 1, 2)
    stem = "110101" + birth.strftime("%Y%m%d") + f"{index * 2 + 1:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    identity = stem + "10X98765432"[sum(int(n) * w for n, w in zip(stem, weights)) % 11]
    phone, email = f"13900000{index:03d}", f"history-restore-{index}@example.com"
    return {
        "id": uuid4(),
        "full_name": f"Synthetic history restore {index}",
        "sex": "male",
        "date_of_birth": birth,
        "identity_ciphertext": cipher.encrypt(identity, field_name="identity_number"),
        "identity_digest": lookup.digest(identity),
        "phone_ciphertext": cipher.encrypt(phone, field_name="phone"),
        "phone_digest": lookup.digest(phone),
        "email_ciphertext": cipher.encrypt(email, field_name="email"),
        "email_digest": lookup.digest_domain("person-email", email),
    }


def history_actor(db, account_id):
    from app.auth.models import AuthSession, UserAccount
    from app.auth.service import AuthContext
    from app.identity.models import Person

    account = db.get(UserAccount, UUID(account_id))
    return AuthContext(
        account=account, person=db.get(Person, account.person_id), session=AuthSession()
    )


def history_reporting_service(db, actor_id):
    from app.housing.reporting_service import HousingReportingService

    cipher, lookup = history_keys()
    return HousingReportingService(
        db, cipher, UUID(actor_id), HISTORY_NOW, "synthetic-history-restore", lookup=lookup
    )


def history_report_command(floor_id):
    from app.housing.reporting_service import GenerateReportCommand

    return GenerateReportCommand(
        range_start=HISTORY_NOW - timedelta(days=10),
        range_end=HISTORY_NOW,
        scope_mode="floors",
        floor_ids=(UUID(floor_id),),
        purpose="Synthetic restore report",
    )


def history_parse_download(raw):
    """Read actual public CSV ZIP output, not a second projection rendering."""
    with ZipFile(io.BytesIO(raw)) as archive:
        tables = {
            name: list(csv.reader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
            for name in sorted(archive.namelist())
        }
    stay_ids = []
    for rows in tables.values():
        if rows and "住宿记录ID" in rows[0]:
            column = rows[0].index("住宿记录ID")
            stay_ids.extend(row[column] for row in rows[1:])
    return {"parsed_sha256": history_fingerprint(tables), "stay_ids": sorted(stay_ids)}


def history_row(db, table, identifier):
    from sqlalchemy import text

    # Table is a controller-owned literal, never user input.
    return db.execute(
        text(f"SELECT to_jsonb(t) FROM {table} t WHERE id=:id"), {"id": UUID(identifier)}
    ).scalar_one_or_none()


def history_source_original(row):
    """Immutable business fields across the real pre-void -> post-void write.

    The ORM legitimately advances updated_at with the marker/version update.
    Restore/replay comparisons use the complete post-void fingerprint separately.
    """
    return history_fingerprint(
        {
            key: value
            for key, value in row.items()
            if key
            not in {
                "voided_at",
                "voided_by_user_id",
                "void_reason_ciphertext",
                "version",
                "updated_at",
            }
        }
    )


def history_trigger_fingerprint(db):
    from sqlalchemy import text

    definitions = db.execute(
        text("""
        SELECT t.tgname, pg_get_triggerdef(t.oid), pg_get_functiondef(t.tgfoid)
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal
        AND c.relname IN ('housing_stays','housing_stay_void_operations')
        ORDER BY c.relname,t.tgname
    """)
    ).fetchall()
    if not {
        "trg_housing_stays_void_fact_immutable",
        "trg_housing_stay_void_operations_immutable",
    } <= {row[0] for row in definitions}:
        raise AssertionError(
            "history_void_verification_failed:required_immutability_triggers_missing"
        )
    return history_fingerprint([list(row) for row in definitions])


def history_source_fact(source, cipher):
    """Produce the same source fact for both real DB readback and pure probes."""
    reason = cipher.decrypt(source["void_reason_ciphertext"], field_name="housing_stay_void_reason")
    return {
        **{
            key: source[key]
            for key in (
                "id",
                "person_id",
                "room_id",
                "status",
                "voided_at",
                "voided_by_user_id",
                "version",
            )
        },
        "reason_ciphertext_sha256": history_sha256(source["void_reason_ciphertext"]),
        "reason_plaintext_sha256": history_sha256(reason),
        "original_fields_sha256": history_source_original(source),
        # Full post-void readback, including updated_at, is immutable across
        # backup/restore and idempotent replay; do not reuse the business subset.
        "row_sha256": history_fingerprint(source),
    }


def collect_history_void_case(engine, identifiers):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    cipher, _ = history_keys()
    with Session(engine) as db:
        person = history_row(db, "people", identifiers["person"])
        accounts = (
            db.execute(
                text("SELECT to_jsonb(a) FROM user_accounts a WHERE person_id=:id ORDER BY id"),
                {"id": UUID(identifiers["person"])},
            )
            .scalars()
            .all()
        )
        source = history_row(db, "housing_stays", identifiers["source"])
        target = history_row(db, "housing_stays", identifiers["target"])
        transfer = history_row(db, "housing_transfers", identifiers["transfer"])
        operations = (
            db.execute(
                text(
                    "SELECT to_jsonb(o) FROM housing_stay_void_operations o "
                    "WHERE stay_id=:id ORDER BY id"
                ),
                {"id": UUID(identifiers["source"])},
            )
            .scalars()
            .all()
        )
        audits = (
            db.execute(
                text(
                    "SELECT to_jsonb(a) FROM audit_events a WHERE resource_id=:id "
                    "AND action='housing.stay.void_history' ORDER BY id"
                ),
                {"id": UUID(identifiers["source"])},
            )
            .scalars()
            .all()
        )
        report = history_row(db, "housing_report_batches", identifiers["report"])
        result = {
            "person_sha256": history_fingerprint(person),
            "immutability_triggers_sha256": history_trigger_fingerprint(db),
            "accounts_sha256": history_fingerprint(accounts),
            "account_count": len(accounts),
            "source": history_source_fact(source, cipher),
            "target": {
                **{
                    key: target[key]
                    for key in ("id", "person_id", "room_id", "status", "voided_at")
                },
                "row_sha256": history_fingerprint(target),
            },
            "transfer": {
                **{key: transfer[key] for key in ("id", "person_id", "from_stay_id", "to_stay_id")},
                "row_sha256": history_fingerprint(transfer),
            },
            "operations": operations,
            "audits": audits,
            "report": {
                "id": report["id"],
                "snapshot_ciphertext_sha256": history_sha256(report["snapshot_ciphertext"]),
                "snapshot_digest": report["snapshot_digest"],
            },
        }
    with Session(engine) as db:
        download = (
            history_reporting_service(db, identifiers["actor"])
            .download(UUID(identifiers["report"]), "csv")
            .content
        )
    result["report"].update(
        download_sha256=history_sha256(download), **history_parse_download(download)
    )
    return result


def history_void_command(identifiers):
    from app.housing.history_void_schemas import StayHistoryVoidRequest

    return StayHistoryVoidRequest(
        expected_version=1,
        reason=HISTORY_REASON,
        confirmed=True,
        idempotency_key=UUID(identifiers["idempotency_key"]),
    )


def seed_history_void_case() -> dict[str, object]:
    """Migrate a fresh isolated DB, seed fictional data, then use the real void service."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.orm import Session

    from app.auth.models import AccountLoginIdentifier, UserAccount
    from app.housing.history_void_service import HousingHistoryVoidService
    from app.housing.models import (
        HousingBuilding,
        HousingFloor,
        HousingRoom,
        HousingStay,
        HousingTransfer,
    )
    from app.identity.models import Person, StaffProfile
    from app.rbac.models import Role, UserRole
    from app.rbac.seed import seed_roles_and_permissions

    cipher, lookup = history_keys()
    migration = Config("/app/alembic.ini")
    migration.attributes["database_url"] = HISTORY_DSN
    command.upgrade(migration, HISTORY_REVISION)
    engine = create_engine(HISTORY_DSN, hide_parameters=True)
    identifiers = {
        name: str(uuid4())
        for name in ("actor", "person", "source", "target", "transfer", "idempotency_key", "floor")
    }
    try:
        with Session(engine) as db, db.begin():
            seed_roles_and_permissions(db)
            actor_person = Person(**history_person_values(71, cipher, lookup))
            resident_values = history_person_values(72, cipher, lookup)
            resident_values["id"] = UUID(identifiers["person"])
            resident = Person(**resident_values)
            db.add_all([actor_person, resident])
            db.flush()
            accounts = []
            for person, account_id in (
                (actor_person, UUID(identifiers["actor"])),
                (resident, uuid4()),
            ):
                account = UserAccount(
                    id=account_id,
                    person_id=person.id,
                    login_phone_digest=person.phone_digest,
                    password_hash="synthetic-unusable-hash",
                    status="active",
                    force_password_change=False,
                    failed_login_count=0,
                )
                accounts.append(account)
                db.add_all([account, StaffProfile(person_id=person.id, staff_type="other")])
            db.flush()
            for person, account in zip((actor_person, resident), accounts):
                phone = cipher.decrypt(person.phone_ciphertext, field_name="phone")
                db.add(
                    AccountLoginIdentifier(
                        account_id=account.id,
                        kind="phone",
                        identifier_digest=lookup.digest_domain("account-login-phone", phone),
                        verified_at=HISTORY_NOW,
                        verification_source="legacy_phone_login",
                        is_primary=True,
                    )
                )
            role = db.scalar(select(Role.id).where(Role.key == "super_admin"))
            db.add(UserRole(user_id=UUID(identifiers["actor"]), role_id=role))
            building = HousingBuilding(
                id=uuid4(), code="RST70", name="Synthetic restore building", is_active=True
            )
            db.add(building)
            db.flush()
            floor = HousingFloor(
                id=UUID(identifiers["floor"]),
                building_id=building.id,
                number=1,
                name="Synthetic restore floor",
                is_active=True,
            )
            db.add(floor)
            db.flush()
            rooms = [
                HousingRoom(
                    id=uuid4(),
                    building_id=building.id,
                    floor_id=floor.id,
                    room_number=number,
                    normalized_key=number,
                    room_type="custom",
                    capacity=2,
                    service_status="available",
                    verification_status="verified",
                    version=1,
                )
                for number in ("RST-101", "RST-102")
            ]
            db.add_all(rooms)
            db.flush()
            for name, room, start in zip(("source", "target"), rooms, (8, 2)):
                db.add(
                    HousingStay(
                        id=UUID(identifiers[name]),
                        person_id=resident.id,
                        room_id=room.id,
                        stay_purpose="permanent",
                        duration_mode="long_term",
                        planned_check_in_at=HISTORY_NOW - timedelta(days=start),
                        actual_check_in_at=HISTORY_NOW - timedelta(days=start),
                        actual_check_out_at=HISTORY_NOW - timedelta(days=2)
                        if name == "source"
                        else None,
                        status="checked_out" if name == "source" else "checked_in",
                        room_name_snapshot=room.room_number,
                        floor_name_snapshot=floor.name,
                        building_name_snapshot=building.name,
                        created_by_user_id=UUID(identifiers["actor"]),
                        checked_in_by_user_id=UUID(identifiers["actor"]),
                        checked_out_by_user_id=UUID(identifiers["actor"])
                        if name == "source"
                        else None,
                        version=1,
                    )
                )
            db.flush()
            db.add(
                HousingTransfer(
                    id=UUID(identifiers["transfer"]),
                    person_id=resident.id,
                    from_stay_id=UUID(identifiers["source"]),
                    to_stay_id=UUID(identifiers["target"]),
                    transferred_at=HISTORY_NOW - timedelta(days=2),
                    reason="Synthetic transfer",
                    transferred_by_user_id=UUID(identifiers["actor"]),
                )
            )
        with Session(engine) as db:
            batch = history_reporting_service(db, identifiers["actor"]).generate(
                history_report_command(identifiers["floor"])
            )
            identifiers["report"] = str(batch.id)
        with Session(engine) as db:
            pre_source = history_source_original(
                history_row(db, "housing_stays", identifiers["source"])
            )
            pre_person = history_fingerprint(history_row(db, "people", identifiers["person"]))
            pre_accounts = history_fingerprint(
                db.execute(
                    text("SELECT to_jsonb(a) FROM user_accounts a WHERE person_id=:id ORDER BY id"),
                    {"id": UUID(identifiers["person"])},
                )
                .scalars()
                .all()
            )
            pre_target = history_fingerprint(
                history_row(db, "housing_stays", identifiers["target"])
            )
            pre_transfer = history_fingerprint(
                history_row(db, "housing_transfers", identifiers["transfer"])
            )
            pre_report = history_row(db, "housing_report_batches", identifiers["report"])
        with Session(engine) as db:
            old_download = (
                history_reporting_service(db, identifiers["actor"])
                .download(batch.id, "csv")
                .content
            )
        old_parsed = history_parse_download(old_download)
        assert old_parsed["stay_ids"] == sorted([identifiers["source"], identifiers["target"]]), (
            "seed_old_report_incomplete"
        )
        with Session(engine) as db:
            service = HousingHistoryVoidService(
                db,
                cipher,
                lookup,
                occurred_at=HISTORY_NOW,
                request_id="synthetic-void-before-backup",
            )
            first = service.void(
                history_actor(db, identifiers["actor"]),
                UUID(identifiers["source"]),
                history_void_command(identifiers),
            )
            db.commit()
            identifiers["operation"] = str(first.operation_id)
        expected = collect_history_void_case(engine, identifiers)
        assert expected["person_sha256"] == pre_person, "seed_person_changed"
        assert expected["accounts_sha256"] == pre_accounts, "seed_accounts_changed"
        assert expected["source"]["original_fields_sha256"] == pre_source, "seed_source_rewritten"
        assert expected["target"]["row_sha256"] == pre_target, "seed_current_target_changed"
        assert expected["transfer"]["row_sha256"] == pre_transfer, "seed_transfer_changed"
        assert expected["source"]["reason_plaintext_sha256"] == history_sha256(HISTORY_REASON), (
            "seed_reason_changed"
        )
        assert expected["report"] == {
            "id": identifiers["report"],
            "snapshot_ciphertext_sha256": history_sha256(pre_report["snapshot_ciphertext"]),
            "snapshot_digest": pre_report["snapshot_digest"],
            "download_sha256": history_sha256(old_download),
            **old_parsed,
        }, "seed_old_report_rewritten"
        assert len(expected["operations"]) == len(expected["audits"]) == 1, "seed_void_not_once"
        assert expected["source"]["version"] == 2 and expected["source"]["voided_at"], (
            "seed_marker_missing"
        )
        validate_history_void_case(expected, expected)
        return {"identifiers": identifiers, "expected": expected}
    finally:
        engine.dispose()


def verify_history_void_case(expected: dict[str, object]) -> dict[str, bool]:
    """Verify restore first, replay exact real command, then generate a new report."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.housing.history_void_service import HousingHistoryVoidService

    engine = create_engine(HISTORY_DSN, hide_parameters=True)
    identifiers = expected["identifiers"]
    try:
        restored = collect_history_void_case(engine, identifiers)
        checks = validate_history_void_case(restored, expected["expected"])
        cipher, lookup = history_keys()
        with Session(engine) as db:
            result = HousingHistoryVoidService(
                db,
                cipher,
                lookup,
                occurred_at=HISTORY_NOW + timedelta(hours=1),
                request_id="synthetic-void-after-restore-replay",
            ).void(
                history_actor(db, identifiers["actor"]),
                UUID(identifiers["source"]),
                history_void_command(identifiers),
            )
            db.commit()
        checks["idempotent_replay_same_operation"] = (
            str(result.operation_id) == identifiers["operation"] and result.version == 2
        )
        validate_history_void_case(
            collect_history_void_case(engine, identifiers), expected["expected"]
        )
        checks["idempotent_replay_one_audit_one_operation_no_changed_facts"] = True
        with Session(engine) as db:
            new_batch = history_reporting_service(db, identifiers["actor"]).generate(
                history_report_command(identifiers["floor"])
            )
        with Session(engine) as db:
            new_download = (
                history_reporting_service(db, identifiers["actor"])
                .download(new_batch.id, "csv")
                .content
            )
        parsed = history_parse_download(new_download)
        checks["new_report_excludes_void_source_retains_current_target"] = parsed["stay_ids"] == [
            identifiers["target"]
        ]
        validate_history_void_case(
            collect_history_void_case(engine, identifiers), expected["expected"]
        )
        checks["new_report_does_not_rewrite_old_snapshot"] = True
        if not all(checks.values()):
            raise AssertionError(
                "history_void_verification_failed:"
                + ",".join(name for name, value in checks.items() if not value)
            )
        return checks
    finally:
        engine.dispose()
