"""Durable one-way refresh checkpoints, independent from connector ownership journals."""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_bridge import ImportWorkResult, RefreshCheckpointRequest
from app.operations.cloud_auth_errors import safe_error_code
from app.operations.cloud_auth_models import CloudAuthOperation, CloudAuthSession
from app.operations.cloud_auth_security import (
    import_token_digests,
    known_import_token_reused,
    load_import_token_digests,
    store_import_token_digests,
)
from app.operations.cloud_auth_state import configuration_deadline


def mark_unknown(
    db: Session, session: CloudAuthSession, operation: CloudAuthOperation, *, now: datetime
) -> None:
    from app.operations import cloud_auth_worker as worker

    operation.refresh_phase, operation.status = "unknown", "failed"
    operation.error_code = "cloud_refresh_outcome_unknown"
    operation.completed_at = now
    operation.lease_id = operation.lease_expires_at = None
    worker.retire(db, session, now=now, code=operation.error_code)


def refresh_checkpoint(
    db: Session, request: RefreshCheckpointRequest, *, now: datetime
) -> dict[str, Any]:
    from app.operations import cloud_auth_worker as worker

    with atomic_write_transaction(db):
        worker._locks(db)
        operation = worker._leased(db, request.fence, now)
        if (
            not operation
            or operation.id != request.operation_id
            or operation.action != "verify_import"
            or worker._maintenance(db)
        ):
            return {"ok": False}
        session = worker._session(db, operation.session_id)
        if not worker.forward_valid(db, session, operation, now=now):
            worker._invalid_result(db, session, operation, now=now)
            return {"ok": False}
        phase = operation.refresh_phase
        record = (
            worker.decrypt(operation, "refresh_checkpoint", operation.refresh_checkpoint_ciphertext)
            or {}
        )
        if request.phase == "not_started":
            # Inspection never grants permission to send or extends any lifetime.
            return {"ok": True, "phase": phase}
        if request.phase == "sent" and phase == "not_started":
            if request.identity is None or request.identity.provider != operation.provider:
                return {"ok": False}
            payload = worker.decrypt(operation, "payload", operation.payload_ciphertext)
            if not payload:
                return {"ok": False}
            digests = import_token_digests(
                get_settings(),
                operation.provider,
                payload["access_token"],
                payload["refresh_token"],
            )
            # Provider advisory locks serialize this recheck with submission/other sends.
            if known_import_token_reused(
                db, operation.provider, digests, operation.id, cipher=worker.cipher()
            ):
                operation.status, operation.completed_at = "failed", now
                operation.error_code = "cloud_import_credentials_reused"
                operation.lease_id = operation.lease_expires_at = None
                worker.retire(db, session, now=now, code=operation.error_code)
                return {"ok": False, "error_code": operation.error_code}
            store_import_token_digests(operation, digests, worker.cipher())
            record = {"identity": request.identity.model_dump(mode="json")}
            operation.refresh_checkpoint_ciphertext = worker.encrypt(
                operation, "refresh_checkpoint", record
            )
            operation.refresh_phase = "sent"
            # Returning only on the initial transition means a lost reply cannot grant send twice.
            return {"ok": True, "phase": "sent"}
        if request.phase == "unknown" and phase == "sent":
            mark_unknown(db, session, operation, now=now)
            return {"ok": True, "phase": "unknown"}
        if request.phase == "refreshed" and phase == "sent":
            if request.tokens is None or request.tokens.provider != operation.provider:
                return {"ok": False}
            tokens = request.tokens.model_dump(mode="json")
            rotated = import_token_digests(
                get_settings(),
                operation.provider,
                request.tokens.access_token,
                request.tokens.refresh_token,
            )
            digests = load_import_token_digests(operation, worker.cipher())
            store_import_token_digests(operation, digests + rotated, worker.cipher())
            record["tokens"] = tokens
            operation.refresh_checkpoint_ciphertext = worker.encrypt(
                operation, "refresh_checkpoint", record
            )
            operation.refresh_phase = "refreshed"
            operation.payload_ciphertext = None
            return {"ok": True, "phase": "refreshed"}
        if request.phase == "verified" and phase == "refreshed":
            if (
                request.identity is None
                or not record.get("tokens")
                or request.identity.provider != operation.provider
                or request.identity.account_id != record.get("identity", {}).get("account_id")
            ):
                return {"ok": False, "error_code": "cloud_import_account_mismatch"}
            record["verified_at"] = now.isoformat()
            operation.refresh_checkpoint_ciphertext = worker.encrypt(
                operation, "refresh_checkpoint", record
            )
            operation.refresh_phase = "verified"
            return {"ok": True, "phase": "verified"}
        return {"ok": False}


def complete_import(
    db: Session,
    session: CloudAuthSession,
    operation: CloudAuthOperation,
    result: ImportWorkResult,
    *,
    now: datetime,
) -> bool:
    from app.operations import cloud_auth_worker as worker

    if operation.refresh_phase == "sent":
        mark_unknown(db, session, operation, now=now)
        return True
    if result.error_code:
        # Transport/checkpoint loss with persisted rotated tokens is recoverable by identity only.
        if operation.refresh_phase in {"refreshed", "verified"} and result.error_code in {
            "cloud_refresh_outcome_unknown",
            "cloud_auth_bridge_failed",
            "cloud_auth_revision_changed",
        }:
            return False
        operation.error_code = safe_error_code(result.error_code)
        operation.status, operation.completed_at = "failed", now
        operation.lease_id = operation.lease_expires_at = None
        worker.retire(db, session, now=now, code=operation.error_code)
        return True
    if operation.refresh_phase != "verified":
        return False
    record = worker.decrypt(
        operation, "refresh_checkpoint", operation.refresh_checkpoint_ciphertext
    )
    if not record or not record.get("tokens") or not record.get("verified_at"):
        return False
    verified_at = datetime.fromisoformat(record["verified_at"])
    session.authorized_at = session.verified_at = verified_at
    session.expires_at = configuration_deadline(verified_at)
    session.token_expires_at = (
        datetime.fromisoformat(record["tokens"]["expires_at"])
        if record["tokens"]["expires_at"]
        else None
    )
    session.expiry_source = record["tokens"]["expiry_source"]
    session.account_label = "***" + record["identity"]["account_id"][-4:]
    session.token_ciphertext = worker.encrypt(session, "token", record["tokens"])
    session.state, session.revision = "authorized", session.revision + 1
    worker.queue(db, session, "prepare", now=now)
    operation.payload_ciphertext = operation.refresh_checkpoint_ciphertext = None
    operation.status, operation.completed_at = "succeeded", now
    operation.lease_id = operation.lease_expires_at = None
    session.error_code = None
    return True
