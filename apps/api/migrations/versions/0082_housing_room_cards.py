"""Store dates per effective housing stay without inventing legacy dates."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0082_housing_room_cards"
down_revision = "0081_housing_cleaning_frequency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "housing_room_cards",
        sa.Column("stay_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("stay_id"),
        sa.ForeignKeyConstraint(["stay_id"], ["housing_stays.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "(issued_on IS NULL AND expires_on IS NULL) OR "
            "(issued_on IS NOT NULL AND expires_on IS NOT NULL AND expires_on >= issued_on "
            "AND issued_on >= DATE '0001-01-01' AND expires_on <= DATE '9999-12-31')",
            name="ck_housing_room_cards_dates",
        ),
        sa.CheckConstraint("version >= 1", name="ck_housing_room_cards_version"),
    )
    op.create_index("ix_housing_room_cards_expiry", "housing_room_cards", ["expires_on", "stay_id"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE housing_room_cards IN ACCESS EXCLUSIVE MODE"))
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM housing_room_cards)")):
        raise RuntimeError("房卡数据存在，禁止静默降级丢失业务记录")
    op.drop_table("housing_room_cards")
