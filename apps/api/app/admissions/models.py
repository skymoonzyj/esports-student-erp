from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin


class ServiceAccount(TimestampMixin, Base):
    __tablename__ = "service_accounts"

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_system: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class ServiceAccountCredential(TimestampMixin, Base):
    __tablename__ = "service_account_credentials"
    __table_args__ = (
        CheckConstraint(
            "(disabled_at IS NULL AND disabled_by_user_id IS NULL) OR "
            "(disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL)",
            name="ck_service_account_credentials_disabled_fields",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    service_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("service_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class ServiceAccountRequestLimit(Base):
    __tablename__ = "service_account_request_limits"
    __table_args__ = (
        CheckConstraint("request_count >= 0", name="ck_service_account_request_limits_count"),
        Index("ix_service_account_request_limits_updated_at", "updated_at"),
    )

    credential_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("service_account_credentials.id", ondelete="CASCADE"),
        primary_key=True,
    )
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ServiceAccountNonce(Base):
    __tablename__ = "service_account_nonces"
    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            "nonce_digest",
            name="uq_service_account_nonces_credential_digest",
        ),
        CheckConstraint("expires_at > requested_at", name="ck_service_account_nonces_expiry"),
        Index("ix_service_account_nonces_expires_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    credential_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("service_account_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )
    nonce_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(
            "amount_cents > 0 AND currency = 'CNY'",
            name="ck_payments_positive_cny",
        ),
        CheckConstraint(
            "source_kind IN ('api', 'manual')",
            name="ck_payments_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'api' AND source_system IS NOT NULL "
            "AND external_order_id IS NOT NULL AND created_by_user_id IS NULL "
            "AND manual_note IS NULL) OR "
            "(source_kind = 'manual' AND source_system IS NULL "
            "AND external_order_id IS NULL AND created_by_user_id IS NOT NULL "
            "AND manual_note IS NOT NULL AND btrim(manual_note) <> '')",
            name="ck_payments_source_fields",
        ),
        Index("ix_payments_payer_account", "payer_account_id"),
        Index("ix_payments_payer_person", "payer_person_id"),
        Index("ix_payments_paid_at", "paid_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(80))
    external_order_id: Mapped[str | None] = mapped_column(String(120))
    payer_account_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    payer_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="CNY",
        server_default="CNY",
    )
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    manual_note: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class PaymentImportEvent(Base):
    __tablename__ = "payment_import_events"
    __table_args__ = (
        CheckConstraint(
            "processing_status IN ('processed', 'pending_match', 'conflict')",
            name="ck_payment_import_events_status",
        ),
        Index(
            "uq_payment_import_source_order",
            "source_system",
            "external_order_id",
            unique=True,
        ),
        Index("uq_payment_import_idempotency", "idempotency_key", unique=True),
        ForeignKeyConstraint(
            ["conflict_id"],
            ["payment_conflicts.id"],
            name="fk_payment_import_events_conflict",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    service_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("service_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_system: Mapped[str] = mapped_column(String(80), nullable=False)
    external_order_id: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    payer_phone_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False)
    payment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
    )
    conflict_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))


class PaymentConflict(TimestampMixin, Base):
    __tablename__ = "payment_conflicts"
    __table_args__ = (
        CheckConstraint(
            "conflict_type IN ('external_order_payload_mismatch', "
            "'idempotency_payload_mismatch', 'ambiguous_payer')",
            name="ck_payment_conflicts_type",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_payment_conflicts_status",
        ),
        CheckConstraint(
            "(status = 'open' AND resolution_note IS NULL "
            "AND resolved_by_user_id IS NULL AND resolved_at IS NULL) OR "
            "(status IN ('resolved', 'dismissed') AND resolution_note IS NOT NULL "
            "AND btrim(resolution_note) <> '' AND resolved_by_user_id IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_payment_conflicts_resolution",
        ),
        ForeignKeyConstraint(
            ["original_import_event_id"],
            ["payment_import_events.id"],
            name="fk_payment_conflicts_original_event",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    original_import_event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    conflict_type: Mapped[str] = mapped_column(String(48), nullable=False)
    attempted_payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attempted_raw_payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    resolved_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdmissionCase(TimestampMixin, Base):
    __tablename__ = "admission_cases"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_match', 'ready', 'enrolled', 'closed')",
            name="ck_admission_cases_status",
        ),
        CheckConstraint(
            "agreed_tuition_cents IS NULL OR agreed_tuition_cents >= 0",
            name="ck_admission_cases_tuition",
        ),
        CheckConstraint(
            "tuition_override_reason IS NULL OR "
            "(agreed_tuition_cents IS NOT NULL AND btrim(tuition_override_reason) <> '')",
            name="ck_admission_cases_tuition_reason",
        ),
        CheckConstraint(
            "(status = 'pending_match' AND current_enrollment_id IS NULL) OR "
            "(status = 'ready' AND student_person_id IS NOT NULL "
            "AND current_enrollment_id IS NULL) OR "
            "(status = 'enrolled' AND student_person_id IS NOT NULL "
            "AND current_enrollment_id IS NOT NULL) OR "
            "(status = 'closed' AND current_enrollment_id IS NULL "
            "AND closed_reason IS NOT NULL AND btrim(closed_reason) <> '')",
            name="ck_admission_cases_state_fields",
        ),
        ForeignKeyConstraint(
            ["current_enrollment_id", "id"],
            ["enrollments.id", "enrollments.admission_case_id"],
            name="fk_admission_cases_current_enrollment",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index("ix_admission_cases_student_status", "student_person_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    student_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending_match",
        server_default="pending_match",
    )
    agreed_tuition_cents: Mapped[int | None] = mapped_column(BigInteger)
    tuition_override_reason: Mapped[str | None] = mapped_column(String(500))
    current_enrollment_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    closed_reason: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class AdmissionCasePayment(Base):
    __tablename__ = "admission_case_payments"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_admission_case_payments_payment"),
    )

    admission_case_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("admission_cases.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    payment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    attached_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class Enrollment(TimestampMixin, Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'cancelled', 'completed')",
            name="ck_enrollments_status",
        ),
        CheckConstraint(
            "agreed_tuition_cents >= 0",
            name="ck_enrollments_tuition",
        ),
        CheckConstraint(
            "(status = 'active' AND cancelled_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL "
            "AND cancelled_at IS NULL)",
            name="ck_enrollments_status_times",
        ),
        ForeignKeyConstraint(
            ["class_id", "cohort_id"],
            ["teaching_classes.id", "teaching_classes.cohort_id"],
            name="fk_enrollments_class_cohort",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "admission_case_id",
            name="uq_enrollments_id_admission_case",
        ),
        UniqueConstraint(
            "id",
            "student_person_id",
            name="uq_enrollments_id_student_person",
        ),
        Index(
            "uq_enrollments_active_student_cohort",
            "student_person_id",
            "cohort_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_enrollments_class_status", "class_id", "status"),
        Index(
            "ix_enrollments_student_status_cohort",
            "student_person_id",
            "status",
            "cohort_id",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    admission_case_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("admission_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cohort_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("cohorts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    class_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    agreed_tuition_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
    )
    capacity_override_reason: Mapped[str | None] = mapped_column(String(500))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class EnrollmentEvent(Base):
    __tablename__ = "enrollment_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('confirmed', 'transferred', 'cancelled', "
            "'withdrawal_refunded', 'completed', 'reopened_for_allocation', "
            "'capacity_overridden')",
            name="ck_enrollment_events_type",
        ),
        CheckConstraint(
            "enrollment_count_snapshot IS NULL OR enrollment_count_snapshot >= 0",
            name="ck_enrollment_events_count_snapshot",
        ),
        Index("ix_enrollment_events_enrollment_time", "enrollment_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    enrollment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    old_class_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teaching_classes.id", ondelete="RESTRICT"),
    )
    new_class_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("teaching_classes.id", ondelete="RESTRICT"),
    )
    old_status: Mapped[str | None] = mapped_column(String(16))
    new_status: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(500))
    enrollment_count_snapshot: Mapped[int | None] = mapped_column(Integer)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class EnrollmentWithdrawal(TimestampMixin, Base):
    __tablename__ = "enrollment_withdrawals"
    __table_args__ = (
        CheckConstraint(
            "refund_policy IN ('prorated_remaining_days', 'full_remaining_paid')",
            name="ck_enrollment_withdrawals_policy",
        ),
        CheckConstraint(
            "total_days_snapshot > 0 AND remaining_days_snapshot >= 0 "
            "AND remaining_days_snapshot <= total_days_snapshot",
            name="ck_enrollment_withdrawals_days",
        ),
        CheckConstraint(
            "agreed_tuition_cents_snapshot >= 0 "
            "AND available_paid_cents_snapshot > 0 "
            "AND calculated_refund_cents > 0 "
            "AND calculated_refund_cents <= available_paid_cents_snapshot",
            name="ck_enrollment_withdrawals_amounts",
        ),
        CheckConstraint("btrim(reason) <> ''", name="ck_enrollment_withdrawals_reason"),
        ForeignKeyConstraint(
            ["enrollment_id", "admission_case_id"],
            ["enrollments.id", "enrollments.admission_case_id"],
            name="fk_enrollment_withdrawals_enrollment_case",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("enrollment_id", name="uq_enrollment_withdrawals_enrollment"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    enrollment_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    admission_case_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    withdrawal_date: Mapped[date] = mapped_column(Date, nullable=False)
    refund_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    total_days_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_days_snapshot: Mapped[int] = mapped_column(Integer, nullable=False)
    agreed_tuition_cents_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    available_paid_cents_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    calculated_refund_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    confirmed_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRefund(TimestampMixin, Base):
    __tablename__ = "payment_refunds"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_payment_refunds_positive"),
        CheckConstraint(
            "refund_type IN ('manual', 'withdrawal_prorated', 'withdrawal_full')",
            name="ck_payment_refunds_type",
        ),
        CheckConstraint("btrim(reason) <> ''", name="ck_payment_refunds_reason"),
        CheckConstraint(
            "(refund_type = 'manual' AND withdrawal_id IS NULL) OR "
            "(refund_type IN ('withdrawal_prorated', 'withdrawal_full') "
            "AND withdrawal_id IS NOT NULL)",
            name="ck_payment_refunds_withdrawal",
        ),
        Index("ix_payment_refunds_payment_time", "payment_id", "refunded_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    payment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    withdrawal_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("enrollment_withdrawals.id", ondelete="RESTRICT"),
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    refunded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    refund_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
