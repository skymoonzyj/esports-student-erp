from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class EvaluationMediaBlob(TimestampMixin, Base):
    __tablename__ = "evaluation_media_blobs"
    __table_args__ = (
        CheckConstraint(
            "btrim(storage_key) <> '' AND btrim(preview_storage_key) <> '' "
            "AND left(storage_key, 1) <> '/' AND left(preview_storage_key, 1) <> '/' "
            "AND position('..' IN storage_key) = 0 "
            "AND position('..' IN preview_storage_key) = 0",
            name="ck_evaluation_media_blobs_storage_keys",
        ),
        CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evaluation_media_blobs_sha256_format",
        ),
        CheckConstraint(
            "(mime_type = 'image/jpeg' AND canonical_extension = 'jpg') OR "
            "(mime_type = 'image/png' AND canonical_extension = 'png') OR "
            "(mime_type = 'image/webp' AND canonical_extension = 'webp')",
            name="ck_evaluation_media_blobs_mime_extension",
        ),
        CheckConstraint(
            "size_bytes BETWEEN 1 AND 20971520",
            name="ck_evaluation_media_blobs_size",
        ),
        CheckConstraint(
            "width_pixels > 0 AND height_pixels > 0 "
            "AND width_pixels::bigint * height_pixels::bigint <= 50000000",
            name="ck_evaluation_media_blobs_dimensions",
        ),
        CheckConstraint(
            "storage_status IN ('available', 'missing')",
            name="ck_evaluation_media_blobs_storage_status",
        ),
        CheckConstraint(
            "validation_status IN ('validated', 'invalid')",
            name="ck_evaluation_media_blobs_validation_status",
        ),
        CheckConstraint(
            "retention_status IN ('retained', 'archived')",
            name="ck_evaluation_media_blobs_retention_status",
        ),
        UniqueConstraint(
            "storage_key",
            name="uq_evaluation_media_blobs_storage_key",
        ),
        UniqueConstraint(
            "preview_storage_key",
            name="uq_evaluation_media_blobs_preview_storage_key",
        ),
        UniqueConstraint(
            "sha256",
            name="uq_evaluation_media_blobs_sha256",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    preview_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_extension: Mapped[str] = mapped_column(String(8), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width_pixels: Mapped[int] = mapped_column(Integer, nullable=False)
    height_pixels: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_status: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_status: Mapped[str] = mapped_column(String(16), nullable=False)


class EvaluationMediaAsset(TimestampMixin, Base):
    __tablename__ = "evaluation_media_assets"
    __table_args__ = (
        CheckConstraint(
            "(student_person_id IS NULL AND enrollment_id IS NULL AND class_id IS NULL "
            "AND matched_at IS NULL AND matched_by_user_id IS NULL "
            "AND match_reason IS NULL) OR "
            "(student_person_id IS NOT NULL AND enrollment_id IS NOT NULL "
            "AND class_id IS NOT NULL AND matched_at IS NOT NULL "
            "AND matched_by_user_id IS NOT NULL AND match_reason IS NOT NULL "
            "AND btrim(match_reason) <> '')",
            name="ck_evaluation_media_assets_target_fields",
        ),
        CheckConstraint(
            "delegated_upload_reason IS NULL OR btrim(delegated_upload_reason) <> ''",
            name="ck_evaluation_media_assets_delegated_reason",
        ),
        ForeignKeyConstraint(
            ["enrollment_id", "student_person_id"],
            ["enrollments.id", "enrollments.student_person_id"],
            name="fk_evaluation_media_assets_enrollment_student",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_evaluation_media_assets_matched_target_blob",
            "student_person_id",
            "enrollment_id",
            "project_id",
            "blob_id",
            unique=True,
            postgresql_where=text("enrollment_id IS NOT NULL"),
        ),
        Index(
            "ix_evaluation_media_assets_pending",
            "project_id",
            "created_at",
            "id",
            postgresql_where=text("enrollment_id IS NULL"),
        ),
        Index(
            "ix_evaluation_media_assets_matched",
            "student_person_id",
            "enrollment_id",
            "project_id",
            postgresql_where=text("enrollment_id IS NOT NULL"),
        ),
        Index(
            "ix_evaluation_media_assets_upload_history",
            "uploaded_by_user_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    blob_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_media_blobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("student_profiles.person_id", ondelete="RESTRICT"),
    )
    enrollment_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    class_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teaching_classes.id", ondelete="RESTRICT"),
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    match_reason: Mapped[str | None] = mapped_column(String(300))
    delegated_upload_reason: Mapped[str | None] = mapped_column(String(300))
