"""Opt-in joint cold snapshot/archive restore; never uses a shared database.

The API executes the current checkout through a read-only mount. Gate, exporter,
pg_dump, OpenList, encryption and isolated restoration are real implementations.
Only the capture boundary is observed; no gate/export result is manufactured.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[4]
SECRET_NAMES = (
    "field_encryption_master_key",
    "field_lookup_key",
    "session_token_pepper",
    "registration_verification_pepper",
    "registration_session_token_pepper",
    "evaluation_tool_service_token",
    "evaluation_tool_web_service_token",
    "evaluation_tool_worker_service_token",
)

SEED = """
import base64, json, sys
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.identity.models import Person
from app.auth.models import UserAccount, AuthSession
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.operations.cloud_auth_models import CloudAuthApplication, CloudAuthSession, CloudAuthCandidate, CloudAuthOperation
from app.operations.cloud_auth_worker import encrypt, queue
from app.operations.cloud_auth_security import import_token_digests, store_import_token_digests
from app.security.encryption import EnvelopeCipher
v = json.load(sys.stdin)
s = get_settings()
cipher = EnvelopeCipher(base64.urlsafe_b64decode(s.field_encryption_master_key.get_secret_value()))
with Session(create_engine(s.database_url)) as db:
    person = Person(full_name='Joint recovery fixture', sex='female', date_of_birth=date(1990,1,1),
        identity_ciphertext=cipher.encrypt('110105199001010012', field_name='identity_number'),
        identity_digest='a'*64)
    db.add(person); db.flush()
    account = UserAccount(person_id=person.id, password_hash='not-a-login-credential', force_password_change=False)
    app = CloudAuthApplication(provider='baidu', client_id='local-only-no-provider')
    db.add_all([account, app]); db.flush()
    login = AuthSession(user_id=account.id, token_digest=uuid4().hex, csrf_digest=uuid4().hex,
        expires_at=datetime.now(UTC)+timedelta(hours=1))
    db.add_all([login, UserRole(user_id=account.id, role_id=ROLES['super_admin'].id)]); db.flush()
    source = dict(mode='openlist_builtin_import', protocol_version=2,
        auth_config_id='openlist-builtin-import-v1:baidu', auth_config_revision=1, app_revision=None)
    session = CloudAuthSession(provider='baidu', owner_account_id=account.id, **source,
        owner_login_session_id=login.id, state='applied',
        expires_at=datetime.now(UTC)+timedelta(minutes=10))
    db.add(session); db.flush()
    candidate = CloudAuthCandidate(id=UUID(v['candidate_id']), session_id=session.id,
        provider='baidu', operation_id=UUID(v['operation_id']), connector_id=UUID(v['connector_id']),
        storage_id=v['storage_id'], webdav_user_id=v['webdav_user_id'],
        mount_path='/joint-local', state='active', **source)
    candidate.username_ciphertext = encrypt(candidate, 'username', v['username'])
    candidate.password_ciphertext = encrypt(candidate, 'password', v['password'])
    db.add(candidate)
    for phase in ('not_started', 'sent', 'refreshed', 'unknown', 'verified'):
        operation = CloudAuthOperation(provider='baidu', session_id=session.id,
            owner_account_id=account.id, owner_login_session_id=session.owner_login_session_id,
            idempotency_key=uuid4(), action='verify_import', status='failed' if phase == 'unknown' else 'succeeded',
            request_fingerprint='a'*64, session_revision=1, target_revision=0,
            refresh_phase=phase, next_attempt_at=datetime.now(UTC), created_at=datetime.now(UTC), **source)
        db.add(operation); db.flush()
        operation.payload_ciphertext = encrypt(operation, 'payload', {'synthetic': 'pending-import'})
        if phase in ('refreshed', 'verified'):
            operation.refresh_checkpoint_ciphertext = encrypt(operation, 'refresh_checkpoint',
                {'synthetic': 'latest-persisted-not-old-chain'})
    pending = CloudAuthSession(id=UUID(v['pending_session_id']), provider='baidu', **source,
        owner_account_id=account.id, owner_login_session_id=login.id, state='verifying_import',
        expires_at=datetime.now(UTC)+timedelta(minutes=10))
    db.add(pending); db.flush()
    operation = queue(db, pending, 'verify_import', now=datetime.now(UTC))
    operation.payload_ciphertext = encrypt(operation, 'payload',
        {'access_token':'synthetic-old-access','refresh_token':'synthetic-old-refresh'})
    store_import_token_digests(operation, import_token_digests(s, 'baidu',
        'synthetic-old-access','synthetic-old-refresh'), cipher)
    db.commit()
print('seeded')
"""

POST_SNAPSHOT_REFRESH = """
import importlib.util, json, sys
from datetime import UTC, datetime
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.operations import cloud_auth_worker as worker
from app.operations.cloud_auth_bridge import REQUEST_ADAPTER
from app.operations.cloud_auth_import_worker import refresh_checkpoint
sys.path.insert(0, '/joint-runtime/scripts')
spec=importlib.util.spec_from_file_location('joint_runner','/joint-runtime/scripts/erp-cloud-authorize.py')
runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
from cloud_auth import builtin_import
from cloud_auth.provider_types import AccountIdentity, ImportedTokenGrant
event=Path('/joint-runtime/external-refresh-count')
def refresh(provider, token, *, now):
    assert token=='synthetic-old-refresh'
    assert not event.exists()
    event.write_text('1')
    return ImportedTokenGrant('synthetic-new-access','synthetic-new-refresh',None,'unreported',now,provider)
builtin_import.refresh_import=refresh
builtin_import.get_import_identity=lambda provider, token: AccountIdentity(provider,'12345','Synthetic')
with Session(create_engine(get_settings().database_url)) as db:
    now=datetime.now(UTC)
    work=worker.claim_auth_operation(db,now=now)
    assert work['action']=='verify_import' and work['refresh_phase']=='not_started'
    def rpc(config, action, **payload):
        request=REQUEST_ADAPTER.validate_python({'action':action,**json.loads(json.dumps(payload,default=runner.wire))})
        if action=='refresh_checkpoint':
            return refresh_checkpoint(db,request,now=now)
        if action=='complete':
            return {'ok':worker.complete_auth_operation(db,request.fence,request.result,now=now)}
        raise AssertionError(action)
    runner.execute({},work,rpc)
    assert event.read_text()=='1'
print('original-post-snapshot-refresh-consumed')
"""

RESTORED_IMPORT_CLOSED_PROBE = """
import json, sys
from uuid import UUID
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.operations.cloud_auth_models import CloudAuthSession, CloudAuthOperation, CloudAuthCandidate
v=json.load(sys.stdin)
with Session(create_engine(get_settings().database_url)) as db:
    session=db.get(CloudAuthSession,UUID(v['pending_session_id']))
    assert session.state=='failed' and session.error_code=='cloud_refresh_outcome_unknown'
    operation=db.scalar(select(CloudAuthOperation).where(CloudAuthOperation.session_id==session.id))
    assert operation.status=='cancelled' and operation.refresh_phase=='not_started'
    assert operation.payload_ciphertext is None and operation.refresh_checkpoint_ciphertext is None
    assert session.token_ciphertext is None
    candidate=db.scalar(select(CloudAuthCandidate))
    assert candidate.state=='active'
    assert db.get(CloudAuthSession,candidate.session_id).state=='applied'
print('rollback-import-closed-before-release-active-preserved')
"""

RESTORED_AUTH_PROBE = """
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.operations.cloud_auth_models import CloudAuthCandidate, CloudAuthOperation, CloudAuthSession
from app.operations.cloud_auth_worker import decrypt
with Session(create_engine(get_settings().database_url)) as db:
    candidate = db.scalar(select(CloudAuthCandidate))
    assert candidate.mode == 'openlist_builtin_import' and candidate.protocol_version == 2
    assert candidate.auth_config_id == 'openlist-builtin-import-v1:baidu'
    assert candidate.auth_config_revision == 1 and candidate.app_revision is None
    operations = db.scalars(select(CloudAuthOperation).join(CloudAuthSession).where(CloudAuthSession.state=='applied')).all()
    assert {operation.refresh_phase for operation in operations} == {'not_started','sent','refreshed','unknown','verified'}
    for operation in operations:
        assert operation.mode == candidate.mode and operation.protocol_version == candidate.protocol_version
        assert decrypt(operation, 'payload', operation.payload_ciphertext) == {'synthetic': 'pending-import'}
        if operation.refresh_phase in ('refreshed','verified'):
            assert decrypt(operation, 'refresh_checkpoint', operation.refresh_checkpoint_ciphertext) == {'synthetic': 'latest-persisted-not-old-chain'}
        else:
            assert operation.refresh_checkpoint_ciphertext is None
print('auth-phases-source-preserved')
"""

GATE_PROBE = """
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.errors import ProblemError
from app.operations.cloud_auth_models import CloudConnectorState
from app.operations.cloud_auth_security import require_no_maintenance
with Session(create_engine(get_settings().database_url)) as db:
    gate = db.scalar(select(CloudConnectorState))
    assert gate.registered and gate.maintenance_operation_id is not None
    try:
        require_no_maintenance(db)
    except ProblemError as exc:
        assert exc.code == 'cloud_maintenance_active'
    else:
        raise AssertionError('real maintenance check allowed writes')
print('gate-active')
"""


def cleanup_joint_resources(names, owner, private_root, *, run=subprocess.run, original_error=None):
    """Unknown Docker state preserves binds and cannot produce a passing drill."""
    failures = []
    for name in reversed(names):
        stage = "inspect"
        try:
            result = run(
                ["docker", "container", "inspect", name],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if result.returncode:
                # Exact targeted daemon response only: connectivity/permission
                # errors, another resource's absence and malformed JSON are not
                # evidence that our bind mount is no longer in use.
                if (
                    result.returncode == 1
                    and result.stdout.strip() == b"[]"
                    and result.stderr.strip()
                    == f"Error response from daemon: No such container: {name}".encode()
                ):
                    continue
                raise RuntimeError("inspect failed")
            details = json.loads(result.stdout)[0]
            stage = "ownership"
            if details["Config"].get("Labels", {}).get("erp.joint.recovery") != owner:
                raise RuntimeError("ownership mismatch")
            stage = "remove"
            result = run(
                ["docker", "rm", "--force", "--volumes", name],
                capture_output=True,
                timeout=60,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("remove failed")
        except (
            OSError,
            subprocess.SubprocessError,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            RuntimeError,
        ):
            # Deliberately omit raw command output, which may contain secrets.
            failures.append(f"{name}:{stage}")
    if not failures:
        try:
            for child in private_root.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        except OSError:
            failures.append("private-files:remove")
    if failures:
        message = (
            "joint fixture cleanup unresolved ("
            + ", ".join(failures)
            + f"); private data retained at {private_root}"
        )
        if original_error is not None:
            original_error.add_note(message)
        else:
            raise RuntimeError(message)


def fake_cleanup_docker(problem, *, owner="test-owner"):
    removed = []

    def run(argv, **kwargs):
        name = argv[-1]
        if argv[1:3] == ["container", "inspect"]:
            if name == "problem":
                if problem == "timeout":
                    raise subprocess.TimeoutExpired(argv, 30)
                if problem == "daemon":
                    return subprocess.CompletedProcess(
                        argv, 1, b"", b"Cannot connect to Docker daemon"
                    )
                if problem in {"absent", "foreign-absence"}:
                    absent = name if problem == "absent" else "another-name"
                    return subprocess.CompletedProcess(
                        argv,
                        1,
                        b"[]\n",
                        f"Error response from daemon: No such container: {absent}\n".encode(),
                    )
            label = "foreign-owner" if name == "problem" and problem == "foreign" else owner
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps([{"Config": {"Labels": {"erp.joint.recovery": label}}}]).encode(),
                b"",
            )
        assert argv[1:4] == ["rm", "--force", "--volumes"]
        if name == "problem" and problem == "remove":
            return subprocess.CompletedProcess(argv, 1, b"", b"daemon disconnected")
        removed.append(name)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    return run, removed


@pytest.mark.parametrize("problem", ["absent", "owned"])
def test_cleanup_deletes_private_data_only_after_positive_absence_or_owned_removal(
    tmp_path, problem
):
    (tmp_path / "private-bind").mkdir()
    run, removed = fake_cleanup_docker(problem)
    cleanup_joint_resources(["problem"], "test-owner", tmp_path, run=run)
    assert not list(tmp_path.iterdir())
    assert removed == ([] if problem == "absent" else ["problem"])


@pytest.mark.parametrize("problem", ["daemon", "timeout", "foreign", "foreign-absence", "remove"])
def test_cleanup_unknown_or_foreign_state_preserves_data_and_tries_other_owned_containers(
    tmp_path, problem
):
    private = tmp_path / "private-bind"
    private.mkdir()
    (private / "data.db").write_bytes(b"private fixture state")
    run, removed = fake_cleanup_docker(problem)
    with pytest.raises(RuntimeError, match="joint fixture cleanup unresolved"):
        cleanup_joint_resources(["last", "problem", "first"], "test-owner", tmp_path, run=run)
    assert (private / "data.db").read_bytes() == b"private fixture state"
    assert removed == ["first", "last"]


def test_cleanup_failure_adds_note_without_masking_original_test_exception(tmp_path):
    (tmp_path / "private-bind").mkdir()
    run, removed = fake_cleanup_docker("foreign")
    original = ValueError("original archive verification failed")
    with pytest.raises(ValueError, match="original archive verification failed") as caught:
        try:
            raise original
        finally:
            cleanup_joint_resources(
                ["last", "problem"], "test-owner", tmp_path, run=run, original_error=sys.exception()
            )
    assert caught.value is original
    assert any("joint fixture cleanup unresolved" in note for note in original.__notes__)
    assert (tmp_path / "private-bind").is_dir()
    assert removed == ["last"]


@pytest.mark.skipif(
    os.environ.get("ERP_CLOUD_JOINT_RECOVERY_TEST") != "1",
    reason="opt-in joint Docker cold snapshot and encrypted archive restoration",
)
def test_joint_real_gate_export_cold_connector_encrypted_archive_restore(tmp_path, monkeypatch):
    # Colima can bind home paths, not macOS's /private pytest default. Never put
    # generated credentials in outputs or use an existing deployment directory.
    assert tmp_path.resolve().is_relative_to(ROOT / ".superpowers"), (
        "use a unique --basetemp under this worktree's excluded .superpowers"
    )
    os.chmod(tmp_path, 0o700)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "joint_erp_backup", ROOT / "scripts/erp-backup.py"
    )
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    backup.REPORT_STAGE = lambda name: print("joint recovery stage: " + name, flush=True)
    from cloud_auth import recovery

    owner = secrets.token_hex(12)
    project = "erp-joint-" + owner
    names = []
    api_image = os.environ.get("ERP_BACKUP_TEST_API_IMAGE", "esports-erp-e2e-api:latest")
    connector_id, operation_id, candidate_id = (str(uuid4()) for _ in range(3))
    pending_session_id = str(uuid4())
    root = tmp_path / "app"
    release = root / "current"
    release.mkdir(parents=True, mode=0o700)
    secret_dir = release / "secrets"
    secret_dir.mkdir(mode=0o700)
    for name in SECRET_NAMES:
        path = secret_dir / name
        path.write_text(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        path.chmod(0o600)
    env = release / ".env"
    env.write_text(
        "APP_ENV=test\nDATABASE_URL=postgresql+psycopg://erp@127.0.0.1:5432/erp\n"
        "EVALUATION_MEDIA_ROOT=/joint-runtime/media\n"
        "EVALUATION_MEDIA_QUARANTINE_ROOT=/joint-runtime/quarantine\n"
        + "".join(f"{name.upper()}_FILE=/run/secrets/{name}\n" for name in SECRET_NAMES)
    )
    env.chmod(0o600)
    runtime_data = tmp_path / "runtime"
    for child in ("media", "quarantine", "certs"):
        (runtime_data / child).mkdir(parents=True, mode=0o700)
    shutil.copytree(ROOT / "scripts", runtime_data / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    # Real source recovery data, not placeholder source/images tar files.
    shutil.copytree(
        ROOT / "apps/api/app",
        release / "apps/api/app",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        ROOT / "apps/api/migrations",
        release / "apps/api/migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("alembic.ini", "pyproject.toml", "Dockerfile"):
        shutil.copyfile(ROOT / "apps/api" / name, release / "apps/api" / name)
    connector_root = root / "cloud-connector"
    connector_root.mkdir(mode=0o700)
    data = connector_root / "data"
    data.mkdir(mode=0o700)
    admin_secret = connector_root / "admin-secret"
    admin_secret.write_text(secrets.token_urlsafe(48))
    admin_secret.chmod(0o600)
    bootstrap = connector_root / "bootstrap-env"
    bootstrap.write_text(
        "OPENLIST_ADMIN_PASSWORD=" + admin_secret.read_text() + "\nLOG_ENABLE=false\nUMASK=077\n"
    )
    bootstrap.chmod(0o600)
    connector_name = project + "-connector"
    args = SimpleNamespace(app_dir=root, project=project, key_file=tmp_path / "offline.key")
    backup.initialize_key(args.key_file)
    payload = tmp_path / "payload"
    payload.mkdir(mode=0o700)
    owned_runtime = recovery.DockerRuntime()

    def docker(*argv, **kwargs):
        return backup.run(["docker", *argv], **kwargs)

    def start(name, argv):
        names.append(name)  # Also clean an owned container whose startup failed.
        return docker(
            "run",
            "--detach",
            "--name",
            name,
            "--log-driver",
            "none",
            "--label",
            "erp.joint.recovery=" + owner,
            *argv,
            timeout=60,
        )

    def api_python(code, value=None, *, target=project):
        return docker(
            "exec",
            "-i",
            target + "-api-1",
            "python",
            "-c",
            code,
            input=json.dumps(value).encode() if value is not None else None,
        )

    def sql(statement, *, target=project):
        return (
            docker("exec", target + "-db-1", "psql", "-U", "erp", "-d", "erp", "-Atc", statement)
            .decode()
            .strip()
        )

    try:
        start(
            project + "-db-1",
            [
                "--network",
                "none",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=erp",
                "-e",
                "POSTGRES_DB=erp",
                "--mount",
                f"type=bind,src={secret_dir},dst=/run/secrets,readonly",
                "postgres:16-alpine",
            ],
        )
        for _ in range(60):
            try:
                docker(
                    "exec",
                    project + "-db-1",
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "erp",
                    "-d",
                    "erp",
                    timeout=5,
                )
                break
            except backup.BackupError:
                time.sleep(0.1)
        else:
            pytest.fail("own disconnected PostgreSQL did not become ready")
        start(
            project + "-api-1",
            [
                "--network",
                "container:" + project + "-db-1",
                "--user",
                "0:0",
                "--env-file",
                str(env),
                "--mount",
                f"type=bind,src={ROOT / 'apps/api'},dst=/app,readonly",
                "--mount",
                f"type=bind,src={secret_dir},dst=/run/secrets,readonly",
                "--mount",
                f"type=bind,src={runtime_data},dst=/joint-runtime",
                "--entrypoint",
                "python",
                api_image,
                "-c",
                "import time; time.sleep(3600)",
            ],
        )
        docker(
            "exec", project + "-api-1", "python", "-m", "alembic", "upgrade", "head", timeout=120
        )
        current_worker = ROOT / "apps/api/app/operations/cloud_auth_worker.py"
        assert (
            api_python(
                "import hashlib; from pathlib import Path; "
                "import app.operations.cloud_auth_worker as worker; "
                "print(hashlib.sha256(Path(worker.__file__).read_bytes()).hexdigest())"
            )
            .decode()
            .strip()
            == hashlib.sha256(current_worker.read_bytes()).hexdigest()
        )
        # snapshot_payload inventories six runtime roles. Inert, disconnected
        # placeholders supply their image/config only; none simulates ERP logic.
        for role in ("scheduler", "web", "proxy", "caddy"):
            start(
                project + "-" + role + "-1",
                [
                    "--network",
                    "none",
                    "--mount",
                    f"type=bind,src={runtime_data / 'certs'},dst=/etc/caddy/certs,readonly",
                    "--entrypoint",
                    "python",
                    api_image,
                    "-c",
                    "import time; time.sleep(3600)",
                ],
            )
        start(
            connector_name,
            [
                "--network",
                "none",
                "--label",
                "erp.cloud.connector=" + connector_id,
                "--label",
                "erp.cloud.dedicated=true",
                "--user",
                recovery.container_bind_user(data, recovery.IMAGE),
                "--env-file",
                str(bootstrap),
                "--mount",
                f"type=bind,src={data},dst=/opt/openlist/data",
                recovery.IMAGE,
            ],
        )

        def local(path, body, token=""):
            return recovery.connector_local_request(
                owned_runtime.call, connector_name, path, body, authorization=token
            )

        deadline = time.monotonic() + 25
        while True:
            try:
                token = local(
                    "/api/auth/login", {"username": "admin", "password": admin_secret.read_text()}
                )["data"]["token"]
                break
            except (recovery.RecoveryError, TypeError, KeyError):
                if time.monotonic() >= deadline:
                    pytest.fail("own disconnected OpenList did not become ready")
                time.sleep(0.1)
        assert (
            local(
                "/api/admin/storage/create",
                {
                    "mount_path": "/joint-local",
                    "driver": "Local",
                    "addition": json.dumps(
                        {"root_folder_path": "/opt/openlist/data", "mkdir_perm": "700"}
                    ),
                    "order": 0,
                },
                token,
            )["code"]
            == 200
        )
        candidate_password = secrets.token_urlsafe(32)
        assert (
            local(
                "/api/admin/user/create",
                {
                    "username": "joint-fixture",
                    "password": candidate_password,
                    "base_path": "/joint-local",
                    "role": 0,
                    "permission": 0,
                    "disabled": False,
                },
                token,
            )["code"]
            == 200
        )
        docker("stop", "--time", "5", connector_name)
        with sqlite3.connect((data / "data.db").as_uri() + "?mode=ro", uri=True) as db:
            storage = str(
                db.execute("SELECT id FROM x_storages WHERE mount_path='/joint-local'").fetchone()[
                    0
                ]
            )
            user = str(
                db.execute("SELECT id FROM x_users WHERE username='joint-fixture'").fetchone()[0]
            )
        details = backup.inspect(connector_name)
        registry = connector_root / "registry.json"
        backup.atomic_json(
            registry,
            {
                "version": 1,
                "managed": True,
                "connector_id": connector_id,
                "container_name": connector_name,
                "image": recovery.IMAGE,
                "image_id": details["Image"],
                "origin": recovery.ORIGIN,
                "data_dir": str(data),
                "admin_secret_file": str(admin_secret),
                "objects": [
                    {
                        "operation_id": operation_id,
                        "storage_id": storage,
                        "webdav_user_id": user,
                        "mount_path": "/joint-local",
                    }
                ],
            },
        )
        value = recovery.load_registry(registry)
        owned_runtime.start(value)
        bridge = recovery.SnapshotBridge(project)
        assert bridge.call("heartbeat", connector_id=connector_id) == {"ok": True}
        assert (
            api_python(
                SEED,
                {
                    "candidate_id": candidate_id,
                    "pending_session_id": pending_session_id,
                    "connector_id": connector_id,
                    "operation_id": operation_id,
                    "storage_id": storage,
                    "webdav_user_id": user,
                    "username": "joint-fixture",
                    "password": candidate_password,
                },
            ).strip()
            == b"seeded"
        )
        observed = []
        original_capture = backup.capture_connector_snapshot

        def observe_capture(capture_args, capture_payload, export_database):
            def observe_export():
                assert not owned_runtime.running(value)
                assert api_python(GATE_PROBE).strip() == b"gate-active"
                metadata = export_database()  # Real pg_export_snapshot subprocess, left open.
                assert metadata["cloud_connector_references"][0]["candidate_id"] == candidate_id
                assert metadata["cloud_connector_references"][0]["mode"] == "openlist_builtin_import"
                assert metadata["cloud_connector_references"][0]["protocol_version"] == 2
                observed.append("stopped-gated-export")
                return metadata

            result = original_capture(capture_args, capture_payload, observe_export)
            assert owned_runtime.running(value)
            assert (
                sql(
                    "SELECT count(*) FROM cloud_connector_state WHERE maintenance_operation_id IS NOT NULL"
                )
                == "0"
            )
            # Change live rows *after* restart, before snapshot_payload pg_dump.
            # Restored counts must retain the exported transaction's old view.
            assert api_python(POST_SNAPSHOT_REFRESH).strip() == b"original-post-snapshot-refresh-consumed"
            assert (runtime_data / "external-refresh-count").read_text() == "1"
            assert sql(f"SELECT state FROM cloud_auth_sessions WHERE id='{pending_session_id}'") == "authorized"
            sql("UPDATE cloud_auth_candidates SET state='cleaned'")
            observed.append("restarted-ungated-before-dump")
            return result

        monkeypatch.setattr(backup, "capture_connector_snapshot", observe_capture)
        with (
            backup.locked(root / ".cloud-backup.lock"),
            backup.locked(root / ".backup.lock"),
            backup.locked(root / ".deploy.lock", shared=True),
            backup.locked(root / ".cloud-connector.lock"),
        ):
            manifest = backup.snapshot_payload(args, release, payload)
        assert observed == ["stopped-gated-export", "restarted-ungated-before-dump"]
        assert not (connector_root / "snapshot-state.json").exists()
        assert manifest["database"]["cloud_connector_references"][0]["state"] == "active"
        assert manifest["cloud_connector"]["version"] == 2
        assert sql("SELECT state FROM cloud_auth_candidates") == "cleaned"
        assert "cloud_connector_credentials" not in manifest["database"]
        assert not any(
            secret in json.dumps(manifest)
            for secret in (candidate_password, args.key_file.read_text().strip())
        ), "manifest exposed generated credentials"
        identity = "backup-20261006T000000Z-" + secrets.token_hex(4)
        manifest["backup_id"] = identity
        manifest["files"] = backup.file_inventory(payload)
        assert not any(name.endswith("offline.key") for name in manifest["files"])
        backup.atomic_json(payload / "manifest.json", manifest)
        plain = tmp_path / "archive.tar.gz"
        with tarfile.open(plain, "w:gz") as archive:
            for child in sorted(payload.iterdir()):
                archive.add(child, arcname=child.name)
        backups_root = tmp_path / "backups"
        backups_root.mkdir(mode=0o700)
        complete = backups_root / identity
        complete.mkdir(mode=0o700)
        encrypted = complete / "archive.tar.gz.enc"
        backup.crypt(plain, encrypted, args.key_file)
        key = backup.key_bytes(args.key_file, complete)
        backup.atomic_json(
            complete / "complete.json",
            backup.seal_metadata(
                {
                    "format_version": 1,
                    "backup_id": identity,
                    "sha256": backup.digest(encrypted),
                    "hmac_sha256": backup.digest(encrypted, key),
                },
                key,
            ),
        )
        shutil.rmtree(payload)
        plain.unlink()
        workspace = tmp_path / "verification"
        workspace.mkdir(mode=0o700)
        result = backup.verify_archive(complete, workspace, args, key)
        assert result["verified"] and result["encrypted"]
        assert result["table_count"] == len(manifest["database"]["row_counts"])
        # The helper separately decrypts candidate fields; this counter counts
        # identity/mail samples only.
        assert result["encrypted_samples"] == 1
        assert {path.name for path in complete.iterdir()} == {
            "archive.tar.gz.enc",
            "complete.json",
        }
        restored = workspace / "restore/recovery/cloud-connector"
        assert recovery.inventory(restored) == manifest["cloud_connector"]["files"]
        assert owned_runtime.running(value)
        assert (
            sql(
                "SELECT count(*) FROM cloud_connector_state WHERE maintenance_operation_id IS NOT NULL"
            )
            == "0"
        )
        # The verifier disposes its database. Restore the authenticated dump
        # again into a distinct owned namespace to exercise the supported
        # fresh-host handoff against a genuinely restored running ERP backend.
        fresh_project = project + "-restored"
        fresh_root = tmp_path / "fresh-host"
        fresh_connector_root = fresh_root / "cloud-connector"
        fresh_connector_root.mkdir(parents=True, mode=0o700)
        fresh_data = fresh_connector_root / "data"
        shutil.copytree(restored / "data", fresh_data)
        fresh_data.chmod(0o700)
        fresh_secret = fresh_connector_root / "admin-secret"
        shutil.copyfile(restored / "admin-secret", fresh_secret)
        fresh_secret.chmod(0o600)
        start(
            fresh_project + "-db-1",
            [
                "--network",
                "none",
                "-e",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "-e",
                "POSTGRES_USER=erp",
                "-e",
                "POSTGRES_DB=erp",
                "postgres:16-alpine",
            ],
        )
        for _ in range(60):
            try:
                docker(
                    "exec",
                    fresh_project + "-db-1",
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "erp",
                    "-d",
                    "erp",
                    timeout=5,
                )
                break
            except backup.BackupError:
                time.sleep(0.1)
        else:
            pytest.fail("fresh-host disconnected PostgreSQL did not become ready")
        with (workspace / "restore/database.dump").open("rb") as dump:
            docker(
                "exec",
                "-i",
                fresh_project + "-db-1",
                "pg_restore",
                "-h",
                "127.0.0.1",
                "-U",
                "erp",
                "-d",
                "erp",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                stdin=dump,
            )
        restored_secrets = workspace / "restore/recovery/mounted-api-secrets"
        start(
            fresh_project + "-api-1",
            [
                "--network",
                "container:" + fresh_project + "-db-1",
                "--user",
                "0:0",
                "--env-file",
                str(workspace / "restore/recovery/.env"),
                "--mount",
                f"type=bind,src={ROOT / 'apps/api'},dst=/app,readonly",
                "--mount",
                f"type=bind,src={restored_secrets},dst=/run/secrets,readonly",
                "--entrypoint",
                "python",
                api_image,
                "-c",
                "import time; time.sleep(3600)",
            ],
        )
        fence = manifest["cloud_connector"]["snapshot_fence"]
        assert (
            sql(
                "SELECT maintenance_operation_id FROM cloud_connector_state "
                f"WHERE id='{connector_id}'",
                target=fresh_project,
            )
            == fence["operation_id"]
        )
        assert (
            sql(
                "SELECT maintenance_lease_expires_at="
                f"'{fence['expires_at']}'::timestamptz FROM cloud_connector_state "
                f"WHERE id='{connector_id}'",
                target=fresh_project,
            )
            == "t"
        )
        assert api_python(GATE_PROBE, target=fresh_project).strip() == b"gate-active"
        assert api_python(RESTORED_AUTH_PROBE, target=fresh_project).strip() == b"auth-phases-source-preserved"
        assert sql(f"SELECT state FROM cloud_auth_sessions WHERE id='{pending_session_id}'", target=fresh_project) == "verifying_import"
        assert (runtime_data / "external-refresh-count").read_text() == "1"
        print("joint recovery stage: restored_exact_fence_and_guard_present", flush=True)
        assert sql("SELECT state FROM cloud_auth_candidates", target=fresh_project) == "active"
        fresh_name = fresh_project + "-connector"
        fresh_registry = fresh_connector_root / "registry.json"
        backup.atomic_json(
            fresh_registry,
            {
                **value,
                "container_name": fresh_name,
                "data_dir": str(fresh_data),
                "admin_secret_file": str(fresh_secret),
            },
        )
        names.append(fresh_name)
        docker(
            "create",
            "--name",
            fresh_name,
            "--network",
            "none",
            "--log-driver",
            "none",
            "--label",
            "erp.joint.recovery=" + owner,
            "--label",
            "erp.cloud.connector=" + connector_id,
            "--label",
            "erp.cloud.dedicated=true",
            "--user",
            recovery.container_bind_user(fresh_data, recovery.IMAGE),
            "--mount",
            f"type=bind,src={fresh_data},dst=/opt/openlist/data",
            recovery.IMAGE,
        )
        fresh_value = recovery.load_registry(fresh_registry)
        assert not owned_runtime.running(fresh_value)
        assert not (fresh_connector_root / "snapshot-state.json").exists()
        foreign_connector, foreign_operation = str(uuid4()), str(uuid4())
        sql(
            "INSERT INTO cloud_connector_state "
            "(id,registered,maintenance_operation_id,maintenance_lease_expires_at) VALUES "
            f"('{foreign_connector}',true,'{foreign_operation}','2099-01-01T00:00:00Z')",
            target=fresh_project,
        )
        handoff_args = SimpleNamespace(
            app_dir=fresh_root,
            project=fresh_project,
            backup_dir=backups_root,
            key_file=args.key_file,
            backup_id=identity,
        )
        handoff = getattr(backup, "recover_connector", None)
        assert callable(handoff), "supported authenticated fresh-host connector handoff is missing"
        handoff(handoff_args)  # Actual archive authentication, journal and private backend RPC.
        assert api_python(RESTORED_IMPORT_CLOSED_PROBE, {"pending_session_id": pending_session_id},
                          target=fresh_project).strip() == b"rollback-import-closed-before-release-active-preserved"
        assert (runtime_data / "external-refresh-count").read_text() == "1"
        assert owned_runtime.running(fresh_value)
        assert not (fresh_connector_root / "snapshot-state.json").exists()
        assert (
            sql(
                "SELECT count(*) FROM cloud_connector_state "
                f"WHERE id='{connector_id}' AND maintenance_operation_id IS NOT NULL",
                target=fresh_project,
            )
            == "0"
        )
        assert (
            sql(
                "SELECT maintenance_operation_id FROM cloud_connector_state "
                f"WHERE id='{foreign_connector}'",
                target=fresh_project,
            )
            == foreign_operation
        )
        # A replay of the older authenticated archive must not release a newer
        # operation on this same connector either.
        newer_operation = str(uuid4())
        sql(
            "UPDATE cloud_connector_state SET "
            f"maintenance_operation_id='{newer_operation}',maintenance_lease_expires_at='2099-01-02T00:00:00Z' "
            f"WHERE id='{connector_id}'",
            target=fresh_project,
        )
        with pytest.raises(backup.BackupError, match="^connector_handoff_failed$"):
            handoff(handoff_args)
        refused_journal = json.loads((fresh_connector_root / "snapshot-state.json").read_text())
        assert refused_journal["phase"] == "release_pending"
        assert refused_journal["intent"] == "restore_handoff"
        assert refused_journal["fence"] == fence
        assert owned_runtime.running(fresh_value)
        assert (
            sql(
                "SELECT maintenance_operation_id FROM cloud_connector_state "
                f"WHERE id='{connector_id}'",
                target=fresh_project,
            )
            == newer_operation
        )
        assert (
            sql(
                "SELECT maintenance_operation_id FROM cloud_connector_state "
                f"WHERE id='{foreign_connector}'",
                target=fresh_project,
            )
            == foreign_operation
        )
        print(
            "joint recovery verified: "
            f"tables={result['table_count']} identity_samples={result['encrypted_samples']} "
            "candidate_credentials=2 connector=Local exported_snapshot_preserved=true "
            "restored_fence_released=true foreign_fences_preserved=true",
            flush=True,
        )
    finally:
        cleanup_joint_resources(names, owner, tmp_path, original_error=sys.exception())
