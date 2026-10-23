from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin
from app.operations import cloud_auth_models  # noqa: F401


class CloudBackupTarget(TimestampMixin, Base):
    __tablename__ = "cloud_backup_targets"
    __table_args__ = (
        CheckConstraint("provider IN ('baidu', '115', 'quark')", name="ck_cloud_target_provider"),
        CheckConstraint(
            "connection_status IN ('untested', 'ok', 'failed')", name="ck_cloud_target_connection"
        ),
        CheckConstraint("revision >= 1", name="ck_cloud_target_revision"),
        CheckConstraint(
            "authorization_status IN ('manual', 'valid', 'reauthorization_required')",
            name="ck_cloud_target_authorization",
        ),
    )
    provider: Mapped[str] = mapped_column(String(16), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    remote_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    username_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    password_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    connection_status: Mapped[str] = mapped_column(String(16), default="untested", nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_backup_id: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    authorization_status: Mapped[str] = mapped_column(
        String(32), default="manual", server_default="manual", nullable=False
    )
    managed_candidate_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cloud_auth_candidates.id", ondelete="RESTRICT")
    )
    switch_operation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("cloud_auth_operations.id", ondelete="RESTRICT")
    )


class CloudBackupJob(Base):
    __tablename__ = "cloud_backup_jobs"
    __table_args__ = (
        CheckConstraint("kind IN ('test', 'copy')", name="ck_cloud_job_kind"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_cloud_job_status"
        ),
        Index(
            "uq_cloud_job_live_target",
            "provider",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "uq_cloud_job_auto_backup", "provider", "revision", "automatic_backup_id", unique=True
        ),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(
        String(16), ForeignKey("cloud_backup_targets.provider", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    backup_id: Mapped[str | None] = mapped_column(String(64))
    automatic_backup_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
