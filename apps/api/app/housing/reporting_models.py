from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base


class HousingReportBatch(Base):
    """An immutable, encrypted real-name housing report projection."""

    __tablename__ = "housing_report_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generated', 'marked_submitted', 'voided', 'failed')",
            name="ck_housing_report_batches_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_housing_report_batches_version",
        ),
        CheckConstraint(
            "range_start < range_end",
            name="ck_housing_report_batches_range",
        ),
        CheckConstraint(
            "btrim(purpose) <> ''",
            name="ck_housing_report_batches_purpose",
        ),
        CheckConstraint(
            "projection_schema_version >= 1",
            name="ck_housing_report_batches_projection_schema_version",
        ),
        CheckConstraint(
            "resident_count >= 0 AND stay_row_count >= 0 AND guardian_row_count >= 0",
            name="ck_housing_report_batches_counts",
        ),
        CheckConstraint(
            "jsonb_typeof(cohort_filter) = 'array'",
            name="ck_housing_report_batches_cohort_filter",
        ),
        CheckConstraint(
            "jsonb_typeof(person_type_filter) = 'array'",
            name="ck_housing_report_batches_person_type_filter",
        ),
        CheckConstraint(
            "snapshot_digest IS NULL OR snapshot_digest ~ '^[0-9a-f]{64}$'",
            name="ck_housing_report_batches_snapshot_digest",
        ),
        CheckConstraint(
            "snapshot_key_version IS NULL OR snapshot_key_version >= 1",
            name="ck_housing_report_batches_snapshot_key_version",
        ),
        CheckConstraint(
            "((status IN ('generated', 'marked_submitted', 'voided') "
            "AND snapshot_ciphertext IS NOT NULL "
            "AND snapshot_key_version IS NOT NULL "
            "AND snapshot_digest IS NOT NULL) OR "
            "(status = 'failed' AND snapshot_ciphertext IS NULL "
            "AND snapshot_key_version IS NULL "
            "AND snapshot_digest IS NULL))",
            name="ck_housing_report_batches_snapshot_state",
        ),
        CheckConstraint(
            "((submitted_at IS NULL AND submitted_by_user_id IS NULL) OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL))",
            name="ck_housing_report_batches_submitted_fields",
        ),
        CheckConstraint(
            "status <> 'marked_submitted' OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
            name="ck_housing_report_batches_submitted_status",
        ),
        CheckConstraint(
            "status NOT IN ('generated', 'failed') OR "
            "(submitted_at IS NULL AND submitted_by_user_id IS NULL "
            "AND submission_note IS NULL)",
            name="ck_housing_report_batches_unsubmitted_status",
        ),
        CheckConstraint(
            "submission_note IS NULL OR "
            "(submitted_at IS NOT NULL AND submitted_by_user_id IS NOT NULL)",
            name="ck_housing_report_batches_submission_note",
        ),
        CheckConstraint(
            "((status = 'voided' AND voided_at IS NOT NULL "
            "AND voided_by_user_id IS NOT NULL AND void_reason IS NOT NULL "
            "AND btrim(void_reason) <> '') OR "
            "(status <> 'voided' AND voided_at IS NULL "
            "AND voided_by_user_id IS NULL AND void_reason IS NULL))",
            name="ck_housing_report_batches_voided_status",
        ),
        CheckConstraint(
            "((status = 'failed' AND failure_code IS NOT NULL "
            "AND failure_summary IS NOT NULL "
            "AND housing_report_failure_summary_is_safe("
            "failure_code, failure_summary)) OR "
            "(status <> 'failed' AND failure_code IS NULL "
            "AND failure_summary IS NULL))",
            name="ck_housing_report_batches_failure_state",
        ),
        CheckConstraint(
            "status <> 'failed' OR corrects_batch_id IS NULL",
            name="ck_housing_report_batches_failed_not_correction",
        ),
        CheckConstraint(
            "corrects_batch_id IS NULL OR corrects_batch_id <> id",
            name="ck_housing_report_batches_corrects_not_self",
        ),
        Index(
            "ix_housing_report_batches_status_created_at",
            "status",
            "created_at",
            "id",
        ),
        Index(
            "ix_housing_report_batches_range",
            "range_start",
            "range_end",
            "id",
        ),
        Index(
            "uq_housing_report_batches_corrects_batch_id",
            "corrects_batch_id",
            unique=True,
            postgresql_where=text("corrects_batch_id IS NOT NULL"),
        ),
        UniqueConstraint("batch_number", name="uq_housing_report_batches_batch_number"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    batch_number: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        server_default=text(
            "'HR-' || to_char(CURRENT_TIMESTAMP AT TIME ZONE "
            "'Asia/Shanghai', 'YYYYMMDD') || '-' || "
            "lpad(nextval('housing_report_batch_number_seq')::text, 6, '0')"
        ),
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="generated",
        server_default="generated",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cohort_filter: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    person_type_filter: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    projection_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    snapshot_ciphertext: Mapped[str | None] = mapped_column(Text)
    snapshot_key_version: Mapped[int | None] = mapped_column(Integer)
    snapshot_digest: Mapped[str | None] = mapped_column(String(64))
    resident_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    stay_row_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    guardian_row_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    submission_note: Mapped[str | None] = mapped_column(Text)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    void_reason: Mapped[str | None] = mapped_column(Text)
    corrects_batch_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_report_batches.id", ondelete="RESTRICT"),
    )
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_summary: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True)
    )


class HousingReportBatchScope(Base):
    """The concrete building/floor scope captured by a report batch."""

    __tablename__ = "housing_report_batch_scopes"
    __table_args__ = (
        PrimaryKeyConstraint(
            "batch_id",
            "floor_id",
            name="pk_housing_report_batch_scopes",
        ),
        ForeignKeyConstraint(
            ["batch_id"],
            ["housing_report_batches.id"],
            name="fk_housing_report_batch_scopes_batch",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["floor_id", "building_id"],
            ["housing_floors.id", "housing_floors.building_id"],
            name="fk_housing_report_batch_scopes_floor_building",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_housing_report_batch_scopes_building_floor",
            "building_id",
            "floor_id",
            "batch_id",
        ),
    )

    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    building_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    floor_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
