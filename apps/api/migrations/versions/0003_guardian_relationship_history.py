"""Preserve guardian relationship history.

Revision ID: 0003_guardian_relationship_history
Revises: 0002_seed_rbac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_guardian_relationship_history"
down_revision: str | Sequence[str] | None = "0002_seed_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.add_column(
        "guardian_relationships",
        sa.Column("id", sa.UUID(), nullable=True),
    )
    op.execute("UPDATE guardian_relationships SET id = gen_random_uuid() WHERE id IS NULL")
    op.alter_column(
        "guardian_relationships",
        "id",
        existing_type=sa.UUID(),
        nullable=False,
    )
    op.drop_constraint(
        "guardian_relationships_pkey",
        "guardian_relationships",
        type_="primary",
    )
    op.create_primary_key(
        "guardian_relationships_pkey",
        "guardian_relationships",
        ["id"],
    )

    op.add_column(
        "guardian_relationships",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("ended_by_user_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "guardian_relationships",
        sa.Column("end_reason", sa.String(length=200), nullable=True),
    )
    op.create_foreign_key(
        "fk_guardian_relationships_ended_by_user_id_user_accounts",
        "guardian_relationships",
        "user_accounts",
        ["ended_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_guardian_relationships_pair",
        "guardian_relationships",
        ["guardian_person_id", "student_person_id"],
        unique=False,
    )
    op.create_index(
        "ix_guardian_relationships_student_history",
        "guardian_relationships",
        ["student_person_id", "id"],
        unique=False,
    )
    op.execute(
        """
        ALTER TABLE guardian_relationships
        ADD CONSTRAINT excl_guardian_relationships_overlapping_dates
        EXCLUDE USING gist (
            guardian_person_id WITH =,
            student_person_id WITH =,
            daterange(
                valid_from,
                COALESCE(valid_until, 'infinity'::date),
                '[]'
            ) WITH &&
        )
        WHERE (ended_at IS NULL)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM guardian_relationships
                GROUP BY guardian_person_id, student_person_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot downgrade guardian history with repeated pairs';
            END IF;
        END $$
        """
    )

    op.execute(
        "ALTER TABLE guardian_relationships "
        "DROP CONSTRAINT excl_guardian_relationships_overlapping_dates"
    )
    op.drop_index(
        "ix_guardian_relationships_student_history",
        table_name="guardian_relationships",
    )
    op.drop_index(
        "ix_guardian_relationships_pair",
        table_name="guardian_relationships",
    )
    op.drop_constraint(
        "fk_guardian_relationships_ended_by_user_id_user_accounts",
        "guardian_relationships",
        type_="foreignkey",
    )
    op.drop_constraint(
        "guardian_relationships_pkey",
        "guardian_relationships",
        type_="primary",
    )
    op.create_primary_key(
        "guardian_relationships_pkey",
        "guardian_relationships",
        ["guardian_person_id", "student_person_id"],
    )
    op.drop_column("guardian_relationships", "end_reason")
    op.drop_column("guardian_relationships", "ended_by_user_id")
    op.drop_column("guardian_relationships", "ended_at")
    op.drop_column("guardian_relationships", "updated_at")
    op.drop_column("guardian_relationships", "created_at")
    op.drop_column("guardian_relationships", "id")
