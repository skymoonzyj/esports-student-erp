"""Assign permanent, database-generated numbers to every admission case."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_admission_order_numbers"
down_revision: str | Sequence[str] | None = "0053_revenue_share_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SEQUENCE public.admission_order_number_seq AS bigint NO CYCLE")
    op.execute("""
        CREATE FUNCTION public.next_admission_order_number() RETURNS text
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public AS $$
        DECLARE
            serial_text text := nextval('public.admission_order_number_seq'::regclass)::text;
        BEGIN
            RETURN 'BM' || to_char(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai', 'YYYYMMDD')
                || lpad(serial_text, greatest(6, length(serial_text)), '0');
        END;
        $$
    """)
    # Add the column without a default first so existing orders use their original
    # China date, rather than the deployment date. Do not alter IDs or timestamps.
    op.add_column("admission_cases", sa.Column("order_number", sa.String(32), nullable=True))
    op.execute("""
        WITH numbered AS MATERIALIZED (
            SELECT id, created_at,
                nextval('public.admission_order_number_seq'::regclass)::text AS serial_text
            FROM public.admission_cases ORDER BY created_at, id
        )
        UPDATE public.admission_cases AS admission
        SET order_number = 'BM'
            || to_char(numbered.created_at AT TIME ZONE 'Asia/Shanghai', 'YYYYMMDD')
            || lpad(numbered.serial_text, greatest(6, length(numbered.serial_text)), '0')
        FROM numbered WHERE numbered.id = admission.id
    """)
    op.alter_column(
        "admission_cases",
        "order_number",
        nullable=False,
        server_default=sa.text("public.next_admission_order_number()"),
    )
    op.create_unique_constraint(
        "uq_admission_cases_order_number", "admission_cases", ["order_number"]
    )
    op.create_check_constraint(
        "ck_admission_cases_order_number_format",
        "admission_cases",
        "order_number ~ '^BM[0-9]{8}[0-9]{6,19}$'",
    )
    op.execute("""
        ALTER SEQUENCE public.admission_order_number_seq
        OWNED BY public.admission_cases.order_number
    """)
    op.execute("""
        CREATE FUNCTION public.protect_admission_order_number() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF NEW.order_number IS DISTINCT FROM OLD.order_number THEN
                RAISE EXCEPTION '报名订单号生成后不可修改'
                    USING ERRCODE = '23514', CONSTRAINT = 'admission_order_number_immutable';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER trg_admission_order_number_immutable
        BEFORE UPDATE OF order_number ON public.admission_cases
        FOR EACH ROW EXECUTE FUNCTION public.protect_admission_order_number()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_admission_order_number_immutable ON public.admission_cases")
    op.execute("DROP FUNCTION public.protect_admission_order_number()")
    op.alter_column("admission_cases", "order_number", server_default=None)
    op.execute("DROP FUNCTION public.next_admission_order_number()")
    op.drop_constraint("ck_admission_cases_order_number_format", "admission_cases", type_="check")
    op.drop_constraint("uq_admission_cases_order_number", "admission_cases", type_="unique")
    # The owned sequence is removed with its column. Financial and enrollment
    # records remain attached to the unchanged admission case UUID.
    op.drop_column("admission_cases", "order_number")
