"""Short database transactions for leased host authorization operations."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_errors import safe_error_code
from app.operations.cloud_auth_models import (
    CloudAuthApplication,
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
    CloudConnectorState,
)
from app.operations.cloud_auth_schemas import (
    TERMINAL_AUTH_STATES,
    AuthActor,
    AuthFence,
    AuthProvider,
)
from app.operations.cloud_auth_security import require_auth_actor
from app.operations.cloud_auth_state import aad, configuration_deadline, scan_deadline
from app.operations.cloud_models import CloudBackupTarget
from app.security.encryption import EnvelopeCipher

if TYPE_CHECKING:
    from app.operations.cloud_auth_bridge import CheckpointRequest, SnapshotFence

LEASE = timedelta(seconds=90)
STATES = {
    "create_qr": {"creating"},
    "verify_import": {"verifying_import"},
    "poll": {"awaiting_confirmation"},
    "prepare": {"authorized", "configuring"},
    "list_directories": {"configuring", "test_pending", "ready"},
    "select_directory": {"configuring", "test_pending", "ready"},
    "probe": {"test_pending", "ready"},
    "apply": {"ready", "applying"},
    "unbind": {"applying"},
}


def cipher() -> EnvelopeCipher:
    from app.auth.dependencies import _secret_bytes
    from app.core.config import get_settings
    from app.security.encryption import EnvelopeCipher

    return EnvelopeCipher(_secret_bytes(get_settings(), "field_encryption_master_key"))


def encrypt(row: Any, field: str, value: Any) -> str:
    return cipher().encrypt(
        json.dumps(value, separators=(",", ":"), default=str),
        field_name=aad(
            row.provider, row.id, field, mode=row.mode, protocol_version=row.protocol_version
        ),
    )


def decrypt(row: Any, field: str, value: str | None) -> Any:
    return (
        json.loads(
            cipher().decrypt(
                value,
                field_name=aad(
                    row.provider,
                    row.id,
                    field,
                    mode=row.mode,
                    protocol_version=row.protocol_version,
                ),
            )
        )
        if value
        else None
    )


SOURCE_FIELDS = (
    "mode",
    "auth_config_id",
    "auth_config_revision",
    "app_revision",
    "protocol_version",
)


def source_fields(row: Any) -> dict[str, Any]:
    return {key: getattr(row, key) for key in SOURCE_FIELDS}


def source_matches(first: Any, second: Any) -> bool:
    return bool(source_fields(first) == source_fields(second) and first.provider == second.provider)


def result_source_matches(operation: CloudAuthOperation, result: Any) -> bool:
    if operation.protocol_version == 1:
        return bool(result.protocol_version == 1 and result.mode == operation.mode == "own_app")
    return source_fields(operation) == source_fields(result)


def linked_source_valid(
    db: Session, session: CloudAuthSession, operation: CloudAuthOperation
) -> bool:
    from app.operations.cloud_auth_source import require_auth_source_frozen

    try:
        require_auth_source_frozen(session)
    except ProblemError:
        return False
    if not source_matches(session, operation):
        return False
    candidate_id = operation.candidate_id or session.candidate_id
    if candidate_id is None:
        return True
    candidate = db.get(CloudAuthCandidate, candidate_id)
    if not candidate or not source_matches(candidate, operation):
        return False
    original = db.get(CloudAuthSession, candidate.session_id)
    return bool(original and source_matches(original, candidate))


def _locks(db: Session) -> None:
    # Same advisory locks as public target/auth writers, before record locks.
    for key in (62000, 62001):
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _session(db: Session, identity: UUID) -> CloudAuthSession:
    row = db.get(CloudAuthSession, identity)
    if row is None:
        raise ValueError("authorization session missing")
    return row


def _invalid_result(
    db: Session, session: CloudAuthSession, operation: CloudAuthOperation, *, now: datetime
) -> None:
    if operation.status in {"queued", "running"}:
        operation.status = "cancelled"
        operation.completed_at = now
    if operation.action == "verify_import":
        if operation.refresh_phase == "sent":
            operation.refresh_phase = "unknown"
        operation.payload_ciphertext = operation.refresh_checkpoint_ciphertext = None
    try:
        require_auth_actor(
            db,
            AuthActor(
                account_id=session.owner_account_id, login_session_id=session.owner_login_session_id
            ),
        )
        revoked = False
    except ProblemError:
        revoked = True
    if session.expires_at <= now or revoked or session.state in {"cancelled", "failed", "expired"}:
        retire(db, session, now=now)
    # A superseded browser operation must not retire its newer session revision.


def heartbeat(db: Session, *, now: datetime, connector_id: UUID | None = None) -> None:
    from app.operations.heartbeat import record_service_heartbeat

    with atomic_write_transaction(db):
        record_service_heartbeat(
            db, service_key="cloud_authorization", succeeded=True, occurred_at=now
        )
        if connector_id:
            connector = db.get(CloudConnectorState, connector_id)
            if connector is None:
                db.add(CloudConnectorState(id=connector_id, registered=True, last_seen_at=now))
            else:
                connector.last_seen_at = now


def lease_matches(operation: CloudAuthOperation | None, fence: AuthFence, *, now: datetime) -> bool:
    return bool(
        operation
        and operation.status == "running"
        and operation.lease_id == fence.lease_id
        and operation.lease_expires_at
        and operation.lease_expires_at > now
        and operation.session_id == fence.session_id
        and operation.session_revision == fence.session_revision
        and operation.app_revision == fence.app_revision
        and operation.target_revision == fence.target_revision
        and operation.protocol_version == fence.protocol_version
        and operation.mode == fence.mode
        and (
            fence.protocol_version == 1
            or (
                operation.auth_config_id == fence.auth_config_id
                and operation.auth_config_revision == fence.auth_config_revision
            )
        )
    )


def queue(
    db: Session,
    session: CloudAuthSession,
    action: str,
    *,
    now: datetime,
    candidate_id: UUID | None = None,
) -> CloudAuthOperation:
    identity = uuid4()
    row = CloudAuthOperation(
        id=identity,
        provider=session.provider,
        **source_fields(session),
        session_id=session.id,
        candidate_id=candidate_id,
        owner_account_id=session.owner_account_id,
        owner_login_session_id=session.owner_login_session_id,
        idempotency_key=identity,
        action=action,
        status="queued",
        request_fingerprint=hashlib.sha256(action.encode()).hexdigest(),
        session_revision=session.revision,
        target_revision=session.target_revision,
        attempts=0,
        cleanup_status="pending" if action == "cleanup" else "none",
        next_attempt_at=now,
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


def retire(
    db: Session, session: CloudAuthSession, *, now: datetime, code: str = "cloud_auth_expired"
) -> None:
    from app.operations.cloud_auth_apply import release_switch

    release_switch(db, session)
    if session.state == "applying" and code == "cloud_auth_expired":
        code = "cloud_target_busy"
    if session.state not in TERMINAL_AUTH_STATES:
        session.state = "expired" if code == "cloud_auth_expired" else "failed"
        session.revision += 1
    session.error_code = safe_error_code(code)
    session.device_ciphertext = session.token_ciphertext = session.qr_ciphertext = None
    for imported in db.scalars(
        select(CloudAuthOperation).where(
            CloudAuthOperation.session_id == session.id,
            CloudAuthOperation.action == "verify_import",
        )
    ):
        if imported.refresh_phase == "sent":
            imported.refresh_phase = "unknown"
        imported.payload_ciphertext = imported.refresh_checkpoint_ciphertext = None
    candidate = db.get(CloudAuthCandidate, session.candidate_id) if session.candidate_id else None
    if candidate and candidate.state not in {"active", "cleaned"}:
        candidate.state = "cleanup_pending"
        exists = db.scalar(
            select(CloudAuthOperation.id).where(
                CloudAuthOperation.candidate_id == candidate.id,
                CloudAuthOperation.action == "cleanup",
                CloudAuthOperation.status.in_(["queued", "running"]),
            )
        )
        if exists is None:
            queue(db, session, "cleanup", now=now, candidate_id=candidate.id)


def forward_valid(
    db: Session, session: CloudAuthSession, operation: CloudAuthOperation, *, now: datetime
) -> bool:
    if session.state not in STATES.get(operation.action, set()) or session.expires_at <= now:
        return False
    if (session.revision, session.app_revision, session.target_revision) != (
        operation.session_revision,
        operation.app_revision,
        operation.target_revision,
    ):
        return False
    if not linked_source_valid(db, session, operation):
        return False
    from app.operations.cloud_auth_source import (
        require_auth_source_current,
        require_auth_source_frozen,
    )

    try:
        if operation.action == "unbind":
            require_auth_source_frozen(session)
        else:
            require_auth_source_current(db, session)
    except ProblemError:
        return False
    target = db.get(CloudBackupTarget, session.provider)
    if (target.revision if target else 0) != operation.target_revision:
        return False
    if operation.action == "unbind" and (
        not target
        or target.managed_candidate_id != session.candidate_id
        or target.switch_operation_id != operation.id
    ):
        return False
    try:
        require_auth_actor(
            db,
            AuthActor(
                account_id=session.owner_account_id, login_session_id=session.owner_login_session_id
            ),
            lock=True,
        )
    except ProblemError:
        return False
    return True


def _maintenance(db: Session) -> bool:
    return (
        db.scalar(
            select(CloudConnectorState.id)
            .where(CloudConnectorState.maintenance_operation_id.is_not(None))
            .limit(1)
        )
        is not None
    )


def _candidate_payload(db: Session, candidate: CloudAuthCandidate | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    origin = db.get(CloudAuthOperation, candidate.operation_id)
    journal = (
        decrypt(origin, "journal", origin.result_ciphertext)
        if origin and origin.result_ciphertext
        else None
    )
    return {
        **source_fields(candidate),
        "id": str(candidate.id),
        "operation_id": str(candidate.operation_id),
        "connector_id": str(candidate.connector_id),
        "config_revision": candidate.config_revision,
        "directory": candidate.selected_directory,
        "journal": journal,
    }


def claim_auth_operation(db: Session, *, now: datetime) -> dict[str, Any] | None:
    work: dict[str, Any] | None = None
    with atomic_write_transaction(db):
        _locks(db)
        if _maintenance(db):
            return None
        # Expiry and revocation are discovered even when no host work is queued.
        for session in db.scalars(
            select(CloudAuthSession)
            .where(~CloudAuthSession.state.in_(TERMINAL_AUTH_STATES))
            .with_for_update(skip_locked=True)
        ):
            try:
                require_auth_actor(
                    db,
                    AuthActor(
                        account_id=session.owner_account_id,
                        login_session_id=session.owner_login_session_id,
                    ),
                )
                valid = session.expires_at > now
            except ProblemError:
                valid = False
            if not valid:
                retire(db, session, now=now)
        for session in db.scalars(
            select(CloudAuthSession).where(
                CloudAuthSession.state.in_(["cancelled", "expired", "failed"]),
                CloudAuthSession.candidate_id.is_not(None),
            )
        ):
            retire(db, session, now=now, code=session.error_code or "cloud_auth_expired")
        operations = db.scalars(
            select(CloudAuthOperation)
            .where(
                CloudAuthOperation.next_attempt_at <= now,
                or_(
                    CloudAuthOperation.status == "queued",
                    (CloudAuthOperation.status == "running")
                    & (CloudAuthOperation.lease_expires_at <= now),
                ),
            )
            .order_by(CloudAuthOperation.action != "cleanup", CloudAuthOperation.created_at)
            .with_for_update(skip_locked=True)
        ).all()
        for operation in operations:
            session = _session(db, operation.session_id)
            if operation.action == "verify_import" and operation.refresh_phase in {
                "sent",
                "unknown",
            }:
                from app.operations.cloud_auth_import_worker import mark_unknown

                mark_unknown(db, session, operation, now=now)
                continue
            candidate = (
                db.get(CloudAuthCandidate, operation.candidate_id or session.candidate_id)
                if (operation.candidate_id or session.candidate_id)
                else None
            )
            cleanup = operation.action == "cleanup"
            if not linked_source_valid(db, session, operation):
                _invalid_result(db, session, operation, now=now)
                continue
            if cleanup:
                if (
                    not candidate
                    or candidate.session_id != session.id
                    or candidate.state in {"active", "cleaned"}
                ):
                    operation.status = "cancelled"
                    continue
            elif not forward_valid(db, session, operation, now=now):
                _invalid_result(db, session, operation, now=now)
                continue
            if operation.action in {"apply", "unbind"}:
                from app.operations.cloud_auth_apply import prepare_apply

                if not prepare_apply(db, session=session, operation=operation, now=now):
                    operation.next_attempt_at = now + timedelta(seconds=2)
                    continue
            if operation.action == "prepare" and candidate is None:
                connector = db.scalar(
                    select(CloudConnectorState)
                    .where(CloudConnectorState.registered.is_(True))
                    .order_by(CloudConnectorState.id)
                    .limit(1)
                )
                if connector is None:
                    operation.next_attempt_at = now + timedelta(seconds=10)
                    continue
                candidate = CloudAuthCandidate(
                    id=uuid4(),
                    session_id=session.id,
                    provider=session.provider,
                    **source_fields(session),
                    operation_id=operation.id,
                    connector_id=connector.id,
                    mount_path=f"/erp-candidates/{connector.id.hex}/{operation.id.hex}",
                    config_revision=1,
                    state="preparing",
                )
                db.add(candidate)
                session.candidate_id = operation.candidate_id = candidate.id
                session.state = "configuring"
                db.flush()
            if candidate and not source_matches(candidate, operation):
                _invalid_result(db, session, operation, now=now)
                continue
            operation.lease_id, operation.lease_expires_at = uuid4(), now + LEASE
            operation.status, operation.attempts = "running", operation.attempts + 1
            if cleanup:
                operation.cleanup_status = "running"
            fence = AuthFence(
                protocol_version=operation.protocol_version,
                mode=operation.mode,
                auth_config_id=operation.auth_config_id
                if operation.protocol_version == 2
                else None,
                auth_config_revision=operation.auth_config_revision
                if operation.protocol_version == 2
                else None,
                session_id=session.id,
                session_revision=operation.session_revision,
                app_revision=operation.app_revision,
                target_revision=operation.target_revision,
                lease_id=operation.lease_id,
            )
            payload = decrypt(operation, "payload", operation.payload_ciphertext) or {}
            work = {
                "id": str(operation.id),
                "action": operation.action,
                "provider": session.provider,
                **source_fields(operation),
                "fence": fence.model_dump(mode="json"),
                "payload": payload,
                "candidate": _candidate_payload(db, candidate),
            }
            if operation.action == "probe" and operation.result_ciphertext:
                work["probe_recovery"] = decrypt(operation, "probe", operation.result_ciphertext)
            if cleanup and candidate:
                work["probe_recoveries"] = []
                for probe in db.scalars(
                    select(CloudAuthOperation).where(
                        CloudAuthOperation.candidate_id == candidate.id,
                        CloudAuthOperation.action == "probe",
                        CloudAuthOperation.result_ciphertext.is_not(None),
                    )
                ):
                    proof = decrypt(probe, "probe", probe.result_ciphertext)
                    if proof and (not proof.get("finished") or proof.get("cleanup_pending")):
                        work["probe_recoveries"].append(
                            {"operation_id": str(probe.id), "proof": proof}
                        )
            if operation.action in {"create_qr", "poll", "prepare"} and session.mode == "own_app":
                app = db.get(CloudAuthApplication, session.provider)
                if app is None:
                    raise ValueError("authorization application missing")
                work["application"] = {
                    "client_id": app.client_id,
                    "client_secret": cipher().decrypt(
                        app.secret_ciphertext,
                        field_name=aad(cast(AuthProvider, app.provider), app.id, "secret"),
                    )
                    if app.secret_ciphertext
                    else None,
                }
            if operation.action == "poll":
                work["grant"] = decrypt(session, "device", session.device_ciphertext)
            if operation.action == "prepare":
                work["tokens"] = decrypt(session, "token", session.token_ciphertext)
            if operation.action == "verify_import":
                work["refresh_phase"] = operation.refresh_phase
                work["refresh_checkpoint"] = decrypt(
                    operation, "refresh_checkpoint", operation.refresh_checkpoint_ciphertext
                )
            break
    return work


def _leased(db: Session, fence: AuthFence, now: datetime) -> CloudAuthOperation | None:
    operation = db.scalar(
        select(CloudAuthOperation)
        .where(
            CloudAuthOperation.session_id == fence.session_id,
            CloudAuthOperation.lease_id == fence.lease_id,
        )
        .with_for_update()
    )
    return operation if lease_matches(operation, fence, now=now) else None


def renew_auth_operation(db: Session, fence: AuthFence, *, now: datetime) -> bool:
    with atomic_write_transaction(db):
        _locks(db)
        operation = _leased(db, fence, now)
        if not operation:
            return False
        session = _session(db, operation.session_id)
        if not linked_source_valid(db, session, operation):
            return False
        if operation.action != "cleanup" and not forward_valid(db, session, operation, now=now):
            _invalid_result(db, session, operation, now=now)
            return False
        # Ignore overly frequent renewal without extending the durable lease.
        if operation.lease_expires_at is not None and operation.lease_expires_at - now <= timedelta(
            seconds=60
        ):
            operation.lease_expires_at = now + LEASE
        return True


def complete_auth_operation(db: Session, fence: AuthFence, result: Any, *, now: datetime) -> bool:
    from app.operations.cloud_auth_bridge import RESULT_ADAPTER

    result = RESULT_ADAPTER.validate_python(result)
    with atomic_write_transaction(db):
        _locks(db)
        operation = _leased(db, fence, now)
        if (
            operation is None
            or operation.action != result.action
            or _maintenance(db)
            or not result_source_matches(operation, result)
        ):
            return False
        session = _session(db, operation.session_id)
        if not linked_source_valid(db, session, operation):
            return False
        candidate = (
            db.get(CloudAuthCandidate, operation.candidate_id or session.candidate_id)
            if (operation.candidate_id or session.candidate_id)
            else None
        )
        if operation.action != "cleanup" and not forward_valid(db, session, operation, now=now):
            _invalid_result(db, session, operation, now=now)
            return False
        if operation.action == "verify_import":
            from app.operations.cloud_auth_import_worker import complete_import

            return complete_import(db, session, operation, result, now=now)
        if result.error_code:
            operation.error_code = session.error_code = safe_error_code(result.error_code)
            proof = (
                decrypt(operation, "probe", operation.result_ciphertext)
                if operation.action == "probe"
                else None
            )
            if operation.action == "probe" and proof is None:
                # The private host must reserve a probe journal before any remote I/O.
                # A fenced error with no reservation therefore has explicit no-I/O proof.
                proof = {
                    "finished": True,
                    "cleanup_pending": False,
                    "no_external_io": True,
                    "error_code": operation.error_code,
                }
                operation.result_ciphertext = encrypt(operation, "probe", proof)
            if operation.action in {"cleanup", "apply", "unbind"} or (
                proof and (not proof.get("finished") or proof.get("cleanup_pending"))
            ):
                operation.status = "queued"
                operation.next_attempt_at = now + timedelta(seconds=15)
                operation.lease_id = operation.lease_expires_at = None
                return True
            operation.status = "failed"
            operation.completed_at = now
            operation.lease_id = operation.lease_expires_at = None
            if operation.action in {"create_qr", "poll", "prepare"}:
                retire(db, session, now=now, code=result.error_code)
            return True
        action = operation.action
        if action == "create_qr":
            if result.grant is None:
                return False
            grant = result.grant
            expiry = min(
                session.expires_at, scan_deadline(now=now, provider_expires_at=grant.expires_at)
            )
            if expiry <= now:
                retire(db, session, now=now)
                return False
            session.expires_at = session.qr_expires_at = expiry
            session.device_ciphertext = encrypt(session, "device", grant.model_dump(mode="json"))
            qr = grant.qr.model_dump(mode="json")
            qr["expires_at"] = expiry.isoformat()
            session.qr_ciphertext = encrypt(session, "qr", qr)
            session.state, session.revision = "awaiting_confirmation", session.revision + 1
            session.next_poll_at = now + timedelta(seconds=grant.interval_seconds)
            queue(db, session, "poll", now=session.next_poll_at)
        elif action == "poll":
            if result.poll is None:
                return False
            poll = result.poll
            if poll.status in {"pending", "throttled"}:
                grant = decrypt(session, "device", session.device_ciphertext)
                delay = max(
                    grant["interval_seconds"],
                    poll.retry_after_seconds or (15 if poll.status == "throttled" else 1),
                )
                session.next_poll_at = operation.next_attempt_at = now + timedelta(seconds=delay)
                operation.status = "queued"
                operation.lease_id = operation.lease_expires_at = None
                return True
            if poll.status in {"denied", "expired"}:
                retire(
                    db,
                    session,
                    now=now,
                    code="cloud_auth_denied" if poll.status == "denied" else "cloud_auth_expired",
                )
            else:
                from app.core.config import get_settings
                from app.operations.cloud_auth_security import (
                    import_token_digests,
                    store_import_token_digests,
                )

                store_import_token_digests(
                    operation,
                    import_token_digests(
                        get_settings(),
                        session.provider,
                        poll.tokens.access_token,
                        poll.tokens.refresh_token,
                    ),
                    cipher(),
                )
                session.authorized_at, session.expires_at = now, configuration_deadline(now)
                session.state, session.revision = "authorized", session.revision + 1
                session.token_ciphertext = encrypt(
                    session, "token", poll.tokens.model_dump(mode="json")
                )
                session.device_ciphertext = session.qr_ciphertext = None
                queue(db, session, "prepare", now=now)
        elif action in {"prepare", "select_directory"}:
            if not candidate or result.access is None:
                return False
            journal = (_candidate_payload(db, candidate) or {}).get("journal")
            access = result.access
            if (
                not journal
                or not journal["delivered"]
                or journal.get("creation_unknown")
                or journal.get("scope_pending")
                or access.ref.operation_id != candidate.operation_id
                or access.ref.mount_path != candidate.mount_path
                or access.ref.storage_id != journal["storage_id"]
                or access.ref.webdav_user_id != journal["webdav_user_id"]
                or access.ref.account_fingerprint != journal["account_fingerprint"]
                or access.username != journal["username"]
                or access.password != journal["password"]
            ):
                return False
            candidate.storage_id, candidate.webdav_user_id = (
                access.ref.storage_id,
                access.ref.webdav_user_id,
            )
            for name, value in [
                ("username", access.username),
                ("password", access.password),
                ("account_fingerprint", access.ref.account_fingerprint),
            ]:
                setattr(candidate, name + "_ciphertext", encrypt(candidate, name, value))
            candidate.state = "configured"
            session.account_label = "***" + journal["raw_account_id"][-4:]
            if action == "select_directory":
                payload = decrypt(operation, "payload", operation.payload_ciphertext)
                if journal["directory"] != payload["directory"]:
                    return False
                candidate.selected_directory = session.selected_directory = journal["directory"]
                candidate.config_revision = journal["config_revision"]
                candidate.probe_operation_id = candidate.probe_sha256 = (
                    candidate.probe_verified_at
                ) = None
                session.state = "test_pending"
            else:
                session.token_ciphertext = None
                session.state = "configuring"
            session.revision += 1
        elif action == "list_directories":
            operation.result_ciphertext = encrypt(
                operation, "result", {"entries": [entry.model_dump() for entry in result.entries]}
            )
        elif action == "probe":
            evidence = result.evidence
            if (
                not candidate
                or not evidence
                or (
                    evidence.operation_id,
                    evidence.candidate_id,
                    evidence.config_revision,
                    evidence.directory,
                )
                != (
                    operation.id,
                    candidate.id,
                    candidate.config_revision,
                    candidate.selected_directory,
                )
            ):
                return False
            proof = decrypt(operation, "probe", operation.result_ciphertext)
            if (
                not proof
                or not proof.get("finished")
                or proof.get("cleanup_pending")
                or proof.get("error_code")
                or proof["sha256"] != evidence.sha256
            ):
                return False
            candidate.probe_operation_id, candidate.probe_sha256, candidate.probe_verified_at = (
                operation.id,
                evidence.sha256,
                evidence.verified_at,
            )
            candidate.state, session.state = "verified", "ready"
            session.revision += 1
        elif action in {"apply", "unbind"}:
            if result.host_drain_receipt is None:
                return False
            from app.operations import cloud_auth_apply

            function = (
                cloud_auth_apply.complete_apply
                if action == "apply"
                else cloud_auth_apply.complete_unbind
            )
            function(db, session=session, operation=operation, candidate=candidate, now=now)
        elif action == "cleanup":
            if not candidate or candidate.state == "active" or not result.cleaned:
                return False
            candidate.state = "cleaned"
            candidate.username_ciphertext = candidate.password_ciphertext = (
                candidate.account_fingerprint_ciphertext
            ) = None
            origin = db.get(CloudAuthOperation, candidate.operation_id)
            if origin is not None:
                origin.result_ciphertext = None
            operation.cleanup_status = "succeeded"
        operation.status, operation.completed_at = "succeeded", now
        operation.lease_id = operation.lease_expires_at = None
        session.error_code = (
            None if session.state not in {"failed", "expired"} else session.error_code
        )
        return True


def checkpoint(db: Session, request: CheckpointRequest, *, now: datetime) -> dict[str, Any]:
    with atomic_write_transaction(db):
        _locks(db)
        operation = _leased(db, request.fence, now)
        if not operation or _maintenance(db):
            return {"ok": False}
        session = _session(db, operation.session_id)
        candidate = (
            db.get(CloudAuthCandidate, operation.candidate_id or session.candidate_id)
            if (operation.candidate_id or session.candidate_id)
            else None
        )
        if (
            not candidate
            or not source_matches(candidate, operation)
            or not source_matches(session, operation)
            or candidate.state == "cleaned"
            or (
                candidate.state == "active"
                and not (operation.action == "unbind" and request.kind == "read")
            )
        ):
            return {"ok": False}
        if operation.action != "cleanup" and not forward_valid(db, session, operation, now=now):
            _invalid_result(db, session, operation, now=now)
            return {"ok": False}
        origin = db.get(CloudAuthOperation, candidate.operation_id)
        if origin is None or not source_matches(origin, candidate):
            return {"ok": False}
        old = decrypt(origin, "journal", origin.result_ciphertext)
        if request.kind in {"read", "reserve", "save"}:
            if request.operation_id != candidate.operation_id:
                return {"ok": False}
            if request.kind == "read":
                return {"ok": True, "record": old}
            if (
                operation.action
                not in {"prepare", "select_directory", "list_directories", "probe", "cleanup"}
                or request.record is None
            ):
                return {"ok": False}
            new = request.record.model_dump(mode="json")
            if not result_source_matches(origin, request.record):
                return {"ok": False}
            if (
                request.record.operation_id,
                request.record.candidate_id,
                request.record.connector_id,
                request.record.provider,
                request.record.mount_path,
            ) != (
                candidate.operation_id,
                candidate.id,
                candidate.connector_id,
                candidate.provider,
                candidate.mount_path,
            ):
                return {"ok": False}
            if old:
                immutable = {
                    "operation_id",
                    "connector_id",
                    "candidate_id",
                    "provider",
                    "mount_path",
                    "username",
                    "password",
                    "marker",
                    "account_fingerprint",
                    "raw_account_id",
                }
                if origin.protocol_version == 2:
                    immutable.update(SOURCE_FIELDS)
                if any(old.get(key) != new.get(key) for key in immutable):
                    return {"ok": False}
                if request.kind == "reserve":
                    return {"ok": True, "record": old}
                if (old["delivered"] and not new["delivered"]) or (
                    old["delivery_started"] and not new["delivery_started"]
                ):
                    return {"ok": False}
                if (
                    any(
                        old.get(key) and new.get(key) != old[key]
                        for key in ["storage_id", "webdav_user_id", "meta_id"]
                    )
                    and operation.action != "cleanup"
                ):
                    return {"ok": False}
                if new["directory"] != old["directory"]:
                    payload = decrypt(operation, "payload", operation.payload_ciphertext) or {}
                    if (
                        operation.action != "select_directory"
                        or new["directory"] != payload.get("directory")
                        or not new["scope_pending"]
                    ):
                        return {"ok": False}
                    new["config_revision"] = old["config_revision"] + 1
                elif new["config_revision"] != old["config_revision"]:
                    return {"ok": False}
            elif (
                request.kind != "reserve"
                or operation.action != "prepare"
                or request.record.config_revision != candidate.config_revision
            ):
                return {"ok": False}
            origin.result_ciphertext = encrypt(origin, "journal", new)
            candidate.storage_id = new["storage_id"]
            candidate.webdav_user_id = new["webdav_user_id"]
            if new["delivered"]:
                session.token_ciphertext = None
            return {"ok": True, "record": new}
        if request.kind == "recover_probe":
            probe = db.get(CloudAuthOperation, request.operation_id)
            if (
                operation.action not in {"probe", "cleanup"}
                or not probe
                or probe.action != "probe"
                or probe.candidate_id != candidate.id
            ):
                return {"ok": False}
            if operation.action == "probe" and probe.id != operation.id:
                return {"ok": False}
            probe.result_ciphertext = None
            return {"ok": True}
        if operation.action != "probe" or request.operation_id != operation.id:
            return {"ok": False}
        if request.kind == "reserve_probe":
            if (
                request.directory != candidate.selected_directory
                or not request.sha256
                or not re.fullmatch(
                    r"/erp-probe-" + operation.id.hex + r"-[a-f0-9]{32}\.bin", request.path or ""
                )
            ):
                return {"ok": False}
            if operation.result_ciphertext:
                return {"ok": False}
            proof = {
                "directory": request.directory,
                "path": request.path,
                "sha256": request.sha256,
                "finished": False,
            }
        else:
            proof = decrypt(operation, "probe", operation.result_ciphertext)
            if not proof:
                return {"ok": False}
            proof.update(
                finished=True,
                cleanup_pending=request.cleanup_pending,
                error_code=safe_error_code(
                    "cloud_remote_corrupt"
                    if request.error_code == "cloud_readback_failed"
                    else request.error_code
                )
                if request.error_code
                else None,
            )
        operation.result_ciphertext = encrypt(operation, "probe", proof)
        return {"ok": True}


def begin_snapshot(
    db: Session, connector_id: UUID, operation_id: UUID, *, now: datetime
) -> dict[str, Any]:
    with atomic_write_transaction(db):
        _locks(db)
        connector = db.scalar(
            select(CloudConnectorState)
            .where(CloudConnectorState.id == connector_id)
            .with_for_update()
        )
        if (
            not connector
            or not connector.registered
            or connector.maintenance_operation_id not in (None, operation_id)
        ):
            return {"ok": False}
        if connector.maintenance_operation_id is None:
            connector.maintenance_operation_id = operation_id
            connector.maintenance_lease_expires_at = now + timedelta(seconds=90)
        return {
            "ok": True,
            "fence": {
                "connector_id": str(connector_id),
                "operation_id": str(operation_id),
                "revision": 1,
                "expires_at": cast(datetime, connector.maintenance_lease_expires_at).isoformat(),
            },
        }


def _retire_restored_imports(db: Session, *, now: datetime) -> None:
    """Provider consumption cannot be rolled back with a full database archive."""
    from app.operations.cloud_auth_errors import auth_problem
    from app.operations.cloud_auth_source import require_auth_source_frozen

    sessions = db.scalars(
        select(CloudAuthSession).where(
            CloudAuthSession.mode == "openlist_builtin_import",
            ~CloudAuthSession.state.in_(TERMINAL_AUTH_STATES),
        ).with_for_update()
    ).all()
    # Validate the entire restore boundary before retiring any session. All writes,
    # including the exact fence release, share this transaction and lock hierarchy.
    for session in sessions:
        require_auth_source_frozen(session)
        candidate = db.get(CloudAuthCandidate, session.candidate_id) if session.candidate_id else None
        if session.candidate_id and (candidate is None or not source_matches(candidate, session)):
            raise auth_problem("cloud_auth_revision_changed")
        for operation in db.scalars(select(CloudAuthOperation).where(
            CloudAuthOperation.session_id == session.id
        ).with_for_update()):
            if not linked_source_valid(db, session, operation):
                raise auth_problem("cloud_auth_revision_changed")
    for session in sessions:
        retire(db, session, now=now, code="cloud_refresh_outcome_unknown")
        for operation in db.scalars(select(CloudAuthOperation).where(
            CloudAuthOperation.session_id == session.id
        )):
            if operation.action != "cleanup" and operation.status in {"queued", "running"}:
                operation.status = "cancelled"
                operation.completed_at = now
                operation.lease_id = operation.lease_expires_at = None
            # Protected candidate ownership/probe result journals are not raw import
            # grants and are needed to reconcile uncertain external mutations.
            if operation.action != "cleanup":
                operation.payload_ciphertext = None


def _release_snapshot(
    db: Session, fence: SnapshotFence, *, now: datetime, restored: bool
) -> dict[str, Any]:
    with atomic_write_transaction(db):
        _locks(db)
        connector = db.scalar(
            select(CloudConnectorState)
            .where(CloudConnectorState.id == fence.connector_id)
            .with_for_update()
        )
        if connector and connector.registered and connector.maintenance_operation_id is None:
            # Replaying a lost release ACK is harmless only while no newer gate exists.
            return {"ok": True, "released": True}
        if (
            not connector
            or not connector.registered
            or connector.maintenance_operation_id != fence.operation_id
            or connector.maintenance_lease_expires_at != fence.expires_at
        ):
            return {"ok": False, "released": False}
        if restored:
            _retire_restored_imports(db, now=now)
        connector.maintenance_operation_id = connector.maintenance_lease_expires_at = None
        return {"ok": True, "released": True}


def end_snapshot(db: Session, fence: SnapshotFence, *, now: datetime) -> dict[str, Any]:
    """Normal capture/process recovery never invalidates import checkpoints."""
    return _release_snapshot(db, fence, now=now, restored=False)


def restore_snapshot(db: Session, fence: SnapshotFence, *, now: datetime) -> dict[str, Any]:
    """Authenticated restore-only handoff; no-gate ACK replay is strictly read-only."""
    return _release_snapshot(db, fence, now=now, restored=True)
