"""Builtin import boundaries: a missing guard must expose an observable failure."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.audit.models import AuditEvent
from app.core.errors import ProblemError
from app.operations.cloud_auth_models import (
    CloudAuthOperation,
    CloudAuthSession,
    CloudConnectorState,
)
from app.operations.cloud_auth_requests import CloudImportCommand
from app.operations.models import OperationsServiceHeartbeat
from tests.api.test_cloud_authorization import (
    BASE,
    actor,
    backend,
    configured,
    login,
    prerequisites,
)

MODE = "openlist_builtin_import"
ACCESS = "synthetic-access-never-public"
REFRESH = "synthetic-refresh-never-public"


def start(client, headers):
    response = client.post(
        BASE + "/sessions", headers=headers, json={"operation_id": str(uuid4()), "mode": MODE}
    )
    assert response.status_code == 202
    assert response.json()["state"] == "awaiting_import"
    return response.json()


def payload(revision=1, key=None, access=ACCESS, refresh=REFRESH):
    return {
        "expected_revision": revision,
        "operation_id": str(key or uuid4()),
        "access_token": access,
        "refresh_token": refresh,
    }


@pytest.mark.parametrize("provider", ["baidu", "115"])
def test_builtin_without_application_queues_only_local_create_record(
    db_client, db, platform_users, provider
):
    headers = login(db_client, platform_users)
    prerequisites(db)
    response = db_client.post(
        BASE.replace("/baidu/", f"/{provider}/") + "/sessions",
        headers=headers,
        json={"operation_id": str(uuid4()), "mode": MODE},
    )
    assert response.status_code == 202
    view = response.json()
    assert view["provider"] == provider and view["state"] == "awaiting_import"
    assert view["mode"] == MODE
    row = db.get(CloudAuthSession, UUID(view["id"]))
    assert row.app_revision is None and row.application_provider is None
    assert row.protocol_version == 2
    operations = list(db.scalars(select(CloudAuthOperation)))
    assert len(operations) == 1
    assert operations[0].action == "create_import" and operations[0].status == "succeeded"
    assert operations[0].payload_ciphertext is None


@pytest.mark.parametrize(
    "missing,code",
    [("connector", "cloud_connector_unavailable"), ("runner", "cloud_runner_offline")],
)
def test_import_without_app_still_requires_connector_and_runner(
    db_client, db, platform_users, missing, code
):
    headers = login(db_client, platform_users)
    prerequisites(db)
    if missing == "connector":
        db.scalar(select(CloudConnectorState)).registered = False
    else:
        db.get(OperationsServiceHeartbeat, "cloud_authorization").status = "failed"
    db.flush()
    response = db_client.post(
        BASE + "/sessions", headers=headers, json={"operation_id": str(uuid4()), "mode": MODE}
    )
    assert response.status_code == 409 and response.json()["code"] == code
    assert db.scalar(select(CloudAuthOperation)) is None


def test_import_replay_conflict_private_query_and_secret_free_audit(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    url = BASE + f"/sessions/{session['id']}/import"
    command = payload(access="  " + ACCESS + "  ")
    first = db_client.post(url, headers=headers, json=command)
    assert first.status_code == 202
    assert first.json()["status"] == "pending"
    assert first.json()["session"]["state"] == "verifying_import"
    duplicate = db_client.post(url, headers=headers, json={**command, "access_token": ACCESS})
    assert duplicate.status_code == 202 and duplicate.json() == first.json()
    conflict = db_client.post(
        url, headers=headers, json={**command, "access_token": "different-token"}
    )
    assert (
        conflict.status_code == 409 and conflict.json()["code"] == "cloud_auth_operation_conflict"
    )
    query = BASE + f"/sessions/{session['id']}/operations/{command['operation_id']}"
    result = db_client.get(query)
    assert result.status_code == 202 and result.json() == first.json()
    for response in (first, duplicate, conflict, result):
        assert ACCESS not in response.text and REFRESH not in response.text
        assert "ciphertext" not in response.text
        assert response.headers["cache-control"] == "no-store, private"
    operations = list(
        db.scalars(select(CloudAuthOperation).where(CloudAuthOperation.action == "verify_import"))
    )
    assert len(operations) == 1 and ACCESS not in operations[0].payload_ciphertext
    assert REFRESH not in operations[0].request_fingerprint
    assert all(
        ACCESS not in str(event.event_metadata) and REFRESH not in str(event.event_metadata)
        for event in db.scalars(select(AuditEvent))
    )
    before = db.get(CloudAuthSession, UUID(session["id"])).expires_at
    operations[0].status = "succeeded"
    db.flush()
    assert db_client.get(query).status_code == 200
    replay = db_client.post(url, headers=headers, json=command)
    assert replay.status_code == 200 and replay.json()["status"] == "succeeded"
    assert db.get(CloudAuthSession, UUID(session["id"])).expires_at == before


def test_import_rechecks_runtime_before_enqueuing(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    db.get(OperationsServiceHeartbeat, "cloud_authorization").status = "failed"
    db.flush()
    response = db_client.post(
        BASE + f"/sessions/{session['id']}/import", headers=headers, json=payload()
    )
    assert response.status_code == 409 and response.json()["code"] == "cloud_runner_offline"
    assert (
        db.scalar(select(CloudAuthOperation).where(CloudAuthOperation.action == "verify_import"))
        is None
    )


def test_verified_builtin_directory_queue_uses_frozen_source_without_application(
    db, platform_users
):
    from app.operations.cloud_auth_requests import CloudDirectoryRequest
    from app.operations.cloud_auth_state import aad

    service = backend(db)
    owner = actor(db, platform_users)
    prerequisites(db)
    session = service.create_session("baidu", owner, uuid4(), mode=MODE)
    row = db.get(CloudAuthSession, session.id)
    row.state = "authorized"
    command = CloudDirectoryRequest(
        expected_revision=row.revision, operation_id=uuid4(), parent="/"
    )
    pending = service.queue_directories(row.id, owner, command)
    assert pending.status == "pending"
    operation = db.scalar(
        select(CloudAuthOperation).where(CloudAuthOperation.idempotency_key == command.operation_id)
    )
    operation.status = "succeeded"
    operation.result_ciphertext = service.cipher.encrypt(
        '{"entries":[{"name":"backup","path":"/backup"}]}',
        field_name=aad("baidu", operation.id, "result", mode=MODE, protocol_version=2),
    )
    db.flush()
    result = service.get_directories(row.id, owner, command.operation_id)
    assert result.status == "ready" and result.entries[0].path == "/backup"


def test_import_limit_five_per_minute_and_positive_retry_after(db, platform_users):
    owner = actor(db, platform_users)
    prerequisites(db)
    service = backend(db)
    session = service.create_session("baidu", owner, uuid4(), mode=MODE)
    row = db.get(CloudAuthSession, session.id)
    for index in range(5):
        result = service.submit_import(
            row.id,
            owner,
            CloudImportCommand(
                **payload(row.revision, access=f"access-{index}", refresh=f"refresh-{index}")
            ),
        )
        operation = db.scalar(
            select(CloudAuthOperation).where(
                CloudAuthOperation.idempotency_key == result.operation_id
            )
        )
        operation.status = "failed"
        row.state = "awaiting_import"
        db.flush()
    with pytest.raises(ProblemError) as error:
        service.submit_import(row.id, owner, CloudImportCommand(**payload(row.revision)))
    assert error.value.status == 429 and error.value.retry_after_seconds > 0


def test_creation_rate_limit_combines_modes(db, platform_users):
    service, owner = configured(db, platform_users)
    for mode in (MODE, "own_app", MODE):
        result = service.create_session("baidu", owner, uuid4(), mode=mode)
        service.cancel_session(result.id, owner, result.revision)
    with pytest.raises(ProblemError) as error:
        service.create_session("baidu", owner, uuid4())
    assert error.value.status == 429 and error.value.retry_after_seconds > 0


@pytest.mark.parametrize(
    "token",
    ["", "a b", "a\u0000b", "https://evil.invalid/secret", "12345", "x" * 4097],
    ids=["empty", "space", "control", "url", "uid", "overlong"],
)
def test_import_validation_has_fixed_fields_and_no_input(
    db_client, db, platform_users, token, caplog
):
    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    response = db_client.post(
        BASE + f"/sessions/{session['id']}/import",
        headers=headers,
        json=payload(access=token),
    )
    assert response.status_code == 422
    assert ACCESS not in response.text and REFRESH not in response.text
    if token:
        assert token not in response.text and json.dumps(token)[1:-1] not in response.text
    assert "evil-field" not in response.text
    assert response.json()["detail"] == "授权请求格式无效"
    assert all(
        item["field"]
        in {"access_token", "refresh_token", "operation_id", "expected_revision", "request"}
        for item in response.json()["errors"]
    )
    assert ACCESS not in caplog.text and REFRESH not in caplog.text
    if token:
        assert token not in caplog.text


def test_import_extra_field_name_and_value_are_never_echoed(db_client, db, platform_users, caplog):
    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    response = db_client.post(
        BASE + f"/sessions/{session['id']}/import",
        headers=headers,
        json={**payload(), "evil-field-" + ACCESS: REFRESH},
    )
    assert response.status_code == 422
    assert response.json()["errors"] == [{"field": "request", "message": "字段格式无效"}]
    assert (
        ACCESS not in response.text
        and REFRESH not in response.text
        and "evil-field" not in response.text
    )
    assert ACCESS not in caplog.text and REFRESH not in caplog.text


def test_actual_body_limit_without_content_length(client):
    def chunks():
        yield b'{"unexpected":"'
        yield b"x" * 17000
        yield b'"}'

    response = client.post(
        BASE + "/sessions", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["code"] == "cloud_auth_body_too_large"
    assert response.headers["cache-control"] == "no-store, private"


def test_import_csrf_other_login_expiry_and_cancel_are_rejected(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    url = BASE + f"/sessions/{session['id']}/import"
    assert db_client.post(url, json=payload()).status_code == 403
    headers = login(db_client, platform_users)
    assert db_client.post(url, headers=headers, json=payload()).status_code == 404
    assert (
        db_client.get(BASE + f"/sessions/{session['id']}/operations/{uuid4()}").status_code == 404
    )
    fresh = start_after_cancel(db_client, db, headers, session)
    row = db.get(CloudAuthSession, UUID(fresh["id"]))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.flush()
    result = db_client.post(
        BASE + f"/sessions/{fresh['id']}/import", headers=headers, json=payload()
    )
    assert result.status_code == 409 and result.json()["code"] == "cloud_auth_expired"


def start_after_cancel(client, db, headers, old):
    # Retire the other-login fixture directly: public ownership must stay private.
    db.get(CloudAuthSession, UUID(old["id"])).state = "cancelled"
    db.flush()
    return start(client, headers)


def test_consumed_token_hmac_is_encrypted_provider_separated_and_blocks_reuse(db, platform_users):
    from app.operations.cloud_auth_security import (
        import_token_digests,
        load_import_token_digests,
        store_import_token_digests,
    )
    from app.operations.cloud_auth_state import aad

    service = backend(db)
    owner = actor(db, platform_users)
    prerequisites(db)
    row = service.create_session("baidu", owner, uuid4(), mode=MODE)
    result = service.submit_import(row.id, owner, CloudImportCommand(**payload()))
    operation = db.scalar(
        select(CloudAuthOperation).where(CloudAuthOperation.idempotency_key == result.operation_id)
    )
    clear = service.cipher.decrypt(
        operation.payload_ciphertext,
        field_name=aad("baidu", operation.id, "payload", mode=MODE, protocol_version=2),
    )
    assert json.loads(clear) == {"access_token": ACCESS, "refresh_token": REFRESH}
    originals = load_import_token_digests(operation, service.cipher)
    assert len(originals) == 2
    rotated = import_token_digests(service.settings, "baidu", "new-access", "new-refresh")
    store_import_token_digests(operation, originals + rotated, service.cipher)
    operation.refresh_phase = "verified"
    assert set(originals).isdisjoint(import_token_digests(service.settings, "115", ACCESS, REFRESH))
    assert all(value not in operation.import_token_digests_ciphertext for value in originals)
    service.cancel_session(row.id, owner, result.session.revision)
    assert operation.payload_ciphertext is None
    assert len(load_import_token_digests(operation, service.cipher)) == 4
    next_session = service.create_session("baidu", owner, uuid4(), mode=MODE)
    for tokens in ((ACCESS, REFRESH), ("new-access", "new-refresh")):
        with pytest.raises(ProblemError) as error:
            service.submit_import(
                next_session.id,
                owner,
                CloudImportCommand(**payload(access=tokens[0], refresh=tokens[1])),
            )
        assert error.value.code == "cloud_import_credentials_reused"


def test_import_cancel_clears_raw_secrets_and_terminal_replay_returns_original(
    db_client, db, platform_users
):
    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    url = BASE + f"/sessions/{session['id']}/import"
    command = payload()
    queued = db_client.post(url, headers=headers, json=command).json()
    cancelled = db_client.post(
        BASE + f"/sessions/{session['id']}/cancel",
        headers=headers,
        json={"expected_revision": queued["session"]["revision"]},
    )
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    operation = db.scalar(
        select(CloudAuthOperation).where(CloudAuthOperation.action == "verify_import")
    )
    assert operation.payload_ciphertext is None and operation.refresh_checkpoint_ciphertext is None
    replay = db_client.post(url, headers=headers, json=command)
    assert replay.status_code == 200 and replay.json()["status"] == "cancelled"
    other = db_client.post(url, headers=headers, json=payload(queued["session"]["revision"]))
    assert other.status_code == 409


def test_known_owned_app_credentials_are_rejected_before_builtin_refresh(db, platform_users):
    from app.operations.cloud_auth_security import import_token_digests, store_import_token_digests

    service, owner = configured(db, platform_users)
    old = service.create_session("baidu", owner, uuid4())
    row = db.get(CloudAuthSession, old.id)
    poll = service._operation(row, owner, uuid4(), "poll", "f" * 64, {})
    poll.status = "succeeded"
    store_import_token_digests(
        poll, import_token_digests(service.settings, "baidu", ACCESS, REFRESH), service.cipher
    )
    service.cancel_session(old.id, owner, old.revision)
    new = service.create_session("baidu", owner, uuid4(), mode=MODE)
    with pytest.raises(ProblemError) as error:
        service.submit_import(new.id, owner, CloudImportCommand(**payload()))
    assert error.value.code == "cloud_import_credentials_reused"


@pytest.mark.parametrize("key", ["customer_service", "admin"])
def test_import_requires_actual_superadmin_and_rechecks_permission(
    db_client, db, platform_users, key
):
    from app.rbac.models import Permission, UserPermissionOverride

    headers = login(db_client, platform_users)
    prerequisites(db)
    session = start(db_client, headers)
    permission = db.scalar(select(Permission).where(Permission.key == "operations.monitor.read"))
    account = platform_users["accounts"][key]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=permission.id,
            effect="deny" if key == "admin" else "allow",
            scope_kind="global",
        )
    )
    db.flush()
    if key != "admin":
        headers = login(db_client, platform_users, key)
    response = db_client.post(
        BASE + f"/sessions/{session['id']}/import", headers=headers, json=payload()
    )
    assert response.status_code == 403
    assert (
        db.scalar(select(CloudAuthOperation).where(CloudAuthOperation.action == "verify_import"))
        is None
    )
