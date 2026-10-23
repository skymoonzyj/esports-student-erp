"""Add private personnel profile notes without changing shared projections."""

import sqlalchemy as sa
from alembic import op

revision = "0067_person_profile_notes"
down_revision = "0066_assisted_personnel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("people", sa.Column("profile_notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_people_profile_notes_length",
        "people",
        "profile_notes IS NULL OR (length(profile_notes) <= 2000 AND btrim(profile_notes) <> '')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_people_profile_notes_length", "people", type_="check")
    op.drop_column("people", "profile_notes")
