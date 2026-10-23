"""Queue new/changed payments for bounded incremental revenue-share reconciliation."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0053_revenue_share_processing"
down_revision: str | Sequence[str] | None = "0052_operations_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "revenue_share_reconcile_queue",
        sa.Column(
            "payment_id",
            sa.UUID(),
            sa.ForeignKey("payments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )
    op.create_index(
        "ix_revenue_share_reconcile_queue_paid_at",
        "revenue_share_reconcile_queue",
        ["paid_at", "payment_id"],
    )
    op.execute("""
        CREATE FUNCTION public.revenue_share_enqueue_payment() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            INSERT INTO public.revenue_share_reconcile_queue (payment_id, paid_at)
            VALUES (NEW.id, NEW.paid_at)
            ON CONFLICT (payment_id) DO NOTHING;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION public.revenue_share_enqueue_refund() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            INSERT INTO public.revenue_share_reconcile_queue (payment_id, paid_at)
            SELECT id, paid_at FROM public.payments WHERE id = NEW.payment_id
            ON CONFLICT (payment_id) DO NOTHING;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE FUNCTION public.revenue_share_update_queued_paid_at() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            UPDATE public.revenue_share_reconcile_queue
            SET paid_at = NEW.paid_at WHERE payment_id = NEW.id;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_revenue_share_payment_insert
        AFTER INSERT ON public.payments
        FOR EACH ROW EXECUTE FUNCTION public.revenue_share_enqueue_payment()
    """)
    op.execute("""
        CREATE TRIGGER trg_revenue_share_refund_insert
        AFTER INSERT ON public.payment_refunds
        FOR EACH ROW EXECUTE FUNCTION public.revenue_share_enqueue_refund()
    """)
    op.execute("""
        CREATE TRIGGER trg_revenue_share_payment_paid_at
        AFTER UPDATE OF paid_at ON public.payments
        FOR EACH ROW WHEN (OLD.paid_at IS DISTINCT FROM NEW.paid_at)
        EXECUTE FUNCTION public.revenue_share_update_queued_paid_at()
    """)
    # The queue is recoverable scheduling state. Existing earning entries stay
    # untouched; refunded payments are queued once to verify any missing offsets.
    op.execute("""
        INSERT INTO public.revenue_share_reconcile_queue (payment_id, paid_at)
        SELECT p.id, p.paid_at FROM public.payments AS p
        LEFT JOIN public.revenue_share_payment_statuses AS s ON s.payment_id = p.id
        WHERE s.payment_id IS NULL OR s.state = 'pending'
           OR NOT EXISTS (
               SELECT 1 FROM public.revenue_share_ledger_entries AS l
               WHERE l.payment_id = p.id AND l.entry_kind = 'earning'
           )
           OR EXISTS (
               SELECT 1 FROM public.payment_refunds AS r WHERE r.payment_id = p.id
           )
        ON CONFLICT (payment_id) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_revenue_share_payment_paid_at ON public.payments")
    op.execute("DROP TRIGGER trg_revenue_share_refund_insert ON public.payment_refunds")
    op.execute("DROP TRIGGER trg_revenue_share_payment_insert ON public.payments")
    op.execute("DROP FUNCTION public.revenue_share_update_queued_paid_at()")
    op.execute("DROP FUNCTION public.revenue_share_enqueue_refund()")
    op.execute("DROP FUNCTION public.revenue_share_enqueue_payment()")
    op.drop_table("revenue_share_reconcile_queue")
