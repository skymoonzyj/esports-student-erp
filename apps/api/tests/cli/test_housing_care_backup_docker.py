"""Fail-closed housing-care recovery checks; Docker rehearsal is explicit opt-in."""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
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
from fastapi import FastAPI, Request, Response

ROOT = Path(__file__).resolve().parents[4]


def write_care_source_archive(root, target):
    """Archive this complete source snapshot, bind only production image bytes."""
    api = root / "apps/api"
    sources = sorted(
        {api / "alembic.ini", api / "pyproject.toml"}
        | set((api / "app").rglob("*.py"))
        | set((api / "migrations").rglob("*.py"))
    )
    required = {
        "migrations/versions/0071_housing_room_care.py",
        "app/housing/room_photo_service.py",
        "app/housing/room_photo_images.py",
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
        raise ValueError("care_recovery_source_incomplete")
    archived_only = [
        api / "tests/cli/test_housing_care_backup_docker.py",
        api / "tests/cli/housing_care_restore_probe.py",
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
                raise ValueError("care_recovery_source_invalid")
            raw = path.read_bytes()
            member = tarfile.TarInfo(path.relative_to(root).as_posix())
            member.size, member.mode = len(raw), 0o600
            archive.addfile(member, io.BytesIO(raw))
            if path in sources:
                relative = path.relative_to(api).as_posix()
                if not relative.startswith("app/cli/") or relative in production_cli:
                    inventory[relative] = hashlib.sha256(raw).hexdigest()
    return inventory


def load_reference(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ownership_reference():
    return load_reference("test_assisted_personnel_backup_docker.py", "care_owned_reference")


def safe_care_diagnostic(stderr):
    """Only our bounded machine labels cross the subprocess privacy boundary."""
    if not isinstance(stderr, bytes):
        return None
    matches = re.findall(
        rb"^care_probe:([a-z_]{1,60}):([A-Za-z]{1,60}):([A-Za-z_]{1,80}):([0-9]{1,6})$",
        stderr,
        re.MULTILINE,
    )
    if len(matches) != 1:
        return None
    return ":".join(item.decode("ascii") for item in matches[0])


def test_care_probe_diagnostics_never_echo_raw_stderr():
    assert safe_care_diagnostic(b"secret config or restored PII") is None
    assert safe_care_diagnostic(b"care_probe:login:ValueError:seed_care_case:123:secret") is None
    assert safe_care_diagnostic(
        b"raw secret\ncare_probe:login:AssertionError:seed_care_case:123\n"
    ) == ("login:AssertionError:seed_care_case:123")


def test_care_probe_real_http_client_preserves_cookie_json_multipart_and_failure_status():
    app = FastAPI()

    @app.get("/api/v1/health/live")
    def live():
        return {"ready": True}

    @app.post("/login")
    def login(response: Response):
        response.set_cookie("probe-session", "synthetic-cookie", httponly=True)
        return {"csrf_token": "synthetic-csrf"}

    @app.put("/photo")
    async def photo(request: Request):
        form = await request.form()
        upload = form["file"]
        return {
            "cookie": request.cookies.get("probe-session"),
            "csrf": request.headers.get("x-csrf-token"),
            "version": form["version"],
            "bytes": (await upload.read()).decode(),
        }

    @app.post("/forbidden", status_code=403)
    def forbidden():
        return {"code": "private_denial"}

    module = probe()
    client_factory = getattr(module, "care_http_client", None)
    assert callable(client_factory), "production-compatible restore HTTP client is missing"
    with client_factory(app) as client:
        logged_in = client.post("/login", json={"synthetic": True})
        assert logged_in.status_code == 200
        uploaded = client.put(
            "/photo",
            headers={"x-csrf-token": logged_in.json()["csrf_token"]},
            data={"version": "4"},
            files={"file": ("synthetic.png", b"pixels", "image/png")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json() == {
            "cookie": "synthetic-cookie",
            "csrf": "synthetic-csrf",
            "version": "4",
            "bytes": "pixels",
        }
        rejected = client.post("/forbidden", json={})
        assert rejected.status_code == 403
        assert rejected.json() == {"code": "private_denial"}


def care_programs():
    reference = load_reference(
        "test_housing_history_void_backup_docker.py", "care_history_reference"
    )
    helper, producer, verifier = reference.history_programs()
    domain = Path(__file__).with_name("housing_care_restore_probe.py").read_text()
    producer = producer.replace(
        "try:\n    value = json.load(sys.stdin)",
        domain + "\ntry:\n    value = json.load(sys.stdin)",
        1,
    )
    producer = producer.replace(
        "except Exception:\n    print('history_void_seed_or_source_failed', file=sys.stderr)",
        "except Exception as exc:\n"
        "    import traceback\n"
        "    frames = [frame for frame in traceback.extract_tb(exc.__traceback__) if frame.filename == '<string>']\n"
        "    frame = frames[-1] if frames else None\n"
        "    print('care_probe:' + globals().get('CARE_PROBE_PHASE', 'source_guard') + ':' "
        "+ type(exc).__name__ + ':' + (frame.name if frame else 'unknown') + ':' "
        "+ str(frame.lineno if frame else 0), file=sys.stderr)",
    )
    verifier = verifier.replace(
        "from contextlib import redirect_stdout",
        domain + "\nfrom contextlib import redirect_stdout",
        1,
    )
    producer = producer.replace(
        'HISTORY_REVISION = "0070_housing_history_void"',
        'HISTORY_REVISION = "0071_housing_room_care"',
    )
    producer = producer.replace(
        "evidence = seed_history_void_case()", "evidence = seed_care_case(seed_history_void_case)"
    )
    producer = producer.replace(
        "evidence = seed_care_case(",
        "os.environ['SESSION_COOKIE_SECURE'] = 'false'\n    evidence = seed_care_case(",
    )
    verifier = verifier.replace(
        "verify_history_void_case(value['domain_evidence'])",
        "verify_care_case(value['domain_evidence'])",
    )
    return (
        helper,
        producer.replace("verify_api_source(", "verify_care_source("),
        verifier.replace("verify_api_source(", "verify_care_source(").replace(
            "history_void_assertions", "housing_care_assertions"
        ),
    )


def test_care_restore_programs_compile_without_runtime():
    _, producer, verifier = care_programs()
    compile(producer, "care-seed", "exec")
    compile(verifier, "care-verify", "exec")


@pytest.mark.parametrize("mutation", ["id", "label", "network", "port", "bind", "volume"])
def test_care_restore_refuses_non_task_container_or_volume(mutation):
    reference = ownership_reference()
    details = {
        "Id": "a" * 64,
        "Config": {"Labels": {"erp.assisted.restore.owner": "care-owner"}},
        "HostConfig": {"NetworkMode": "none", "PortBindings": {}, "Binds": None},
        "Mounts": [{"Type": "volume", "Name": "b" * 64}],
    }
    if mutation == "id":
        details["Id"] = "c" * 64
    elif mutation == "label":
        details["Config"]["Labels"] = {"erp.assisted.restore.owner": "other-owner"}
    elif mutation == "network":
        details["HostConfig"]["NetworkMode"] = "bridge"
    elif mutation == "port":
        details["HostConfig"]["PortBindings"] = {"5432/tcp": [{"HostPort": "55433"}]}
    elif mutation == "bind":
        details["Mounts"] = [{"Type": "bind", "Source": "/private/shared"}]
    else:
        details["Mounts"][0]["Name"] = "c" * 64
    with pytest.raises(ValueError, match="ownership_mismatch"):
        reference.check_owned(
            details, "a" * 64, ("erp.assisted.restore.owner", "care-owner"), "none", ("b" * 64,)
        )


@pytest.mark.parametrize("passed", [False, True])
def test_care_cleanup_preserves_unrelated_scratch_and_retains_failed_evidence(tmp_path, passed):
    reference = ownership_reference()
    docker = reference.OwnedDocker(("sha256:" + "a" * 64,), "", "", {})
    # The reused cleanup allows only this exact legacy scratch prefix.
    scratch = tmp_path / "assisted-restore-0069.housing_care_0071"
    scratch.mkdir(mode=0o700)
    docker.scratch = scratch
    reference.private_write(scratch / "owner", docker.owner)
    unrelated = tmp_path / "housing-care-other-task"
    unrelated.mkdir()
    status = docker.cleanup_owned(passed=passed, parent=tmp_path)
    assert not status["cleanup_failed"]
    assert status["private_scratch_removed"] is passed
    assert unrelated.is_dir()


def test_care_source_archive_preserves_photo_migration_and_probe_bytes(tmp_path):
    archive_path = tmp_path / "source.tar.gz"
    inventory = write_care_source_archive(ROOT, archive_path)
    with tarfile.open(archive_path) as archive:
        for relative in (
            "migrations/versions/0071_housing_room_care.py",
            "app/housing/room_photo_service.py",
            "app/housing/room_photo_images.py",
        ):
            raw = (ROOT / "apps/api" / relative).read_bytes()
            assert archive.extractfile("apps/api/" + relative).read() == raw
            assert inventory[relative] == hashlib.sha256(raw).hexdigest()
        for relative in (
            "tests/cli/housing_care_restore_probe.py",
            "tests/cli/test_housing_care_backup_docker.py",
        ):
            assert (
                archive.extractfile("apps/api/" + relative).read()
                == (ROOT / "apps/api" / relative).read_bytes()
            )


@pytest.mark.skipif(
    os.environ.get("RUN_HOUSING_CARE_RESTORE_TESTS") != "1", reason="opt-in Docker rehearsal"
)
def test_encrypted_care_recovery_preserves_photos_cleaning_and_original_fields(monkeypatch):
    """Actual gate: encrypted archive -> fresh owned restore -> real service replay."""
    host, context = os.environ.get("DOCKER_HOST"), os.environ.get("DOCKER_CONTEXT")
    inspected = None
    if context or not host:
        inspected = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    endpoint = probe().require_local_docker_environment(host, context, inspected)
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    reference = ownership_reference()
    api_image = reference.require_image(os.environ.get("ERP_BACKUP_TEST_API_IMAGE"))
    postgres_image = reference.require_image(os.environ.get("ERP_BACKUP_TEST_POSTGRES_IMAGE"))
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "DATABASE_URL_FILE"):
        monkeypatch.delenv(key, raising=False)
    backup = reference.load_backup()
    helper, producer, verifier = care_programs()
    evidence = {}

    class CareOwnedDocker(reference.OwnedDocker):
        diagnostic = None

        def run(self, command, **kwargs):
            try:
                return super().run(command, **kwargs)
            except subprocess.CalledProcessError as exc:
                self.diagnostic = safe_care_diagnostic(exc.stderr)
                raise

    docker = CareOwnedDocker((api_image, postgres_image), helper, verifier, evidence)
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
    # revision, source binding, labels and summary explicitly identify 0071.
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
        api_source = write_care_source_archive(ROOT, recovery / "source.tar.gz")
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
                    "housing-care-source-" + docker.owner,
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
            "release": "isolated-0071-housing-care-photo-real-service-drill",
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
        assertions = result["housing_care_assertions"]
        assert len(assertions) >= 9 and all(value is True for value in assertions.values())
        assert produced["database"]["alembic_revision"] == [["0071_housing_room_care"]]
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
            "housing_care_assertions": assertions,
        }
        passed = True
    except Exception:  # noqa: BLE001 - suppress subprocess/PII values at this boundary.
        # Do not let pytest stringify a CalledProcessError, stdin or restored PII.
        suffix = ":" + docker.diagnostic if docker.diagnostic else ""
        raise AssertionError("housing_care_restore_failed:" + phase + suffix) from None
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


def probe():
    path = Path(__file__).with_name("housing_care_restore_probe.py")
    assert path.is_file(), "housing-care restore validation is not implemented"
    spec = importlib.util.spec_from_file_location("care_restore_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def intact_case():
    return {
        "people_sha256": "a" * 64,
        "accounts_sha256": "b" * 64,
        "stays_sha256": "c" * 64,
        "transfers_sha256": "d" * 64,
        "rooms": [
            {
                "id": "room",
                "version": 4,
                "daily_cleaning_enabled": True,
                "cleaning_status": "ready",
                "cleaning_pending_at": "2026-10-23T10:00:00+00:00",
                "cleaning_completed_at": "2026-10-23T11:00:00+00:00",
                "cleaning_completed_by_user_id": "actor",
                "row_sha256": "e" * 64,
            }
        ],
        "photos": [
            {
                "id": "photo",
                "room_id": "room",
                "slot": 1,
                "original_sha256": "f" * 64,
                "stored_sha256": "f" * 64,
                "thumbnail_sha256": "1" * 64,
                "byte_size": 50,
                "thumbnail_byte_size": 20,
                "row_sha256": "2" * 64,
            }
        ],
        "audits": [
            {
                "action": "housing.room.cleaning.complete",
                "resource_id": "room",
                "actor_user_id": "actor",
                "outcome": "allowed",
                "row_sha256": "3" * 64,
            },
            {
                "action": "housing.room_photo.put",
                "resource_id": "photo",
                "metadata": {"room_id": "room"},
                "actor_user_id": "actor",
                "outcome": "allowed",
                "row_sha256": "4" * 64,
            },
        ],
    }


def test_restore_preserves_room_care_and_photo_content(intact_case):
    checks = probe().validate_care_case(copy.deepcopy(intact_case), intact_case)
    assert len(checks) >= 7
    assert all(value is True for value in checks.values())


@pytest.mark.parametrize(
    "field",
    [
        "people_sha256",
        "accounts_sha256",
        "stays_sha256",
        "transfers_sha256",
        "rooms",
        "photos",
        "audits",
    ],
)
def test_restore_rejects_missing_evidence_even_when_both_snapshots_match(intact_case, field):
    intact_case.pop(field)
    with pytest.raises(AssertionError, match="housing_care_verification_failed"):
        probe().validate_care_case(copy.deepcopy(intact_case), intact_case)


@pytest.mark.parametrize("field", ["original_sha256", "thumbnail_sha256", "row_sha256"])
def test_restore_rejects_tampered_photo_hashes(intact_case, field):
    observed = copy.deepcopy(intact_case)
    observed["photos"][0][field] = "9" * 64
    with pytest.raises(AssertionError, match="housing_care_verification_failed"):
        probe().validate_care_case(observed, intact_case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 3),
        ("daily_cleaning_enabled", False),
        ("cleaning_status", "pending"),
        ("cleaning_completed_at", None),
        ("cleaning_completed_by_user_id", None),
    ],
)
def test_restore_rejects_care_state_version_or_actor_drift(intact_case, field, value):
    observed = copy.deepcopy(intact_case)
    observed["rooms"][0][field] = value
    with pytest.raises(AssertionError, match="housing_care_verification_failed"):
        probe().validate_care_case(observed, intact_case)


@pytest.mark.parametrize(
    "mutation", ["empty_original", "empty_thumbnail", "wrong_room", "no_audit"]
)
def test_seed_cannot_bless_incomplete_care_proof(intact_case, mutation):
    if mutation == "empty_original":
        intact_case["photos"][0]["original_sha256"] = ""
    elif mutation == "empty_thumbnail":
        intact_case["photos"][0]["thumbnail_byte_size"] = 0
    elif mutation == "wrong_room":
        intact_case["photos"][0]["room_id"] = "other"
    else:
        intact_case["audits"] = intact_case["audits"][:1]
    with pytest.raises(AssertionError, match="housing_care_verification_failed"):
        probe().validate_care_case(copy.deepcopy(intact_case), intact_case)


@pytest.mark.parametrize(
    "endpoint", ["tcp://127.0.0.1:2375", "ssh://nas", "", "unix:///tmp/remote.sock"]
)
def test_restore_refuses_remote_or_unknown_docker_endpoint(endpoint):
    with pytest.raises(ValueError, match="local_docker_endpoint_required"):
        probe().require_local_docker_endpoint(endpoint)


def test_restore_accepts_exact_owned_local_endpoint():
    endpoint = "unix:///Users/skymoonzyj/.colima/default/docker.sock"
    assert probe().require_local_docker_endpoint(endpoint) == endpoint


def test_remote_context_cannot_hide_behind_an_allowed_docker_host():
    with pytest.raises(ValueError, match="local_docker_endpoint_required"):
        probe().require_local_docker_environment(
            "unix:///Users/skymoonzyj/.colima/default/docker.sock", "remote", "ssh://nas"
        )


@pytest.mark.parametrize("mutation", ["changed", "missing", "escape", "empty"])
def test_restore_source_guard_refuses_unknown_bytes(tmp_path, mutation):
    path = tmp_path / "app/housing/room_photo_service.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"expected")
    inventory = {"app/housing/room_photo_service.py": hashlib.sha256(b"expected").hexdigest()}
    module = probe()
    module.verify_care_source(inventory, tmp_path)
    if mutation == "changed":
        path.write_bytes(b"stale")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "escape":
        inventory = {"../escape": "a" * 64}
    else:
        inventory = {}
    with pytest.raises(ValueError, match="api_image_source_mismatch"):
        module.verify_care_source(inventory, tmp_path)
