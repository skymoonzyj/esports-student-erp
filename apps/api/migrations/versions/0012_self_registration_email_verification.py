"""Add self-service registration and email verification requests.

Revision ID: 0012_self_registration_email_verification
Revises: 0011_housing_real_name_reporting
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_self_registration_email_verification"
down_revision: str | Sequence[str] | None = "0011_housing_real_name_reporting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "registration_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("full_name", sa.String(length=80), nullable=False),
        sa.Column("identity_ciphertext", sa.Text(), nullable=False),
        sa.Column("identity_digest", sa.String(length=64), nullable=False),
        sa.Column("phone_ciphertext", sa.Text(), nullable=False),
        sa.Column("phone_digest", sa.String(length=64), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("sex", sa.String(length=16), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=False),
        sa.Column("requested_person_type", sa.String(length=16), nullable=False),
        sa.Column("requested_staff_type", sa.String(length=32), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("verification_code_digest", sa.String(length=64), nullable=False),
        sa.Column("verification_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_code_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="pending_verification",
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("person_id", sa.UUID(), nullable=True),
        sa.Column("account_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "requested_person_type IN ('student', 'guardian', 'staff')",
            name="ck_registration_requests_person_type",
        ),
        sa.CheckConstraint(
            "requested_person_type <> 'staff' OR requested_staff_type IS NOT NULL",
            name="ck_registration_requests_staff_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending_verification', 'completed', 'expired', 'rejected')",
            name="ck_registration_requests_status",
        ),
        sa.CheckConstraint("sex IN ('male', 'female')", name="ck_registration_requests_sex"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_registration_requests_email",
        "registration_requests",
        ["email_normalized"],
        unique=False,
    )
    op.create_index(
        "ix_registration_requests_created_at",
        "registration_requests",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_registration_requests_created_at", table_name="registration_requests")
    op.drop_index("ix_registration_requests_email", table_name="registration_requests")
    op.drop_table("registration_requests")
