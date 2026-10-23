from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.auth.models import AuthSession
from app.operations import cloud_auth_worker as worker
from app.operations.cloud_auth_models import (
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
)
from app.operations.cloud_auth_requests import CloudDirectorySelection
from app.operations.cloud_auth_schemas import AuthActor, AuthFence, CloudAppUpdate
from app.operations.cloud_auth_service import CloudAuthorizationService
from app.security.encryption import EnvelopeCipher
from tests.api.conftest import platform_users as _platform_users


@pytest.fixture(name="platform_users")
def users(db):
    return _platform_users.__wrapped__(db)


@pytest.fixture
def setup(db, platform_users):
    now = datetime.now(UTC)
    account_id = platform_users["accounts"]["admin"].id
    login = AuthSession(
        user_id=account_id,
        token_digest=uuid4().hex,
        csrf_digest=uuid4().hex,
        expires_at=now + timedelta(hours=1),
    )
    db.add(login)
    db.flush()
    actor = AuthActor(account_id=account_id, login_session_id=login.id)
    connector_id = uuid4()
    worker.heartbeat(db, now=now, connector_id=connector_id)
    service = CloudAuthorizationService(db, cipher=EnvelopeCipher(bytes(range(32))))
    service.save_application(
        "baidu",
        actor,
        CloudAppUpdate(client_id="own-app", client_secret="my-secret", expected_revision=0),
    )
    view = service.create_session("baidu", actor, uuid4())
    session = db.get(CloudAuthSession, view.id)
    return now, actor, login, service, session, connector_id


def grant(now):
    expiry = (now + timedelta(minutes=9)).isoformat()
    return {
        "action": "create_qr",
        "grant": {
            "secret_payload": {"device_code": "private-device"},
            "qr": {
                "kind": "payload",
                "content": "https://openapi.baidu.com/device?code=short",
                "expires_at": expiry,
            },
            "expires_at": expiry,
            "interval_seconds": 5,
        },
    }


def complete(db, work, result, now):
    if work.get("protocol_version") == 2:
        result = {**result, **{key: work[key] for key in worker.SOURCE_FIELDS}}
    return worker.complete_auth_operation(
        db, AuthFence.model_validate(work["fence"]), result, now=now
    )


def authorize(db, setup):
    now, _, _, _, _, _ = setup
    now += timedelta(seconds=1)
    work = worker.claim_auth_operation(db, now=now)
    assert complete(db, work, grant(now), now)
    poll = worker.claim_auth_operation(db, now=now + timedelta(seconds=5))
    assert complete(
        db,
        poll,
        {
            "action": "poll",
            "poll": {
                "status": "authorized",
                "tokens": {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "expires_at": (now + timedelta(hours=1)).isoformat(),
                },
            },
        },
        now + timedelta(seconds=5),
    )
    return worker.claim_auth_operation(db, now=now + timedelta(seconds=6))


def test_expired_lease_reclaims_with_new_fence_and_old_result_cannot_mutate(db, setup):
    now = setup[0] + timedelta(seconds=1)
    first = worker.claim_auth_operation(db, now=now)
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=1)) is None
    second = worker.claim_auth_operation(db, now=now + timedelta(seconds=90))
    assert first["fence"]["lease_id"] != second["fence"]["lease_id"]
    assert complete(db, first, grant(now), now + timedelta(seconds=90)) is False
    assert setup[4].state == "creating"
    assert complete(db, second, grant(now), now + timedelta(seconds=90))


def test_browser_queries_do_not_duplicate_provider_polls_and_429_honors_backoff(db, setup):
    now, actor, _, service, session, _ = setup
    now += timedelta(seconds=1)
    first = worker.claim_auth_operation(db, now=now)
    assert complete(db, first, grant(now), now)
    for _ in range(4):
        service.get_session(session.id, actor)
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=4)) is None
    poll = worker.claim_auth_operation(db, now=now + timedelta(seconds=5))
    assert complete(
        db,
        poll,
        {"action": "poll", "poll": {"status": "throttled", "retry_after_seconds": 30}},
        now + timedelta(seconds=5),
    )
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=34)) is None
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=35))["action"] == "poll"
    assert (
        len(db.scalars(select(CloudAuthOperation).where(CloudAuthOperation.action == "poll")).all())
        == 1
    )


def test_logout_rejects_inflight_prepare_and_queues_independent_cleanup(db, setup):
    work = authorize(db, setup)
    setup[2].revoked_at = datetime.now(UTC)
    db.flush()
    assert (
        complete(
            db,
            work,
            {"action": "prepare", "error_code": "secret-token-error"},
            setup[0] + timedelta(seconds=8),
        )
        is False
    )
    cleanup = worker.claim_auth_operation(db, now=setup[0] + timedelta(seconds=9))
    assert cleanup["action"] == "cleanup"
    assert "application" not in cleanup and "tokens" not in cleanup
    assert complete(
        db, cleanup, {"action": "cleanup", "cleaned": True}, setup[0] + timedelta(seconds=10)
    )
    assert db.get(CloudAuthCandidate, UUID(work["candidate"]["id"])).state == "cleaned"


def test_api_cancelled_candidate_is_swept_even_with_no_remaining_forward_work(db, setup):
    authorize(db, setup)
    _, actor, _, service, session, _ = setup
    service.cancel_session(session.id, actor, session.revision)
    cleanup = worker.claim_auth_operation(db, now=setup[0] + timedelta(seconds=10))
    assert cleanup is not None and cleanup["action"] == "cleanup"


def test_snapshot_gate_never_expires_without_matching_host_release(db, setup):
    now, _, _, _, _, connector_id = setup
    result = worker.begin_snapshot(db, connector_id, uuid4(), now=now)
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=121)) is None
    assert worker.begin_snapshot(db, connector_id, uuid4(), now=now + timedelta(seconds=121)) == {
        "ok": False
    }
    from app.operations.cloud_auth_bridge import SnapshotFence

    fence = SnapshotFence.model_validate(result["fence"])
    assert worker.end_snapshot(db, fence, now=now + timedelta(seconds=121)) == {
        "ok": True,
        "released": True,
    }
    assert worker.end_snapshot(db, fence, now=now + timedelta(seconds=122)) == {
        "ok": True,
        "released": True,
    }
    assert worker.claim_auth_operation(db, now=now + timedelta(seconds=122)) is not None


def test_ownership_checkpoint_is_encrypted_and_restart_returns_same_record(db, setup):
    from app.operations.cloud_auth_bridge import CheckpointRequest

    work = authorize(db, setup)
    candidate = work["candidate"]
    record = {
        **{key: work[key] for key in worker.SOURCE_FIELDS},
        "operation_id": candidate["operation_id"],
        "candidate_id": candidate["id"],
        "connector_id": candidate["connector_id"],
        "config_revision": 1,
        "provider": "baidu",
        "mount_path": f"/erp-candidates/{UUID(candidate['connector_id']).hex}/{UUID(candidate['operation_id']).hex}",
        "username": "private-user",
        "password": "private-pass",
        "marker": "private-marker",
        "account_fingerprint": "fingerprint",
        "raw_account_id": "12345678",
    }
    request = {
        "action": "checkpoint",
        "fence": work["fence"],
        "kind": "reserve",
        "operation_id": candidate["operation_id"],
        "record": record,
    }
    assert worker.checkpoint(
        db, CheckpointRequest.model_validate(request), now=setup[0] + timedelta(seconds=8)
    )["ok"]
    origin = db.get(CloudAuthOperation, UUID(candidate["operation_id"]))
    assert "private-pass" not in origin.result_ciphertext
    next_work = worker.claim_auth_operation(db, now=setup[0] + timedelta(seconds=98))
    assert next_work["candidate"]["journal"]["password"] == "private-pass"
    request["fence"] = next_work["fence"]
    request["kind"] = "save"
    request["record"]["mount_path"] = "/manual-other"
    assert worker.checkpoint(
        db, CheckpointRequest.model_validate(request), now=setup[0] + timedelta(seconds=99)
    ) == {"ok": False}


def test_stale_directory_result_does_not_cancel_newer_authorized_selection(db, setup):
    from app.operations.cloud_auth_requests import CloudDirectoryRequest

    work = authorize(db, setup)
    candidate = db.get(CloudAuthCandidate, UUID(work["candidate"]["id"]))
    candidate.state = "configured"
    origin = db.get(CloudAuthOperation, UUID(work["id"]))
    origin.status = "succeeded"
    _, actor, _, service, session, _ = setup
    service.queue_directories(
        session.id,
        actor,
        CloudDirectoryRequest(expected_revision=session.revision, operation_id=uuid4(), parent="/"),
    )
    listing = worker.claim_auth_operation(db, now=setup[0] + timedelta(seconds=8))
    service.select_directory(
        session.id,
        actor,
        CloudDirectorySelection(
            expected_revision=session.revision, operation_id=uuid4(), directory="/备份"
        ),
    )
    assert not complete(
        db, listing, {"action": "list_directories", "entries": []}, setup[0] + timedelta(seconds=9)
    )
    assert session.state == "configuring"
    assert session.selected_directory == "/备份"


def test_renewal_before_thirty_seconds_does_not_extend_then_renews_at_boundary(db, setup):
    now = setup[0] + timedelta(seconds=1)
    work = worker.claim_auth_operation(db, now=now)
    fence = AuthFence.model_validate(work["fence"])
    operation = db.get(CloudAuthOperation, UUID(work["id"]))
    assert worker.renew_auth_operation(db, fence, now=now + timedelta(seconds=29))
    assert operation.lease_expires_at == now + timedelta(seconds=90)
    assert worker.renew_auth_operation(db, fence, now=now + timedelta(seconds=30))
    assert operation.lease_expires_at == now + timedelta(seconds=120)


@pytest.mark.parametrize("missing", ["storage", "user", "meta", "all"])
def test_host_dispatch_through_strict_bridge_persists_candidate_and_probe(
    db, setup, monkeypatch, tmp_path, missing
):
    import io
    import json

    from app.operations.cloud_auth_bridge import serve
    from tests.cli.test_cloud_connector_candidates import API
    from tests.cli.test_erp_cloud_authorization import runner

    host = runner()
    monkeypatch.setattr(
        host,
        "safe_error",
        lambda error: pytest.fail(f"host dispatch failed: {type(error).__name__}: {error}"),
    )
    from cloud_auth import candidate as candidate_module
    from cloud_auth.provider_types import (
        AccountIdentity,
        DeviceGrant,
        PollResult,
        QrPresentation,
        TokenGrant,
    )

    now = setup[0] + timedelta(seconds=1)
    config = {"project": "erp", "app_dir": str(tmp_path)}

    def rpc(config, action, **payload):
        def dispatch(request):
            if request.action == "claim":
                return {"ok": True, "work": worker.claim_auth_operation(db, now=now)}
            if request.action == "checkpoint":
                return worker.checkpoint(db, request, now=now)
            if request.action == "complete":
                if request.result.action in {"apply", "unbind"}:
                    for name in [
                        ".cloud-backup.lock",
                        ".backup.lock",
                        ".deploy.lock",
                        ".cloud-connector.lock",
                    ]:
                        with pytest.raises(host.AuthRunnerError), host.locked(tmp_path / name):
                            pass
                return {
                    "ok": worker.complete_auth_operation(db, request.fence, request.result, now=now)
                }
            raise AssertionError(request.action)

        output = io.StringIO()
        status = serve(
            io.BytesIO(json.dumps({"action": action, **payload}, default=host.wire).encode()),
            output,
            dispatch=dispatch,
        )
        assert status == 0, output.getvalue()
        result = json.loads(output.getvalue())
        assert result["ok"], f"{action}:{payload.get('kind')}"
        return result

    class Provider:
        def start(self, *args):
            expiry = now + timedelta(minutes=9)
            return DeviceGrant(
                {"device_code": "device"},
                QrPresentation(
                    "payload", "https://openapi.baidu.com/device?code=short", None, expiry
                ),
                expiry,
                5,
            )

        def poll(self, *args):
            return PollResult(
                "authorized", tokens=TokenGrant("token-a", "token-r", now + timedelta(hours=1))
            )

        def get_identity(self, tokens):
            return AccountIdentity("baidu", "12345678")

    monkeypatch.setattr(host, "provider_for", lambda provider: Provider())

    class PersistedJournal:
        @property
        def records(self):
            rows = db.scalars(
                select(CloudAuthOperation).where(
                    CloudAuthOperation.action == "prepare",
                    CloudAuthOperation.result_ciphertext.is_not(None),
                )
            ).all()
            return {
                row.id: host.ownership(worker.decrypt(row, "journal", row.result_ciphertext))
                for row in rows
            }

    api = API(PersistedJournal())
    secret = tmp_path / "admin-secret"
    secret.write_text("private-admin")
    monkeypatch.setattr(
        host,
        "load_registry",
        lambda path: {
            "connector_id": str(setup[5]),
            "origin": "http://127.0.0.1:5244",
            "admin_secret_file": str(secret),
            "connector_version": "4.2.6",
        },
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        host, "OpenListAPI", SimpleNamespace(from_admin_password=lambda connector, secret: api)
    )

    class Dav:
        def __init__(self):
            self.files = {}

        def put(self, path, source):
            operation = db.scalar(
                select(CloudAuthOperation).where(CloudAuthOperation.action == "probe")
            )
            assert worker.decrypt(operation, "probe", operation.result_ciphertext)["path"] == path
            self.files[path] = source.read_bytes()

        def get(self, path, destination, limit):
            destination.write_bytes(self.files[path])

        def delete(self, path):
            self.files.pop(path, None)

    dav = Dav()
    monkeypatch.setattr(
        host,
        "probe_candidate",
        lambda access, directory, identity, **kwargs: candidate_module.probe_candidate(
            access,
            directory,
            identity,
            transport=dav,
            scope_checker=lambda access, path: None,
            **kwargs,
        ),
    )

    def dispatch_next(expected):
        work = rpc(config, "claim")["work"]
        assert work["action"] == expected
        with host.operation_locks(tmp_path):
            host.execute(config, work, rpc)
        return work

    dispatch_next("create_qr")
    now += timedelta(seconds=5)
    dispatch_next("poll")
    now += timedelta(seconds=1)
    prepare = dispatch_next("prepare")
    session = setup[4]
    assert session.state == "configuring" and session.token_ciphertext is None
    assert session.account_label == "***5678"
    from app.operations.cloud_auth_requests import CloudDirectorySelection, CloudOperationCommand

    service, actor = setup[3], setup[1]
    service.select_directory(
        session.id,
        actor,
        CloudDirectorySelection(
            expected_revision=session.revision, operation_id=uuid4(), directory="/备份 空格%"
        ),
    )
    now += timedelta(seconds=1)
    dispatch_next("select_directory")
    service.queue_test(
        session.id,
        actor,
        CloudOperationCommand(expected_revision=session.revision, operation_id=uuid4()),
    )
    now += timedelta(seconds=1)
    dispatch_next("probe")
    assert (
        service.get_session(session.id, actor).model_dump(mode="json")["probe_attempt"]["status"]
        == "succeeded"
    )
    assert session.state == "ready" and not dav.files
    candidate = db.get(CloudAuthCandidate, UUID(prepare["candidate"]["id"]))
    assert candidate.state == "verified" and candidate.config_revision == 2
    assert len(api.objects["storage"]) == 1
    assert "private-admin" not in (candidate.password_ciphertext or "")
    from app.operations.cloud_auth_apply import request_apply
    from app.operations.cloud_models import CloudBackupTarget

    request_apply(db, actor, session.id, session.revision, uuid4())
    now += timedelta(seconds=1)
    dispatch_next("apply")
    assert session.state == "applied" and candidate.state == "active"
    target = db.get(CloudBackupTarget, "baidu")
    assert target.remote_path == "/" and not target.enabled
    from app.operations.cloud_auth_apply import request_unbind
    from app.operations.cloud_auth_models import CloudBackupTargetHistory

    for resource in list(api.objects) if missing == "all" else [missing]:
        api.objects[resource] = []
    unbind_key = uuid4()
    old_revision = target.revision
    request_unbind(db, actor, "baidu", old_revision, unbind_key)
    now += timedelta(seconds=1)
    dispatch_next("unbind")
    assert (
        target.managed_candidate_id is None
        and target.username_ciphertext == target.password_ciphertext == ""
    )
    assert candidate.state == "cleanup_pending"
    assert request_unbind(db, actor, "baidu", old_revision, unbind_key).state == "applied"
    assert len(db.scalars(select(CloudBackupTargetHistory)).all()) == 1
    now += timedelta(seconds=1)
    dispatch_next("cleanup")
    assert candidate.state == "cleaned" and all(not values for values in api.objects.values())
    assert not dav.files


def test_probe_checkpoint_preserves_canonical_readback_mismatch(db, setup):
    from app.operations.cloud_auth_bridge import CheckpointRequest

    prepare = authorize(db, setup)
    session = setup[4]
    origin = db.get(CloudAuthOperation, UUID(prepare["id"]))
    origin.status = "succeeded"
    candidate = db.get(CloudAuthCandidate, UUID(prepare["candidate"]["id"]))
    candidate.state = "configured"
    candidate.selected_directory = "/backup"
    session.selected_directory = "/backup"
    session.state = "test_pending"
    probe = worker.queue(db, session, "probe", now=setup[0], candidate_id=candidate.id)
    now = setup[0] + timedelta(seconds=9)
    work = worker.claim_auth_operation(db, now=now)
    assert work["action"] == "probe"

    def checkpoint(kind, **payload):
        return worker.checkpoint(
            db,
            CheckpointRequest.model_validate(
                {
                    "action": "checkpoint",
                    "kind": kind,
                    "fence": work["fence"],
                    "operation_id": str(probe.id),
                    **payload,
                }
            ),
            now=now,
        )

    assert checkpoint(
        "reserve_probe",
        directory="/backup",
        path=f"/erp-probe-{probe.id.hex}-" + "a" * 32 + ".bin",
        sha256="a" * 64,
    )["ok"]
    assert checkpoint("finish_probe", error_code="cloud_readback_failed", cleanup_pending=False)[
        "ok"
    ]
    assert (
        worker.decrypt(probe, "probe", probe.result_ciphertext)["error_code"]
        == "cloud_remote_corrupt"
    )


@pytest.mark.parametrize("method", ["renew", "complete"])
def test_changed_candidate_source_rejects_inflight_worker_mutations(db, setup, method):
    work = authorize(db, setup)
    candidate = db.get(CloudAuthCandidate, UUID(work["candidate"]["id"]))
    candidate.app_revision = candidate.auth_config_revision = 2
    db.flush()
    now = setup[0] + timedelta(seconds=8)
    if method == "renew":
        assert not worker.renew_auth_operation(db, AuthFence.model_validate(work["fence"]), now=now)
    else:
        assert not complete(db, work, {"action": "prepare", "error_code": "cloud_auth_failed"}, now)


def test_unbind_reclaims_original_candidate_after_application_revision_change(db, setup):
    from app.operations.cloud_models import CloudBackupTarget

    prepared = authorize(db, setup)
    now, actor, _, service, original, _ = setup
    candidate = db.get(CloudAuthCandidate, UUID(prepared["candidate"]["id"]))
    candidate.state, original.state = "active", "applied"
    db.get(CloudAuthOperation, UUID(prepared["id"])).status = "succeeded"
    target = CloudBackupTarget(
        provider="baidu",
        revision=1,
        remote_path="/",
        username_ciphertext="synthetic",
        password_ciphertext="synthetic",
        managed_candidate_id=candidate.id,
    )
    db.add(target)
    db.flush()
    service.save_application(
        "baidu",
        actor,
        CloudAppUpdate(client_id="new-app", client_secret="new-secret", expected_revision=1),
    )
    session = CloudAuthSession(
        id=uuid4(),
        provider="baidu",
        **worker.source_fields(original),
        application_provider="baidu",
        owner_account_id=actor.account_id,
        owner_login_session_id=actor.login_session_id,
        state="applying",
        revision=1,
        target_revision=1,
        expires_at=now + timedelta(minutes=10),
        candidate_id=candidate.id,
    )
    db.add(session)
    db.flush()
    operation = worker.queue(db, session, "unbind", now=now)
    target.switch_operation_id = operation.id
    db.flush()
    work = worker.claim_auth_operation(db, now=now + timedelta(seconds=8))
    assert work is not None and work["action"] == "unbind"
    assert work["app_revision"] == work["candidate"]["app_revision"] == 1
    assert worker.renew_auth_operation(
        db, AuthFence.model_validate(work["fence"]), now=now + timedelta(seconds=9)
    )


@pytest.mark.parametrize("proof_kind", ["clean", "pending", "none"])
def test_failed_probe_attempt_is_public_only_after_explicit_cleanup_proof(db, setup, proof_kind):
    from app.operations.cloud_auth_requests import CloudOperationCommand

    prepare = authorize(db, setup)
    now, actor, _, service, session, _ = setup
    db.get(CloudAuthOperation, UUID(prepare["id"])).status = "succeeded"
    candidate = db.get(CloudAuthCandidate, UUID(prepare["candidate"]["id"]))
    candidate.state, candidate.selected_directory = "configured", "/backup"
    session.state, session.selected_directory = "test_pending", "/backup"
    key = uuid4()
    service.queue_test(
        session.id,
        actor,
        CloudOperationCommand(expected_revision=session.revision, operation_id=key),
    )
    view = service.get_session(session.id, actor).model_dump(mode="json")
    assert view["probe_attempt"] == {"operation_id": str(key), "status": "pending"}
    work = worker.claim_auth_operation(db, now=now + timedelta(seconds=9))
    probe = db.get(CloudAuthOperation, UUID(work["id"]))
    if proof_kind != "none":
        probe.result_ciphertext = worker.encrypt(
            probe,
            "probe",
            {
                "finished": True,
                "cleanup_pending": proof_kind == "pending",
                "error_code": "cloud_remote_corrupt",
            },
        )
        db.commit()
    assert complete(
        db,
        work,
        {"action": "probe", "error_code": "cloud_remote_corrupt"},
        now + timedelta(seconds=10),
    )
    attempt = service.get_session(session.id, actor).model_dump(mode="json")["probe_attempt"]
    assert attempt == {
        "operation_id": str(key),
        "status": "cleanup_pending" if proof_kind == "pending" else "failed",
    }
    if proof_kind == "none":
        assert worker.decrypt(probe, "probe", probe.result_ciphertext)["no_external_io"] is True
    if proof_kind != "pending":
        new_key = uuid4()
        service.queue_test(
            session.id,
            actor,
            CloudOperationCommand(expected_revision=session.revision, operation_id=new_key),
        )
        assert service.get_session(session.id, actor).model_dump(mode="json")["probe_attempt"] == {
            "operation_id": str(new_key),
            "status": "pending",
        }
    session.revision += 1
    db.commit()
    assert service.get_session(session.id, actor).model_dump(mode="json")["probe_attempt"] is None


def test_terminal_probe_without_proof_is_not_reported_clean(db, setup):
    _, actor, _, service, session, _ = setup
    probe = service._operation(session, actor, uuid4(), "probe", "f" * 64, {})
    probe.status = "failed"
    db.commit()
    assert (
        service.get_session(session.id, actor).model_dump(mode="json")["probe_attempt"]["status"]
        == "cleanup_pending"
    )


def test_public_session_poll_releases_expired_switch_without_waiting_for_host_lock(
    db, platform_users
):
    from tests.services.test_cloud_auth_apply import ready, request

    values = ready.__wrapped__(db, platform_users)
    _, actor, session, target, _, _, _ = values
    request(db, values)
    assert target.switch_operation_id is not None
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    service = CloudAuthorizationService(db, cipher=EnvelopeCipher(bytes(range(32))))
    view = service.get_session(session.id, actor)
    assert target.switch_operation_id is None
    assert view.error_code == "cloud_target_busy"
    assert target.remote_path == "/manual-backups" and target.enabled
