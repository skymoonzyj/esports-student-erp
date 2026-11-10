"""Preserve legacy cleaning intent while adding explicit cleaning frequency."""

import sqlalchemy as sa
from alembic import op

revision = "0081_housing_cleaning_frequency"
down_revision = "0080_reactionx_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("housing_rooms", sa.Column("cleaning_frequency", sa.String(16), nullable=True))
    op.execute(
        "UPDATE housing_rooms SET cleaning_frequency = "
        "CASE WHEN daily_cleaning_enabled IS TRUE THEN 'daily' ELSE 'unspecified' END"
    )
    op.alter_column(
        "housing_rooms", "cleaning_frequency", nullable=False, server_default=sa.text("'unspecified'")
    )
    op.create_check_constraint(
        "ck_housing_rooms_cleaning_frequency", "housing_rooms",
        "cleaning_frequency IN ('unspecified', 'daily', 'weekly', 'on_demand')",
    )


def downgrade() -> None:
    # The old Boolean cannot encode weekly/on-demand intent. Refuse data loss.
    connection = op.get_bind()
    # Hold the DDL lock before inspecting facts so a concurrent writer cannot
    # add an unrepresentable value between this check and dropping the column.
    connection.execute(sa.text("LOCK TABLE housing_rooms IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM housing_rooms "
        "WHERE cleaning_frequency IN ('weekly', 'on_demand'))"
    )):
        raise RuntimeError("cannot downgrade housing cleaning frequencies without losing business data")
    # An old writer may have set the Boolean without knowing the enum column.
    op.execute(
        "UPDATE housing_rooms SET daily_cleaning_enabled = "
        "(daily_cleaning_enabled IS TRUE OR cleaning_frequency = 'daily')"
    )
    op.drop_constraint("ck_housing_rooms_cleaning_frequency", "housing_rooms", type_="check")
    op.drop_column("housing_rooms", "cleaning_frequency")
