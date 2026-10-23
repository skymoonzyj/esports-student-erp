"""Add optional person nicknames."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_person_nicknames"
down_revision: str | Sequence[str] | None = "0033_asset_photos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("people", sa.Column("nickname", sa.String(length=40), nullable=True))
    op.create_check_constraint(
        "ck_people_nickname_not_blank",
        "people",
        "nickname IS NULL OR btrim(nickname) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint("ck_people_nickname_not_blank", "people", type_="check")
    op.drop_column("people", "nickname")
