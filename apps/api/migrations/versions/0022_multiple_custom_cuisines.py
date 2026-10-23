"""Allow multiple distinct custom cuisines.

Revision ID: 0022_multiple_custom_cuisines
Revises: 0021_registration_review_candidate_binding
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_multiple_custom_cuisines"
down_revision: str | Sequence[str] | None = (
    "0021_registration_review_candidate_binding"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "person_food_preference_cuisines"
OLD_CONSTRAINT = "uq_person_food_preference_cuisines_option"
NEW_INDEX = "uq_person_food_preference_cuisines_standard_option"


def _lock_rows() -> None:
    op.get_bind().execute(
        sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE")
    )


def _preflight_downgrade() -> None:
    _lock_rows()
    repeated = op.get_bind().scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {TABLE} "
            "GROUP BY person_id, cuisine_option_id HAVING count(*) > 1)"
        )
    )
    if repeated:
        raise RuntimeError(
            "cannot downgrade multiple custom cuisines with repeated options"
        )


def upgrade() -> None:
    _lock_rows()
    op.drop_constraint(OLD_CONSTRAINT, TABLE, type_="unique")
    op.create_index(
        NEW_INDEX,
        TABLE,
        ["person_id", "cuisine_option_id"],
        unique=True,
        postgresql_where=sa.text("custom_name IS NULL"),
    )


def downgrade() -> None:
    _preflight_downgrade()
    op.drop_index(NEW_INDEX, table_name=TABLE)
    op.create_unique_constraint(
        OLD_CONSTRAINT,
        TABLE,
        ["person_id", "cuisine_option_id"],
    )
