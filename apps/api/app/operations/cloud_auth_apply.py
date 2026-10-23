"""Explicit, owner-bound configuration switches; host locking stays in private RPC.

Completion helpers are transaction participants, not public entry points: the
leased worker invokes them only while the host holds its ordered drain locks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_errors import auth_problem
from app.operations.cloud_auth_models import (
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
    CloudBackupTargetHistory,
)
from app.operations.cloud_auth_schemas import (
    TERMINAL_AUTH_STATES,
    AuthActor,
    AuthProvider,
    CloudAuthSessionView,
)
from app.operations.cloud_auth_security import require_auth_actor, require_no_maintenance
from app.operations.cloud_auth_service import CloudAuthorizationService, _fingerprint
from app.operations.cloud_auth_source import require_auth_source_current, require_auth_source_frozen
from app.operations.cloud_auth_state import AUTH_LIFETIME, aad
from app.operations.cloud_models import CloudBackupJob, CloudBackupTarget


def _service(db: Session) -> CloudAuthorizationService:
    from app.operations.cloud_auth_worker import cipher

    return CloudAuthorizationService(db, cipher=cipher())


def _target(db: Session, provider: str) -> CloudBackupTarget | None:
    return db.scalar(
        select(CloudBackupTarget)
        .where(CloudBackupTarget.provider == provider)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _retirement_source(
    db: Session, session: CloudAuthSession, target: CloudBackupTarget | None
) -> None:
    require_auth_source_frozen(session)
    candidate = db.get(CloudAuthCandidate, session.candidate_id) if session.candidate_id else None
    original = db.get(CloudAuthSession, candidate.session_id) if candidate else None
    if not target or not candidate or not original:
        raise auth_problem("cloud_config_changed")
    require_auth_source_frozen(original)
    fields = (
        "provider",
        "mode",
        "protocol_version",
        "auth_config_id",
        "auth_config_revision",
        "app_revision",
    )
    source = tuple(getattr(session, field) for field in fields)
    if (
        target.managed_candidate_id != candidate.id
        or target.provider != session.provider
        or candidate.state != "active"
        or original.state != "applied"
        or candidate.selected_directory != session.selected_directory
        or source != tuple(getattr(candidate, field) for field in fields)
        or source != tuple(getattr(original, field) for field in fields)
    ):
        raise auth_problem("cloud_config_changed")


def _versions(
    db: Session,
    session: CloudAuthSession,
    target: CloudBackupTarget | None,
    *,
    operation: CloudAuthOperation | None = None,
) -> None:
    if operation is not None and operation.action == "unbind":
        _retirement_source(db, session, target)
    else:
        require_auth_source_current(db, session)
    if (target.revision if target else 0) != session.target_revision:
        raise auth_problem("cloud_config_changed")


def _candidate(db: Session, session: CloudAuthSession) -> CloudAuthCandidate:
    from app.operations.cloud_auth_worker import decrypt

    row = db.get(CloudAuthCandidate, session.candidate_id) if session.candidate_id else None
    if (
        not row
        or row.session_id != session.id
        or row.provider != session.provider
        or (
            row.mode,
            row.protocol_version,
            row.auth_config_id,
            row.auth_config_revision,
            row.app_revision,
        )
        != (
            session.mode,
            session.protocol_version,
            session.auth_config_id,
            session.auth_config_revision,
            session.app_revision,
        )
        or row.state != "verified"
        or not row.selected_directory
        or row.selected_directory != session.selected_directory
        or not row.probe_operation_id
        or not row.probe_sha256
        or not row.probe_verified_at
    ):
        raise auth_problem("cloud_config_changed")
    origin = db.get(CloudAuthOperation, row.operation_id)
    probe = db.get(CloudAuthOperation, row.probe_operation_id)
    journal = decrypt(origin, "journal", origin.result_ciphertext) if origin else None
    if (
        not origin
        or not journal
        or not probe
        or probe.status != "succeeded"
        or probe.action != "probe"
        or probe.candidate_id != row.id
        or probe.session_id != session.id
        or (
            origin.mode,
            origin.protocol_version,
            origin.auth_config_id,
            origin.auth_config_revision,
            origin.app_revision,
        )
        != (
            session.mode,
            session.protocol_version,
            session.auth_config_id,
            session.auth_config_revision,
            session.app_revision,
        )
        or (
            probe.mode,
            probe.protocol_version,
            probe.auth_config_id,
            probe.auth_config_revision,
            probe.app_revision,
        )
        != (
            session.mode,
            session.protocol_version,
            session.auth_config_id,
            session.auth_config_revision,
            session.app_revision,
        )
        or row.mount_path != f"/erp-candidates/{row.connector_id.hex}/{row.operation_id.hex}"
        or not journal.get("delivered")
        or journal.get("creation_unknown")
        or journal.get("scope_pending")
        or (
            journal.get("operation_id"),
            journal.get("candidate_id"),
            journal.get("connector_id"),
            journal.get("provider"),
            journal.get("mount_path"),
            journal.get("storage_id"),
            journal.get("webdav_user_id"),
            journal.get("directory"),
            journal.get("config_revision"),
        )
        != (
            str(row.operation_id),
            str(row.id),
            str(row.connector_id),
            row.provider,
            row.mount_path,
            row.storage_id,
            row.webdav_user_id,
            row.selected_directory,
            row.config_revision,
        )
    ):
        raise auth_problem("cloud_config_changed")
    username = decrypt(row, "username", row.username_ciphertext)
    password = decrypt(row, "password", row.password_ciphertext)
    identity = decrypt(row, "account_fingerprint", row.account_fingerprint_ciphertext)
    if (
        not username
        or username.casefold() in {"admin", "administrator", "root"}
        or not password
        or not identity
        or not row.storage_id
        or not row.webdav_user_id
        or (username, password, identity)
        != (journal.get("username"), journal.get("password"), journal.get("account_fingerprint"))
    ):
        raise auth_problem("cloud_credentials_invalid")
    return row


def request_apply(
    db: Session, actor: AuthActor, session_id: UUID, expected_revision: int, operation_key: UUID
) -> CloudAuthSessionView:
    service = _service(db)
    fingerprint = _fingerprint(
        {"action": "apply", "session_id": str(session_id), "expected_revision": expected_revision}
    )
    with atomic_write_transaction(db):
        session = service._owned(session_id, actor, lock=True)
        existing = service._existing(actor, operation_key, fingerprint)
        if existing:
            return service._view(session)
        require_no_maintenance(db)
        if session.revision != expected_revision:
            raise auth_problem("cloud_auth_revision_changed")
        now = datetime.now(UTC)
        if session.expires_at <= now:
            raise auth_problem("cloud_auth_expired")
        if session.state != "ready":
            raise auth_problem("cloud_auth_state_invalid")
        target = _target(db, session.provider)
        _versions(db, session, target)
        if target and target.switch_operation_id:
            raise auth_problem("cloud_target_busy")
        candidate = _candidate(db, session)
        session.state, session.revision = "applying", session.revision + 1
        if session.mode == "own_app":
            session.expires_at = now + AUTH_LIFETIME
        session.error_code = None
        operation = service._operation(
            session,
            actor,
            operation_key,
            "apply",
            fingerprint,
            {
                "candidate_id": str(candidate.id),
                "config_revision": candidate.config_revision,
                "directory": candidate.selected_directory,
                "probe_operation_id": str(candidate.probe_operation_id),
            },
        )
        if target:
            target.switch_operation_id = operation.id
        db.flush()
        return service._view(session)


def request_unbind(
    db: Session,
    actor: AuthActor,
    provider: AuthProvider,
    expected_target_revision: int,
    operation_key: UUID,
) -> CloudAuthSessionView:
    service = _service(db)
    provider = service._provider(provider)
    fingerprint = _fingerprint(
        {
            "action": "unbind",
            "provider": provider,
            "expected_target_revision": expected_target_revision,
        }
    )
    with atomic_write_transaction(db):
        service._lock(provider, actor)
        existing = service._existing(actor, operation_key, fingerprint)
        if existing:
            return service._view(service._owned(existing.session_id, actor))
        require_no_maintenance(db)
        target = _target(db, provider)
        if not target or target.revision != expected_target_revision:
            raise auth_problem("cloud_config_changed")
        if not target.managed_candidate_id:
            raise auth_problem("cloud_auth_state_invalid")
        if target.switch_operation_id or db.scalar(
            select(CloudAuthSession.id).where(
                CloudAuthSession.provider == provider,
                CloudAuthSession.state.not_in(TERMINAL_AUTH_STATES),
            )
        ):
            raise auth_problem("cloud_target_busy")
        candidate = db.get(CloudAuthCandidate, target.managed_candidate_id)
        if not candidate or candidate.state != "active" or candidate.provider != provider:
            raise auth_problem("cloud_auth_state_invalid")
        now = datetime.now(UTC)
        session = CloudAuthSession(
            id=uuid4(),
            provider=provider,
            owner_account_id=actor.account_id,
            owner_login_session_id=actor.login_session_id,
            state="applying",
            revision=1,
            mode=candidate.mode,
            protocol_version=candidate.protocol_version,
            auth_config_id=candidate.auth_config_id,
            auth_config_revision=candidate.auth_config_revision,
            app_revision=candidate.app_revision,
            application_provider=provider if candidate.mode == "own_app" else None,
            target_revision=target.revision,
            expires_at=now + AUTH_LIFETIME,
            candidate_id=candidate.id,
            selected_directory=candidate.selected_directory,
        )
        db.add(session)
        db.flush()
        _retirement_source(db, session, target)
        operation = service._operation(session, actor, operation_key, "unbind", fingerprint, {})
        target.switch_operation_id = operation.id
        db.flush()
        return service._view(session)


def release_switch(db: Session, session: CloudAuthSession) -> None:
    """Retirement may clear only a freeze owned by this exact session."""
    target = _target(db, session.provider)
    if target and target.switch_operation_id:
        operation = db.get(CloudAuthOperation, target.switch_operation_id)
        if operation and operation.session_id == session.id:
            target.switch_operation_id = None


def prepare_apply(
    db: Session, *, session: CloudAuthSession, operation: CloudAuthOperation, now: datetime
) -> bool:
    if now >= session.expires_at:
        from app.operations.cloud_auth_worker import retire

        retire(db, session, now=now, code="cloud_target_busy")
        operation.status, operation.completed_at = "failed", now
        return False
    target = _target(db, session.provider)
    _versions(db, session, target, operation=operation)
    if target and target.switch_operation_id != operation.id:
        return False
    # Database drain is necessary, never sufficient: the private worker must
    # still receive a host receipt while actual external-transfer locks are held.
    return (
        db.scalar(
            select(CloudBackupJob.id).where(
                CloudBackupJob.provider == session.provider,
                CloudBackupJob.status.in_(("queued", "running")),
            )
        )
        is None
    )


def _completion(
    db: Session, session: CloudAuthSession, operation: CloudAuthOperation, now: datetime
) -> CloudBackupTarget | None:
    require_auth_actor(
        db,
        AuthActor(
            account_id=session.owner_account_id, login_session_id=session.owner_login_session_id
        ),
        lock=True,
    )
    require_no_maintenance(db)
    if (
        session.state != "applying"
        or session.expires_at <= now
        or operation.session_id != session.id
        or (operation.session_revision, operation.app_revision, operation.target_revision)
        != (session.revision, session.app_revision, session.target_revision)
        or (
            operation.mode,
            operation.protocol_version,
            operation.auth_config_id,
            operation.auth_config_revision,
        )
        != (
            session.mode,
            session.protocol_version,
            session.auth_config_id,
            session.auth_config_revision,
        )
    ):
        raise auth_problem("cloud_config_changed")
    if not prepare_apply(db, session=session, operation=operation, now=now):
        raise auth_problem("cloud_target_busy")
    return _target(db, session.provider)


def _history(db: Session, target: CloudBackupTarget, *, now: datetime) -> None:
    service = _service(db)
    history = CloudBackupTargetHistory(
        id=uuid4(),
        provider=target.provider,
        revision=target.revision,
        candidate_id=target.managed_candidate_id,
        last_backup_id=target.last_backup_id,
        last_success_at=target.last_success_at,
        connection_status=target.connection_status,
        created_at=now,
    )
    old = (
        db.get(CloudAuthCandidate, target.managed_candidate_id)
        if target.managed_candidate_id
        else None
    )
    owner = db.get(CloudAuthSession, old.session_id) if old else None
    history.account_label = owner.account_label if owner else None
    history.config_ciphertext = service.cipher.encrypt(
        json.dumps(
            {
                "remote_path": target.remote_path,
                "username_ciphertext": target.username_ciphertext,
                "password_ciphertext": target.password_ciphertext,
                "enabled": target.enabled,
                "authorization_status": target.authorization_status,
            }
        ),
        field_name=aad(cast(AuthProvider, target.provider), history.id, "config"),
    )
    db.add(history)


def complete_apply(
    db: Session,
    *,
    session: CloudAuthSession,
    operation: CloudAuthOperation,
    candidate: CloudAuthCandidate | None,
    now: datetime,
) -> None:
    from app.operations.cloud_auth_worker import decrypt

    target = _completion(db, session, operation, now)
    verified = _candidate(db, session)
    payload = decrypt(operation, "payload", operation.payload_ciphertext)
    if (
        candidate is None
        or candidate.id != verified.id
        or payload
        != {
            "candidate_id": str(verified.id),
            "config_revision": verified.config_revision,
            "directory": verified.selected_directory,
            "probe_operation_id": str(verified.probe_operation_id),
        }
    ):
        raise auth_problem("cloud_config_changed")
    service = _service(db)
    if target:
        _history(db, target, now=now)
        target.revision += 1
    else:
        target = CloudBackupTarget(provider=session.provider, revision=1)
        db.add(target)
    target.username_ciphertext = service.cipher.encrypt(
        decrypt(candidate, "username", candidate.username_ciphertext),
        field_name=f"cloud-backup:{session.provider}:username",
    )
    target.password_ciphertext = service.cipher.encrypt(
        decrypt(candidate, "password", candidate.password_ciphertext),
        field_name=f"cloud-backup:{session.provider}:password",
    )
    target.remote_path, target.enabled = "/", False
    target.authorization_status, target.managed_candidate_id = "valid", candidate.id
    target.connection_status, target.last_tested_at = "ok", candidate.probe_verified_at
    target.last_success_at = target.last_backup_id = target.last_error_code = None
    target.switch_operation_id = None
    candidate.state, session.state = "active", "applied"
    session.revision += 1
    session.device_ciphertext = session.token_ciphertext = session.qr_ciphertext = None


def complete_unbind(
    db: Session,
    *,
    session: CloudAuthSession,
    operation: CloudAuthOperation,
    candidate: CloudAuthCandidate | None,
    now: datetime,
) -> None:
    from app.operations.cloud_auth_worker import queue

    target = _completion(db, session, operation, now)
    if not target or not candidate or target.managed_candidate_id != candidate.id:
        raise auth_problem("cloud_config_changed")
    _history(db, target, now=now)
    target.revision += 1
    target.enabled, target.managed_candidate_id = False, None
    target.username_ciphertext = target.password_ciphertext = ""
    target.authorization_status, target.connection_status = "manual", "untested"
    target.last_tested_at = target.last_success_at = None
    target.last_backup_id = target.last_error_code = target.switch_operation_id = None
    candidate.state = "cleanup_pending"
    original_session = db.get(CloudAuthSession, candidate.session_id)
    assert original_session is not None
    queue(db, original_session, "cleanup", now=now, candidate_id=candidate.id)
    session.state, session.revision = "applied", session.revision + 1
