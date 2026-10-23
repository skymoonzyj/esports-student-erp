"""Private owner-bound builtin imports. No provider network I/O in transactions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_errors import auth_problem, safe_error_code
from app.operations.cloud_auth_models import CloudAuthOperation, CloudAuthSession
from app.operations.cloud_auth_requests import CloudImportCommand
from app.operations.cloud_auth_schemas import AuthActor, CloudImportOperationView
from app.operations.cloud_auth_security import (
    import_payload_fingerprint,
    import_token_digests,
    known_import_token_reused,
    store_import_token_digests,
)
from app.operations.cloud_auth_source import require_auth_source_current
from app.operations.cloud_models import CloudBackupTarget

if TYPE_CHECKING:
    from app.operations.cloud_auth_service import CloudAuthorizationService


class CloudImportService:
    def __init__(self, authorization: CloudAuthorizationService) -> None:
        self.authorization = authorization
        self.db = authorization.db

    def _view(
        self, row: CloudAuthSession, operation: CloudAuthOperation
    ) -> CloudImportOperationView:
        status = (
            operation.status
            if operation.status in {"succeeded", "failed", "cancelled"}
            else "pending"
        )
        return CloudImportOperationView(
            operation_id=operation.idempotency_key,
            status=status,
            session=self.authorization._view(row),
            error_code=safe_error_code(operation.error_code) if operation.error_code else None,
        )

    def submit_import(
        self, session_id: UUID, actor: AuthActor, input: CloudImportCommand
    ) -> CloudImportOperationView:
        service = self.authorization
        with atomic_write_transaction(self.db):
            row = service._owned(session_id, actor, lock=True)
            if row.mode != "openlist_builtin_import":
                raise auth_problem("cloud_auth_state_invalid")
            access, refresh = (
                input.access_token.get_secret_value(),
                input.refresh_token.get_secret_value(),
            )
            fingerprint = import_payload_fingerprint(service.settings, row, access, refresh)
            existing = service._existing(actor, input.operation_id, fingerprint)
            if existing is not None:
                if existing.session_id != row.id or existing.action != "verify_import":
                    raise auth_problem("cloud_auth_operation_conflict")
                return self._view(row, existing)
            service._require_runtime()
            require_auth_source_current(self.db, row)
            now = datetime.now(UTC)
            if row.expires_at <= now:
                raise auth_problem("cloud_auth_expired")
            if row.revision != input.expected_revision:
                raise auth_problem("cloud_auth_revision_changed")
            times = list(
                self.db.scalars(
                    select(CloudAuthOperation.created_at)
                    .where(
                        CloudAuthOperation.owner_account_id == actor.account_id,
                        CloudAuthOperation.provider == row.provider,
                        CloudAuthOperation.action == "verify_import",
                        CloudAuthOperation.created_at > now - timedelta(minutes=1),
                    )
                    .order_by(CloudAuthOperation.created_at)
                )
            )
            if len(times) >= 5:
                retry = max(1, int((times[0] + timedelta(minutes=1) - now).total_seconds()) + 1)
                raise auth_problem("cloud_import_rate_limited", 429, retry_after_seconds=retry)
            if row.state != "awaiting_import":
                raise auth_problem("cloud_auth_state_invalid")
            target = self.db.get(CloudBackupTarget, row.provider, populate_existing=True)
            if (target.revision if target else 0) != row.target_revision:
                raise auth_problem("cloud_auth_revision_changed")
            digests = import_token_digests(service.settings, row.provider, access, refresh)
            if known_import_token_reused(self.db, row.provider, digests, cipher=service.cipher):
                raise auth_problem("cloud_import_credentials_reused")
            row.state, row.revision = "verifying_import", row.revision + 1
            operation = service._operation(
                row,
                actor,
                input.operation_id,
                "verify_import",
                fingerprint,
                {"access_token": access, "refresh_token": refresh},
            )
            store_import_token_digests(operation, digests, service.cipher)
            self.db.flush()
            return self._view(row, operation)

    def get_import_operation(
        self, session_id: UUID, actor: AuthActor, operation_key: UUID
    ) -> CloudImportOperationView:
        service = self.authorization
        with atomic_write_transaction(self.db):
            row = service._owned(session_id, actor, lock=True)
            operation = self.db.scalar(
                select(CloudAuthOperation).where(
                    CloudAuthOperation.session_id == row.id,
                    CloudAuthOperation.owner_account_id == actor.account_id,
                    CloudAuthOperation.owner_login_session_id == actor.login_session_id,
                    CloudAuthOperation.idempotency_key == operation_key,
                    CloudAuthOperation.action == "verify_import",
                )
            )
            if operation is None:
                raise auth_problem("cloud_auth_not_found", 404)
            if row.state not in {
                "applied",
                "expired",
                "cancelled",
                "failed",
            } and row.expires_at <= datetime.now(UTC):
                service._retire(row, "expired")
                self.db.flush()
            return self._view(row, operation)
