"""Real DB checkpoints with synthetic provider I/O and injected process failures."""

import importlib.util
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import AuthSession
from app.core.config import get_settings
from app.operations import cloud_auth_worker as worker
from app.operations.cloud_auth_bridge import REQUEST_ADAPTER
from app.operations.cloud_auth_models import CloudAuthOperation, CloudAuthSession
from app.operations.cloud_auth_schemas import AuthFence
from app.operations.cloud_auth_security import import_token_digests, store_import_token_digests
from tests.api.conftest import platform_users as _platform_users

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def imported(db):
    users = _platform_users.__wrapped__(db)
    now = datetime.now(UTC)
    account_id = users["accounts"]["admin"].id
    login = AuthSession(
        user_id=account_id,
        token_digest=uuid4().hex,
        csrf_digest=uuid4().hex,
        expires_at=now + timedelta(hours=1),
    )
    db.add(login)
    db.flush()
    session = CloudAuthSession(
        id=uuid4(),
        provider="baidu",
        mode="openlist_builtin_import",
        protocol_version=2,
        application_provider=None,
        auth_config_id="openlist-builtin-import-v1:baidu",
        auth_config_revision=1,
        app_revision=None,
        owner_account_id=account_id,
        owner_login_session_id=login.id,
        state="verifying_import",
        revision=1,
        target_revision=0,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(session)
    db.flush()
    operation = worker.queue(db, session, "verify_import", now=now)
    # Intentional direct envelope: the producer must bind the persisted source, not legacy defaults.
    from app.operations.cloud_auth_state import aad

    operation.payload_ciphertext = worker.cipher().encrypt(
        json.dumps({"access_token": "old-access", "refresh_token": "old-refresh"}),
        field_name=aad("baidu", operation.id, "payload", mode=session.mode, protocol_version=2),
    )
    store_import_token_digests(
        operation,
        import_token_digests(get_settings(), "baidu", "old-access", "old-refresh"),
        worker.cipher(),
    )
    db.flush()
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "import_runner", ROOT / "scripts/erp-cloud-authorize.py"
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return now, session, operation, runner


def checkpoint(db, work, phase, now, **extra):
    from app.operations.cloud_auth_import_worker import refresh_checkpoint

    request = REQUEST_ADAPTER.validate_python(
        {
            "action": "refresh_checkpoint",
            "fence": work["fence"],
            "operation_id": work["id"],
            "phase": phase,
            **extra,
        }
    )
    return refresh_checkpoint(db, request, now=now)


def identity():
    return {"provider": "baidu", "account_id": "12345", "display_name": "Synthetic"}


def rotated(now):
    return {
        "provider": "baidu",
        "mode": "openlist_builtin_import",
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "expires_at": None,
        "expiry_source": "unreported",
        "refreshed_at": now.isoformat(),
    }


def install_provider(monkeypatch, *, fail_identity=False):
    from cloud_auth import builtin_import
    from cloud_auth.provider_types import AccountIdentity, ImportedTokenGrant, ProviderError

    calls = {"refresh": 0, "identity": []}

    def get_identity(provider, token):
        calls["identity"].append(token)
        if fail_identity and token == "new-access":
            raise ProviderError("provider_network_error")
        return AccountIdentity(provider, "12345", "Synthetic")

    def refresh(provider, token, *, now):
        assert token == "old-refresh"
        calls["refresh"] += 1
        return ImportedTokenGrant("new-access", "new-refresh", None, "unreported", now, provider)

    monkeypatch.setattr(builtin_import, "get_import_identity", get_identity)
    monkeypatch.setattr(builtin_import, "refresh_import", refresh)
    return calls


def rpc(db, work, now, *, fail_phase=None):
    def call(config, action, **kwargs):
        assert action == "refresh_checkpoint"
        if kwargs["phase"] == fail_phase:
            raise RuntimeError("SYNTHETIC_PRIVATE_ERROR")
        return checkpoint(
            db,
            work,
            kwargs.pop("phase"),
            now,
            **{key: value for key, value in kwargs.items() if key not in {"fence", "operation_id"}},
        )

    return call


def test_sent_refresh_is_not_replayed_after_lease_expiry(db, imported, monkeypatch):
    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    work = worker.claim_auth_operation(db, now=now)
    assert work is not None and work["action"] == "verify_import"
    with pytest.raises(runner.AuthRunnerError, match="cloud_refresh_outcome_unknown"):
        runner.perform({}, work, rpc(db, work, now, fail_phase="refreshed"))
    assert operation.refresh_phase == "sent" and calls["refresh"] == 1
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=91)) is None
    assert operation.refresh_phase == "unknown" and operation.status == "failed"
    assert session.error_code == "cloud_refresh_outcome_unknown"
    assert calls["refresh"] == 1
    assert operation.payload_ciphertext is None and operation.refresh_checkpoint_ciphertext is None


def test_persisted_refresh_resumes_identity_without_second_refresh(db, imported, monkeypatch):
    now, _, operation, runner = imported
    calls = install_provider(monkeypatch, fail_identity=True)
    work = worker.claim_auth_operation(db, now=now)
    with pytest.raises(runner.AuthRunnerError, match="cloud_auth_bridge_failed"):
        runner.perform({}, work, rpc(db, work, now))
    assert operation.refresh_phase == "refreshed" and operation.payload_ciphertext is None
    calls = install_provider(monkeypatch)
    resumed = worker.claim_auth_operation(db, now=now + timedelta(seconds=91))
    result = runner.perform({}, resumed, rpc(db, resumed, now + timedelta(seconds=91)))
    assert result["action"] == "verify_import" and operation.refresh_phase == "verified"
    assert calls == {"refresh": 0, "identity": ["new-access"]}


def test_baidu_unreported_expiry_never_changes_configuration_deadline(db, imported):
    now, session, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity())["ok"]
    assert checkpoint(db, work, "refreshed", now, tokens=rotated(now))["ok"]
    verified_at = now + timedelta(seconds=2)
    assert checkpoint(db, work, "verified", verified_at, identity=identity())["ok"]
    source = {
        key: work[key]
        for key in (
            "protocol_version",
            "mode",
            "auth_config_id",
            "auth_config_revision",
            "app_revision",
        )
    }
    assert worker.complete_auth_operation(
        db,
        AuthFence.model_validate(work["fence"]),
        {"action": "verify_import", **source},
        now=now + timedelta(seconds=5),
    )
    assert session.token_expires_at is None and session.expiry_source == "unreported"
    assert session.expires_at == verified_at + timedelta(seconds=600)
    assert operation.status == "succeeded" and operation.refresh_checkpoint_ciphertext is None
    assert (
        len(
            db.scalars(
                select(CloudAuthOperation).where(
                    CloudAuthOperation.action == "prepare",
                    CloudAuthOperation.session_id == session.id,
                )
            ).all()
        )
        == 1
    )


def test_sent_permission_is_never_granted_twice_on_lost_checkpoint_reply(db, imported):
    now, _, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity()) == {"ok": True, "phase": "sent"}
    assert checkpoint(db, work, "sent", now, identity=identity()) == {"ok": False}
    assert operation.refresh_phase == "sent"


def test_source_mismatch_rejects_renew_complete_and_checkpoint(db, imported):
    now, _, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    work["fence"]["auth_config_id"] = "openlist-builtin-import-v1:115"
    fence = AuthFence.model_validate(work["fence"])
    assert not worker.renew_auth_operation(db, fence, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity()) == {"ok": False}
    assert operation.refresh_phase == "not_started"


@pytest.mark.parametrize(
    "boundary", ["before_sent", "lost_sent_reply", "lost_verified_reply", "lease_lost"]
)
def test_crash_boundaries_never_repeat_external_refresh(db, imported, monkeypatch, boundary):
    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    work = worker.claim_auth_operation(db, now=now)
    base_call = rpc(db, work, now)

    def crashing(config, action, **payload):
        phase = payload["phase"]
        if boundary == "before_sent" and phase == "sent":
            raise RuntimeError("synthetic process exit")
        result = base_call(config, action, **payload)
        if (boundary == "lost_sent_reply" and phase == "sent") or (
            boundary == "lost_verified_reply" and phase == "verified"
        ):
            raise RuntimeError("synthetic reply loss")
        if boundary == "lease_lost" and phase == "refreshed":
            operation.lease_expires_at = now
            db.flush()
        return result

    with pytest.raises(RuntimeError):
        runner.perform({}, work, crashing)
    resumed = worker.claim_auth_operation(db, now=now + timedelta(seconds=91))
    if boundary == "lost_sent_reply":
        assert resumed is None and session.error_code == "cloud_refresh_outcome_unknown"
        assert calls["refresh"] == 0
        return
    assert resumed is not None
    identities_before = len(calls["identity"])
    result = runner.perform({}, resumed, rpc(db, resumed, now + timedelta(seconds=91)))
    assert calls["refresh"] == 1 and operation.refresh_phase == "verified"
    if boundary == "lost_verified_reply":
        assert len(calls["identity"]) == identities_before
    assert worker.complete_auth_operation(
        db, AuthFence.model_validate(resumed["fence"]), result, now=now + timedelta(seconds=91)
    )


def test_identity_mismatch_fails_without_handoff_and_keeps_digest_history(
    db, imported, monkeypatch
):
    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    from cloud_auth import builtin_import
    from cloud_auth.provider_types import AccountIdentity

    monkeypatch.setattr(
        builtin_import,
        "get_import_identity",
        lambda p, token: AccountIdentity(p, "12345" if token == "old-access" else "99999"),
    )
    work = worker.claim_auth_operation(db, now=now)
    runner.execute({}, work, rpc_with_completion(db, work, now))
    assert calls["refresh"] == 1
    assert operation.status == "failed" and session.state == "failed"
    assert session.error_code == "cloud_import_account_mismatch"
    assert operation.refresh_checkpoint_ciphertext is None and operation.payload_ciphertext is None
    assert operation.import_token_digests_ciphertext is not None
    assert not db.scalar(
        select(CloudAuthOperation.id).where(CloudAuthOperation.action == "prepare")
    )


def rpc_with_completion(db, work, now):
    base_call = rpc(db, work, now)

    def call(config, action, **payload):
        if action == "complete":
            return {
                "ok": worker.complete_auth_operation(
                    db, AuthFence.model_validate(payload["fence"]), payload["result"], now=now
                )
            }
        return base_call(config, action, **payload)

    return call


def test_lost_completion_reply_resumes_handoff_only_and_keeps_first_deadline(
    db, imported, monkeypatch
):
    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    work = worker.claim_auth_operation(db, now=now)
    result = runner.perform({}, work, rpc(db, work, now))
    assert operation.refresh_phase == "verified"
    resumed = worker.claim_auth_operation(db, now=now + timedelta(seconds=91))
    result = runner.perform({}, resumed, rpc(db, resumed, now + timedelta(seconds=91)))
    assert calls == {"refresh": 1, "identity": ["old-access", "new-access"]}
    assert worker.complete_auth_operation(
        db, AuthFence.model_validate(resumed["fence"]), result, now=now + timedelta(seconds=91)
    )
    assert session.expires_at == now + timedelta(seconds=600)
    assert not worker.complete_auth_operation(
        db, AuthFence.model_validate(resumed["fence"]), result, now=now + timedelta(seconds=92)
    )
    assert session.expires_at == now + timedelta(seconds=600)


def test_completion_committed_before_reply_loss_cannot_queue_second_handoff(
    db, imported, monkeypatch
):
    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    work = worker.claim_auth_operation(db, now=now)
    base_call = rpc_with_completion(db, work, now)

    def lost_reply(config, action, **payload):
        reply = base_call(config, action, **payload)
        if action == "complete":
            assert reply["ok"]
            raise RuntimeError("synthetic post-commit reply loss")
        return reply

    with pytest.raises(RuntimeError, match="synthetic post-commit reply loss"):
        runner.execute({}, work, lost_reply)
    assert operation.status == "succeeded" and calls["refresh"] == 1
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=91)) is None
    assert (
        len(
            db.scalars(
                select(CloudAuthOperation).where(
                    CloudAuthOperation.action == "prepare",
                    CloudAuthOperation.session_id == session.id,
                )
            ).all()
        )
        == 1
    )


@pytest.mark.parametrize("token", ["old-access", "old-refresh", "new-access", "new-refresh"])
def test_durable_send_rechecks_original_and_rotated_hmac_history(db, imported, token):
    now, session, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity())["ok"]
    assert checkpoint(db, work, "refreshed", now, tokens=rotated(now))["ok"]
    operation.status, session.state = "failed", "failed"
    db.flush()
    # Another session was queued while the same credential had not yet been consumed.
    new_session = CloudAuthSession(
        id=uuid4(),
        provider=session.provider,
        mode=session.mode,
        protocol_version=2,
        application_provider=None,
        auth_config_id=session.auth_config_id,
        auth_config_revision=1,
        app_revision=None,
        owner_account_id=session.owner_account_id,
        owner_login_session_id=session.owner_login_session_id,
        state="verifying_import",
        revision=1,
        target_revision=0,
        expires_at=now + timedelta(minutes=10),
    )
    db.add(new_session)
    db.flush()
    new_operation = worker.queue(db, new_session, "verify_import", now=now)
    new_operation.payload_ciphertext = worker.encrypt(
        new_operation, "payload", {"access_token": token, "refresh_token": "unrelated-refresh"}
    )
    next_work = worker.claim_auth_operation(db, now=now)
    reply = checkpoint(db, next_work, "sent", now, identity=identity())
    assert reply == {"ok": False, "error_code": "cloud_import_credentials_reused"}
    assert new_operation.refresh_phase == "not_started" and new_operation.status == "failed"
    assert new_operation.payload_ciphertext is None


def test_refresh_network_failure_is_terminal_unknown_even_when_completion_arrives(
    db, imported, monkeypatch
):
    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    from cloud_auth import builtin_import
    from cloud_auth.provider_types import ProviderError

    def fail_refresh(provider, refresh_token, *, now):
        calls["refresh"] += 1
        raise ProviderError("provider_network_error")

    monkeypatch.setattr(builtin_import, "refresh_import", fail_refresh)
    work = worker.claim_auth_operation(db, now=now)
    runner.execute({}, work, rpc_with_completion(db, work, now))
    assert operation.status == "failed" and operation.refresh_phase == "unknown"
    assert session.error_code == "cloud_refresh_outcome_unknown"
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=91)) is None
    assert calls["refresh"] == 1 and operation.payload_ciphertext is None


def test_candidate_and_import_refresh_checkpoints_remain_distinct(db, imported):
    now, _, operation, _ = imported
    from app.operations.cloud_auth_bridge import CheckpointRequest

    work = worker.claim_auth_operation(db, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity())["ok"]
    assert worker.checkpoint(
        db,
        CheckpointRequest.model_validate(
            {
                "action": "checkpoint",
                "fence": work["fence"],
                "operation_id": work["id"],
                "kind": "read",
            }
        ),
        now=now,
    ) == {"ok": False}
    assert operation.refresh_phase == "sent" and operation.result_ciphertext is None


def test_cancelled_refresh_clears_temporary_secrets_not_candidate_recovery(db, imported):
    now, session, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity())["ok"]
    assert checkpoint(db, work, "refreshed", now, tokens=rotated(now))["ok"]
    worker.retire(db, session, now=now, code="cloud_auth_cancelled")
    assert operation.payload_ciphertext is None and operation.refresh_checkpoint_ciphertext is None
    assert operation.import_token_digests_ciphertext is not None


def test_superseded_import_operation_clears_raw_credentials_without_retiring_new_revision(
    db, imported
):
    now, session, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    session.revision += 1
    db.flush()
    assert checkpoint(db, work, "sent", now, identity=identity()) == {"ok": False}
    assert operation.status == "cancelled" and operation.payload_ciphertext is None
    assert operation.refresh_checkpoint_ciphertext is None
    assert session.state == "verifying_import" and session.revision == 2


def test_result_source_mismatch_cannot_complete_verified_import(db, imported):
    now, session, operation, _ = imported
    work = worker.claim_auth_operation(db, now=now)
    assert checkpoint(db, work, "sent", now, identity=identity())["ok"]
    assert checkpoint(db, work, "refreshed", now, tokens=rotated(now))["ok"]
    assert checkpoint(db, work, "verified", now, identity=identity())["ok"]
    source = {key: work[key] for key in worker.SOURCE_FIELDS}
    source["auth_config_id"] = "openlist-builtin-import-v1:115"
    assert not worker.complete_auth_operation(
        db, AuthFence.model_validate(work["fence"]), {"action": "verify_import", **source}, now=now
    )
    assert session.state == "verifying_import" and operation.refresh_phase == "verified"


def test_builtin_runner_delivers_only_latest_tokens_through_source_bound_bridge(
    db, imported, monkeypatch, tmp_path
):
    from cloud_auth import builtin_import
    from cloud_auth.provider_types import AccountIdentity

    from app.operations.cloud_auth_bridge import serve
    from tests.cli.test_cloud_connector_candidates import API

    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    refresh = builtin_import.refresh_import

    def durable_refresh(provider, value, *, now):
        # This assertion observes the real Session transaction owner after RPC returns.
        # A checkpoint merely flushed within an open transaction would fail here.
        assert not db.in_transaction()
        return refresh(provider, value, now=now)

    monkeypatch.setattr(builtin_import, "refresh_import", durable_refresh)
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    secret = tmp_path / "admin-secret"
    secret.write_text("synthetic-installed-secret")
    monkeypatch.setattr(
        runner,
        "load_registry",
        lambda path: {
            "connector_id": str(connector_id),
            "origin": "http://127.0.0.1:5244",
            "admin_secret_file": str(secret),
            "connector_version": "4.2.6",
        },
    )
    monkeypatch.setattr(
        runner,
        "provider_for",
        lambda provider: SimpleNamespace(
            get_identity=lambda tokens: AccountIdentity(provider, "12345")
        ),
    )

    class PersistedJournal:
        @property
        def records(self):
            return {
                row.id: runner.ownership(worker.decrypt(row, "journal", row.result_ciphertext))
                for row in db.scalars(
                    select(CloudAuthOperation).where(
                        CloudAuthOperation.action == "prepare",
                        CloudAuthOperation.result_ciphertext.is_not(None),
                    )
                )
            }

    api = API(PersistedJournal())
    monkeypatch.setattr(
        runner, "OpenListAPI", SimpleNamespace(from_admin_password=lambda connector, secret: api)
    )

    def rpc(config, action, **payload):
        def dispatch(request):
            if request.action == "refresh_checkpoint":
                from app.operations.cloud_auth_import_worker import refresh_checkpoint

                return refresh_checkpoint(db, request, now=now)
            if request.action == "checkpoint":
                return worker.checkpoint(db, request, now=now)
            if request.action == "complete":
                return {
                    "ok": worker.complete_auth_operation(db, request.fence, request.result, now=now)
                }
            raise AssertionError(request.action)

        output = io.StringIO()
        raw = json.dumps({"action": action, **payload}, default=runner.wire).encode()
        assert serve(io.BytesIO(raw), output, dispatch=dispatch) == 0
        result = json.loads(output.getvalue())
        assert result["ok"]
        return result

    config = {"app_dir": str(tmp_path)}
    work = worker.claim_auth_operation(db, now=now)
    runner.execute(config, work, rpc)
    assert operation.status == "succeeded" and session.token_ciphertext is not None
    prepared = worker.claim_auth_operation(db, now=now)
    assert prepared["action"] == "prepare" and "application" not in prepared
    assert prepared["mode"] == prepared["candidate"]["mode"] == "openlist_builtin_import"
    runner.execute(config, prepared, rpc)
    assert session.state == "configuring" and session.token_ciphertext is None
    addition = json.loads(api.objects["storage"][0]["addition"])
    assert addition["AccessToken"] == "new-access" and addition["refresh_token"] == "new-refresh"
    assert addition["use_online_api"] is True and "client_id" not in addition
    assert calls["refresh"] == 1
    # An expired stale verify lease cannot reacquire tokens after connector ownership.
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=91)) is None
    assert operation.payload_ciphertext is None and operation.refresh_checkpoint_ciphertext is None


@pytest.mark.parametrize("phase", ["not_started", "sent", "refreshed", "verified"])
def test_database_rollback_restore_never_replays_external_refresh_or_delivery(
    db, imported, monkeypatch, tmp_path, phase
):
    """PostgreSQL savepoint rolls back actual checkpoints, not external drive history."""
    from cloud_auth import builtin_import
    from cloud_auth.provider_types import AccountIdentity

    from app.operations.cloud_auth_bridge import SnapshotFence
    from tests.cli.test_cloud_connector_candidates import API

    now, session, operation, runner = imported
    calls = install_provider(monkeypatch)
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    secret = tmp_path / "admin-secret"
    secret.write_text("synthetic-installed-secret")
    monkeypatch.setattr(runner, "load_registry", lambda path: {
        "connector_id": str(connector_id), "origin": "http://127.0.0.1:5244",
        "admin_secret_file": str(secret), "connector_version": "4.2.6",
    })
    monkeypatch.setattr(runner, "provider_for", lambda provider: SimpleNamespace(
        get_identity=lambda tokens: AccountIdentity(provider, "12345")
    ))

    class Journal:
        @property
        def records(self):
            return {row.id: runner.ownership(worker.decrypt(row, "journal", row.result_ciphertext))
                    for row in db.scalars(select(CloudAuthOperation).where(
                        CloudAuthOperation.action == "prepare",
                        CloudAuthOperation.result_ciphertext.is_not(None)))}

    api = API(Journal())
    monkeypatch.setattr(runner, "OpenListAPI", SimpleNamespace(from_admin_password=lambda *_: api))
    clock = [now]

    def rpc(config, action, **payload):
        request = REQUEST_ADAPTER.validate_python({"action": action, **json.loads(json.dumps(payload, default=runner.wire))})
        if action == "refresh_checkpoint":
            from app.operations.cloud_auth_import_worker import refresh_checkpoint
            return refresh_checkpoint(db, request, now=clock[0])
        if action == "checkpoint":
            return worker.checkpoint(db, request, now=clock[0])
        if action == "complete":
            return {"ok": worker.complete_auth_operation(db, request.fence, request.result, now=clock[0])}
        raise AssertionError(action)

    # Explicit transaction owns this real earlier DB state throughout nested rollback.
    db.commit()
    with db.begin():
        work = worker.claim_auth_operation(db, now=now)
        if phase != "not_started":
            assert checkpoint(db, work, "sent", now, identity=identity())["ok"]
        if phase in {"refreshed", "verified"}:
            builtin_import.refresh_import("baidu", "old-refresh", now=now)
            assert checkpoint(db, work, "refreshed", now, tokens=rotated(now))["ok"]
        if phase == "verified":
            assert checkpoint(db, work, "verified", now, identity=identity())["ok"]
        snapshot = worker.begin_snapshot(db, connector_id, uuid4(), now=now)
        fence = SnapshotFence.model_validate(snapshot["fence"])
        original_host = db.begin_nested()
        assert worker.end_snapshot(db, fence, now=now)["released"]
        if phase == "sent":
            builtin_import.refresh_import("baidu", "old-refresh", now=now)
            assert checkpoint(db, work, "refreshed", now, tokens=rotated(now))["ok"]
        work["refresh_phase"] = operation.refresh_phase
        work["refresh_checkpoint"] = worker.decrypt(operation, "refresh_checkpoint", operation.refresh_checkpoint_ciphertext)
        runner.execute({"app_dir": str(tmp_path)}, work, rpc)
        prepared = worker.claim_auth_operation(db, now=now)
        assert prepared["action"] == "prepare"
        runner.execute({"app_dir": str(tmp_path)}, prepared, rpc)
        assert calls["refresh"] == 1 and len(api.objects["storage"]) == 1
        # Connector consumption exists outside PostgreSQL's rollback boundary.
        api.objects["storage"][0]["addition"] = json.dumps({"refresh_token": "connector-consumed"})
        original_host.rollback()
        db.expire_all()
        assert operation.refresh_phase == phase and operation.status == "running"
        assert session.state == "verifying_import"
        # Before the fix this supported restore boundary was ordinary end_snapshot.
        assert worker.restore_snapshot(db, fence, now=now)["released"]
        assert session.state == "failed", "restored import must close before gate release"
        clock[0] = now + timedelta(seconds=91)
        replay = worker.claim_auth_operation(db, now=clock[0])
        if replay is not None:
            runner.execute({"app_dir": str(tmp_path)}, replay, rpc)
            replay = worker.claim_auth_operation(db, now=clock[0])
            if replay is not None:
                runner.execute({"app_dir": str(tmp_path)}, replay, rpc)
        assert calls["refresh"] == 1, "restoration replayed externally consumed old refresh"
        assert len(api.objects["storage"]) == 1, "restoration delivered a stale refresh chain"
        assert session.state == "failed" and session.error_code == "cloud_refresh_outcome_unknown"
        assert operation.payload_ciphertext is None and operation.refresh_checkpoint_ciphertext is None


def test_restore_lost_ack_replay_does_not_retire_new_authorization(db, imported):
    from app.operations.cloud_auth_bridge import SnapshotFence
    from app.operations.cloud_auth_schemas import AuthActor
    from app.operations.cloud_auth_service import CloudAuthorizationService
    from app.security.encryption import EnvelopeCipher

    now, session, operation, _ = imported
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    fence = SnapshotFence.model_validate(worker.begin_snapshot(db, connector_id, uuid4(), now=now)["fence"])
    assert worker.restore_snapshot(db, fence, now=now)["released"]
    assert session.state == "failed" and operation.status == "cancelled"
    service = CloudAuthorizationService(db, cipher=EnvelopeCipher(bytes(range(32))))
    view = service.create_session("baidu", AuthActor(account_id=session.owner_account_id,
        login_session_id=session.owner_login_session_id), uuid4(), mode="openlist_builtin_import")
    fresh = db.get(CloudAuthSession, view.id)
    before = (fresh.state, fresh.revision, fresh.expires_at)
    # Host lost the response, durably retains restore intent, and retries after
    # the administrator already obtained a fresh new authorization session.
    assert worker.restore_snapshot(db, fence, now=now + timedelta(seconds=1))["released"]
    assert (fresh.state, fresh.revision, fresh.expires_at) == before
    new_fence = SnapshotFence.model_validate(worker.begin_snapshot(db, connector_id, uuid4(), now=now)["fence"])
    assert worker.restore_snapshot(db, fence, now=now) == {"ok": False, "released": False}
    assert (fresh.state, fresh.revision, fresh.expires_at) == before
    assert worker.end_snapshot(db, new_fence, now=now)["released"]
    assert (fresh.state, fresh.revision, fresh.expires_at) == before


def test_restore_retirement_failure_rolls_back_exact_gate_and_import(db, imported, monkeypatch):
    from app.operations.cloud_auth_bridge import SnapshotFence
    from app.operations.cloud_auth_models import CloudConnectorState

    now, session, operation, _ = imported
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    fence = SnapshotFence.model_validate(worker.begin_snapshot(db, connector_id, uuid4(), now=now)["fence"])
    payload = operation.payload_ciphertext
    original = worker.retire

    def fail_after_retirement(*args, **kwargs):
        original(*args, **kwargs)
        db.flush()
        raise RuntimeError("synthetic-restore-write-failure")

    monkeypatch.setattr(worker, "retire", fail_after_retirement)
    with pytest.raises(RuntimeError, match="synthetic-restore-write-failure"):
        worker.restore_snapshot(db, fence, now=now)
    assert db.get(CloudConnectorState, connector_id).maintenance_operation_id == fence.operation_id
    assert session.state == "verifying_import" and operation.status == "queued"
    assert operation.payload_ciphertext == payload


def test_restore_preserves_active_target_and_candidate_cleanup_journal(db, imported):
    from app.operations.cloud_auth_bridge import SnapshotFence
    from app.operations.cloud_auth_models import CloudAuthCandidate
    from app.operations.cloud_models import CloudBackupTarget

    now, session, operation, _ = imported
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    active = CloudAuthSession(provider="baidu", **worker.source_fields(session),
        owner_account_id=session.owner_account_id, owner_login_session_id=session.owner_login_session_id,
        state="applied", expires_at=now + timedelta(minutes=10))
    db.add(active); db.flush()
    candidate = CloudAuthCandidate(provider="baidu", **worker.source_fields(active),
        session_id=active.id, operation_id=uuid4(), connector_id=connector_id,
        mount_path="/old-active", state="active")
    db.add(candidate); db.flush()
    active.candidate_id = candidate.id
    target = CloudBackupTarget(provider="baidu", enabled=False, remote_path="/old-backups",
        username_ciphertext="protected-old-username", password_ciphertext="protected-old-password",
        managed_candidate_id=candidate.id, authorization_status="valid")
    db.add(target)
    prepared = worker.queue(db, session, "prepare", now=now)
    pending = CloudAuthCandidate(provider="baidu", **worker.source_fields(session),
        session_id=session.id, operation_id=prepared.id, connector_id=connector_id,
        mount_path="/pending", state="preparing")
    db.add(pending); db.flush()
    session.candidate_id = prepared.candidate_id = pending.id
    prepared.result_ciphertext = worker.encrypt(prepared, "journal", {"creation_unknown": True})
    retained = prepared.result_ciphertext
    fence = SnapshotFence.model_validate(worker.begin_snapshot(db, connector_id, uuid4(), now=now)["fence"])
    assert worker.restore_snapshot(db, fence, now=now)["released"]
    assert (active.state, candidate.state, target.remote_path, target.managed_candidate_id) == (
        "applied", "active", "/old-backups", candidate.id)
    assert not target.enabled and target.password_ciphertext == "protected-old-password"
    assert pending.state == "cleanup_pending" and prepared.result_ciphertext == retained
    assert worker.decrypt(prepared, "journal", retained) == {"creation_unknown": True}
    assert prepared.status == "cancelled" and operation.status == "cancelled"
    cleanup = db.scalar(select(CloudAuthOperation).where(CloudAuthOperation.candidate_id == pending.id,
        CloudAuthOperation.action == "cleanup"))
    assert cleanup is not None and cleanup.status == "queued"


@pytest.mark.parametrize("invalid", ["connector", "operation", "expiry", "unregistered", "source"])
def test_restore_validation_failure_retains_exact_gate_and_pending_import(db, imported, invalid):
    from app.core.errors import ProblemError
    from app.operations.cloud_auth_bridge import SnapshotFence
    from app.operations.cloud_auth_models import CloudConnectorState

    now, session, operation, _ = imported
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    original = SnapshotFence.model_validate(worker.begin_snapshot(db, connector_id, uuid4(), now=now)["fence"])
    fence = original
    if invalid in {"connector", "operation", "expiry"}:
        key = {"connector": "connector_id", "operation": "operation_id", "expiry": "expires_at"}[invalid]
        fence = original.model_copy(update={key: original.expires_at + timedelta(seconds=1) if key == "expires_at" else uuid4()})
    elif invalid == "unregistered":
        db.get(CloudConnectorState, connector_id).registered = False
        db.flush()
    else:
        operation.mode, operation.auth_config_id = "own_app", "own-app:baidu"
        operation.auth_config_revision = operation.app_revision = 1
        db.flush()  # Valid row format but false linked provenance.
    if invalid == "source":
        with pytest.raises(ProblemError) as failure:
            worker.restore_snapshot(db, fence, now=now)
        assert failure.value.code == "cloud_auth_revision_changed"
    else:
        assert worker.restore_snapshot(db, fence, now=now) == {"ok": False, "released": False}
    assert db.get(CloudConnectorState, connector_id).maintenance_operation_id == original.operation_id
    assert session.state == "verifying_import" and operation.status == "queued"
    assert operation.payload_ciphertext is not None
