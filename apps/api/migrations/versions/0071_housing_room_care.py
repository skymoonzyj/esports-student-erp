"""Persist independent room cleaning state and private three-slot photos."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_housing_room_care"
down_revision: str | Sequence[str] | None = "0070_housing_history_void"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "housing_rooms",
        sa.Column(
            "daily_cleaning_enabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column(
        "housing_rooms",
        sa.Column(
            "cleaning_status",
            sa.String(16),
            server_default="ready",
            nullable=False,
        ),
    )
    op.add_column("housing_rooms", sa.Column("cleaning_pending_at", sa.DateTime(timezone=True)))
    op.add_column("housing_rooms", sa.Column("cleaning_completed_at", sa.DateTime(timezone=True)))
    op.add_column("housing_rooms", sa.Column("cleaning_completed_by_user_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_housing_rooms_cleaning_completed_actor",
        "housing_rooms",
        "user_accounts",
        ["cleaning_completed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_housing_rooms_cleaning_status",
        "housing_rooms",
        "cleaning_status IN ('ready', 'pending')",
    )
    op.create_check_constraint(
        "ck_housing_rooms_cleaning_pending_fact",
        "housing_rooms",
        "(cleaning_status = 'pending' AND cleaning_pending_at IS NOT NULL) OR "
        "(cleaning_status = 'ready' AND (cleaning_pending_at IS NULL OR "
        "(cleaning_completed_at IS NOT NULL AND cleaning_completed_at >= cleaning_pending_at)))",
    )
    op.create_check_constraint(
        "ck_housing_rooms_cleaning_completed_fact",
        "housing_rooms",
        "(cleaning_completed_at IS NULL AND cleaning_completed_by_user_id IS NULL) OR "
        "(cleaning_completed_at IS NOT NULL AND cleaning_completed_by_user_id IS NOT NULL "
        "AND cleaning_pending_at IS NOT NULL)",
    )
    op.create_table(
        "housing_room_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("thumbnail_content", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(32), nullable=False),
        sa.Column("thumbnail_mime_type", sa.String(32), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("thumbnail_byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("room_id", "slot", name="uq_housing_room_photos_room_slot"),
        sa.ForeignKeyConstraint(["room_id"], ["housing_rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["user_accounts.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("slot BETWEEN 1 AND 3", name="ck_housing_room_photos_slot"),
        sa.CheckConstraint(
            "mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_housing_room_photos_mime_type",
        ),
        sa.CheckConstraint(
            "thumbnail_mime_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_housing_room_photos_thumbnail_mime_type",
        ),
        sa.CheckConstraint(
            "byte_size BETWEEN 1 AND 10485760 AND octet_length(content) = byte_size",
            name="ck_housing_room_photos_byte_size",
        ),
        sa.CheckConstraint(
            "thumbnail_byte_size BETWEEN 1 AND 10485760 "
            "AND octet_length(thumbnail_content) = thumbnail_byte_size",
            name="ck_housing_room_photos_thumbnail_byte_size",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_housing_room_photos_sha256"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    # Hold the facts stable until the transactional DDL finishes: no concurrent
    # upload or room-care update may slip between this guard and column removal.
    connection.execute(
        sa.text("LOCK TABLE housing_rooms, housing_room_photos IN ACCESS EXCLUSIVE MODE")
    )
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM housing_room_photos) OR EXISTS ("
            "SELECT 1 FROM housing_rooms WHERE daily_cleaning_enabled "
            "OR cleaning_status <> 'ready' OR cleaning_pending_at IS NOT NULL "
            "OR cleaning_completed_at IS NOT NULL OR cleaning_completed_by_user_id IS NOT NULL)"
        )
    ):
        raise RuntimeError("cannot downgrade room care with existing care facts or photos")
    op.drop_table("housing_room_photos")
    for constraint in (
        "ck_housing_rooms_cleaning_completed_fact",
        "ck_housing_rooms_cleaning_pending_fact",
        "ck_housing_rooms_cleaning_status",
    ):
        op.drop_constraint(constraint, "housing_rooms", type_="check")
    op.drop_constraint(
        "fk_housing_rooms_cleaning_completed_actor", "housing_rooms", type_="foreignkey"
    )
    for column in (
        "cleaning_completed_by_user_id",
        "cleaning_completed_at",
        "cleaning_pending_at",
        "cleaning_status",
        "daily_cleaning_enabled",
    ):
        op.drop_column("housing_rooms", column)
