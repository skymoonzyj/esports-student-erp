from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.core.errors import ProblemError
from app.operations.cloud_models import CloudBackupJob, CloudBackupTarget
from app.operations.cloud_schemas import CloudTargetUpdate
from app.operations.cloud_service import CloudBackupService
from app.security.encryption import EnvelopeCipher

BACKUP = "backup-20261002T030000Z-1234abcd"


def service(db):
    return CloudBackupService(db, cipher=EnvelopeCipher(bytes(range(32))))


def save(s, provider="baidu", enabled=False, password="super-secret"):
    return s.save(
        provider,
        CloudTargetUpdate(
            remote_path="/backup/ERP", username="operator", password=password, enabled=enabled
        ),
    )


def configure_tested(s):
    save(s)
    s.enqueue("baidu", "test")
    job = s.claim()["job"]
    s.finish(
        job_id=job["id"], revision=job["revision"], success=True, error_code=None, backup_id=None
    )


def test_credentials_encrypted_and_public_secret_free(db):
    s = service(db)
    save(s)
    row = db.get(CloudBackupTarget, "baidu")
    assert "super-secret" not in row.password_ciphertext
    assert "operator" not in row.username_ciphertext
    assert "super-secret" not in s.public().model_dump_json()
    save(s, password="")
    s.enqueue("baidu", "test")
    assert s.claim()["job"]["password"] == "super-secret"


def test_enable_requires_current_connection_test_and_enable_preserves_test(db):
    s = service(db)
    with pytest.raises(ProblemError):
        save(s, enabled=True)
    configure_tested(s)
    before = s.public().targets[0]
    save(s, enabled=True, password="")
    after = s.public().targets[0]
    assert after.enabled and after.connection_status == "ok"
    assert after.revision == before.revision
    assert after.last_success_at is None
    s.save(
        "baidu",
        CloudTargetUpdate(remote_path="/changed", username="operator", password="", enabled=False),
    )
    assert s.public().targets[0].connection_status == "untested"


def test_single_live_job_and_live_config_conflict(db):
    s = service(db)
    save(s)
    s.enqueue("baidu", "test")
    for operation in [lambda: s.enqueue("baidu", "test"), lambda: save(s)]:
        with pytest.raises(ProblemError) as error:
            operation()
        assert error.value.status == 409


def test_sync_deduplicated_and_verified_copy_updates_success_only(db):
    s = service(db)
    configure_tested(s)
    save(s, enabled=True, password="")
    s.sync(BACKUP)
    s.sync(BACKUP)
    job = s.claim()["job"]
    assert job["backup_id"] == BACKUP
    s.finish(
        job_id=job["id"], revision=job["revision"], success=True, error_code=None, backup_id=BACKUP
    )
    s.sync(BACKUP)
    assert s.claim() == {"job": None}
    assert s.public().targets[0].last_backup_id == BACKUP
    assert s.public().targets[0].last_success_at is not None
    assert s.public().worker_last_seen_at is not None


def test_failed_retest_preserves_enabled_alert_and_success_resumes_automatic_copy(db):
    from app.operations.cloud_status import cloud_conditions, read_cloud_evidence

    s = service(db)
    configure_tested(s)
    save(s, enabled=True, password="")
    s.enqueue("baidu", "test")
    failed_job = s.claim()["job"]
    s.finish(
        job_id=failed_job["id"],
        revision=failed_job["revision"],
        success=False,
        error_code="cloud_connector_failed",
        backup_id=None,
    )
    failed_target = s.public().targets[0]
    assert failed_target.enabled
    assert failed_target.connection_status == "failed"
    evidence, last_seen = read_cloud_evidence(db)
    conditions = cloud_conditions(evidence, worker_last_seen_at=last_seen, now=datetime.now(UTC))
    assert any(
        c.key == "cloud_backup_baidu_failed" and c.severity == "critical" for c in conditions
    )
    assert s.sync(BACKUP)["enqueued"] == 0
    assert s.claim() == {"job": None}

    s.enqueue("baidu", "test")
    recovered_job = s.claim()["job"]
    s.finish(
        job_id=recovered_job["id"],
        revision=recovered_job["revision"],
        success=True,
        error_code=None,
        backup_id=None,
    )
    recovered_target = s.public().targets[0]
    assert recovered_target.enabled and recovered_target.connection_status == "ok"
    assert recovered_target.last_error_code is None
    next_backup = "backup-20261003T030000Z-5678abcd"
    assert s.sync(next_backup)["enqueued"] == 1
    copy_job = s.claim()["job"]
    assert copy_job["kind"] == "copy" and copy_job["backup_id"] == next_backup


def test_stale_completion_cannot_mutate_target_and_errors_sanitized(db):
    s = service(db)
    save(s)
    s.enqueue("baidu", "test")
    job = s.claim()["job"]
    row = db.get(CloudBackupTarget, "baidu")
    row.revision += 1
    db.flush()
    s.finish(
        job_id=job["id"],
        revision=job["revision"],
        success=False,
        error_code="super-secret URL",
        backup_id=None,
    )
    assert row.connection_status == "untested"
    assert s.public().jobs[0].error_code == "cloud_backup_failed"
    assert "super-secret" not in s.public().model_dump_json()


def test_stuck_job_recovers_and_wrong_revision_finish_rejected(db):
    s = service(db)
    save(s)
    s.enqueue("baidu", "test")
    job = s.claim()["job"]
    with pytest.raises(ProblemError):
        s.finish(job_id=job["id"], revision=999, success=True, error_code=None, backup_id=None)
    row = db.get(CloudBackupJob, job["id"])
    row.started_at = datetime.now(UTC) - timedelta(hours=1)
    row.lease_seen_at = row.started_at
    db.flush()
    assert s.claim() == {"job": None}
    assert row.status == "failed" and row.error_code == "cloud_worker_timeout"


def test_job_heartbeat_requires_running_identity_and_refreshes_lease(db):
    s = service(db)
    save(s)
    s.enqueue("baidu", "test")
    job = s.claim()["job"]
    row = db.get(CloudBackupJob, job["id"])
    row.lease_seen_at = datetime.now(UTC) - timedelta(hours=1)
    assert s.heartbeat(job_id=job["id"], revision=job["revision"]) == {"ok": True}
    assert row.lease_seen_at > datetime.now(UTC) - timedelta(seconds=10)
    with pytest.raises(ProblemError):
        s.heartbeat(job_id=job["id"], revision=999)


def test_heartbeat_side_effect_is_rolled_back_with_failed_finish(db, monkeypatch):
    from app.operations import cloud_service
    from app.operations.models import OperationsServiceHeartbeat

    s = service(db)
    save(s)
    s.enqueue("baidu", "test")
    job = s.claim()["job"]
    before = db.get(OperationsServiceHeartbeat, "cloud_backup").last_seen_at
    original = cloud_service.record_service_heartbeat

    def fail_after_write(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("controlled failed finish")

    monkeypatch.setattr(cloud_service, "record_service_heartbeat", fail_after_write)
    with pytest.raises(RuntimeError):
        s.finish(
            job_id=job["id"],
            revision=job["revision"],
            success=True,
            error_code=None,
            backup_id=None,
        )
    db.expire_all()
    assert db.get(OperationsServiceHeartbeat, "cloud_backup").last_seen_at == before
    assert db.get(CloudBackupJob, job["id"]).status == "running"


def test_http_role_csrf_and_secret_redaction(db_client, platform_users):
    def login(key):
        identifier, password, _ = platform_users["credentials"][key]
        r = db_client.post(
            "/api/v1/auth/login", json={"identifier": identifier, "password": password}
        )
        assert r.status_code == 200
        return {"X-CSRF-Token": r.json()["csrf_token"]}

    headers = login("customer_service")
    assert db_client.get("/api/v1/operations/cloud-backups").status_code == 403
    assert (
        db_client.put(
            "/api/v1/operations/cloud-backups/baidu",
            headers=headers,
            json={"remote_path": "/b", "username": "u", "password": "s", "enabled": False},
        ).status_code
        == 403
    )
    headers = login("admin")
    assert db_client.get("/api/v1/operations/cloud-backups").status_code == 200
    data = {
        "remote_path": "/backup",
        "username": "operator",
        "password": "super-secret",
        "enabled": False,
    }
    assert db_client.put("/api/v1/operations/cloud-backups/baidu", json=data).status_code == 403
    response = db_client.put("/api/v1/operations/cloud-backups/baidu", headers=headers, json=data)
    assert response.status_code == 200
    assert "super-secret" not in response.text
    assert response.headers["cache-control"] == "no-store, private"
    assert (
        db_client.post("/api/v1/operations/cloud-backups/baidu/test", headers=headers).status_code
        == 200
    )
    assert (
        db_client.put(
            "/api/v1/operations/cloud-backups/baidu", headers=headers, json=data
        ).status_code
        == 409
    )
    assert (
        db_client.put(
            "/api/v1/operations/cloud-backups/unknown", headers=headers, json=data
        ).status_code
        == 422
    )


def test_api_changes_have_secret_free_audit(db_client, platform_users, db):
    identifier, password, _ = platform_users["credentials"]["admin"]
    login = db_client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    assert (
        db_client.put(
            "/api/v1/operations/cloud-backups/115",
            headers=headers,
            json={
                "remote_path": "/b",
                "username": "operator",
                "password": "super-secret",
                "enabled": False,
            },
        ).status_code
        == 200
    )
    assert (
        db_client.post("/api/v1/operations/cloud-backups/115/test", headers=headers).status_code
        == 200
    )
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.action.like("operations.cloud_backup.%")))
    )
    assert {e.action for e in events} == {
        "operations.cloud_backup.115.update",
        "operations.cloud_backup.115.test",
    }
    assert all(e.actor_user_id == platform_users["accounts"]["admin"].id for e in events)
    assert "super-secret" not in str([e.event_metadata for e in events])


def test_explicit_monitor_grant_cannot_replace_role_and_superadmin_deny_blocks(
    db_client, platform_users, db
):
    from app.rbac.models import Permission, UserPermissionOverride

    permission = db.scalar(select(Permission).where(Permission.key == "operations.monitor.read"))
    for key, effect in [("customer_service", "allow"), ("admin", "deny")]:
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"][key].id,
                permission_id=permission.id,
                effect=effect,
                scope_kind="global",
            )
        )
    db.commit()
    for key in ["customer_service", "admin"]:
        identifier, password, _ = platform_users["credentials"][key]
        assert (
            db_client.post(
                "/api/v1/auth/login", json={"identifier": identifier, "password": password}
            ).status_code
            == 200
        )
        assert db_client.get("/api/v1/operations/cloud-backups").status_code == 403
