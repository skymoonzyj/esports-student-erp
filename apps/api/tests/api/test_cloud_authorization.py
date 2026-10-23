from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.core.errors import ProblemError
from app.operations.cloud_auth_models import (
    CloudAuthApplication,
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
    CloudConnectorState,
)
from app.operations.cloud_auth_schemas import AuthActor, CloudAppUpdate
from app.operations.cloud_auth_state import aad
from app.operations.models import OperationsServiceHeartbeat
from app.rbac.models import Permission, UserPermissionOverride
from app.security.encryption import EnvelopeCipher

BASE = "/api/v1/operations/cloud-backups/baidu/authorization"


def test_malformed_authorization_json_has_private_headers(client):
    response = client.post(
        BASE + "/sessions", content="{", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_directory_result_rejects_changed_live_application_revision(db, platform_users):
    from app.operations.cloud_auth_requests import CloudDirectoryRequest

    service, owner = configured(db, platform_users)
    view = service.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, view.id)
    row.state = "configuring"
    key = uuid4()
    service.queue_directories(
        row.id,
        owner,
        CloudDirectoryRequest(expected_revision=row.revision, operation_id=key, parent="/"),
    )
    operation = db.scalar(
        select(CloudAuthOperation).where(CloudAuthOperation.idempotency_key == key)
    )
    operation.status = "succeeded"
    operation.result_ciphertext = service.cipher.encrypt(
        '{"entries":[]}',
        field_name=aad(
            "baidu",
            operation.id,
            "result",
            mode=operation.mode,
            protocol_version=operation.protocol_version,
        ),
    )
    app = db.get(CloudAuthApplication, "baidu")
    app.revision += 1
    db.flush()
    result = service.get_directories(row.id, owner, key)
    assert result.status == "failed"
    assert result.error_code == "cloud_auth_revision_changed"


@pytest.mark.parametrize("probe_status", ["queued", "failed"])
def test_directory_change_rejects_pending_probe_to_preserve_cleanup_scope(
    db, platform_users, probe_status
):
    from app.operations.cloud_auth_requests import CloudDirectorySelection

    service, owner = configured(db, platform_users)
    view = service.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, view.id)
    row.state = "test_pending"
    row.selected_directory = "/before"
    connector = db.scalar(select(CloudConnectorState))
    candidate = CloudAuthCandidate(
        id=uuid4(),
        session_id=row.id,
        provider="baidu",
        operation_id=uuid4(),
        connector_id=connector.id,
        mount_path="/erp-candidates/test",
        state="configured",
        config_revision=1,
        selected_directory="/before",
    )
    db.add(candidate)
    row.candidate_id = candidate.id
    db.flush()
    probe = service._operation(row, owner, uuid4(), "probe", "f" * 64, {})
    probe.status = probe_status
    if probe_status == "failed":
        probe.result_ciphertext = service.cipher.encrypt(
            '{"finished":false,"cleanup_pending":true}',
            field_name=aad(
                "baidu", probe.id, "probe", mode=probe.mode, protocol_version=probe.protocol_version
            ),
        )
    db.commit()
    with pytest.raises(ProblemError) as error:
        service.select_directory(
            row.id,
            owner,
            CloudDirectorySelection(
                expected_revision=row.revision, operation_id=uuid4(), directory="/after"
            ),
        )
    assert error.value.code == "cloud_target_busy"
    assert row.selected_directory == "/before"


def test_probe_during_pending_directory_update_is_rejected(db, platform_users):
    from app.operations.cloud_auth_requests import CloudOperationCommand

    service, owner = configured(db, platform_users)
    view = service.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, view.id)
    row.state = "configuring"
    row.selected_directory = "/new"
    row.candidate_id = uuid4()
    db.flush()
    with pytest.raises(ProblemError) as error:
        service.queue_test(
            row.id,
            owner,
            CloudOperationCommand(expected_revision=row.revision, operation_id=uuid4()),
        )
    assert error.value.code == "cloud_auth_state_invalid"


def login(client, users, key="admin"):
    identifier, password, _ = users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login", json={"identifier": identifier, "password": password}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def backend(db):
    from app.operations.cloud_auth_service import CloudAuthorizationService

    return CloudAuthorizationService(db, cipher=EnvelopeCipher(bytes(range(32))))


def actor(db, users):
    account_id = users["accounts"]["admin"].id
    session = AuthSession(
        user_id=account_id,
        token_digest=uuid4().hex,
        csrf_digest=uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(session)
    db.flush()
    return AuthActor(account_id=account_id, login_session_id=session.id)


def prerequisites(db):
    now = datetime.now(UTC)
    connector = CloudConnectorState(id=uuid4(), registered=True, last_seen_at=now)
    db.add(connector)
    db.add(
        OperationsServiceHeartbeat(
            service_key="cloud_authorization",
            status="healthy",
            last_seen_at=now,
            last_success_at=now,
            consecutive_failures=0,
        )
    )
    db.flush()
    return connector


def configured(db, users):
    s = backend(db)
    owner = actor(db, users)
    prerequisites(db)
    s.save_application(
        "baidu",
        owner,
        CloudAppUpdate(
            client_id="my-owned-app", client_secret="never-return-this-secret", expected_revision=0
        ),
    )
    return s, owner


def test_authorization_entrypoint_requires_login(client):
    # Removing the route or omitting session authentication must fail this.
    response = client.get(BASE + "/application")
    assert response.status_code == 401


def test_unconfigured_application_is_readable_and_reports_prerequisites(db_client, platform_users):
    login(db_client, platform_users)
    response = db_client.get(BASE + "/application")
    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["revision"] == 0
    assert set(response.json()["prerequisites"]) == {
        "cloud_app_unconfigured",
        "cloud_connector_unavailable",
        "cloud_runner_offline",
    }
    assert response.headers["cache-control"] == "no-store, private"


def test_real_superadmin_and_global_permission_both_required(db_client, platform_users, db):
    permission = db.scalar(select(Permission).where(Permission.key == "operations.monitor.read"))
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["customer_service"].id,
            permission_id=permission.id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=permission.id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    for key in ("customer_service", "admin"):
        login(db_client, platform_users, key)
        response = db_client.get(BASE + "/application")
        assert response.status_code == 403
        assert response.headers["cache-control"] == "no-store, private"


def test_application_secret_is_write_only_and_csrf_required(db_client, platform_users, db):
    headers = login(db_client, platform_users)
    payload = {
        "client_id": "my-owned-app",
        "client_secret": "never-return-this-secret",
        "expected_revision": 0,
    }
    assert db_client.put(BASE + "/application", json=payload).status_code == 403
    response = db_client.put(BASE + "/application", headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()["secret_present"] is True
    assert "never-return-this-secret" not in response.text
    row = db.get(CloudAuthApplication, "baidu")
    assert "never-return-this-secret" not in row.secret_ciphertext
    for event in db.scalars(
        select(AuditEvent).where(AuditEvent.action.like("operations.cloud_authorization.%"))
    ):
        assert "never-return-this-secret" not in str(event.event_metadata)


def test_missing_prerequisite_never_creates_session_or_fake_qr(db_client, platform_users, db):
    headers = login(db_client, platform_users)
    response = db_client.post(
        BASE + "/sessions", headers=headers, json={"operation_id": str(uuid4())}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "cloud_app_unconfigured"
    assert db.scalar(select(CloudAuthSession)) is None


def test_application_empty_secret_preserves_and_explicit_clear_removes(db, platform_users):
    s, owner = configured(db, platform_users)
    view = s.save_application(
        "baidu",
        owner,
        CloudAppUpdate(client_id="my-owned-app", client_secret="", expected_revision=1),
    )
    assert view.secret_present
    assert view.revision == 1
    view = s.save_application(
        "baidu",
        owner,
        CloudAppUpdate(client_id="my-owned-app", clear_secret=True, expected_revision=1),
    )
    assert not view.secret_present and not view.configured
    assert view.revision == 2


def test_create_idempotency_and_cross_login_owner_hidden(db, platform_users):
    s, owner = configured(db, platform_users)
    key = uuid4()
    first = s.create_session("baidu", owner, key)
    assert s.create_session("baidu", owner, key).id == first.id
    another = actor(db, platform_users)
    with pytest.raises(ProblemError) as error:
        s.get_session(first.id, another)
    assert error.value.status == 404
    assert first.state == "creating"
    assert len(list(db.scalars(select(CloudAuthOperation)))) == 1


@pytest.mark.parametrize("change", ["revoke", "disable", "password", "delete", "permission"])
def test_queued_owner_authority_is_reloaded_before_any_later_operation(db, platform_users, change):
    from app.identity.models import Person

    s, owner = configured(db, platform_users)
    view = s.create_session("baidu", owner, uuid4())
    account = db.get(UserAccount, owner.account_id)
    if change == "revoke":
        db.get(AuthSession, owner.login_session_id).revoked_at = datetime.now(UTC)
    elif change == "disable":
        account.status = "disabled"
    elif change == "password":
        account.force_password_change = True
    elif change == "delete":
        db.get(Person, account.person_id).deleted_at = datetime.now(UTC)
    else:
        permission = db.scalar(
            select(Permission).where(Permission.key == "operations.monitor.read")
        )
        db.add(
            UserPermissionOverride(
                user_id=owner.account_id,
                permission_id=permission.id,
                effect="deny",
                scope_kind="global",
            )
        )
    db.flush()
    with pytest.raises(ProblemError):
        s.get_session(view.id, owner)


def test_create_rate_limit_is_database_count_and_returns_retry_after(db, platform_users):
    s, owner = configured(db, platform_users)
    for _ in range(3):
        view = s.create_session("baidu", owner, uuid4())
        s.cancel_session(view.id, owner, view.revision)
    with pytest.raises(ProblemError) as error:
        s.create_session("baidu", owner, uuid4())
    assert error.value.status == 429
    assert 0 < error.value.retry_after_seconds <= 60


def test_cancel_wipes_transient_secrets_and_cancels_forward_jobs(db, platform_users):
    s, owner = configured(db, platform_users)
    view = s.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, view.id)
    row.qr_ciphertext = row.device_ciphertext = row.token_ciphertext = "encrypted-material"
    view = s.cancel_session(view.id, owner, view.revision)
    assert view.state == "cancelled"
    assert row.qr_ciphertext is row.device_ciphertext is row.token_ciphertext is None
    assert db.scalar(select(CloudAuthOperation)).status == "cancelled"


def test_stale_revision_and_maintenance_reject_changes(db, platform_users):
    s, owner = configured(db, platform_users)
    view = s.create_session("baidu", owner, uuid4())
    with pytest.raises(ProblemError) as error:
        s.cancel_session(view.id, owner, 100)
    assert error.value.code == "cloud_auth_revision_changed"
    connector = db.scalar(select(CloudConnectorState))
    connector.maintenance_operation_id = uuid4()
    connector.maintenance_lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)
    db.flush()
    with pytest.raises(ProblemError) as error:
        s.save_application("baidu", owner, CloudAppUpdate(client_id="changed", expected_revision=1))
    assert error.value.code == "cloud_maintenance_active"


def test_directory_queue_is_owner_bound_and_poll_exposes_only_entries(db, platform_users):
    from app.operations.cloud_auth_requests import CloudDirectoryRequest

    s, owner = configured(db, platform_users)
    view = s.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, view.id)
    row.state = "test_pending"
    key = uuid4()
    pending = s.queue_directories(
        view.id, owner, CloudDirectoryRequest(operation_id=key, expected_revision=1, parent="/")
    )
    assert pending.status == "pending" and pending.entries == []
    operation = db.scalar(
        select(CloudAuthOperation).where(CloudAuthOperation.action == "list_directories")
    )
    operation.status = "succeeded"
    operation.result_ciphertext = s.cipher.encrypt(
        '{"entries":[{"name":"中文 空格%","path":"/中文 空格%"}]}',
        field_name=aad(
            "baidu",
            operation.id,
            "result",
            mode=operation.mode,
            protocol_version=operation.protocol_version,
        ),
    )
    result = s.get_directories(view.id, owner, key)
    assert result.status == "ready"
    assert result.entries[0].path == "/中文 空格%"
    assert result.operation_id == key
    with pytest.raises(ProblemError) as error:
        s.get_directories(view.id, actor(db, platform_users), key)
    assert error.value.status == 404


def test_directory_result_does_not_survive_expiry_or_revision_change(db, platform_users):
    from app.operations.cloud_auth_requests import CloudDirectoryRequest

    s, owner = configured(db, platform_users)
    view = s.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, view.id)
    row.state = "test_pending"
    key = uuid4()
    s.queue_directories(
        view.id, owner, CloudDirectoryRequest(operation_id=key, expected_revision=1)
    )
    operation = db.scalar(
        select(CloudAuthOperation).where(CloudAuthOperation.action == "list_directories")
    )
    operation.status = "succeeded"
    operation.result_ciphertext = s.cipher.encrypt(
        '{"entries":[]}',
        field_name=aad(
            "baidu",
            operation.id,
            "result",
            mode=operation.mode,
            protocol_version=operation.protocol_version,
        ),
    )
    row.revision = 2
    assert s.get_directories(view.id, owner, key).error_code == "cloud_auth_revision_changed"
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(ProblemError) as error:
        s.get_directories(view.id, owner, key)
    assert error.value.code == "cloud_auth_expired"


def test_one_hour_create_limit_counts_across_login_sessions(db, platform_users):
    s, owner = configured(db, platform_users)
    for _ in range(10):
        view = s.create_session("baidu", owner, uuid4())
        s.cancel_session(view.id, owner, view.revision)
        operation = db.scalar(
            select(CloudAuthOperation).where(CloudAuthOperation.session_id == view.id)
        )
        operation.created_at = datetime.now(UTC) - timedelta(minutes=10)
        db.flush()
    another_login = actor(db, platform_users)
    with pytest.raises(ProblemError) as error:
        s.create_session("baidu", another_login, uuid4())
    assert error.value.status == 429
    assert 2900 < error.value.retry_after_seconds <= 3000


def test_operation_key_reuse_cannot_change_provider_or_directory_request(db, platform_users):
    from app.operations.cloud_auth_requests import CloudDirectoryRequest

    s, owner = configured(db, platform_users)
    key = uuid4()
    view = s.create_session("baidu", owner, key)
    with pytest.raises(ProblemError) as error:
        s.create_session("115", owner, key)
    assert error.value.code == "cloud_auth_operation_conflict"
    row = db.get(CloudAuthSession, view.id)
    row.state = "test_pending"
    key = uuid4()
    s.queue_directories(
        view.id, owner, CloudDirectoryRequest(operation_id=key, expected_revision=1)
    )
    with pytest.raises(ProblemError) as error:
        s.queue_directories(
            view.id,
            owner,
            CloudDirectoryRequest(operation_id=key, expected_revision=1, parent="/other"),
        )
    assert error.value.code == "cloud_auth_operation_conflict"


def test_qr_is_owned_ephemeral_and_not_in_session_view(db_client, platform_users, db):
    import json

    headers = login(db_client, platform_users)
    prerequisites(db)
    assert (
        db_client.put(
            BASE + "/application",
            headers=headers,
            json={"client_id": "owned", "client_secret": "secret", "expected_revision": 0},
        ).status_code
        == 200
    )
    response = db_client.post(
        BASE + "/sessions", headers=headers, json={"operation_id": str(uuid4())}
    )
    assert response.status_code == 202
    row = db.get(CloudAuthSession, UUID(response.json()["id"]))
    row.state = "awaiting_confirmation"
    row.qr_expires_at = row.expires_at
    content = "https://openapi.baidu.com/oauth/2.0/device?code=short-device"
    row.qr_ciphertext = EnvelopeCipher(bytes(range(32))).encrypt(
        json.dumps(
            {"kind": "payload", "content": content, "expires_at": row.expires_at.isoformat()}
        ),
        field_name=aad("baidu", row.id, "qr", mode=row.mode, protocol_version=row.protocol_version),
    )
    db.flush()
    response = db_client.get(BASE + f"/sessions/{row.id}/qr")
    assert response.status_code == 200 and response.json()["content"] == content
    assert response.headers["cache-control"] == "no-store, private"
    assert "short-device" not in db_client.get(BASE + f"/sessions/{row.id}").text
    login(db_client, platform_users)
    assert db_client.get(BASE + f"/sessions/{row.id}/qr").status_code == 404
    assert db_client.get(BASE + f"/sessions/{row.id}").status_code == 404


@pytest.mark.parametrize(
    "path", ["/../outside", "/%2e%2e/outside", "/%252e%252e/outside", "/back\\evil", "/bad%00name"]
)
def test_directory_commands_reject_traversal_and_control_characters(path):
    from pydantic import ValidationError

    from app.operations.cloud_auth_requests import CloudDirectoryRequest

    with pytest.raises(ValidationError):
        CloudDirectoryRequest(operation_id=uuid4(), expected_revision=1, parent=path)


def test_audit_retains_only_allowlisted_authorization_error_codes():
    from app.audit.service import AuditService

    assert AuditService.public_metadata({"error_code": "cloud_auth_denied"}) == {
        "error_code": "cloud_auth_denied"
    }
    assert AuditService.public_metadata({"error_code": "access_token=do-not-return-secret"}) == {}
