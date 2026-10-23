"""Separate guardian invitation handoff secrets from management identifiers.

Revision ID: 0019_guardian_invitation_tokens
Revises: 0018_registration_review_metadata
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_guardian_invitation_tokens"
down_revision: str | Sequence[str] | None = "0018_registration_review_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_ACTIVE_APPLICATION_STATES = (
    "'draft', 'contact_verified', 'identity_completed', 'pending_guardian', "
    "'ready_to_finalize', 'conflict'"
)
_VERIFIED_IDENTIFIER_STATES = (
    "'contact_verified', 'identity_completed', 'pending_guardian', "
    "'ready_to_finalize', 'conflict'"
)


def _replace_primary_identifier_function(*, enforce_all_accounts: bool) -> None:
    compatibility_filter = (
        ""
        if enforce_all_accounts
        else """
                          AND (
                              self_service_primary_purpose IS NOT NULL
                              OR login_phone_digest IS NULL
                          )
        """
    )
    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION enforce_account_login_identifier_primary()
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
                        SELECT 1 FROM user_accounts
                        WHERE id = target_account_id
                        {compatibility_filter}
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
            $$
            """
        )
    )


def _preflight_account_identifier_invariant() -> None:
    bind = op.get_bind()
    invalid = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM user_accounts ua "
            "LEFT JOIN account_login_identifiers ali ON ali.account_id = ua.id "
            "GROUP BY ua.id "
            "HAVING count(ali.id) < 1 "
            "OR count(ali.id) FILTER (WHERE ali.is_primary) <> 1"
            ")"
        )
    )
    if invalid:
        raise RuntimeError(
            "cannot enforce canonical login identifier invariant"
        )


def _upgrade_registration_invariants() -> None:
    _preflight_account_identifier_invariant()
    op.create_check_constraint(
        "ck_people_legacy_email_cleared",
        "people",
        "email IS NULL",
    )
    op.drop_index(
        "uq_registration_applications_active_identifier",
        table_name="registration_applications",
    )
    op.create_index(
        "uq_registration_applications_active_identifier",
        "registration_applications",
        ["verified_identifier_digest"],
        unique=True,
        postgresql_where=sa.text(
            "verified_identifier_digest IS NOT NULL AND state IN ("
            f"{_VERIFIED_IDENTIFIER_STATES})"
        ),
    )
    _replace_primary_identifier_function(enforce_all_accounts=True)


def _downgrade_registration_invariants() -> None:
    _replace_primary_identifier_function(enforce_all_accounts=False)
    op.drop_index(
        "uq_registration_applications_active_identifier",
        table_name="registration_applications",
    )
    op.create_index(
        "uq_registration_applications_active_identifier",
        "registration_applications",
        ["verified_identifier_digest"],
        unique=True,
        postgresql_where=sa.text(
            "verified_identifier_digest IS NOT NULL AND state IN ("
            f"{_LEGACY_ACTIVE_APPLICATION_STATES})"
        ),
    )
    op.drop_constraint(
        "ck_people_legacy_email_cleared",
        "people",
        type_="check",
    )


def _preflight_downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "LOCK TABLE guardian_registration_invitations "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM guardian_registration_invitations)"
        )
    ):
        raise RuntimeError(
            "cannot downgrade guardian invitation token isolation with invitation history"
        )


def upgrade() -> None:
    _upgrade_registration_invariants()
    op.add_column(
        "guardian_registration_invitations",
        sa.Column("handoff_token_digest", sa.String(length=64), nullable=True),
    )
    # Legacy pending links exposed the management UUID as their external secret.
    # They cannot be upgraded safely, so invalidate them and require a fresh invite.
    op.execute(
        sa.text(
            "UPDATE guardian_registration_invitations SET "
            "status = CASE WHEN status = 'pending' THEN 'revoked' ELSE status END, "
            "revoked_at = CASE WHEN status = 'pending' "
            "THEN COALESCE(revoked_at, CURRENT_TIMESTAMP) ELSE revoked_at END, "
            "handoff_token_digest = NULL, "
            "invited_identifier_digest = 'redacted:guardian-invitation:v1', "
            "invited_identifier_ciphertext = 'redacted:guardian-invitation:v1'"
        )
    )
    op.create_check_constraint(
        "ck_guardian_registration_invitations_handoff_token",
        "guardian_registration_invitations",
        "(status = 'pending' AND handoff_token_digest IS NOT NULL) OR "
        "(status <> 'pending' AND handoff_token_digest IS NULL)",
    )
    op.create_index(
        "uq_guardian_registration_invitations_handoff_token",
        "guardian_registration_invitations",
        ["handoff_token_digest"],
        unique=True,
        postgresql_where=sa.text("handoff_token_digest IS NOT NULL"),
    )


def downgrade() -> None:
    _preflight_downgrade()
    op.drop_index(
        "uq_guardian_registration_invitations_handoff_token",
        table_name="guardian_registration_invitations",
    )
    op.drop_constraint(
        "ck_guardian_registration_invitations_handoff_token",
        "guardian_registration_invitations",
        type_="check",
    )
    op.drop_column(
        "guardian_registration_invitations",
        "handoff_token_digest",
    )
    _downgrade_registration_invariants()
