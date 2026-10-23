"""Pure fail-closed checks and opt-in, exactly-owned 0070 encrypted recovery."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def probe():
    return load_module("housing_history_void_restore_probe.py", "history_void_probe")


def ownership_reference():
    # Reuse the mature exact-ID ledger/cleanup implementation without changing
    # or executing its historical 0069 rehearsal or shared DB fixtures.
    return load_module("test_assisted_personnel_backup_docker.py", "history_owned_reference")


@pytest.fixture
def intact_case():
    """Hand-derived readback; changes to any persisted proof must be rejected."""
    return {
        "person_sha256": "a" * 64,
        "accounts_sha256": "b" * 64,
        "account_count": 1,
        "immutability_triggers_sha256": "7" * 64,
        "source": {
            "id": "source",
            "person_id": "person",
            "room_id": "original-room",
            "status": "checked_out",
            "voided_at": "2026-10-21T12:00:00+00:00",
            "voided_by_user_id": "actor",
            "version": 2,
            "reason_ciphertext_sha256": "c" * 64,
            "reason_plaintext_sha256": "d" * 64,
            "original_fields_sha256": "e" * 64,
            "row_sha256": "8" * 64,
        },
        "target": {
            "id": "target",
            "person_id": "person",
            "room_id": "target-room",
            "status": "checked_in",
            "voided_at": None,
            "row_sha256": "f" * 64,
        },
        "transfer": {
            "id": "transfer",
            "person_id": "person",
            "from_stay_id": "source",
            "to_stay_id": "target",
            "row_sha256": "1" * 64,
        },
        "operations": [
            {
                "id": "operation",
                "actor_account_id": "actor",
                "idempotency_key": "key",
                "stay_id": "source",
                "result_version": 2,
                "request_fingerprint": "2" * 64,
                "created_at": "2026-10-21T12:00:00+00:00",
            }
        ],
        "audits": [
            {
                "id": "audit",
                "actor_user_id": "actor",
                "resource_id": "source",
                "action": "housing.stay.void_history",
                "outcome": "allowed",
                "metadata": {
                    "operation_id": "operation",
                    "room_id": "original-room",
                    "person_id": "person",
                    "old_version": 1,
                    "new_version": 2,
                },
            }
        ],
        "report": {
            "id": "old-report",
            "snapshot_ciphertext_sha256": "3" * 64,
            "snapshot_digest": "4" * 64,
            "download_sha256": "5" * 64,
            "parsed_sha256": "6" * 64,
            "stay_ids": ["source", "target"],
        },
    }


def test_restore_probe_accepts_complete_original_void_fact(intact_case):
    result = probe().validate_history_void_case(copy.deepcopy(intact_case), intact_case)
    assert result and all(value is True for value in result.values())


@pytest.fixture
def original_source_row():
    return {
        "id": "source",
        "person_id": "person",
        "room_id": "original-room",
        "status": "checked_out",
        "created_at": "2026-10-12T12:00:00+00:00",
        "updated_at": "2026-10-12T12:00:00+00:00",
        "planned_check_in_at": "2026-10-13T12:00:00+00:00",
        "actual_check_in_at": "2026-10-13T12:00:00+00:00",
        "actual_check_out_at": "2026-10-19T12:00:00+00:00",
        "voided_at": None,
        "voided_by_user_id": None,
        "void_reason_ciphertext": None,
        "version": 1,
    }


def test_source_business_fingerprint_allows_only_void_metadata_transition(original_source_row):
    after = copy.deepcopy(original_source_row)
    after.update(
        updated_at="2026-10-21T12:00:00+00:00",
        voided_at="2026-10-21T12:00:00+00:00",
        voided_by_user_id="actor",
        void_reason_ciphertext="synthetic-envelope",
        version=2,
    )
    module = probe()
    assert module.history_source_original(original_source_row) == module.history_source_original(
        after
    )


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "person_id",
        "room_id",
        "status",
        "created_at",
        "planned_check_in_at",
        "actual_check_in_at",
        "actual_check_out_at",
    ],
)
def test_source_business_fingerprint_rejects_original_field_changes(original_source_row, field):
    after = copy.deepcopy(original_source_row)
    after[field] = "changed-business-value"
    module = probe()
    assert module.history_source_original(original_source_row) != module.history_source_original(
        after
    )


@pytest.fixture
def post_void_source(original_source_row):
    from app.security.encryption import EnvelopeCipher

    cipher = EnvelopeCipher(b"p" * 32)
    row = copy.deepcopy(original_source_row)
    row.update(
        updated_at="2026-10-21T12:00:00+00:00",
        voided_at="2026-10-21T12:00:00+00:00",
        voided_by_user_id="actor",
        version=2,
        void_reason_ciphertext=cipher.encrypt(
            "Synthetic void reason", field_name="housing_stay_void_reason"
        ),
    )
    return row, cipher


def test_source_readback_binds_complete_post_void_row(post_void_source):
    row, cipher = post_void_source
    fact = probe().history_source_fact(row, cipher)
    assert (
        fact.get("row_sha256")
        == hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    )


@pytest.mark.parametrize(
    "stage_timestamp",
    ["2026-10-21T12:01:00+00:00", "2026-10-21T13:00:00+00:00"],
    ids=["restore", "idempotent-replay"],
)
def test_restored_or_replayed_source_timestamp_drift_is_rejected(
    intact_case, post_void_source, stage_timestamp
):
    row, cipher = post_void_source
    module = probe()
    intact_case["source"] = module.history_source_fact(row, cipher)
    observed = copy.deepcopy(intact_case)
    changed = copy.deepcopy(row)
    changed["updated_at"] = stage_timestamp
    observed["source"] = module.history_source_fact(changed, cipher)
    with pytest.raises(AssertionError, match="history_void_verification_failed"):
        module.validate_history_void_case(observed, intact_case)


@pytest.mark.parametrize(
    "mutation",
    [
        "marker",
        "reason_ciphertext",
        "reason_plaintext",
        "operation",
        "audit",
        "duplicate_operation",
        "duplicate_audit",
        "operation_target",
        "operation_fingerprint",
        "audit_target",
        "audit_operation",
        "source_room",
        "source_original_fields",
        "target",
        "target_person",
        "target_status",
        "transfer_source",
        "transfer_target",
        "transfer_person",
        "person",
        "account",
        "account_missing",
        "report_ciphertext",
        "report_digest",
        "report_download",
        "report_parsed",
        "report_stays",
    ],
)
def test_restore_probe_rejects_missing_or_tampered_void_fact(intact_case, mutation):
    original = copy.deepcopy(intact_case)
    observed = copy.deepcopy(original)
    if mutation == "marker":
        observed["source"]["voided_at"] = None
    elif mutation in {"reason_ciphertext", "reason_plaintext"}:
        observed["source"][mutation + "_sha256"] = None
    elif mutation in {"operation", "audit"}:
        observed[mutation + "s"] = []
    elif mutation in {"duplicate_operation", "duplicate_audit"}:
        name = mutation.removeprefix("duplicate_") + "s"
        observed[name].append(copy.deepcopy(observed[name][0]))
    elif mutation == "operation_target":
        observed["operations"][0]["stay_id"] = "target"
    elif mutation == "operation_fingerprint":
        observed["operations"][0]["request_fingerprint"] = "9" * 64
    elif mutation == "audit_target":
        observed["audits"][0]["resource_id"] = "target"
    elif mutation == "audit_operation":
        observed["audits"][0]["metadata"]["operation_id"] = "replacement"
    elif mutation == "source_room":
        observed["source"]["room_id"] = "target-room"
    elif mutation == "source_original_fields":
        observed["source"]["original_fields_sha256"] = "9" * 64
    elif mutation == "target":
        observed["target"]["id"] = "replacement"
    elif mutation == "target_person":
        observed["target"]["person_id"] = "replacement"
    elif mutation == "target_status":
        observed["target"]["status"] = "checked_out"
    elif mutation.startswith("transfer_"):
        name = {"source": "from_stay_id", "target": "to_stay_id", "person": "person_id"}[
            mutation.removeprefix("transfer_")
        ]
        observed["transfer"][name] = "replacement"
    elif mutation == "person":
        observed["person_sha256"] = "9" * 64
    elif mutation == "account":
        observed["accounts_sha256"] = "9" * 64
    elif mutation == "account_missing":
        observed["account_count"] = 0
    else:
        name = {
            "report_ciphertext": "snapshot_ciphertext_sha256",
            "report_digest": "snapshot_digest",
            "report_download": "download_sha256",
            "report_parsed": "parsed_sha256",
            "report_stays": "stay_ids",
        }[mutation]
        observed["report"][name] = [] if name == "stay_ids" else "9" * 64
    with pytest.raises(AssertionError, match="history_void_verification_failed"):
        probe().validate_history_void_case(observed, original)


def test_restore_failure_does_not_disclose_fact_values(intact_case):
    observed = copy.deepcopy(intact_case)
    observed["source"]["reason_plaintext_sha256"] = "NEVER_OUTPUT_PRIVATE_VALUE"
    with pytest.raises(AssertionError) as failure:
        probe().validate_history_void_case(observed, intact_case)
    assert "NEVER_OUTPUT_PRIVATE_VALUE" not in str(failure.value)


def test_restore_probe_rejects_missing_or_changed_immutability_triggers(intact_case):
    observed = copy.deepcopy(intact_case)
    observed["immutability_triggers_sha256"] = "8" * 64
    with pytest.raises(AssertionError, match="history_void_verification_failed"):
        probe().validate_history_void_case(observed, intact_case)


@pytest.mark.parametrize(
    "missing", ["source", "target", "transfer", "operations", "audits", "report"]
)
def test_restore_probe_rejects_missing_top_level_evidence(intact_case, missing):
    observed = copy.deepcopy(intact_case)
    observed.pop(missing)
    with pytest.raises(AssertionError, match="history_void_verification_failed"):
        probe().validate_history_void_case(observed, intact_case)


@pytest.mark.parametrize("missing", ["marker", "operation", "audit", "source_target_distinct"])
def test_restore_probe_cannot_bless_incomplete_expected_snapshot(intact_case, missing):
    if missing == "marker":
        intact_case["source"]["voided_at"] = None
    elif missing in {"operation", "audit"}:
        intact_case[missing + "s"] = []
    else:
        intact_case["target"]["id"] = "source"
    with pytest.raises(AssertionError, match="history_void_verification_failed"):
        probe().validate_history_void_case(copy.deepcopy(intact_case), intact_case)


def write_history_source_archive(root, target):
    """Archive this complete source snapshot, bind only production image bytes."""
    api = root / "apps/api"
    sources = sorted(
        {api / "alembic.ini", api / "pyproject.toml"}
        | set((api / "app").rglob("*.py"))
        | set((api / "migrations").rglob("*.py"))
    )
    required = {
        "migrations/versions/0070_housing_history_void.py",
        "app/housing/history_void_models.py",
        "app/housing/history_void_service.py",
        "app/housing/history_void_access.py",
        "app/housing/history_void_schemas.py",
        "app/housing/history_void_router.py",
        "app/housing/history_validity.py",
        "app/housing/reporting_service.py",
        "app/housing/reporting_projection.py",
    }
    if not required <= {path.relative_to(api).as_posix() for path in sources}:
        raise ValueError("history_recovery_source_incomplete")
    archived_only = [
        root / "scripts/erp_backup_container.py",
        root / "scripts/erp-backup.py",
        api / "Dockerfile",
        api / "tests/cli/test_housing_history_void_backup_docker.py",
        api / "tests/cli/housing_history_void_restore_probe.py",
        api / "tests/cli/test_assisted_personnel_backup_docker.py",
        api / "tests/cli/assisted_restore_probe.py",
    ]
    inventory = {}
    production_cli = {
        "app/cli/__init__.py",
        "app/cli/create_admin.py",
        "app/cli/run_teaching_scheduler.py",
    }
    with tarfile.open(target, "x:gz") as archive:
        for path in [*archived_only, *sources]:
            if path.is_symlink() or not path.is_file() or path.resolve() != path.absolute():
                raise ValueError("history_recovery_source_invalid")
            raw = path.read_bytes()
            member = tarfile.TarInfo(path.relative_to(root).as_posix())
            member.size, member.mode = len(raw), 0o600
            archive.addfile(member, io.BytesIO(raw))
            if path in sources:
                relative = path.relative_to(api).as_posix()
                if not relative.startswith("app/cli/") or relative in production_cli:
                    inventory[relative] = hashlib.sha256(raw).hexdigest()
    return inventory


def history_programs():
    """Build test programs without evaluating imports, opening DBs or invoking Docker."""
    helper = (ROOT / "scripts/erp_backup_container.py").read_text()
    definitions = helper.split('if __name__ == "__main__":')[0]
    domain = Path(__file__).with_name("housing_history_void_restore_probe.py").read_text()
    producer = (
        definitions
        + "\n"
        + domain
        + """
try:
    value = json.load(sys.stdin)
    verify_api_source(value['api_source'])
    os.environ['APP_ENV'] = 'test'
    os.environ.pop('DATABASE_URL_FILE', None)
    os.environ['DATABASE_URL'] = HISTORY_DSN
    for key, item in value['runtime_config'].items():
        os.environ[key.upper()] = item
    evidence = seed_history_void_case()
    with psycopg.connect(HISTORY_DSN.replace('postgresql+psycopg://', 'postgresql://')) as db:
        metadata = database_manifest(db)
    cipher, _ = history_keys()
    metadata.update(
        media=[], key_canary=cipher.encrypt('erp-backup-recovery-v1', field_name='backup'),
        lookup_canary=history_sha256('erp-backup-lookup-v1:' + value['runtime_config']['field_lookup_key']),
    )
    print(json.dumps({'database': metadata, 'evidence': evidence}))
except Exception:
    print('history_void_seed_or_source_failed', file=sys.stderr)
    sys.exit(1)
"""
    )
    verifier = (
        definitions
        + "\n"
        + domain
        + """
from contextlib import redirect_stdout
try:
    value = json.load(sys.stdin)
    verify_api_source(value['domain_evidence']['api_source'])
    os.environ['APP_ENV'] = 'test'
    os.environ.pop('DATABASE_URL_FILE', None)
    os.environ['DATABASE_URL'] = HISTORY_DSN
    for key, item in value['runtime_config'].items():
        os.environ[key.upper()] = item
    sys.stdin = io.StringIO(json.dumps(value))
    output = io.StringIO()
    with redirect_stdout(output):
        verify()
    checks = verify_history_void_case(value['domain_evidence'])
    response = json.loads(output.getvalue())
    response['history_void_assertions'] = checks
    print(json.dumps(response, sort_keys=True))
except Exception:
    print('history_void_restore_verification_failed', file=sys.stderr)
    sys.exit(1)
"""
    )
    return helper, producer, verifier


def test_history_restore_programs_compile_without_runtime():
    _, producer, verifier = history_programs()
    compile(producer, "history-void-seed", "exec")
    compile(verifier, "history-void-verify", "exec")


def test_history_archive_and_image_guard_bind_new_source_without_docker(tmp_path):
    target = tmp_path / "source.tar.gz"
    inventory = write_history_source_archive(ROOT, target)
    with tarfile.open(target) as archive:
        for relative in (
            "app/housing/history_void_service.py",
            "app/housing/history_void_models.py",
            "app/housing/history_validity.py",
            "migrations/versions/0070_housing_history_void.py",
            "app/housing/reporting_projection.py",
        ):
            raw = (ROOT / "apps/api" / relative).read_bytes()
            assert archive.extractfile("apps/api/" + relative).read() == raw
            assert inventory[relative] == hashlib.sha256(raw).hexdigest()
        for relative in (
            "tests/cli/housing_history_void_restore_probe.py",
            "tests/cli/test_housing_history_void_backup_docker.py",
        ):
            assert (
                archive.extractfile("apps/api/" + relative).read()
                == (ROOT / "apps/api" / relative).read_bytes()
            )


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink", "escape", "empty"])
def test_history_image_guard_rejects_mismatch_without_docker(tmp_path, mutation):
    relative = "app/housing/history_void_service.py"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"expected")
    inventory = {relative: hashlib.sha256(b"expected").hexdigest()}
    module = probe()
    module.verify_api_source(inventory, tmp_path)
    if mutation == "changed":
        path.write_bytes(b"stale")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(__file__)
    elif mutation == "escape":
        inventory = {"../" + relative: inventory[relative]}
    else:
        inventory = {}
    with pytest.raises(ValueError, match="api_image_source_mismatch"):
        module.verify_api_source(inventory, tmp_path)


@pytest.mark.parametrize("index", [71, 72])
def test_history_synthetic_documents_and_phone_evidence_are_valid(index):
    from app.personnel_intake.claims.schemas import ClaimStartInput
    from app.security.encryption import EnvelopeCipher, LookupDigest

    cipher, lookup = EnvelopeCipher(b"c" * 32), LookupDigest(b"d" * 32)
    values = probe().history_person_values(index, cipher, lookup)
    identity = cipher.decrypt(values["identity_ciphertext"], field_name="identity_number")
    email = cipher.decrypt(values["email_ciphertext"], field_name="email")
    command = ClaimStartInput(full_name=values["full_name"], identity_number=identity, email=email)
    assert command.identity_number.get_secret_value() == identity
    assert values["identity_digest"] == lookup.digest(identity)
    assert cipher.decrypt(values["phone_ciphertext"], field_name="phone") == f"13900000{index:03d}"


@pytest.mark.parametrize("passed", [True, False])
def test_history_cleanup_only_owns_exact_ledger_scratch(tmp_path, passed):
    reference = ownership_reference()
    docker = reference.OwnedDocker(("sha256:" + "a" * 64,), "", "", {})
    # Retain the reference's exact allowlisted basename; the ledger owner is fresh.
    scratch = tmp_path / "assisted-restore-0069.history_void_0070"
    scratch.mkdir(mode=0o700)
    docker.scratch = scratch
    reference.private_write(scratch / "owner", docker.owner)
    unrelated = tmp_path / "unrelated-history"
    unrelated.mkdir()
    result = docker.cleanup_owned(passed=passed, parent=tmp_path)
    assert result["cleanup_failed"] is False
    assert result["private_scratch_removed"] is passed
    assert scratch.exists() is not passed
    assert unrelated.is_dir()


@pytest.mark.skipif(
    os.environ.get("ERP_BACKUP_DOCKER_TEST") != "1", reason="opt-in Docker rehearsal"
)
def test_encrypted_0070_recovery_preserves_historical_void_and_replay(monkeypatch):
    """Actual gate: encrypted archive -> fresh owned restore -> real service replay."""
    reference = ownership_reference()
    api_image = reference.require_image(os.environ.get("ERP_BACKUP_TEST_API_IMAGE"))
    postgres_image = reference.require_image(os.environ.get("ERP_BACKUP_TEST_POSTGRES_IMAGE"))
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "DATABASE_URL_FILE"):
        monkeypatch.delenv(key, raising=False)
    backup = reference.load_backup()
    helper, producer, verifier = history_programs()
    evidence = {}
    docker = reference.OwnedDocker((api_image, postgres_image), helper, verifier, evidence)
    monkeypatch.setattr(
        backup,
        "subprocess",
        SimpleNamespace(
            run=docker.run,
            PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL,
            CalledProcessError=subprocess.CalledProcessError,
            TimeoutExpired=subprocess.TimeoutExpired,
            SubprocessError=subprocess.SubprocessError,
        ),
    )
    parent = Path.home() / ".codex"
    # Name retained solely for the reused exact scratch allowlist; release/DB
    # revision, source binding, labels and summary explicitly identify 0070.
    scratch = Path(tempfile.mkdtemp(prefix="assisted-restore-0069.", dir=parent))
    docker.scratch = scratch
    os.chmod(scratch, 0o700)
    reference.private_write(scratch / "owner", docker.owner)
    old_umask = os.umask(0o077)
    passed, summary, phase = False, {}, "source_archive"
    try:
        config = {
            name: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            for name in (
                "field_encryption_master_key",
                "field_lookup_key",
                "session_token_pepper",
                "registration_verification_pepper",
                "registration_session_token_pepper",
                "evaluation_tool_service_token",
                "evaluation_tool_web_service_token",
                "evaluation_tool_worker_service_token",
            )
        }
        payload = scratch / "payload"
        recovery = payload / "recovery"
        recovery.mkdir(parents=True, mode=0o700)
        api_source = write_history_source_archive(ROOT, recovery / "source.tar.gz")
        phase = "immutable_image_preflight"
        backup.run(["docker", "image", "inspect", api_image, postgres_image])
        phase = "create_owned_source"
        source = (
            backup.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    "history-void-source-" + docker.owner,
                    "--network",
                    "none",
                    "--memory",
                    "2g",
                    "--cpus",
                    "1",
                    "--security-opt",
                    "no-new-privileges:true",
                    "-e",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "-e",
                    "POSTGRES_USER=erp_verify",
                    "-e",
                    "POSTGRES_DB=erp_verify",
                    postgres_image,
                ]
            )
            .decode()
            .strip()
        )
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
            raise AssertionError("owned_source_unavailable")
        phase = "synthetic_real_service_seed"
        produced = json.loads(
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
                input=json.dumps({"runtime_config": config, "api_source": api_source}).encode(),
            )
        )
        evidence.update(produced["evidence"], api_source=api_source)
        for name, value in config.items():
            reference.private_write(recovery / "secrets" / name, value)
            reference.private_write(recovery / "mounted-api-secrets" / name, value)
        reference.private_write(recovery / "runtime-config.json", json.dumps(config))
        reference.private_write(recovery / ".env", "APP_ENV=test\n")
        phase = "backup_exact_images_and_database"
        with (recovery / "images.tar").open("xb") as output:
            backup.run(["docker", "save", api_image, postgres_image], stdout=output)
        with (payload / "database.dump").open("xb") as output:
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
                stdout=output,
            )
        identity = (
            "backup-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        )
        manifest = {
            "backup_id": identity,
            "created_at": backup.now(),
            "release": "isolated-0070-historical-void-real-service-drill",
            "api_image": api_image,
            "postgres_image": postgres_image,
            "postgres_major": 16,
            "database": produced["database"],
            "files": backup.file_inventory(payload),
        }
        backup.atomic_json(payload / "manifest.json", manifest)
        key_file = scratch / "keys" / "backup.key"
        reference.private_write(key_file, base64.urlsafe_b64encode(secrets.token_bytes(48)))
        candidate = scratch / "backups" / identity
        candidate.mkdir(parents=True, mode=0o700)
        key = backup.key_bytes(key_file, candidate.parent)
        plain = scratch / "archive.tar.gz"
        with tarfile.open(plain, "w:gz") as archive:
            for child in sorted(payload.iterdir()):
                archive.add(child, arcname=child.name)
        phase = "encrypt_and_authenticate_backup"
        encrypted = candidate / "archive.tar.gz.enc"
        backup.crypt(plain, encrypted, key_file)
        meta = {
            "format_version": 1,
            "backup_id": identity,
            "created_at": manifest["created_at"],
            "sha256": backup.digest(encrypted),
            "hmac_sha256": backup.digest(encrypted, key),
            "size_bytes": encrypted.stat().st_size,
            "alembic_revision": produced["database"]["alembic_revision"],
            "release": manifest["release"],
            "restore_size_bytes": sum(item["size"] for item in manifest["files"].values()),
            "database_size_bytes": (payload / "database.dump").stat().st_size,
            "image_size_bytes": (recovery / "images.tar").stat().st_size,
        }
        backup.atomic_json(candidate / "complete.json", backup.seal_metadata(meta, key))
        shutil.rmtree(payload)
        plain.unlink()
        assert not payload.exists() and not plain.exists()
        phase = "decrypt_fresh_owned_restore_and_replay"
        result = backup.verify_archive(candidate, scratch, SimpleNamespace(key_file=key_file), key)
        assert docker.probes == 1
        assert result["encrypted"] and result["verified"] and result["encrypted_samples"] > 0
        assertions = result["history_void_assertions"]
        assert len(assertions) >= 12 and all(value is True for value in assertions.values())
        assert produced["database"]["alembic_revision"] == [["0070_housing_history_void"]]
        summary = {
            "api_image": api_image,
            "postgres_image": postgres_image,
            "backup_id": identity,
            "ciphertext_sha256": meta["sha256"],
            "snapshot_time": manifest["created_at"],
            "revision": produced["database"]["alembic_revision"],
            "recognized_probes": docker.probes,
            "api_source_sha256": hashlib.sha256(
                json.dumps(api_source, sort_keys=True).encode()
            ).hexdigest(),
            "schema_sha256": hashlib.sha256(
                json.dumps(produced["database"]["schema"], sort_keys=True).encode()
            ).hexdigest(),
            "history_void_assertions": assertions,
        }
        passed = True
    except Exception:  # noqa: BLE001 - suppress subprocess/PII values at this boundary.
        # Do not let pytest stringify a CalledProcessError, stdin or restored PII.
        raise AssertionError("history_restore_failed:" + phase) from None
    finally:
        try:
            docker.cleanup_owned(passed=passed, parent=parent)
        finally:
            os.umask(old_umask)
    summary.update(
        owned_containers_removed=len(docker.removed),
        owned_containers_remaining=len(docker.records),
        private_scratch_removed=not scratch.exists(),
    )
    print(json.dumps(summary, sort_keys=True))
