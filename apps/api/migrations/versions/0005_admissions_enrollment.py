"""Add admissions enrollment schema and permissions.

Revision ID: 0005_admissions_enrollment
Revises: 0004_teaching_configuration
Create Date: 2026-07-28 22:27:01.578821

"""
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

# revision identifiers, used by Alembic.
revision: str = "0005_admissions_enrollment"
down_revision: str | Sequence[str] | None = "0004_teaching_configuration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ADMISSIONS_PERMISSIONS = {
    "admission_cases.read": (
        UUID("20000000-0000-4000-8000-000000000016"),
        "查看待分配",
        False,
    ),
    "admission_cases.manage": (
        UUID("20000000-0000-4000-8000-000000000017"),
        "管理待分配",
        False,
    ),
    "payments.read": (
        UUID("20000000-0000-4000-8000-000000000018"),
        "查看付款金额",
        True,
    ),
    "payments.manage": (
        UUID("20000000-0000-4000-8000-000000000019"),
        "管理付款",
        True,
    ),
    "refunds.manage": (
        UUID("20000000-0000-4000-8000-000000000020"),
        "管理退款",
        True,
    ),
    "enrollments.read": (
        UUID("20000000-0000-4000-8000-000000000021"),
        "查看正式报名",
        False,
    ),
    "enrollments.manage": (
        UUID("20000000-0000-4000-8000-000000000022"),
        "管理正式报名",
        False,
    ),
    "enrollments.capacity_override": (
        UUID("20000000-0000-4000-8000-000000000023"),
        "超额报名审批",
        True,
    ),
    "payment_imports.manage": (
        UUID("20000000-0000-4000-8000-000000000024"),
        "管理付款导入",
        True,
    ),
}

ADMISSIONS_GRANTS = [
    *[("admin", key, "global") for key in ADMISSIONS_PERMISSIONS],
    ("customer_service", "admission_cases.read", "global"),
    ("customer_service", "enrollments.read", "global"),
]

ROLE_IDS = {
    "admin": UUID("10000000-0000-4000-8000-000000000001"),
    "customer_service": UUID("10000000-0000-4000-8000-000000000003"),
}


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('admission_cases',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('student_person_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='pending_match', nullable=False),
    sa.Column('agreed_tuition_cents', sa.BigInteger(), nullable=True),
    sa.Column('tuition_override_reason', sa.String(length=500), nullable=True),
    sa.Column('current_enrollment_id', sa.UUID(), nullable=True),
    sa.Column('closed_reason', sa.String(length=500), nullable=True),
    sa.Column('created_by_user_id', sa.UUID(), nullable=True),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'pending_match' AND current_enrollment_id IS NULL) OR (status = 'ready' AND student_person_id IS NOT NULL AND current_enrollment_id IS NULL) OR (status = 'enrolled' AND student_person_id IS NOT NULL AND current_enrollment_id IS NOT NULL) OR (status = 'closed' AND current_enrollment_id IS NULL AND closed_reason IS NOT NULL AND btrim(closed_reason) <> '')", name='ck_admission_cases_state_fields'),
    sa.CheckConstraint("status IN ('pending_match', 'ready', 'enrolled', 'closed')", name='ck_admission_cases_status'),
    sa.CheckConstraint("tuition_override_reason IS NULL OR (agreed_tuition_cents IS NOT NULL AND btrim(tuition_override_reason) <> '')", name='ck_admission_cases_tuition_reason'),
    sa.CheckConstraint('agreed_tuition_cents IS NULL OR agreed_tuition_cents >= 0', name='ck_admission_cases_tuition'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_person_id'], ['people.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_admission_cases_student_status', 'admission_cases', ['student_person_id', 'status'], unique=False)
    op.create_table('payment_conflicts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('original_import_event_id', sa.UUID(), nullable=False),
    sa.Column('conflict_type', sa.String(length=48), nullable=False),
    sa.Column('attempted_payload_digest', sa.String(length=64), nullable=False),
    sa.Column('attempted_raw_payload_ciphertext', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('resolution_note', sa.String(length=500), nullable=True),
    sa.Column('resolved_by_user_id', sa.UUID(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'open' AND resolution_note IS NULL AND resolved_by_user_id IS NULL AND resolved_at IS NULL) OR (status IN ('resolved', 'dismissed') AND resolution_note IS NOT NULL AND btrim(resolution_note) <> '' AND resolved_by_user_id IS NOT NULL AND resolved_at IS NOT NULL)", name='ck_payment_conflicts_resolution'),
    sa.CheckConstraint("conflict_type IN ('external_order_payload_mismatch', 'idempotency_payload_mismatch', 'ambiguous_payer')", name='ck_payment_conflicts_type'),
    sa.CheckConstraint("status IN ('open', 'resolved', 'dismissed')", name='ck_payment_conflicts_status'),
    sa.ForeignKeyConstraint(['resolved_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('payments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('source_kind', sa.String(length=16), nullable=False),
    sa.Column('source_system', sa.String(length=80), nullable=True),
    sa.Column('external_order_id', sa.String(length=120), nullable=True),
    sa.Column('payer_account_id', sa.UUID(), nullable=True),
    sa.Column('payer_person_id', sa.UUID(), nullable=True),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='CNY', nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('manual_note', sa.String(length=500), nullable=True),
    sa.Column('created_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(source_kind = 'api' AND source_system IS NOT NULL AND external_order_id IS NOT NULL AND created_by_user_id IS NULL AND manual_note IS NULL) OR (source_kind = 'manual' AND source_system IS NULL AND external_order_id IS NULL AND created_by_user_id IS NOT NULL AND manual_note IS NOT NULL AND btrim(manual_note) <> '')", name='ck_payments_source_fields'),
    sa.CheckConstraint("amount_cents > 0 AND currency = 'CNY'", name='ck_payments_positive_cny'),
    sa.CheckConstraint("source_kind IN ('api', 'manual')", name='ck_payments_source_kind'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payer_account_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payer_person_id'], ['people.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_payments_paid_at', 'payments', ['paid_at', 'id'], unique=False)
    op.create_index('ix_payments_payer_account', 'payments', ['payer_account_id'], unique=False)
    op.create_index('ix_payments_payer_person', 'payments', ['payer_person_id'], unique=False)
    op.create_table('service_accounts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('source_system', sa.String(length=80), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_system')
    )
    op.create_table('admission_case_payments',
    sa.Column('admission_case_id', sa.UUID(), nullable=False),
    sa.Column('payment_id', sa.UUID(), nullable=False),
    sa.Column('attached_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['admission_case_id'], ['admission_cases.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['attached_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('admission_case_id', 'payment_id'),
    sa.UniqueConstraint('payment_id', name='uq_admission_case_payments_payment')
    )
    op.create_table('payment_import_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('service_account_id', sa.UUID(), nullable=False),
    sa.Column('source_system', sa.String(length=80), nullable=False),
    sa.Column('external_order_id', sa.String(length=120), nullable=False),
    sa.Column('idempotency_key', sa.String(length=120), nullable=False),
    sa.Column('payer_phone_digest', sa.String(length=64), nullable=False),
    sa.Column('payload_digest', sa.String(length=64), nullable=False),
    sa.Column('raw_payload_ciphertext', sa.Text(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('processing_status', sa.String(length=24), nullable=False),
    sa.Column('payment_id', sa.UUID(), nullable=True),
    sa.Column('conflict_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("processing_status IN ('processed', 'pending_match', 'conflict')", name='ck_payment_import_events_status'),
    sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['service_account_id'], ['service_accounts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_payment_import_idempotency', 'payment_import_events', ['idempotency_key'], unique=True)
    op.create_index('uq_payment_import_source_order', 'payment_import_events', ['source_system', 'external_order_id'], unique=True)
    op.create_table('service_account_credentials',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('service_account_id', sa.UUID(), nullable=False),
    sa.Column('public_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('secret_ciphertext', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disabled_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('(disabled_at IS NULL AND disabled_by_user_id IS NULL) OR (disabled_at IS NOT NULL AND disabled_by_user_id IS NOT NULL)', name='ck_service_account_credentials_disabled_fields'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['disabled_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['service_account_id'], ['service_accounts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_id')
    )
    op.create_index(op.f('ix_service_account_credentials_service_account_id'), 'service_account_credentials', ['service_account_id'], unique=False)
    op.create_table('service_account_nonces',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('credential_id', sa.UUID(), nullable=False),
    sa.Column('nonce_digest', sa.String(length=64), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('expires_at > requested_at', name='ck_service_account_nonces_expiry'),
    sa.ForeignKeyConstraint(['credential_id'], ['service_account_credentials.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('credential_id', 'nonce_digest', name='uq_service_account_nonces_credential_digest')
    )
    op.create_index('ix_service_account_nonces_expires_at', 'service_account_nonces', ['expires_at'], unique=False)
    op.create_table('service_account_request_limits',
    sa.Column('credential_id', sa.UUID(), nullable=False),
    sa.Column('window_started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('request_count', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('request_count >= 0', name='ck_service_account_request_limits_count'),
    sa.ForeignKeyConstraint(['credential_id'], ['service_account_credentials.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('credential_id')
    )
    op.create_index('ix_service_account_request_limits_updated_at', 'service_account_request_limits', ['updated_at'], unique=False)
    op.create_table('enrollments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('admission_case_id', sa.UUID(), nullable=False),
    sa.Column('student_person_id', sa.UUID(), nullable=False),
    sa.Column('cohort_id', sa.UUID(), nullable=False),
    sa.Column('class_id', sa.UUID(), nullable=False),
    sa.Column('agreed_tuition_cents', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('capacity_override_reason', sa.String(length=500), nullable=True),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('updated_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'active' AND cancelled_at IS NULL AND completed_at IS NULL) OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR (status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL)", name='ck_enrollments_status_times'),
    sa.CheckConstraint("status IN ('active', 'cancelled', 'completed')", name='ck_enrollments_status'),
    sa.CheckConstraint('agreed_tuition_cents >= 0', name='ck_enrollments_tuition'),
    sa.ForeignKeyConstraint(['admission_case_id'], ['admission_cases.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['class_id', 'cohort_id'], ['teaching_classes.id', 'teaching_classes.cohort_id'], name='fk_enrollments_class_cohort', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['cohort_id'], ['cohorts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['student_person_id'], ['people.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'admission_case_id', name='uq_enrollments_id_admission_case')
    )
    op.create_index('ix_enrollments_class_status', 'enrollments', ['class_id', 'status'], unique=False)
    op.create_index('uq_enrollments_active_student_cohort', 'enrollments', ['student_person_id', 'cohort_id'], unique=True, postgresql_where=sa.text("status = 'active'"))
    # These constraints intentionally follow table creation so the two cycles are
    # represented deterministically in both upgrade and downgrade operations.
    op.create_foreign_key(
        'fk_admission_cases_current_enrollment',
        'admission_cases',
        'enrollments',
        ['current_enrollment_id', 'id'],
        ['id', 'admission_case_id'],
        ondelete='RESTRICT',
        deferrable=True,
        initially='DEFERRED',
    )
    op.create_foreign_key(
        'fk_payment_import_events_conflict',
        'payment_import_events',
        'payment_conflicts',
        ['conflict_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    op.create_foreign_key(
        'fk_payment_conflicts_original_event',
        'payment_conflicts',
        'payment_import_events',
        ['original_import_event_id'],
        ['id'],
        ondelete='RESTRICT',
    )
    op.create_table('enrollment_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('enrollment_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=32), nullable=False),
    sa.Column('old_class_id', sa.UUID(), nullable=True),
    sa.Column('new_class_id', sa.UUID(), nullable=True),
    sa.Column('old_status', sa.String(length=16), nullable=True),
    sa.Column('new_status', sa.String(length=16), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('enrollment_count_snapshot', sa.Integer(), nullable=True),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('request_id', sa.String(length=128), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("event_type IN ('confirmed', 'transferred', 'cancelled', 'withdrawal_refunded', 'completed', 'reopened_for_allocation', 'capacity_overridden')", name='ck_enrollment_events_type'),
    sa.CheckConstraint('enrollment_count_snapshot IS NULL OR enrollment_count_snapshot >= 0', name='ck_enrollment_events_count_snapshot'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['enrollment_id'], ['enrollments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['new_class_id'], ['teaching_classes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['old_class_id'], ['teaching_classes.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_enrollment_events_enrollment_time', 'enrollment_events', ['enrollment_id', 'occurred_at', 'id'], unique=False)
    op.create_table('enrollment_withdrawals',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('enrollment_id', sa.UUID(), nullable=False),
    sa.Column('admission_case_id', sa.UUID(), nullable=False),
    sa.Column('withdrawal_date', sa.Date(), nullable=False),
    sa.Column('refund_policy', sa.String(length=32), nullable=False),
    sa.Column('total_days_snapshot', sa.Integer(), nullable=False),
    sa.Column('remaining_days_snapshot', sa.Integer(), nullable=False),
    sa.Column('agreed_tuition_cents_snapshot', sa.BigInteger(), nullable=False),
    sa.Column('available_paid_cents_snapshot', sa.BigInteger(), nullable=False),
    sa.Column('calculated_refund_cents', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('confirmed_by_user_id', sa.UUID(), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("btrim(reason) <> ''", name='ck_enrollment_withdrawals_reason'),
    sa.CheckConstraint("refund_policy IN ('prorated_remaining_days', 'full_remaining_paid')", name='ck_enrollment_withdrawals_policy'),
    sa.CheckConstraint('agreed_tuition_cents_snapshot >= 0 AND available_paid_cents_snapshot > 0 AND calculated_refund_cents > 0 AND calculated_refund_cents <= available_paid_cents_snapshot', name='ck_enrollment_withdrawals_amounts'),
    sa.CheckConstraint('total_days_snapshot > 0 AND remaining_days_snapshot >= 0 AND remaining_days_snapshot <= total_days_snapshot', name='ck_enrollment_withdrawals_days'),
    sa.ForeignKeyConstraint(['confirmed_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['enrollment_id', 'admission_case_id'], ['enrollments.id', 'enrollments.admission_case_id'], name='fk_enrollment_withdrawals_enrollment_case', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('enrollment_id', name='uq_enrollment_withdrawals_enrollment')
    )
    op.create_table('payment_refunds',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('payment_id', sa.UUID(), nullable=False),
    sa.Column('withdrawal_id', sa.UUID(), nullable=True),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('refunded_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('refund_type', sa.String(length=32), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(refund_type = 'manual' AND withdrawal_id IS NULL) OR (refund_type IN ('withdrawal_prorated', 'withdrawal_full') AND withdrawal_id IS NOT NULL)", name='ck_payment_refunds_withdrawal'),
    sa.CheckConstraint("btrim(reason) <> ''", name='ck_payment_refunds_reason'),
    sa.CheckConstraint("refund_type IN ('manual', 'withdrawal_prorated', 'withdrawal_full')", name='ck_payment_refunds_type'),
    sa.CheckConstraint('amount_cents > 0', name='ck_payment_refunds_positive'),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['user_accounts.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['withdrawal_id'], ['enrollment_withdrawals.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_payment_refunds_payment_time', 'payment_refunds', ['payment_id', 'refunded_at', 'id'], unique=False)

    bind = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for key, (permission_id, name, sensitive) in ADMISSIONS_PERMISSIONS.items():
        bind.execute(
            postgresql_insert(permissions)
            .values(id=permission_id, key=key, name=name, sensitive=sensitive)
            .on_conflict_do_nothing(index_elements=[permissions.c.key])
        )
    for role_key, permission_key, scope_kind in ADMISSIONS_GRANTS:
        bind.execute(
            postgresql_insert(role_permissions)
            .values(
                role_id=ROLE_IDS[role_key],
                permission_id=ADMISSIONS_PERMISSIONS[permission_key][0],
                scope_kind=scope_kind,
            )
            .on_conflict_do_nothing()
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM service_accounts)
               OR EXISTS (SELECT 1 FROM service_account_credentials)
               OR EXISTS (SELECT 1 FROM service_account_request_limits)
               OR EXISTS (SELECT 1 FROM service_account_nonces)
               OR EXISTS (SELECT 1 FROM payments)
               OR EXISTS (SELECT 1 FROM payment_import_events)
               OR EXISTS (SELECT 1 FROM payment_conflicts)
               OR EXISTS (SELECT 1 FROM payment_refunds)
               OR EXISTS (SELECT 1 FROM admission_cases)
               OR EXISTS (SELECT 1 FROM admission_case_payments)
               OR EXISTS (SELECT 1 FROM enrollments)
               OR EXISTS (SELECT 1 FROM enrollment_events)
               OR EXISTS (SELECT 1 FROM enrollment_withdrawals) THEN
                RAISE EXCEPTION
                    'cannot downgrade admissions enrollment with business data';
            END IF;

            IF EXISTS (
                SELECT 1 FROM user_permission_overrides
                WHERE permission_id BETWEEN
                    '20000000-0000-4000-8000-000000000016'::uuid AND
                    '20000000-0000-4000-8000-000000000024'::uuid
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade admissions enrollment with account permission overrides';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM (
                    VALUES
                        ('20000000-0000-4000-8000-000000000016'::uuid, 'admission_cases.read', '查看待分配', false),
                        ('20000000-0000-4000-8000-000000000017'::uuid, 'admission_cases.manage', '管理待分配', false),
                        ('20000000-0000-4000-8000-000000000018'::uuid, 'payments.read', '查看付款金额', true),
                        ('20000000-0000-4000-8000-000000000019'::uuid, 'payments.manage', '管理付款', true),
                        ('20000000-0000-4000-8000-000000000020'::uuid, 'refunds.manage', '管理退款', true),
                        ('20000000-0000-4000-8000-000000000021'::uuid, 'enrollments.read', '查看正式报名', false),
                        ('20000000-0000-4000-8000-000000000022'::uuid, 'enrollments.manage', '管理正式报名', false),
                        ('20000000-0000-4000-8000-000000000023'::uuid, 'enrollments.capacity_override', '超额报名审批', true),
                        ('20000000-0000-4000-8000-000000000024'::uuid, 'payment_imports.manage', '管理付款导入', true)
                ) AS expected(id, key, name, sensitive)
                LEFT JOIN permissions AS actual ON actual.id = expected.id
                WHERE actual.id IS NULL
                   OR actual.key IS DISTINCT FROM expected.key
                   OR actual.name IS DISTINCT FROM expected.name
                   OR actual.sensitive IS DISTINCT FROM expected.sensitive
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade admissions enrollment with modified permission seeds';
            END IF;

            IF (
                SELECT count(*) FROM role_permissions
                WHERE permission_id BETWEEN
                    '20000000-0000-4000-8000-000000000016'::uuid AND
                    '20000000-0000-4000-8000-000000000024'::uuid
            ) <> 11 OR EXISTS (
                SELECT 1 FROM role_permissions
                WHERE permission_id BETWEEN
                    '20000000-0000-4000-8000-000000000016'::uuid AND
                    '20000000-0000-4000-8000-000000000024'::uuid
                  AND NOT (
                    (role_id = '10000000-0000-4000-8000-000000000001'::uuid
                     AND scope_kind = 'global')
                    OR (role_id = '10000000-0000-4000-8000-000000000003'::uuid
                        AND permission_id IN (
                            '20000000-0000-4000-8000-000000000016'::uuid,
                            '20000000-0000-4000-8000-000000000021'::uuid
                        ) AND scope_kind = 'global')
                  )
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade admissions enrollment with customized role grants';
            END IF;
        END $$
        """
    )

    op.drop_constraint(
        'fk_admission_cases_current_enrollment', 'admission_cases', type_='foreignkey'
    )
    op.drop_constraint(
        'fk_payment_conflicts_original_event', 'payment_conflicts', type_='foreignkey'
    )
    op.drop_constraint(
        'fk_payment_import_events_conflict', 'payment_import_events', type_='foreignkey'
    )
    permission_ids = [value[0] for value in ADMISSIONS_PERMISSIONS.values()]
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_id = ANY(:ids)"),
        {"ids": permission_ids},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE id = ANY(:ids)"),
        {"ids": permission_ids},
    )
    op.drop_index('ix_payment_refunds_payment_time', table_name='payment_refunds')
    op.drop_table('payment_refunds')
    op.drop_table('enrollment_withdrawals')
    op.drop_index('ix_enrollment_events_enrollment_time', table_name='enrollment_events')
    op.drop_table('enrollment_events')
    op.drop_index('uq_enrollments_active_student_cohort', table_name='enrollments', postgresql_where=sa.text("status = 'active'"))
    op.drop_index('ix_enrollments_class_status', table_name='enrollments')
    op.drop_table('enrollments')
    op.drop_index('ix_service_account_request_limits_updated_at', table_name='service_account_request_limits')
    op.drop_table('service_account_request_limits')
    op.drop_index('ix_service_account_nonces_expires_at', table_name='service_account_nonces')
    op.drop_table('service_account_nonces')
    op.drop_index(op.f('ix_service_account_credentials_service_account_id'), table_name='service_account_credentials')
    op.drop_table('service_account_credentials')
    op.drop_index('uq_payment_import_source_order', table_name='payment_import_events')
    op.drop_index('uq_payment_import_idempotency', table_name='payment_import_events')
    op.drop_table('payment_import_events')
    op.drop_table('admission_case_payments')
    op.drop_table('service_accounts')
    op.drop_index('ix_payments_payer_person', table_name='payments')
    op.drop_index('ix_payments_payer_account', table_name='payments')
    op.drop_index('ix_payments_paid_at', table_name='payments')
    op.drop_table('payments')
    op.drop_table('payment_conflicts')
    op.drop_index('ix_admission_cases_student_status', table_name='admission_cases')
    op.drop_table('admission_cases')
