"""Configuration switching is fenced, drained, explicit and history preserving."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.models import AuthSession
from app.core.errors import ProblemError
from app.operations import cloud_auth_worker as worker
from app.operations.cloud_auth_models import (
    CloudAuthApplication,
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
    CloudBackupTargetHistory,
    CloudConnectorState,
)
from app.operations.cloud_auth_schemas import AuthActor, AuthFence
from app.operations.cloud_models import CloudBackupJob, CloudBackupTarget
from app.operations.cloud_schemas import CloudTargetUpdate
from app.operations.cloud_service import CloudBackupService
from app.security.encryption import EnvelopeCipher
from tests.api.conftest import platform_users as _platform_users


def apply_module():
    try:
        from app.operations import cloud_auth_apply
    except ImportError:
        return None
    return cloud_auth_apply


@pytest.fixture(name="platform_users")
def users(db):
    return _platform_users.__wrapped__(db)


@pytest.fixture
def ready(db, platform_users):
    now = datetime.now(UTC)
    account = platform_users["accounts"]["admin"]
    login = AuthSession(
        user_id=account.id,
        token_digest=uuid4().hex,
        csrf_digest=uuid4().hex,
        expires_at=now + timedelta(hours=1),
    )
    db.add(login)
    db.flush()
    actor = AuthActor(account_id=account.id, login_session_id=login.id)
    cipher = EnvelopeCipher(bytes(range(32)))
    backup = CloudBackupService(db, cipher=cipher)
    backup.save(
        "baidu",
        CloudTargetUpdate(remote_path="/manual-backups", username="manual", password="old-secret"),
    )
    target = db.get(CloudBackupTarget, "baidu")
    target.enabled = True
    target.connection_status = "ok"
    target.last_success_at = now
    target.last_backup_id = "backup-20261002T030000Z-1234abcd"
    app = CloudAuthApplication(provider="baidu", client_id="own-app", revision=1)
    connector = CloudConnectorState(id=uuid4(), registered=True, last_seen_at=now)
    session = CloudAuthSession(
        id=uuid4(),
        provider="baidu",
        owner_account_id=account.id,
        owner_login_session_id=login.id,
        state="ready",
        revision=4,
        app_revision=1,
        target_revision=1,
        selected_directory="/备份 目录",
        account_label="账号尾号 1234",
        expires_at=now + timedelta(minutes=3),
    )
    db.add_all([app, connector, session])
    db.flush()
    origin = worker.queue(db, session, "prepare", now=now)
    origin.status = "succeeded"
    candidate = CloudAuthCandidate(
        id=uuid4(),
        session_id=session.id,
        provider="baidu",
        connector_id=connector.id,
        operation_id=origin.id,
        mount_path=f"/erp-candidates/{connector.id.hex}/{origin.id.hex}",
        storage_id="storage-own",
        webdav_user_id="user-own",
        config_revision=2,
        selected_directory=session.selected_directory,
        state="verified",
    )
    db.add(candidate)
    db.flush()
    session.candidate_id = candidate.id
    probe = worker.queue(db, session, "probe", now=now, candidate_id=candidate.id)
    probe.status = "succeeded"
    candidate.probe_operation_id = probe.id
    candidate.probe_sha256 = "a" * 64
    candidate.probe_verified_at = now
    for field, value in (
        ("username", "erp-private"),
        ("password", "new-secret"),
        ("account_fingerprint", "fingerprint-private"),
    ):
        setattr(candidate, field + "_ciphertext", worker.encrypt(candidate, field, value))
    origin.result_ciphertext = worker.encrypt(
        origin,
        "journal",
        {
            "operation_id": str(origin.id),
            "candidate_id": str(candidate.id),
            "connector_id": str(connector.id),
            "provider": "baidu",
            "mount_path": candidate.mount_path,
            "storage_id": candidate.storage_id,
            "webdav_user_id": candidate.webdav_user_id,
            "username": "erp-private",
            "password": "new-secret",
            "account_fingerprint": "fingerprint-private",
            "config_revision": 2,
            "directory": session.selected_directory,
            "delivered": True,
            "scope_pending": False,
        },
    )
    db.flush()
    db.commit()
    return now, actor, session, target, candidate, backup, login


def request(db, ready, key=None):
    module = apply_module()
    assert module is not None, "configuration switch service must exist"
    return module.request_apply(db, ready[1], ready[2].id, ready[2].revision, key or uuid4())


def operation(db, session):
    return db.scalar(
        select(CloudAuthOperation).where(
            CloudAuthOperation.session_id == session.id, CloudAuthOperation.action == "apply"
        )
    )


def test_apply_freezes_new_work_but_old_queue_drains(db, ready):
    now, _, session, target, _, backup, _ = ready
    queued = backup.enqueue("baidu", "copy")
    request(db, ready)
    switch = operation(db, session)
    assert target.switch_operation_id == switch.id
    assert session.expires_at > now + timedelta(minutes=9)
    with pytest.raises(ProblemError):
        backup.save("baidu", CloudTargetUpdate(remote_path="/manual-backups", username="manual"))
    with pytest.raises(ProblemError):
        backup.enqueue("baidu", "test")
    assert backup.sync("backup-20261003T030000Z-1234abcd")["enqueued"] == 0
    assert not apply_module().prepare_apply(db, session=session, operation=switch, now=now)
    assert backup.claim()["job"]["id"] == str(queued.id)


def test_apply_preserves_manual_history_and_clears_only_current_copy_evidence(db, ready):
    now, actor, session, target, candidate, _, _ = ready
    key = uuid4()
    revision = session.revision
    request(db, ready, key)
    switch = operation(db, session)
    assert apply_module().prepare_apply(db, session=session, operation=switch, now=now)
    apply_module().complete_apply(
        db, session=session, operation=switch, candidate=candidate, now=now
    )
    db.flush()
    assert target.revision == 2 and not target.enabled
    assert target.managed_candidate_id == candidate.id and candidate.state == "active"
    assert target.remote_path == "/" and target.authorization_status == "valid"
    assert target.connection_status == "ok" and target.last_tested_at == candidate.probe_verified_at
    assert target.last_success_at is None and target.last_backup_id is None
    assert target.switch_operation_id is None
    history = db.scalars(select(CloudBackupTargetHistory)).all()
    assert len(history) == 1 and history[0].revision == 1
    assert history[0].last_backup_id == "backup-20261002T030000Z-1234abcd"
    assert "old-secret" not in history[0].config_ciphertext
    replay = apply_module().request_apply(db, actor, session.id, revision, key)
    assert replay.state == "applied" and target.revision == 2
    assert len(db.scalars(select(CloudBackupTargetHistory)).all()) == 1


def builtin_ready(db, ready):
    _, _, session, _, candidate, _, _ = ready
    records = [
        session,
        candidate,
        *list(
            db.scalars(
                select(CloudAuthOperation).where(CloudAuthOperation.session_id == session.id)
            )
        ),
    ]
    secrets = []
    for row in records:
        for field in ("username", "password", "account_fingerprint", "journal", "payload"):
            column = "result_ciphertext" if field == "journal" else field + "_ciphertext"
            if hasattr(row, column) and getattr(row, column):
                secrets.append(
                    (row, field, column, worker.decrypt(row, field, getattr(row, column)))
                )
    for row in records:
        row.mode, row.protocol_version = "openlist_builtin_import", 2
        row.auth_config_id, row.auth_config_revision, row.app_revision = (
            "openlist-builtin-import-v1:baidu",
            1,
            None,
        )
    session.application_provider = None
    for row, field, column, value in secrets:
        setattr(row, column, worker.encrypt(row, field, value))
    db.flush()
    db.delete(db.get(CloudAuthApplication, "baidu"))
    db.flush()


def test_builtin_apply_does_not_require_app_or_extend_configuration_deadline(db, ready):
    builtin_ready(db, ready)
    now, _, session, target, candidate, backup, _ = ready
    deadline = session.expires_at
    request(db, ready)
    assert session.expires_at == deadline
    apply_module().complete_apply(
        db, session=session, operation=operation(db, session), candidate=candidate, now=now
    )
    db.flush()
    assert target.managed_candidate_id == candidate.id and not target.enabled
    assert backup.public().targets[0].authorization_mode == "openlist_builtin_import"


def test_builtin_unbind_inherits_actual_candidate_source_without_application(db, ready):
    builtin_ready(db, ready)
    now, actor, session, target, candidate, _, _ = ready
    request(db, ready)
    apply_module().complete_apply(
        db, session=session, operation=operation(db, session), candidate=candidate, now=now
    )
    db.flush()
    result = apply_module().request_unbind(db, actor, "baidu", target.revision, uuid4())
    unbind_session = db.get(CloudAuthSession, result.id)
    assert unbind_session.mode == "openlist_builtin_import" and unbind_session.app_revision is None
    queued = db.scalar(select(CloudAuthOperation).where(CloudAuthOperation.session_id == result.id))
    assert queued.mode == candidate.mode and queued.auth_config_id == candidate.auth_config_id


def test_candidate_source_mismatch_cannot_apply(db, ready):
    builtin_ready(db, ready)
    candidate = ready[4]
    candidate.mode, candidate.protocol_version, candidate.app_revision = "own_app", 2, 1
    candidate.auth_config_id = "own-app:baidu"
    db.flush()
    with pytest.raises(ProblemError):
        request(db, ready)
    assert ready[3].switch_operation_id is None


@pytest.mark.parametrize("change", ["directory", "app", "target", "admin_user", "scope"])
def test_changed_proof_or_fence_rejects_switch_without_history(db, ready, change):
    _, _, session, target, candidate, _, _ = ready
    if change == "directory":
        session.selected_directory = "/different"
    elif change == "app":
        db.get(CloudAuthApplication, "baidu").revision += 1
    elif change == "target":
        target.revision += 1
    else:
        origin = db.get(CloudAuthOperation, candidate.operation_id)
        journal = worker.decrypt(origin, "journal", origin.result_ciphertext)
        if change == "admin_user":
            journal["username"] = "admin"
            candidate.username_ciphertext = worker.encrypt(candidate, "username", "admin")
        else:
            journal["scope_pending"] = True
        origin.result_ciphertext = worker.encrypt(origin, "journal", journal)
    db.flush()
    with pytest.raises(ProblemError):
        request(db, ready)
    assert target.switch_operation_id is None and target.remote_path == "/manual-backups"
    assert db.scalars(select(CloudBackupTargetHistory)).all() == []


def test_expired_switch_releases_only_its_freeze_and_preserves_old_target(db, ready):
    now, _, session, target, _, _, _ = ready
    request(db, ready)
    switch = operation(db, session)
    assert not apply_module().prepare_apply(
        db, session=session, operation=switch, now=now + timedelta(minutes=11)
    )
    assert target.switch_operation_id is None and target.revision == 1
    assert session.state == "failed" and session.error_code == "cloud_target_busy"


def test_no_host_receipt_cannot_switch_even_after_job_database_timeout(db, ready):
    now, _, _session, target, _, backup, _ = ready
    backup.enqueue("baidu", "copy")
    job = backup.claim()["job"]
    row = db.get(CloudBackupJob, job["id"])
    row.lease_seen_at = now - timedelta(hours=1)
    request(db, ready)
    assert backup.claim() == {"job": None}
    work = worker.claim_auth_operation(db, now=now + timedelta(seconds=1))
    assert work is not None and work["action"] == "apply"
    assert not worker.complete_auth_operation(
        db,
        AuthFence.model_validate(work["fence"]),
        {"action": "apply"},
        now=now + timedelta(seconds=2),
    )
    assert target.revision == 1 and target.remote_path == "/manual-backups"
    assert db.scalars(select(CloudBackupTargetHistory)).all() == []


def test_invalid_managed_auth_stops_new_work_without_clearing_history(db, ready):
    now, _, session, target, candidate, backup, _ = ready
    request(db, ready)
    apply_module().complete_apply(
        db, session=session, operation=operation(db, session), candidate=candidate, now=now
    )
    target.enabled = True
    target.last_success_at = now
    target.last_backup_id = "backup-20261002T030000Z-1234abcd"
    db.flush()
    backup.enqueue("baidu", "copy")
    job = backup.claim()["job"]
    backup.finish(
        job_id=job["id"],
        revision=job["revision"],
        success=False,
        error_code="cloud_authorization_invalid",
        backup_id=None,
    )
    assert target.authorization_status == "reauthorization_required"
    assert target.last_backup_id == "backup-20261002T030000Z-1234abcd"
    assert backup.sync("backup-20261003T030000Z-1234abcd")["enqueued"] == 0
    with pytest.raises(ProblemError):
        backup.enqueue("baidu", "test")


def test_unbind_preserves_data_and_queues_only_owned_managed_cleanup(db, ready):
    now, actor, session, target, candidate, _, _ = ready
    request(db, ready)
    apply_module().complete_apply(
        db, session=session, operation=operation(db, session), candidate=candidate, now=now
    )
    key = uuid4()
    view = apply_module().request_unbind(db, actor, "baidu", 2, key)
    unbind_session = db.get(CloudAuthSession, view.id)
    unbind = db.scalar(
        select(CloudAuthOperation).where(
            CloudAuthOperation.session_id == view.id, CloudAuthOperation.action == "unbind"
        )
    )
    assert target.switch_operation_id == unbind.id
    apply_module().complete_unbind(
        db, session=unbind_session, operation=unbind, candidate=candidate, now=now
    )
    db.flush()
    assert target.revision == 3 and not target.enabled and target.managed_candidate_id is None
    assert candidate.state == "cleanup_pending"
    assert len(db.scalars(select(CloudBackupTargetHistory)).all()) == 2
    assert apply_module().request_unbind(db, actor, "baidu", 2, key).state == "applied"


def test_unbind_cannot_take_over_manual_target(db, ready):
    module = apply_module()
    assert module is not None
    with pytest.raises(ProblemError):
        module.request_unbind(db, ready[1], "baidu", 1, uuid4())
    assert ready[3].switch_operation_id is None


def test_owned_candidate_unbind_after_app_update_keeps_historical_source_through_completion(
    db, ready
):
    now, actor, original_session, target, candidate, _, _ = ready
    module = apply_module()
    request(db, ready)
    module.complete_apply(
        db,
        session=original_session,
        operation=operation(db, original_session),
        candidate=candidate,
        now=now,
    )
    app = db.get(CloudAuthApplication, "baidu")
    app.revision = 2
    db.flush()
    unbound = module.request_unbind(db, actor, "baidu", target.revision, uuid4())
    session = db.get(CloudAuthSession, unbound.id)
    assert session.app_revision == candidate.app_revision == 1
    assert session.auth_config_revision == 1 and session.auth_config_id == "own-app:baidu"
    assert session.protocol_version == candidate.protocol_version == 1
    work = worker.claim_auth_operation(db, now=now + timedelta(seconds=1))
    assert work is not None and work["action"] == "unbind"
    queued = db.get(CloudAuthOperation, work["id"])
    assert queued.app_revision == 1 and queued.auth_config_revision == 1
    assert module.prepare_apply(
        db, session=session, operation=queued, now=now + timedelta(seconds=2)
    )
    module.complete_unbind(
        db, session=session, operation=queued, candidate=candidate, now=now + timedelta(seconds=3)
    )
    db.flush()
    assert target.managed_candidate_id is None and not target.enabled
    assert candidate.state == "cleanup_pending" and session.state == "applied"
    cleanup = db.scalar(
        select(CloudAuthOperation).where(
            CloudAuthOperation.action == "cleanup", CloudAuthOperation.candidate_id == candidate.id
        )
    )
    assert cleanup.auth_config_id == "own-app:baidu" and cleanup.app_revision == 1
    assert app.revision == 2


def test_unbind_cannot_retire_candidate_with_changed_frozen_source(db, ready):
    now, actor, original_session, target, candidate, _, _ = ready
    module = apply_module()
    request(db, ready)
    module.complete_apply(
        db,
        session=original_session,
        operation=operation(db, original_session),
        candidate=candidate,
        now=now,
    )
    db.get(CloudAuthApplication, "baidu").revision = 2
    candidate.app_revision = candidate.auth_config_revision = 2
    db.flush()
    db.commit()
    with pytest.raises(ProblemError):
        module.request_unbind(db, actor, "baidu", target.revision, uuid4())
    assert target.managed_candidate_id == candidate.id and target.switch_operation_id is None


def test_switch_owner_logout_and_maintenance_are_rechecked(db, ready):
    _, _, _, target, candidate, _, login = ready
    connector = db.get(CloudConnectorState, candidate.connector_id)
    connector.maintenance_operation_id = uuid4()
    connector.maintenance_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db.flush()
    with pytest.raises(ProblemError):
        request(db, ready)
    connector.maintenance_operation_id = connector.maintenance_lease_expires_at = None
    login.revoked_at = datetime.now(UTC)
    db.flush()
    with pytest.raises(ProblemError):
        request(db, ready)
    assert target.switch_operation_id is None


def test_managed_root_cannot_auto_enable_without_provider_restore_acceptance(db, ready):
    now, _, session, target, candidate, backup, _ = ready
    request(db, ready)
    apply_module().complete_apply(
        db, session=session, operation=operation(db, session), candidate=candidate, now=now
    )
    db.flush()
    backup.save("baidu", CloudTargetUpdate(remote_path="/", username="erp-private", enabled=False))
    with pytest.raises(ProblemError) as error:
        backup.save(
            "baidu", CloudTargetUpdate(remote_path="/", username="erp-private", enabled=True)
        )
    assert error.value.code == "cloud_provider_acceptance_required"
    assert backup.enqueue("baidu", "copy").kind == "copy"
    assert not target.enabled


def test_manual_root_remains_rejected_by_service(db, ready):
    with pytest.raises(ProblemError):
        ready[5].save(
            "115", CloudTargetUpdate(remote_path="/", username="operator", password="secret")
        )


def test_public_target_distinguishes_managed_acceptance_and_directory_without_internal_ids(
    db, ready
):
    now, _, session, _target, candidate, backup, _ = ready
    before = backup.public().model_dump(mode="json")["targets"][0]
    assert before["managed"] is False and before["authorization_status"] == "manual"
    request(db, ready)
    apply_module().complete_apply(
        db, session=session, operation=operation(db, session), candidate=candidate, now=now
    )
    db.flush()
    after = backup.public().model_dump(mode="json")["targets"][0]
    assert after["managed"] is True and after["authorization_status"] == "valid"
    assert after["selected_directory"] == "/备份 目录" and after["remote_path"] == "/"
    assert "candidate_id" not in after and "new-secret" not in str(after)


def test_unknown_external_creation_never_applies_even_with_delivered_and_probe_evidence():
    """An object observed later does not prove the originating request has stopped."""
    session = CloudAuthSession(id=uuid4(), provider="baidu", selected_directory="/backup")
    origin = CloudAuthOperation(id=uuid4(), provider="baidu")
    probe = CloudAuthOperation(
        id=uuid4(), provider="baidu", session_id=session.id, action="probe", status="succeeded"
    )
    candidate = CloudAuthCandidate(
        id=uuid4(),
        provider="baidu",
        session_id=session.id,
        connector_id=uuid4(),
        operation_id=origin.id,
        selected_directory="/backup",
        config_revision=1,
        storage_id="storage",
        webdav_user_id="user",
        state="verified",
        probe_operation_id=probe.id,
        probe_sha256="a" * 64,
        probe_verified_at=datetime.now(UTC),
    )
    # These deliberately unpersisted doubles must state their legacy envelope
    # source: ORM insert defaults are unavailable before a database flush.
    for record in (session, origin, probe, candidate):
        record.mode, record.protocol_version = "own_app", 1
        record.auth_config_id, record.auth_config_revision, record.app_revision = (
            "own-app:baidu",
            1,
            1,
        )
    session.candidate_id = candidate.id
    candidate.mount_path = f"/erp-candidates/{candidate.connector_id.hex}/{origin.id.hex}"
    probe.candidate_id = candidate.id
    for field, value in (
        ("username", "erp-user"),
        ("password", "secret"),
        ("account_fingerprint", "identity"),
    ):
        setattr(candidate, field + "_ciphertext", worker.encrypt(candidate, field, value))
    origin.result_ciphertext = worker.encrypt(
        origin,
        "journal",
        {
            "operation_id": str(origin.id),
            "candidate_id": str(candidate.id),
            "connector_id": str(candidate.connector_id),
            "provider": "baidu",
            "mount_path": candidate.mount_path,
            "storage_id": "storage",
            "webdav_user_id": "user",
            "directory": "/backup",
            "config_revision": 1,
            "delivered": True,
            "scope_pending": False,
            "creation_unknown": True,
            "username": "erp-user",
            "password": "secret",
            "account_fingerprint": "identity",
        },
    )

    class JournalRead:
        def get(self, model, identity):
            return {candidate.id: candidate, origin.id: origin, probe.id: probe}.get(identity)

    with pytest.raises(ProblemError) as error:
        apply_module()._candidate(JournalRead(), session)
    assert error.value.code == "cloud_config_changed"
