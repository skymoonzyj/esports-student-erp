from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class HousingBuilding(TimestampMixin, Base):
    __tablename__ = "housing_buildings"
    __table_args__ = (
        Index(
            "ix_housing_buildings_display_order",
            "display_order",
            "code",
            "id",
        ),
        CheckConstraint("btrim(code) <> ''", name="ck_housing_buildings_code"),
        CheckConstraint("btrim(name) <> ''", name="ck_housing_buildings_name"),
        CheckConstraint(
            "display_order >= 0",
            name="ck_housing_buildings_display_order",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class HousingFloor(TimestampMixin, Base):
    __tablename__ = "housing_floors"
    __table_args__ = (
        Index(
            "ix_housing_floors_display_order",
            "building_id",
            "display_order",
            "number",
            "id",
        ),
        UniqueConstraint("id", "building_id", name="uq_housing_floors_id_building"),
        UniqueConstraint(
            "building_id",
            "number",
            name="uq_housing_floors_building_number",
        ),
        UniqueConstraint(
            "building_id",
            "name",
            name="uq_housing_floors_building_name",
        ),
        CheckConstraint(
            "number >= -20 AND number <= 200",
            name="ck_housing_floors_number",
        ),
        CheckConstraint("btrim(name) <> ''", name="ck_housing_floors_name"),
        CheckConstraint(
            "display_order >= 0",
            name="ck_housing_floors_display_order",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    building_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_buildings.id", ondelete="RESTRICT"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class HousingRoom(TimestampMixin, Base):
    __tablename__ = "housing_rooms"
    __table_args__ = (
        Index(
            "ix_housing_rooms_floor_number",
            "floor_id",
            "room_number",
            "id",
        ),
        Index(
            "ix_housing_rooms_verification_status",
            "verification_status",
            "id",
        ),
        Index(
            "ix_housing_rooms_service_status",
            "service_status",
            "id",
        ),
        Index("ix_housing_rooms_type", "room_type", "id"),
        ForeignKeyConstraint(
            ["floor_id", "building_id"],
            ["housing_floors.id", "housing_floors.building_id"],
            name="fk_housing_rooms_floor_building",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "building_id",
            "normalized_key",
            name="uq_housing_rooms_building_key",
        ),
        CheckConstraint("btrim(room_number) <> ''", name="ck_housing_rooms_number"),
        CheckConstraint("btrim(normalized_key) <> ''", name="ck_housing_rooms_key"),
        CheckConstraint(
            "room_type IN ('single', 'double', 'triple', 'custom', 'functional')",
            name="ck_housing_rooms_type",
        ),
        CheckConstraint(
            "(room_type = 'single' AND capacity = 1) OR "
            "(room_type = 'double' AND capacity = 2) OR "
            "(room_type = 'triple' AND capacity = 3) OR "
            "(room_type = 'custom' AND capacity BETWEEN 1 AND 100) OR "
            "(room_type = 'functional' AND capacity = 0)",
            name="ck_housing_rooms_type_capacity",
        ),
        CheckConstraint(
            "area_square_meters IS NULL OR area_square_meters > 0",
            name="ck_housing_rooms_area",
        ),
        CheckConstraint(
            "verification_status IN ('pending_verification', 'verified')",
            name="ck_housing_rooms_verification",
        ),
        CheckConstraint(
            "service_status IN ('available', 'problem', 'maintenance', 'disabled')",
            name="ck_housing_rooms_service_status",
        ),
        CheckConstraint("version >= 1", name="ck_housing_rooms_version"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    building_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=False
    )
    floor_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    room_number: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_key: Mapped[str] = mapped_column(String(32), nullable=False)
    room_type: Mapped[str] = mapped_column(String(24), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    area_square_meters: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_verification"
    )
    service_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="available"
    )
    notes: Mapped[str | None] = mapped_column(String(500))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class HousingImportBatch(TimestampMixin, Base):
    __tablename__ = "housing_import_batches"
    __table_args__ = (
        CheckConstraint(
            "btrim(source_name) <> ''",
            name="ck_housing_import_source_name",
        ),
        CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_housing_import_sha",
        ),
        CheckConstraint("row_count >= 0", name="ck_housing_import_row_count"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    correction_summary: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    imported_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class HousingRoomIssue(TimestampMixin, Base):
    __tablename__ = "housing_room_issues"
    __table_args__ = (
        CheckConstraint(
            "btrim(description) <> ''",
            name="ck_housing_room_issues_description",
        ),
        CheckConstraint(
            "previous_service_status IN "
            "('available', 'problem', 'maintenance', 'disabled')",
            name="ck_housing_room_issues_previous_service_status",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_housing_room_issues_status",
        ),
        CheckConstraint(
            "(status = 'open' AND resolved_at IS NULL "
            "AND resolved_by_user_id IS NULL AND resolution_note IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND resolved_by_user_id IS NOT NULL "
            "AND resolution_note IS NOT NULL AND btrim(resolution_note) <> '')",
            name="ck_housing_room_issues_resolution",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= reported_at",
            name="ck_housing_room_issues_resolved_at",
        ),
        Index(
            "uq_housing_room_issues_open_room",
            "room_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_rooms.id", ondelete="RESTRICT"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    previous_service_status: Mapped[str] = mapped_column(String(24), nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reported_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    resolution_note: Mapped[str | None] = mapped_column(Text)


class HousingStay(TimestampMixin, Base):
    __tablename__ = "housing_stays"
    __table_args__ = (
        CheckConstraint(
            "stay_purpose IN ('permanent', 'camp', 'temporary')",
            name="ck_housing_stays_purpose",
        ),
        CheckConstraint(
            "duration_mode IN ('long_term', 'fixed_term')",
            name="ck_housing_stays_duration_mode",
        ),
        CheckConstraint(
            "(duration_mode = 'fixed_term' AND planned_check_out_at IS NOT NULL "
            "AND planned_check_out_at > planned_check_in_at) OR "
            "(duration_mode = 'long_term' AND planned_check_out_at IS NULL)",
            name="ck_housing_stays_planned_range",
        ),
        CheckConstraint(
            "stay_purpose <> 'camp' OR cohort_id IS NOT NULL",
            name="ck_housing_stays_camp_cohort",
        ),
        CheckConstraint(
            "stay_purpose <> 'temporary' OR "
            "(duration_mode = 'fixed_term' AND visit_reason IS NOT NULL "
            "AND btrim(visit_reason) <> '')",
            name="ck_housing_stays_temporary_details",
        ),
        CheckConstraint(
            "status IN ('planned', 'checked_in', 'checkout_pending', "
            "'checked_out', 'cancelled')",
            name="ck_housing_stays_status",
        ),
        CheckConstraint(
            "(status = 'planned' AND actual_check_in_at IS NULL "
            "AND actual_check_out_at IS NULL AND checked_in_by_user_id IS NULL "
            "AND checked_out_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status IN ('checked_in', 'checkout_pending') "
            "AND actual_check_in_at IS NOT NULL AND actual_check_out_at IS NULL "
            "AND checked_in_by_user_id IS NOT NULL "
            "AND checked_out_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'checked_out' AND actual_check_in_at IS NOT NULL "
            "AND actual_check_out_at IS NOT NULL "
            "AND checked_in_by_user_id IS NOT NULL "
            "AND checked_out_by_user_id IS NOT NULL "
            "AND cancelled_by_user_id IS NULL AND cancellation_reason IS NULL) OR "
            "(status = 'cancelled' AND actual_check_in_at IS NULL "
            "AND actual_check_out_at IS NULL AND checked_in_by_user_id IS NULL "
            "AND checked_out_by_user_id IS NULL "
            "AND cancelled_by_user_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL "
            "AND btrim(cancellation_reason) <> '')",
            name="ck_housing_stays_status_fields",
        ),
        CheckConstraint(
            "btrim(room_name_snapshot) <> '' "
            "AND btrim(floor_name_snapshot) <> '' "
            "AND btrim(building_name_snapshot) <> ''",
            name="ck_housing_stays_snapshot_names",
        ),
        CheckConstraint(
            "(actual_check_out_at IS NULL OR "
            "(actual_check_in_at IS NOT NULL "
            "AND actual_check_out_at > actual_check_in_at)) AND "
            "(actual_check_in_at IS NULL OR planned_check_out_at IS NULL OR "
            "actual_check_in_at < planned_check_out_at)",
            name="ck_housing_stays_actual_range",
        ),
        CheckConstraint(
            "(status IN ('planned', 'checked_in', 'cancelled') "
            "AND checkout_requested_actual_at IS NULL "
            "AND checkout_requested_at IS NULL "
            "AND checkout_requested_by_user_id IS NULL) OR "
            "(status IN ('checkout_pending', 'checked_out') AND "
            "((checkout_requested_actual_at IS NULL "
            "AND checkout_requested_at IS NULL "
            "AND checkout_requested_by_user_id IS NULL) OR "
            "(checkout_requested_actual_at IS NOT NULL "
            "AND checkout_requested_at IS NOT NULL "
            "AND checkout_requested_by_user_id IS NOT NULL)))",
            name="ck_housing_stays_checkout_request_fields",
        ),
        CheckConstraint("version >= 1", name="ck_housing_stays_version"),
        ExcludeConstraint(
            ("person_id", "="),
            (
                text(
                    "tstzrange(COALESCE(actual_check_in_at, planned_check_in_at), "
                    "COALESCE(planned_check_out_at, "
                    "'infinity'::timestamptz), '[)')"
                ),
                "&&",
            ),
            name="excl_housing_stays_person_active_overlap",
            using="gist",
            where=text("status IN ('planned', 'checked_in', 'checkout_pending')"),
        ),
        Index(
            "ix_housing_stays_room_status_time",
            "room_id",
            "status",
            "planned_check_in_at",
            "planned_check_out_at",
            "id",
        ),
        Index(
            "ix_housing_stays_person_status_time",
            "person_id",
            "status",
            "planned_check_in_at",
            "planned_check_out_at",
            "id",
        ),
        Index(
            "ix_housing_stays_cohort_status",
            "cohort_id",
            "status",
            "planned_check_in_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    room_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_rooms.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stay_purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    cohort_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="RESTRICT"),
    )
    visit_reason: Mapped[str | None] = mapped_column(String(500))
    planned_check_in_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    planned_check_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    actual_check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_check_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="planned")
    room_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    floor_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    building_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
    checkout_requested_actual_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    checkout_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    checkout_requested_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    checked_in_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    checked_out_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    cancelled_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class HousingTransfer(TimestampMixin, Base):
    __tablename__ = "housing_transfers"
    __table_args__ = (
        CheckConstraint(
            "from_stay_id <> to_stay_id",
            name="ck_housing_transfers_distinct_stays",
        ),
        CheckConstraint(
            "btrim(reason) <> ''",
            name="ck_housing_transfers_reason",
        ),
        UniqueConstraint(
            "from_stay_id",
            name="uq_housing_transfers_from_stay",
        ),
        UniqueConstraint(
            "to_stay_id",
            name="uq_housing_transfers_to_stay",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    from_stay_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_stays.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_stay_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("housing_stays.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transferred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    transferred_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
