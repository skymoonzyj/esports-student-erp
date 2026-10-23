"""Owner-bound public orchestration. External I/O belongs to the leased runner."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.operations.cloud_auth_errors import auth_problem, safe_error_code
from app.operations.cloud_auth_models import (
    CloudAuthApplication,
    CloudAuthCandidate,
    CloudAuthOperation,
    CloudAuthSession,
    CloudConnectorState,
)
from app.operations.cloud_auth_requests import (
    CloudDirectoryRequest,
    CloudDirectoryResult,
    CloudDirectorySelection,
    CloudImportCommand,
    CloudOperationCommand,
)
from app.operations.cloud_auth_schemas import (
    AUTH_PROVIDERS,
    TERMINAL_AUTH_STATES,
    AuthActor,
    AuthMode,
    AuthProvider,
    CloudAppUpdate,
    CloudAppView,
    CloudAuthSessionView,
    CloudImportOperationView,
    CloudProbeAttemptView,
    QrPresentation,
)
from app.operations.cloud_auth_security import require_auth_actor, require_no_maintenance
from app.operations.cloud_auth_source import require_auth_source_current
from app.operations.cloud_auth_state import AUTH_LIFETIME, aad
from app.operations.cloud_models import CloudBackupTarget
from app.operations.models import OperationsServiceHeartbeat
from app.security.encryption import EncryptedFieldError, EnvelopeCipher

RUNNER_SERVICE_KEY = "cloud_authorization"
HEALTH_LIFETIME = timedelta(seconds=120)


def _fingerprint(data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CloudAuthorizationService:
    def __init__(
        self, db: Session, *, cipher: EnvelopeCipher, settings: Settings | None = None
    ) -> None:
        self.db, self.cipher = db, cipher
        self.settings = settings or get_settings()

    def _provider(self, provider: str) -> AuthProvider:
        if provider not in AUTH_PROVIDERS:
            raise auth_problem("cloud_provider_unsupported", 422)
        return cast(AuthProvider, provider)

    def _lock(self, provider: AuthProvider, actor: AuthActor) -> None:
        self.db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": 62000 + AUTH_PROVIDERS.index(provider)},
        )
        require_auth_actor(self.db, actor, lock=True)

    def _app_view(self, provider: AuthProvider, row: CloudAuthApplication | None) -> CloudAppView:
        configured = (
            row is not None
            and bool(row.client_id)
            and (provider == "115" or bool(row.secret_ciphertext))
        )
        prerequisites = [] if configured else ["cloud_app_unconfigured"]
        prerequisites.extend(self._runtime_prerequisites())
        return CloudAppView(
            provider=provider,
            client_id=row.client_id if row else None,
            secret_present=bool(row and row.secret_ciphertext),
            revision=row.revision if row else 0,
            configured=configured,
            prerequisites=prerequisites,
        )

    def _runtime_prerequisites(self) -> list[str]:
        now = datetime.now(UTC)
        prerequisites = []
        connector = self.db.scalar(
            select(CloudConnectorState)
            .where(
                CloudConnectorState.registered.is_(True),
                CloudConnectorState.last_seen_at >= now - HEALTH_LIFETIME,
            )
            .limit(1)
        )
        if connector is None:
            prerequisites.append("cloud_connector_unavailable")
        heartbeat = self.db.get(
            OperationsServiceHeartbeat, RUNNER_SERVICE_KEY, populate_existing=True
        )
        if (
            heartbeat is None
            or heartbeat.status != "healthy"
            or heartbeat.last_seen_at < now - HEALTH_LIFETIME
        ):
            prerequisites.append("cloud_runner_offline")
        if self.db.scalar(
            select(CloudConnectorState.id)
            .where(CloudConnectorState.maintenance_operation_id.is_not(None))
            .limit(1)
        ):
            prerequisites.append("cloud_maintenance_active")
        return prerequisites

    def _require_runtime(self) -> None:
        require_no_maintenance(self.db)
        prerequisites = self._runtime_prerequisites()
        if prerequisites:
            raise auth_problem(prerequisites[0])

    def _source_current(self, row: CloudAuthSession) -> bool:
        try:
            require_auth_source_current(self.db, row)
            return True
        except ProblemError:
            return False

    def get_application(self, provider: AuthProvider, actor: AuthActor) -> CloudAppView:
        require_auth_actor(self.db, actor)
        return self._app_view(
            self._provider(provider),
            self.db.get(CloudAuthApplication, provider, populate_existing=True),
        )

    def save_application(
        self, provider: AuthProvider, actor: AuthActor, input: CloudAppUpdate
    ) -> CloudAppView:
        provider = self._provider(provider)
        with atomic_write_transaction(self.db):
            self._lock(provider, actor)
            require_no_maintenance(self.db)
            row = self.db.get(CloudAuthApplication, provider, populate_existing=True)
            if input.expected_revision != (row.revision if row else 0):
                raise auth_problem("cloud_auth_revision_changed")
            secret = input.client_secret.get_secret_value() if input.client_secret else None
            if row is None:
                row = CloudAuthApplication(
                    id=uuid4(), provider=provider, client_id=input.client_id, revision=1
                )
                self.db.add(row)
                changed = True
            else:
                changed = row.client_id != input.client_id or (
                    input.clear_secret and row.secret_ciphertext is not None
                )
                if secret is not None:
                    changed = (
                        changed
                        or row.secret_ciphertext is None
                        or secret
                        != self.cipher.decrypt(
                            row.secret_ciphertext, field_name=aad(provider, row.id, "secret")
                        )
                    )
                if changed:
                    row.revision += 1
            row.client_id = input.client_id
            if input.clear_secret:
                row.secret_ciphertext = None
            elif secret is not None and changed:
                row.secret_ciphertext = self.cipher.encrypt(
                    secret, field_name=aad(provider, row.id, "secret")
                )
            self.db.flush()
            return self._app_view(provider, row)

    def _view(self, row: CloudAuthSession) -> CloudAuthSessionView:
        result = CloudAuthSessionView.model_validate(row)
        result.probe_attempt = self._probe_attempt(row)
        if row.next_poll_at is not None:
            result.next_poll_after_seconds = min(
                600, max(1, int((row.next_poll_at - datetime.now(UTC)).total_seconds()))
            )
        if result.error_code is not None:
            result.error_code = safe_error_code(result.error_code)
        return result

    def _probe_clean(self, operation: CloudAuthOperation) -> bool:
        if not operation.result_ciphertext:
            return False
        try:
            proof = json.loads(
                self.cipher.decrypt(
                    operation.result_ciphertext,
                    field_name=aad(
                        self._provider(operation.provider),
                        operation.id,
                        "probe",
                        mode=cast(AuthMode, operation.mode),
                        protocol_version=operation.protocol_version,
                    ),
                )
            )
            return (
                isinstance(proof, dict)
                and proof.get("finished") is True
                and proof.get("cleanup_pending") is False
            )
        except (EncryptedFieldError, ValueError, TypeError):
            return False

    def _probe_attempt(self, row: CloudAuthSession) -> CloudProbeAttemptView | None:
        operation = self.db.scalar(
            select(CloudAuthOperation)
            .where(
                CloudAuthOperation.session_id == row.id,
                CloudAuthOperation.owner_account_id == row.owner_account_id,
                CloudAuthOperation.owner_login_session_id == row.owner_login_session_id,
                CloudAuthOperation.action == "probe",
            )
            .order_by(CloudAuthOperation.created_at.desc(), CloudAuthOperation.id.desc())
            .limit(1)
        )
        if operation is None:
            return None
        target = self.db.get(CloudBackupTarget, row.provider, populate_existing=True)
        success = operation.status == "succeeded"
        if (
            not self._source_current(row)
            or operation.mode != row.mode
            or operation.auth_config_id != row.auth_config_id
            or operation.auth_config_revision != row.auth_config_revision
            or (target.revision if target else 0) != row.target_revision
            or operation.app_revision != row.app_revision
            or operation.target_revision != row.target_revision
            or operation.session_revision + int(success) != row.revision
            or operation.candidate_id != row.candidate_id
        ):
            return None
        clean = self._probe_clean(operation)
        if success:
            candidate = (
                self.db.get(CloudAuthCandidate, row.candidate_id) if row.candidate_id else None
            )
            if (
                candidate is None
                or candidate.probe_operation_id != operation.id
                or candidate.selected_directory != row.selected_directory
                or not clean
            ):
                return None
            status = "succeeded"
        elif operation.status in {"queued", "running"}:
            status = "cleanup_pending" if operation.result_ciphertext and not clean else "pending"
        else:
            status = "failed" if operation.status == "failed" and clean else "cleanup_pending"
        return CloudProbeAttemptView(operation_id=operation.idempotency_key, status=status)

    def _existing(
        self, actor: AuthActor, operation_key: UUID, fingerprint: str
    ) -> CloudAuthOperation | None:
        row = self.db.scalar(
            select(CloudAuthOperation)
            .where(
                CloudAuthOperation.owner_account_id == actor.account_id,
                CloudAuthOperation.owner_login_session_id == actor.login_session_id,
                CloudAuthOperation.idempotency_key == operation_key,
            )
            .execution_options(populate_existing=True)
        )
        if row is not None and row.request_fingerprint != fingerprint:
            raise auth_problem("cloud_auth_operation_conflict")
        return row

    def _operation(
        self,
        row: CloudAuthSession,
        actor: AuthActor,
        key: UUID,
        action: str,
        fingerprint: str,
        payload: dict[str, Any],
    ) -> CloudAuthOperation:
        operation = CloudAuthOperation(
            id=uuid4(),
            provider=row.provider,
            session_id=row.id,
            candidate_id=row.candidate_id,
            owner_account_id=actor.account_id,
            owner_login_session_id=actor.login_session_id,
            idempotency_key=key,
            action=action,
            status="queued",
            request_fingerprint=fingerprint,
            session_revision=row.revision,
            mode=row.mode,
            protocol_version=row.protocol_version,
            auth_config_id=row.auth_config_id,
            auth_config_revision=row.auth_config_revision,
            app_revision=row.app_revision,
            target_revision=row.target_revision,
            next_attempt_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
            attempts=0,
            cleanup_status="none",
        )
        operation.payload_ciphertext = self.cipher.encrypt(
            json.dumps(payload, separators=(",", ":")),
            field_name=aad(
                self._provider(row.provider),
                operation.id,
                "payload",
                mode=cast(AuthMode, operation.mode),
                protocol_version=operation.protocol_version,
            ),
        )
        self.db.add(operation)
        self.db.flush()
        return operation

    def _retire(self, row: CloudAuthSession, state: str) -> None:
        from app.operations.cloud_auth_apply import release_switch

        release_switch(self.db, row)
        if row.state == "applying" and state == "expired":
            row.error_code = "cloud_target_busy"
        row.state, row.revision = state, row.revision + 1
        row.device_ciphertext = row.token_ciphertext = row.qr_ciphertext = None
        row.qr_expires_at = None
        for operation in self.db.scalars(
            select(CloudAuthOperation).where(CloudAuthOperation.session_id == row.id)
        ):
            if operation.action not in ("cleanup", "unbind") and operation.status in (
                "queued",
                "running",
            ):
                operation.status = "cancelled"
                operation.completed_at = datetime.now(UTC)
            if row.candidate_id is not None or operation.result_ciphertext is not None:
                operation.cleanup_status = "pending"
            operation.payload_ciphertext = None
            # Candidate recovery journals stay intact. Temporary import credentials
            # are never needed once this session is terminal.
            if operation.action == "verify_import":
                operation.refresh_checkpoint_ciphertext = None

    def _owned(self, session_id: UUID, actor: AuthActor, *, lock: bool = False) -> CloudAuthSession:
        require_auth_actor(self.db, actor)
        query = select(CloudAuthSession).where(
            CloudAuthSession.id == session_id,
            CloudAuthSession.owner_account_id == actor.account_id,
            CloudAuthSession.owner_login_session_id == actor.login_session_id,
        )
        row = self.db.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise auth_problem("cloud_auth_not_found", 404)
        if lock:
            self._lock(self._provider(row.provider), actor)
            row = self.db.scalar(query.with_for_update().execution_options(populate_existing=True))
            assert row is not None
        return row

    def create_session(
        self,
        provider: AuthProvider,
        actor: AuthActor,
        operation_key: UUID,
        mode: AuthMode = "own_app",
    ) -> CloudAuthSessionView:
        provider = self._provider(provider)
        if mode not in ("own_app", "openlist_builtin_import"):
            raise auth_problem("cloud_provider_unsupported", 422)
        action = "create_qr" if mode == "own_app" else "create_import"
        # Preserve the original omitted-mode own-app replay fingerprint.
        fingerprint = _fingerprint({"action": action, "provider": provider})
        with atomic_write_transaction(self.db):
            self._lock(provider, actor)
            existing = self._existing(actor, operation_key, fingerprint)
            if existing is not None:
                return self.get_session(existing.session_id, actor)
            require_no_maintenance(self.db)
            app = self.db.get(CloudAuthApplication, provider, populate_existing=True)
            if mode == "own_app":
                view = self._app_view(provider, app)
                if view.prerequisites:
                    raise auth_problem(view.prerequisites[0])
                assert app is not None
            else:
                self._require_runtime()
            now = datetime.now(UTC)
            for period, limit in ((timedelta(minutes=1), 3), (timedelta(hours=1), 10)):
                times = list(
                    self.db.scalars(
                        select(CloudAuthOperation.created_at)
                        .where(
                            CloudAuthOperation.owner_account_id == actor.account_id,
                            CloudAuthOperation.provider == provider,
                            CloudAuthOperation.action.in_(["create_qr", "create_import"]),
                            CloudAuthOperation.created_at > now - period,
                        )
                        .order_by(CloudAuthOperation.created_at)
                    )
                )
                if len(times) >= limit:
                    retry = max(1, int((times[0] + period - now).total_seconds()) + 1)
                    raise auth_problem("cloud_auth_rate_limited", 429, retry_after_seconds=retry)
            live = self.db.scalar(
                select(CloudAuthSession)
                .where(
                    CloudAuthSession.provider == provider,
                    CloudAuthSession.state.not_in(TERMINAL_AUTH_STATES),
                )
                .with_for_update()
            )
            if live is not None:
                if live.expires_at > now:
                    raise auth_problem("cloud_auth_live")
                self._retire(live, "expired")
                self.db.flush()
            target = self.db.get(CloudBackupTarget, provider, populate_existing=True)
            row = CloudAuthSession(
                id=uuid4(),
                provider=provider,
                owner_account_id=actor.account_id,
                owner_login_session_id=actor.login_session_id,
                state="creating" if mode == "own_app" else "awaiting_import",
                revision=1,
                mode=mode,
                protocol_version=2,
                application_provider=provider if mode == "own_app" else None,
                auth_config_id=("own-app:" if mode == "own_app" else "openlist-builtin-import-v1:")
                + provider,
                auth_config_revision=app.revision if app and mode == "own_app" else 1,
                app_revision=app.revision if app and mode == "own_app" else None,
                target_revision=target.revision if target else 0,
                expires_at=now + AUTH_LIFETIME,
            )
            self.db.add(row)
            self.db.flush()
            operation = self._operation(row, actor, operation_key, action, fingerprint, {})
            if mode == "openlist_builtin_import":
                operation.status, operation.completed_at = "succeeded", now
                operation.payload_ciphertext = None
                self.db.flush()
            return self._view(row)

    def submit_import(
        self, session_id: UUID, actor: AuthActor, input: CloudImportCommand
    ) -> CloudImportOperationView:
        from app.operations.cloud_auth_import import CloudImportService

        return CloudImportService(self).submit_import(session_id, actor, input)

    def get_import_operation(
        self, session_id: UUID, actor: AuthActor, operation_key: UUID
    ) -> CloudImportOperationView:
        from app.operations.cloud_auth_import import CloudImportService

        return CloudImportService(self).get_import_operation(session_id, actor, operation_key)

    def get_session(self, session_id: UUID, actor: AuthActor) -> CloudAuthSessionView:
        with atomic_write_transaction(self.db):
            row = self._owned(session_id, actor, lock=True)
            if row.state not in TERMINAL_AUTH_STATES and row.expires_at <= datetime.now(UTC):
                self._retire(row, "expired")
                self.db.flush()
            return self._view(row)

    def cancel_session(
        self, session_id: UUID, actor: AuthActor, expected_revision: int
    ) -> CloudAuthSessionView:
        with atomic_write_transaction(self.db):
            row = self._owned(session_id, actor, lock=True)
            if row.revision != expected_revision:
                raise auth_problem("cloud_auth_revision_changed")
            if row.state == "cancelled":
                return self._view(row)
            if row.state in TERMINAL_AUTH_STATES or row.state == "applying":
                raise auth_problem("cloud_auth_state_invalid")
            self._retire(row, "cancelled")
            self.db.flush()
            return self._view(row)

    def get_qr(self, session_id: UUID, actor: AuthActor) -> QrPresentation:
        with atomic_write_transaction(self.db):
            row = self._owned(session_id, actor, lock=True)
            now = datetime.now(UTC)
            if (
                row.state != "awaiting_confirmation"
                or row.expires_at <= now
                or row.qr_expires_at is None
                or row.qr_expires_at <= now
                or row.qr_ciphertext is None
            ):
                raise auth_problem("cloud_auth_qr_unavailable")
            try:
                result = QrPresentation.model_validate_json(
                    self.cipher.decrypt(
                        row.qr_ciphertext,
                        field_name=aad(
                            self._provider(row.provider),
                            row.id,
                            "qr",
                            mode=cast(AuthMode, row.mode),
                            protocol_version=row.protocol_version,
                        ),
                    )
                )
            except (EncryptedFieldError, ValidationError):
                raise auth_problem("cloud_credentials_invalid", 503) from None
            if (
                result.expires_at > min(row.expires_at, row.qr_expires_at)
                or result.expires_at <= now
            ):
                raise auth_problem("cloud_auth_qr_unavailable")
            return result

    def _queue(
        self,
        session_id: UUID,
        actor: AuthActor,
        input: CloudOperationCommand,
        action: str,
        payload: dict[str, Any],
    ) -> tuple[CloudAuthSession, CloudAuthOperation]:
        row = self._owned(session_id, actor, lock=True)
        fingerprint = _fingerprint(
            {
                "action": action,
                "session_id": str(session_id),
                "expected_revision": input.expected_revision,
                **payload,
            }
        )
        existing = self._existing(actor, input.operation_id, fingerprint)
        if existing is not None:
            return row, existing
        require_no_maintenance(self.db)
        if row.revision != input.expected_revision:
            raise auth_problem("cloud_auth_revision_changed")
        if row.expires_at <= datetime.now(UTC):
            raise auth_problem("cloud_auth_expired")
        if row.state not in ("authorized", "configuring", "test_pending", "ready"):
            raise auth_problem("cloud_auth_state_invalid")
        require_auth_source_current(self.db, row)
        target = self.db.get(CloudBackupTarget, row.provider, populate_existing=True)
        if (target.revision if target else 0) != row.target_revision:
            raise auth_problem("cloud_auth_revision_changed")
        if action == "select_directory":
            candidate = (
                self.db.get(CloudAuthCandidate, row.candidate_id) if row.candidate_id else None
            )
            if candidate is None or candidate.state not in {"configured", "verified"}:
                raise auth_problem("cloud_auth_state_invalid")
            # A lease expiring is not proof that its remote probe has stopped.
            for probe in self.db.scalars(
                select(CloudAuthOperation).where(
                    CloudAuthOperation.candidate_id == row.candidate_id,
                    CloudAuthOperation.action == "probe",
                )
            ):
                if probe.status in {"queued", "running"}:
                    raise auth_problem("cloud_target_busy")
                if probe.result_ciphertext:
                    try:
                        proof = json.loads(
                            self.cipher.decrypt(
                                probe.result_ciphertext,
                                field_name=aad(
                                    self._provider(row.provider),
                                    probe.id,
                                    "probe",
                                    mode=cast(AuthMode, probe.mode),
                                    protocol_version=probe.protocol_version,
                                ),
                            )
                        )
                        unresolved = (
                            not isinstance(proof, dict)
                            or not proof.get("finished")
                            or proof.get("cleanup_pending")
                        )
                    except (EncryptedFieldError, ValueError, TypeError):
                        unresolved = True
                    if unresolved:
                        raise auth_problem("cloud_target_busy")
                elif probe.status == "failed":
                    raise auth_problem("cloud_target_busy")
            if (
                self.db.scalar(
                    select(CloudAuthOperation.id)
                    .where(
                        CloudAuthOperation.session_id == row.id,
                        CloudAuthOperation.action.in_(["prepare", "select_directory"]),
                        CloudAuthOperation.status.in_(["queued", "running"]),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise auth_problem("cloud_target_busy")
            row.selected_directory, row.state = payload["directory"], "configuring"
            row.revision += 1
        elif action == "probe":
            if (
                row.state not in {"test_pending", "ready"}
                or row.selected_directory is None
                or row.candidate_id is None
            ):
                raise auth_problem("cloud_auth_state_invalid")
            candidate = self.db.get(CloudAuthCandidate, row.candidate_id)
            if (
                candidate is None
                or candidate.state not in {"configured", "verified"}
                or candidate.selected_directory != row.selected_directory
            ):
                raise auth_problem("cloud_auth_state_invalid")
            if (
                self.db.scalar(
                    select(CloudAuthOperation.id)
                    .where(
                        CloudAuthOperation.candidate_id == row.candidate_id,
                        CloudAuthOperation.action == "probe",
                        CloudAuthOperation.status.in_(["queued", "running"]),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise auth_problem("cloud_target_busy")
            if any(
                not self._probe_clean(probe)
                for probe in self.db.scalars(
                    select(CloudAuthOperation).where(
                        CloudAuthOperation.candidate_id == row.candidate_id,
                        CloudAuthOperation.action == "probe",
                        CloudAuthOperation.status == "failed",
                    )
                )
            ):
                raise auth_problem("cloud_target_busy")
            row.state, row.revision = "test_pending", row.revision + 1
        return row, self._operation(row, actor, input.operation_id, action, fingerprint, payload)

    def queue_directories(
        self, session_id: UUID, actor: AuthActor, input: CloudDirectoryRequest
    ) -> CloudDirectoryResult:
        with atomic_write_transaction(self.db):
            row, operation = self._queue(
                session_id, actor, input, "list_directories", {"parent": input.parent}
            )
            return self._directory_result(row, operation)

    def _directory_result(
        self, row: CloudAuthSession, operation: CloudAuthOperation
    ) -> CloudDirectoryResult:
        result = CloudDirectoryResult(operation_id=operation.idempotency_key, status="pending")
        target = self.db.get(CloudBackupTarget, row.provider, populate_existing=True)
        if (
            operation.session_revision != row.revision
            or operation.app_revision != row.app_revision
            or operation.target_revision != row.target_revision
            or not self._source_current(row)
            or operation.mode != row.mode
            or operation.auth_config_id != row.auth_config_id
            or operation.auth_config_revision != row.auth_config_revision
            or (target.revision if target else 0) != row.target_revision
        ):
            return CloudDirectoryResult(
                operation_id=operation.idempotency_key,
                status="failed",
                error_code="cloud_auth_revision_changed",
            )
        if operation.status in ("failed", "cancelled"):
            result.status, result.error_code = "failed", safe_error_code(operation.error_code)
        elif operation.status == "succeeded":
            try:
                payload = json.loads(
                    self.cipher.decrypt(
                        operation.result_ciphertext or "",
                        field_name=aad(
                            self._provider(row.provider),
                            operation.id,
                            "result",
                            mode=cast(AuthMode, operation.mode),
                            protocol_version=operation.protocol_version,
                        ),
                    )
                )
                if not isinstance(payload, dict) or set(payload) != {"entries"}:
                    raise ValueError("invalid directory result")
                result = CloudDirectoryResult.model_validate(
                    {"operation_id": operation.idempotency_key, "status": "ready", **payload}
                )
            except (EncryptedFieldError, ValidationError, ValueError, TypeError):
                raise auth_problem("cloud_credentials_invalid", 503) from None
        return result

    def get_directories(
        self, session_id: UUID, actor: AuthActor, operation_key: UUID
    ) -> CloudDirectoryResult:
        with atomic_write_transaction(self.db):
            row = self._owned(session_id, actor, lock=True)
            if row.expires_at <= datetime.now(UTC) or row.state == "expired":
                raise auth_problem("cloud_auth_expired")
            if row.state in TERMINAL_AUTH_STATES:
                raise auth_problem("cloud_auth_state_invalid")
            operation = self.db.scalar(
                select(CloudAuthOperation).where(
                    CloudAuthOperation.session_id == session_id,
                    CloudAuthOperation.owner_account_id == actor.account_id,
                    CloudAuthOperation.owner_login_session_id == actor.login_session_id,
                    CloudAuthOperation.idempotency_key == operation_key,
                    CloudAuthOperation.action == "list_directories",
                )
            )
            if operation is None:
                raise auth_problem("cloud_auth_not_found", 404)
            return self._directory_result(row, operation)

    def select_directory(
        self, session_id: UUID, actor: AuthActor, input: CloudDirectorySelection
    ) -> CloudAuthSessionView:
        with atomic_write_transaction(self.db):
            row, _ = self._queue(
                session_id, actor, input, "select_directory", {"directory": input.directory}
            )
            return self._view(row)

    def queue_test(
        self, session_id: UUID, actor: AuthActor, input: CloudOperationCommand
    ) -> CloudAuthSessionView:
        with atomic_write_transaction(self.db):
            row, _ = self._queue(session_id, actor, input, "probe", {})
            return self._view(row)
