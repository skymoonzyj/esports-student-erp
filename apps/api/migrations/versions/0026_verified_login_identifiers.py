"""Remove the primary-login concept from verified account identifiers.

The ``is_primary`` column is kept as a compatibility field for historical
rows and older exports, but it no longer participates in authentication. An
account may authenticate with any verified phone or email identifier.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_verified_login_identifiers"
down_revision: str | Sequence[str] | None = "0025_password_reset_mail_template"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_primary_identifier_invariant() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_account_login_identifiers_primary_account "
            "ON account_login_identifiers"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_user_accounts_primary_identifier "
            "ON user_accounts"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS enforce_account_login_identifier_primary()")
    )
    op.drop_index(
        "uq_account_login_identifiers_primary_account",
        table_name="account_login_identifiers",
    )


def _create_primary_identifier_invariant() -> None:
    op.create_index(
        "uq_account_login_identifiers_primary_account",
        "account_login_identifiers",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_account_login_identifier_primary()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                target_account_id uuid;
                account_ids uuid[];
                identifier_count bigint;
                primary_count bigint;
            BEGIN
                IF TG_TABLE_NAME = 'user_accounts' THEN
                    account_ids := ARRAY[NEW.id];
                ELSIF TG_OP = 'INSERT' THEN
                    account_ids := ARRAY[NEW.account_id];
                ELSIF TG_OP = 'DELETE' THEN
                    account_ids := ARRAY[OLD.account_id];
                ELSE
                    account_ids := ARRAY[NEW.account_id, OLD.account_id];
                END IF;

                FOREACH target_account_id IN ARRAY account_ids LOOP
                    IF target_account_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM user_accounts WHERE id = target_account_id
                    ) THEN
                        CONTINUE;
                    END IF;

                    SELECT count(*), count(*) FILTER (WHERE is_primary)
                    INTO identifier_count, primary_count
                    FROM account_login_identifiers
                    WHERE account_id = target_account_id;

                    IF identifier_count < 1 OR primary_count <> 1 THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            CONSTRAINT = 'ck_account_login_identifiers_primary',
                            MESSAGE = 'account must have exactly one primary login identifier';
                    END IF;
                END LOOP;
                RETURN NULL;
            END;
            $$;

            CREATE CONSTRAINT TRIGGER trg_account_login_identifiers_primary_account
            AFTER INSERT OR UPDATE OR DELETE ON account_login_identifiers
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION enforce_account_login_identifier_primary();

            CREATE CONSTRAINT TRIGGER trg_user_accounts_primary_identifier
            AFTER INSERT OR UPDATE ON user_accounts
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION enforce_account_login_identifier_primary();
            """
        )
    )


def upgrade() -> None:
    _drop_primary_identifier_invariant()


def downgrade() -> None:
    bind = op.get_bind()
    missing = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM user_accounts ua "
            "LEFT JOIN account_login_identifiers ali ON ali.account_id = ua.id "
            "GROUP BY ua.id HAVING count(ali.id) < 1"
            ")"
        )
    )
    if missing:
        raise RuntimeError(
            "cannot restore primary-login invariant for an account without identifiers"
        )

    # Pick a deterministic historical primary (phone before email, then the
    # oldest verified row) before recreating the old unique index and triggers.
    op.execute(sa.text("UPDATE account_login_identifiers SET is_primary = false"))
    op.execute(
        sa.text(
            """
            WITH preferred AS (
                SELECT DISTINCT ON (account_id) id
                FROM account_login_identifiers
                ORDER BY account_id,
                    CASE WHEN kind = 'phone' THEN 0 ELSE 1 END,
                    verified_at,
                    id
            )
            UPDATE account_login_identifiers ali
            SET is_primary = true
            FROM preferred
            WHERE ali.id = preferred.id
            """
        )
    )
    _create_primary_identifier_invariant()
