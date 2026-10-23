"""Opt-in real restore rehearsal; owns a separate Docker database, no shared E2E DB."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import secrets
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("erp_backup_docker", ROOT / "scripts/erp-backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


@pytest.mark.skipif(
    os.environ.get("ERP_BACKUP_DOCKER_TEST") != "1", reason="opt-in Docker rehearsal"
)
@pytest.mark.parametrize("standard_strings", ["on", "off"])
def test_real_database_restore_counts_schema_and_encrypted_fields(tmp_path, monkeypatch, standard_strings):
    api_image = os.environ.get("ERP_BACKUP_TEST_API_IMAGE", "esports-erp-e2e-api:latest")
    postgres_image = "postgres:16-alpine"
    source = "erp-backup-source-test-" + secrets.token_hex(8)
    config = {}
    for name in (
        "field_encryption_master_key",
        "field_lookup_key",
        "session_token_pepper",
        "registration_verification_pepper",
        "registration_session_token_pepper",
        "evaluation_tool_service_token",
        "evaluation_tool_web_service_token",
        "evaluation_tool_worker_service_token",
    ):
        config[name] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    helper = (
        (ROOT / "scripts/erp_backup_container.py")
        .read_text()
        .split('if __name__ == "__main__":')[0]
    )
    producer = (
        helper
        + """
config = json.load(sys.stdin)
with psycopg.connect("postgresql://erp_verify@127.0.0.1:5432/erp_verify") as db:
    db.execute("CREATE EXTENSION btree_gist")
    db.execute("CREATE TABLE alembic_version (version_num varchar(80) PRIMARY KEY)")
    db.execute("INSERT INTO alembic_version VALUES ('fixture_revision')")
    db.execute("CREATE TABLE people (id integer PRIMARY KEY, email_ciphertext text, "
        "state varchar(16) NOT NULL DEFAULT 'active', score integer DEFAULT 5, "
        "starts_at timestamptz DEFAULT '2026-01-01T00:00:00Z', "
        "CONSTRAINT ck_people_state CHECK (state IN ('active','pending')), "
        "CONSTRAINT ck_people_score CHECK ((score >= 1 AND score <= 10) "
        "AND (score <> 3 AND score <> 4)))")
    db.execute("ALTER TABLE people ADD CONSTRAINT ck_people_noinherit "
        "CHECK (score >= 0) NO INHERIT NOT VALID")
    db.execute("ALTER TABLE people ADD CONSTRAINT ex_people_range EXCLUDE USING gist "
        "(id WITH =, tstzrange(starts_at,'infinity'::timestamptz,'[)') WITH &&) "
        "WHERE (state IN ('active','pending')) DEFERRABLE INITIALLY DEFERRED")
    db.execute("CREATE UNIQUE INDEX ix_people_active ON people(email_ciphertext) "
        "WHERE state IN ('active','pending')")
    db.execute("INSERT INTO people(id,email_ciphertext) VALUES (1, %s)",
        (cipher_for(config).encrypt("recovery@example.test", field_name="email"),))
    db.execute("CREATE SEQUENCE admission_order_number_seq")
    db.execute("CREATE SEQUENCE registration_student_number_seq")
    db.execute(sql.SQL("CREATE SEQUENCE {}").format(
        sql.Identifier("student's " + chr(34) + "seq" + chr(34))))
    db.execute(sql.SQL("CREATE SEQUENCE {}").format(sql.Identifier("student" + chr(92) + "seq")))
    db.execute("SELECT setval('admission_order_number_seq', 42, true)")
    db.execute("SELECT setval('registration_student_number_seq', 7, false)")
    db.execute("CREATE TABLE admission_cases (order_number text)")
    db.execute("INSERT INTO admission_cases VALUES ('BM20260914000042')")
    db.execute("CREATE TABLE student_profiles (student_number text)")
    db.execute("INSERT INTO student_profiles VALUES ('REG-202600000006'), ('MANUAL-99999')")
    db.commit()
    metadata = database_manifest(db)
metadata.update({"media": [], "key_canary": cipher_for(config).encrypt(
    "erp-backup-recovery-v1", field_name="backup"), "lookup_canary": hashlib.sha256(
    ("erp-backup-lookup-v1:" + config["field_lookup_key"]).encode()).hexdigest()})
print(json.dumps(metadata))
"""
    )
    created = False
    try:
        backup.run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                source,
                "--network",
                "none",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=erp_verify",
                "-e",
                "POSTGRES_DB=erp_verify",
                postgres_image,
            ]
        )
        created = True
        for _ in range(60):
            try:
                backup.run(
                    [
                        "docker",
                        "exec",
                        source,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "erp_verify",
                        "-d",
                        "erp_verify",
                    ],
                    timeout=5,
                )
                break
            except backup.BackupError:
                time.sleep(1)
        else:
            pytest.fail("disposable source database unavailable")
        metadata = json.loads(
            backup.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--interactive",
                    "--network",
                    "container:" + source,
                    "--entrypoint",
                    "python",
                    api_image,
                    "-c",
                    producer,
                ],
                input=json.dumps(config).encode(),
            )
        )
        backup.run([
            "docker", "exec", source, "psql", "-U", "erp_verify", "-d", "erp_verify",
            "-c", "ALTER DATABASE erp_verify SET standard_conforming_strings = " + standard_strings,
        ])
        payload = tmp_path / "payload"
        payload.mkdir()
        recovery = payload / "recovery"
        recovery.mkdir()
        (recovery / "secrets").mkdir()
        (recovery / "mounted-api-secrets").mkdir()
        for name, value in config.items():
            (recovery / "mounted-api-secrets" / name).write_text(value)
            (recovery / "secrets" / name).write_text(value)
        (recovery / "runtime-config.json").write_text(json.dumps(config))
        (recovery / ".env").write_text("APP_ENV=test\n")
        (recovery / "source.tar.gz").write_bytes(b"source-fixture")
        (recovery / "images.tar").write_bytes(b"images-fixture")
        with (payload / "database.dump").open("wb") as dump:
            backup.run(
                [
                    "docker",
                    "exec",
                    source,
                    "pg_dump",
                    "-U",
                    "erp_verify",
                    "-d",
                    "erp_verify",
                    "--format=custom",
                    "--no-owner",
                ],
                stdout=dump,
            )
        manifest = {
            "files": backup.file_inventory(payload),
            "database": metadata,
            "api_image": api_image,
            "postgres_image": postgres_image,
        }
        result = backup.verify_payload(payload, manifest)
        assert result == {
            "encrypted": True,
            "verified": True,
            "media_files": 0,
            "table_count": 4,
            "encrypted_samples": 1,
        }
        negative_probe = (
            helper
            + """
import copy
expected = json.load(sys.stdin)
rejected = []
with psycopg.connect("postgresql://erp_verify@127.0.0.1:5432/erp_verify") as db:
    actual = database_manifest(db)["schema"]
    for case in ("predicate", "missing_constraint", "no_inherit", "not_valid", "deferrable",
                 "index_predicate", "index_unique", "missing_index"):
        changed = copy.deepcopy(expected["schema"])
        if case.startswith("index_") or case == "missing_index":
            index = next(row for row in changed["indexes"] if row[1] == "ix_people_active")
            if case == "index_predicate":
                index[2] = index[2].replace("'active'", "'different'")
            elif case == "index_unique":
                index[2] = index[2].replace("CREATE UNIQUE INDEX", "CREATE INDEX")
            else:
                changed["indexes"].remove(index)
            checker, category = verify_indexes, "indexes"
        else:
            constraint = next(row for row in changed["constraints"] if row[1] == (
                "ck_people_state" if case == "predicate" else "ex_people_range"
                if case == "deferrable" else "ck_people_noinherit"))
            if case == "predicate":
                constraint[2] = constraint[2].replace("'active'", "'different'")
            elif case == "no_inherit":
                constraint[2] = constraint[2].replace(" NO INHERIT", "")
            elif case == "not_valid":
                constraint[2] = constraint[2].replace(" NOT VALID", "")
            elif case == "deferrable":
                constraint[2] = constraint[2].replace(" DEFERRABLE INITIALLY DEFERRED", "")
            else:
                changed["constraints"].remove(constraint)
            checker, category = verify_constraints, "constraints"
        db.rollback()
        try:
            checker(db, changed[category], actual[category])
        except CheckFailure as exc:
            assert str(exc) == "restore_schema_" + category + "_mismatch"
            rejected.append(case)
        else:
            raise RuntimeError("changed schema was accepted")
print(json.dumps({"rejected": rejected}))
"""
        )
        probe = json.loads(
            backup.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--interactive",
                    "--network",
                    "container:" + source,
                    "--entrypoint",
                    "python",
                    api_image,
                    "-c",
                    negative_probe,
                ],
                input=json.dumps(metadata).encode(),
            )
        )
        assert len(probe["rejected"]) == 8
        # Read real archive evidence; advancing source sequences after dumping
        # cannot invalidate a valid backup, since sequences are not MVCC.
        evidence = backup.dump_sequence_state(payload, source)
        assert ["public.admission_order_number_seq", 42, True] in evidence
        assert ["public.registration_student_number_seq", 7, False] in evidence
        sequence_probe = helper + """
payload = json.load(sys.stdin)
evidence = payload["evidence"]
rejected = []
with psycopg.connect("postgresql://erp_verify@127.0.0.1:5432/erp_verify") as db:
    schema = database_manifest(db)["schema"]
    def check(values):
        verify_sequence_state(db, values, schema["sequences"], schema["columns"])
    check(evidence)
    for value, called in ((1, False), (41, True), (42, False)):
        db.execute("SELECT setval('admission_order_number_seq', %s, %s)", (value, called))
        try:
            check(evidence)
        except CheckFailure as exc:
            assert str(exc) == "restore_sequence_state_mismatch"
            rejected.append("state")
        else:
            raise AssertionError("sequence rollback was accepted")
    db.execute("SELECT setval('admission_order_number_seq', 42, true)")
    for name, value in (("admission_order_number_seq", 41),
                        ("registration_student_number_seq", 6)):
        modified = [list(row) for row in evidence]
        row = next(row for row in modified if row[0] == "public." + name)
        row[1:] = [value, False]
        db.execute("SELECT setval(%s::regclass, %s, false)", (name, value))
        try:
            check(modified)
        except CheckFailure as exc:
            assert str(exc) == "restore_sequence_next_identifier_unsafe"
            rejected.append("unsafe_next")
        else:
            raise AssertionError("colliding identifier was accepted")
        original = next(row for row in evidence if row[0] == "public." + name)
        db.execute("SELECT setval(%s::regclass, %s, %s)", (name, original[1], original[2]))
    db.execute("SELECT setval('admission_order_number_seq', 100, true)")
    db.commit()
print(json.dumps({"rejected": rejected}))
"""
        sequence_result = json.loads(backup.run(
            ["docker", "run", "--rm", "--interactive", "--network", "container:" + source,
             "--entrypoint", "python", api_image, "-c", sequence_probe],
            input=json.dumps({"evidence": evidence}).encode(),
        ))
        assert sequence_result["rejected"] == ["state"] * 3 + ["unsafe_next"] * 2
        assert backup.verify_payload(payload, manifest)["verified"]
        original_run = backup.run

        def lose_restored_sequence_position(command, **kwargs):
            output = original_run(command, **kwargs)
            if "pg_restore" in command and "--list" in command:
                isolated = command[3]
                assert isolated.startswith("erp-backup-verify-")
                original_run([
                    "docker", "exec", isolated, "psql", "-U", "erp_verify", "-d", "erp_verify",
                    "-c", "SELECT setval('admission_order_number_seq', 1, false)",
                ])
            return output

        with monkeypatch.context() as patch:
            patch.setattr(backup, "run", lose_restored_sequence_position)
            with pytest.raises(backup.BackupError, match="restore_sequence_state_mismatch"):
                backup.verify_payload(payload, manifest)
        # A mismatched expected snapshot count must fail the real restore drill.
        metadata["row_counts"]["people"] = 99
        with pytest.raises(backup.BackupError, match="restore_row_counts_mismatch"):
            backup.verify_payload(payload, manifest)
    finally:
        if created:
            backup.run(["docker", "rm", "--force", "--volumes", source])
