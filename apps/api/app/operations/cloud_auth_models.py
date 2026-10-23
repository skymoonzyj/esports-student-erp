"""Durable authorization fences and ERP-owned connector operation journals."""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Connection,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin

_LIVE_SESSION = "state NOT IN ('applied', 'expired', 'cancelled', 'failed')"


class ColumnDefaultContext(Protocol):
    connection: Connection

    def get_current_parameters(self) -> dict[str, Any]: ...


def _source_check(*, application: bool = False) -> str:
    own = "mode = 'own_app' AND app_revision IS NOT NULL AND app_revision >= 1 AND auth_config_revision = app_revision AND auth_config_id = 'own-app:' || provider"
    builtin = "mode = 'openlist_builtin_import' AND app_revision IS NULL AND auth_config_revision = 1 AND auth_config_id = 'openlist-builtin-import-v1:' || provider"
    if application:
        own += " AND application_provider IS NOT NULL AND application_provider = provider"
        builtin += " AND application_provider IS NULL"
    return (
        "mode IS NOT NULL AND auth_config_id IS NOT NULL AND auth_config_revision IS NOT NULL AND auth_config_revision > 0 AND "
        "protocol_version IS NOT NULL AND protocol_version IN (1, 2) AND (protocol_version = 2 OR mode = 'own_app') AND "
        f"(({own}) OR ({builtin}))"
    )


def _application_provider(context: ColumnDefaultContext) -> str | None:
    values = context.get_current_parameters()
    return cast(str, values["provider"]) if values.get("mode", "own_app") == "own_app" else None


def _legacy_protocol(context: ColumnDefaultContext) -> int:
    """Only omitted own-app source fields are the actual old constructor form."""
    values = context.get_current_parameters()
    return (
        2
        if values.get("mode") == "openlist_builtin_import"
        or values.get("auth_config_id") is not None
        else 1
    )


def _auth_config_id(context: ColumnDefaultContext) -> str:
    values = context.get_current_parameters()
    prefix = (
        "own-app:" if values.get("mode", "own_app") == "own_app" else "openlist-builtin-import-v1:"
    )
    return prefix + cast(str, values["provider"])


def _auth_config_revision(context: ColumnDefaultContext) -> int | None:
    values = context.get_current_parameters()
    return (
        cast(int | None, values.get("app_revision"))
        if values.get("mode", "own_app") == "own_app"
        else 1
    )


def _session_source_default(field: str) -> Callable[[ColumnDefaultContext], Any]:
    def default(context: ColumnDefaultContext) -> Any:
        values = context.get_current_parameters()
        return context.connection.scalar(
            select(getattr(CloudAuthSession, field)).where(
                CloudAuthSession.id == values["session_id"]
            )
        )

    return default


class CloudAuthApplication(TimestampMixin, Base):
    __tablename__ = "cloud_auth_applications"
    __table_args__ = (
        CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_app_provider"),
        CheckConstraint("revision >= 1", name="ck_cloud_auth_app_revision"),
        Index("uq_cloud_auth_app_id", "id", unique=True),
    )
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), default=uuid4, nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CloudAuthSession(TimestampMixin, Base):
    __tablename__ = "cloud_auth_sessions"
    __table_args__ = (
        CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_session_provider"),
        CheckConstraint(
            "state IN ('creating', 'awaiting_import', 'verifying_import', 'awaiting_confirmation', 'authorized', 'configuring', "
            "'test_pending', 'ready', 'applying', 'applied', 'expired', 'cancelled', 'failed')",
            name="ck_cloud_auth_session_state",
        ),
        CheckConstraint(
            "revision >= 1 AND target_revision >= 0",
            name="ck_cloud_auth_session_revisions",
        ),
        CheckConstraint(_source_check(application=True), name="ck_cloud_auth_session_source"),
        CheckConstraint(
            "(expiry_source IS NULL AND token_expires_at IS NULL) OR (expiry_source IS NOT NULL AND expiry_source = 'provider' AND token_expires_at IS NOT NULL) OR (expiry_source IS NOT NULL AND expiry_source = 'unreported' AND token_expires_at IS NULL AND provider = 'baidu' AND mode = 'openlist_builtin_import')",
            name="ck_cloud_auth_session_token_expiry",
        ),
        Index(
            "uq_cloud_auth_session_live_provider",
            "provider",
            unique=True,
            postgresql_where=text(_LIVE_SESSION),
        ),
        Index("ix_cloud_auth_session_owner", "owner_account_id", "owner_login_session_id"),
        Index("ix_cloud_auth_session_expiry", "expires_at", postgresql_where=text(_LIVE_SESSION)),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32), default="own_app", server_default="own_app", nullable=False
    )
    protocol_version: Mapped[int] = mapped_column(
        Integer, default=_legacy_protocol, server_default="2", nullable=False
    )
    application_provider: Mapped[str | None] = mapped_column(
        String(16),
        ForeignKey("cloud_auth_applications.provider", ondelete="RESTRICT"),
        default=_application_provider,
    )
    auth_config_id: Mapped[str] = mapped_column(String(80), default=_auth_config_id, nullable=False)
    auth_config_revision: Mapped[int] = mapped_column(
        Integer, default=_auth_config_revision, nullable=False
    )
    owner_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # Retain the actual login-session UUID even after session purging so cleanup
    # ownership survives logout; acceptance always reloads the live auth session.
    owner_login_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="creating", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    app_revision: Mapped[int | None] = mapped_column(Integer)
    target_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qr_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_source: Mapped[str | None] = mapped_column(String(16))
    device_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_ciphertext: Mapped[str | None] = mapped_column(Text)
    qr_ciphertext: Mapped[str | None] = mapped_column(Text)
    account_label: Mapped[str | None] = mapped_column(String(255))
    selected_directory: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class CloudAuthCandidate(TimestampMixin, Base):
    __tablename__ = "cloud_auth_candidates"
    __table_args__ = (
        CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_candidate_provider"),
        CheckConstraint(
            "state IN ('preparing', 'configured', 'verified', 'active', 'cleanup_pending', 'cleaned', 'failed')",
            name="ck_cloud_auth_candidate_state",
        ),
        CheckConstraint("config_revision >= 1", name="ck_cloud_auth_candidate_revision"),
        CheckConstraint(_source_check(), name="ck_cloud_auth_candidate_source"),
        CheckConstraint(
            "(probe_operation_id IS NULL AND probe_sha256 IS NULL AND probe_verified_at IS NULL) OR (probe_operation_id IS NOT NULL AND probe_sha256 IS NOT NULL AND probe_verified_at IS NOT NULL AND selected_directory IS NOT NULL)",
            name="ck_cloud_auth_candidate_probe",
        ),
        Index("uq_cloud_auth_candidate_session", "session_id", unique=True),
        Index("uq_cloud_auth_candidate_operation", "operation_id", unique=True),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloud_auth_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32),
        default=_session_source_default("mode"),
        server_default="own_app",
        nullable=False,
    )
    protocol_version: Mapped[int] = mapped_column(
        Integer,
        default=_session_source_default("protocol_version"),
        server_default="2",
        nullable=False,
    )
    auth_config_id: Mapped[str] = mapped_column(
        String(80), default=_session_source_default("auth_config_id"), nullable=False
    )
    auth_config_revision: Mapped[int] = mapped_column(
        Integer, default=_session_source_default("auth_config_revision"), nullable=False
    )
    app_revision: Mapped[int | None] = mapped_column(
        Integer, default=_session_source_default("app_revision")
    )
    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    connector_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_id: Mapped[str | None] = mapped_column(String(255))
    webdav_user_id: Mapped[str | None] = mapped_column(String(255))
    mount_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    account_fingerprint_ciphertext: Mapped[str | None] = mapped_column(Text)
    username_ciphertext: Mapped[str | None] = mapped_column(Text)
    password_ciphertext: Mapped[str | None] = mapped_column(Text)
    config_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    selected_directory: Mapped[str | None] = mapped_column(String(1024))
    probe_operation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    probe_sha256: Mapped[str | None] = mapped_column(String(64))
    probe_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32), default="preparing", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))


class CloudAuthOperation(Base):
    __tablename__ = "cloud_auth_operations"
    __table_args__ = (
        CheckConstraint("provider IN ('baidu', '115')", name="ck_cloud_auth_operation_provider"),
        CheckConstraint(
            "action IN ('create_qr', 'create_import', 'verify_import', 'poll', 'prepare', 'list_directories', 'select_directory', 'probe', 'apply', 'cleanup', 'unbind')",
            name="ck_cloud_auth_operation_action",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_cloud_auth_operation_status",
        ),
        CheckConstraint(
            "cleanup_status IN ('none', 'pending', 'running', 'succeeded', 'failed')",
            name="ck_cloud_auth_operation_cleanup",
        ),
        CheckConstraint(
            "attempts >= 0 AND session_revision >= 1 AND target_revision >= 0",
            name="ck_cloud_auth_operation_revisions",
        ),
        CheckConstraint(_source_check(), name="ck_cloud_auth_operation_source"),
        CheckConstraint(
            "refresh_phase IN ('not_started', 'sent', 'refreshed', 'verified', 'unknown')",
            name="ck_cloud_auth_operation_refresh_phase",
        ),
        CheckConstraint(
            "(lease_id IS NULL) = (lease_expires_at IS NULL)", name="ck_cloud_auth_operation_lease"
        ),
        Index(
            "uq_cloud_auth_operation_owner_key",
            "owner_account_id",
            "owner_login_session_id",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_cloud_auth_operation_claim", "status", "next_attempt_at", "lease_expires_at"),
        Index(
            "ix_cloud_auth_operation_cleanup",
            "cleanup_status",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32),
        default=_session_source_default("mode"),
        server_default="own_app",
        nullable=False,
    )
    protocol_version: Mapped[int] = mapped_column(
        Integer,
        default=_session_source_default("protocol_version"),
        server_default="2",
        nullable=False,
    )
    auth_config_id: Mapped[str] = mapped_column(
        String(80), default=_session_source_default("auth_config_id"), nullable=False
    )
    auth_config_revision: Mapped[int] = mapped_column(
        Integer, default=_session_source_default("auth_config_revision"), nullable=False
    )
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cloud_auth_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    owner_account_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    owner_login_session_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    session_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    app_revision: Mapped[int | None] = mapped_column(Integer)
    target_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleanup_status: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    payload_ciphertext: Mapped[str | None] = mapped_column(Text)
    result_ciphertext: Mapped[str | None] = mapped_column(Text)
    refresh_phase: Mapped[str] = mapped_column(
        String(16), default="not_started", server_default="not_started", nullable=False
    )
    refresh_checkpoint_ciphertext: Mapped[str | None] = mapped_column(Text)
    import_token_digests_ciphertext: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CloudBackupTargetHistory(Base):
    __tablename__ = "cloud_backup_target_history"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('baidu', '115', 'quark')", name="ck_cloud_target_history_provider"
        ),
        CheckConstraint("revision >= 1", name="ck_cloud_target_history_revision"),
        CheckConstraint(
            "connection_status IN ('untested', 'ok', 'failed')",
            name="ck_cloud_target_history_connection",
        ),
        Index("uq_cloud_target_history_revision", "provider", "revision", unique=True),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    config_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    account_label: Mapped[str | None] = mapped_column(String(255))
    last_backup_id: Mapped[str | None] = mapped_column(String(64))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connection_status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CloudConnectorState(Base):
    __tablename__ = "cloud_connector_state"
    __table_args__ = (
        CheckConstraint(
            "(maintenance_operation_id IS NULL) = (maintenance_lease_expires_at IS NULL)",
            name="ck_cloud_connector_maintenance_lease",
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    registered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    maintenance_operation_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    maintenance_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
