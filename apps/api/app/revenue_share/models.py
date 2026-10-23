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

_BENEFICIARY_CHECK = (
    "(beneficiary_kind = 'person' AND beneficiary_person_id IS NOT NULL "
    "AND beneficiary_organization_id IS NULL) OR "
    "(beneficiary_kind = 'organization' AND beneficiary_person_id IS NULL "
    "AND beneficiary_organization_id IS NOT NULL)"
)


class RevenueShareOrganization(TimestampMixin, Base):
    __tablename__ = "revenue_share_organizations"
    __table_args__ = (
        UniqueConstraint("name", name="uq_revenue_share_organizations_name"),
        CheckConstraint("btrim(name) <> ''", name="ck_revenue_share_organizations_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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


class RevenueShareOrganizationManagerAssignment(Base):
    __tablename__ = "revenue_share_organization_manager_assignments"
    __table_args__ = (
        CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_user_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)",
            name="ck_referral_org_manager_revocation_pair",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="ck_referral_org_manager_revocation_time",
        ),
        Index(
            "uq_referral_org_manager_active", "organization_id", "account_id",
            unique=True, postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "ix_referral_org_manager_account_active", "account_id", "organization_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"), nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    granted_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class ReferralCode(TimestampMixin, Base):
    __tablename__ = "referral_codes"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('person', 'organization')",
            name="ck_referral_codes_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'person' AND referrer_person_id IS NOT NULL "
            "AND referrer_organization_id IS NULL) OR "
            "(source_kind = 'organization' AND referrer_person_id IS NULL "
            "AND referrer_organization_id IS NOT NULL)",
            name="ck_referral_codes_source",
        ),
        CheckConstraint("btrim(label) <> ''", name="ck_referral_codes_label"),
        UniqueConstraint("code", name="uq_referral_codes_code"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    referrer_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    referrer_organization_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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


class StudentReferralBinding(Base):
    __tablename__ = "student_referral_bindings"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ('person', 'organization')",
            name="ck_student_referral_bindings_source_kind",
        ),
        CheckConstraint(
            "(source_kind = 'person' AND referrer_person_id IS NOT NULL "
            "AND referrer_organization_id IS NULL) OR "
            "(source_kind = 'organization' AND referrer_person_id IS NULL "
            "AND referrer_organization_id IS NOT NULL)",
            name="ck_student_referral_bindings_source",
        ),
        UniqueConstraint("student_person_id", name="uq_student_referral_bindings_student"),
        UniqueConstraint(
            "registration_application_id",
            name="uq_student_referral_bindings_application",
        ),
        Index(
            "ix_student_referral_bindings_person_source", "referrer_person_id", "student_person_id",
            postgresql_where=text("source_kind = 'person'"),
        ),
        Index(
            "ix_student_referral_bindings_org_source", "referrer_organization_id", "student_person_id",
            postgresql_where=text("source_kind = 'organization'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    student_person_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=False,
    )
    referral_code_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("referral_codes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    registration_application_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("registration_applications.id", ondelete="RESTRICT"),
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    referrer_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    referrer_organization_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"),
    )
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )


class RevenueShareRule(TimestampMixin, Base):
    __tablename__ = "revenue_share_rules"
    __table_args__ = (
        CheckConstraint(
            "role_kind IN ('inviter', 'subject_coach', 'platform_developer', "
            "'assistant', 'additional')",
            name="ck_revenue_share_rules_role_kind",
        ),
        CheckConstraint(
            "calculation_type IN ('percentage', 'fixed')",
            name="ck_revenue_share_rules_calculation_type",
        ),
        CheckConstraint(
            "(calculation_type = 'percentage' AND percentage_basis_points "
            "BETWEEN 1 AND 10000 AND fixed_amount_cents IS NULL) OR "
            "(calculation_type = 'fixed' AND percentage_basis_points IS NULL "
            "AND fixed_amount_cents > 0)",
            name="ck_revenue_share_rules_value",
        ),
        CheckConstraint(
            "(role_kind IN ('inviter', 'subject_coach', 'assistant') "
            "AND recipient_person_id IS NULL AND recipient_organization_id IS NULL) OR "
            "(role_kind IN ('platform_developer', 'additional') AND "
            "((recipient_person_id IS NOT NULL AND recipient_organization_id IS NULL) OR "
            "(recipient_person_id IS NULL AND recipient_organization_id IS NOT NULL)))",
            name="ck_revenue_share_rules_recipient",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_revenue_share_rules_effective_range",
        ),
        CheckConstraint(
            "label IS NULL OR btrim(label) <> ''",
            name="ck_revenue_share_rules_label",
        ),
        Index(
            "ix_revenue_share_rules_resolution",
            "project_id",
            "role_kind",
            "is_active",
            "effective_from",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
    )
    role_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    recipient_organization_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"),
    )
    label: Mapped[str | None] = mapped_column(String(120))
    calculation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    percentage_basis_points: Mapped[int | None] = mapped_column(Integer)
    fixed_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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


class RevenueSharePaymentStatus(TimestampMixin, Base):
    __tablename__ = "revenue_share_payment_statuses"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'allocated')",
            name="ck_revenue_share_payment_statuses_state",
        ),
        CheckConstraint(
            "(state = 'allocated' AND reason_code IS NULL) OR "
            "(state = 'pending' AND reason_code IS NOT NULL)",
            name="ck_revenue_share_payment_statuses_reason",
        ),
        Index("ix_revenue_share_payment_statuses_state", "state", "updated_at"),
    )

    payment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    project_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("game_projects.id", ondelete="RESTRICT"),
    )
    enrollment_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("enrollments.id", ondelete="RESTRICT"),
    )
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RevenueShareReconcileQueue(Base):
    __tablename__ = "revenue_share_reconcile_queue"
    __table_args__ = (
        Index("ix_revenue_share_reconcile_queue_paid_at", "paid_at", "payment_id"),
    )

    payment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )


class RevenueShareLedgerEntry(Base):
    __tablename__ = "revenue_share_ledger_entries"
    __table_args__ = (
        CheckConstraint(
            "entry_kind IN ('earning', 'refund_adjustment', 'manual_adjustment')",
            name="ck_revenue_share_ledger_entries_kind",
        ),
        CheckConstraint(
            "role_kind IN ('inviter', 'subject_coach', 'platform_developer', "
            "'assistant', 'additional')",
            name="ck_revenue_share_ledger_entries_role_kind",
        ),
        CheckConstraint(_BENEFICIARY_CHECK, name="ck_revenue_share_ledger_entries_beneficiary"),
        CheckConstraint("amount_cents <> 0", name="ck_revenue_share_ledger_entries_amount"),
        CheckConstraint("base_amount_cents > 0", name="ck_revenue_share_ledger_entries_base"),
        CheckConstraint(
            "(entry_kind = 'earning' AND amount_cents > 0 AND refund_id IS NULL "
            "AND parent_entry_id IS NULL) OR "
            "(entry_kind = 'refund_adjustment' AND amount_cents < 0 "
            "AND refund_id IS NOT NULL AND parent_entry_id IS NOT NULL) OR "
            "entry_kind = 'manual_adjustment'",
            name="ck_revenue_share_ledger_entries_event_fields",
        ),
        UniqueConstraint("event_key", name="uq_revenue_share_ledger_entries_event_key"),
        Index(
            "ix_revenue_share_ledger_entries_person",
            "beneficiary_person_id",
            "occurred_at",
            "id",
        ),
        Index(
            "ix_revenue_share_ledger_entries_organization",
            "beneficiary_organization_id",
            "occurred_at",
            "id",
        ),
        Index("ix_revenue_share_ledger_entries_payment", "payment_id", "id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(String(180), nullable=False)
    payment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    refund_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("payment_refunds.id", ondelete="RESTRICT"),
    )
    parent_entry_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_ledger_entries.id", ondelete="RESTRICT"),
    )
    rule_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_rules.id", ondelete="RESTRICT"),
    )
    entry_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    role_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    beneficiary_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    beneficiary_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    beneficiary_organization_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"),
    )
    calculation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    percentage_basis_points_snapshot: Mapped[int | None] = mapped_column(Integer)
    fixed_amount_cents_snapshot: Mapped[int | None] = mapped_column(BigInteger)
    base_amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RevenueShareSettlement(TimestampMixin, Base):
    __tablename__ = "revenue_share_settlements"
    __table_args__ = (
        CheckConstraint(_BENEFICIARY_CHECK, name="ck_revenue_share_settlements_beneficiary"),
        CheckConstraint("amount_cents > 0", name="ck_revenue_share_settlements_amount"),
        CheckConstraint("btrim(reference) <> ''", name="ck_revenue_share_settlements_reference"),
        UniqueConstraint(
            "created_by_user_id",
            "idempotency_key",
            name="uq_revenue_share_settlements_operation",
        ),
        UniqueConstraint("reference_key", name="uq_revenue_share_settlements_reference_key"),
        Index(
            "ix_revenue_share_settlements_person",
            "beneficiary_person_id",
            "settled_on",
            "id",
        ),
        Index(
            "ix_revenue_share_settlements_organization",
            "beneficiary_organization_id",
            "settled_on",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    beneficiary_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    beneficiary_person_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("people.id", ondelete="RESTRICT"),
    )
    beneficiary_organization_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("revenue_share_organizations.id", ondelete="RESTRICT"),
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    # Nullable for historical rows; all new writes populate these fields.
    idempotency_key: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    reference_key: Mapped[str | None] = mapped_column(String(64))
    created_by_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
