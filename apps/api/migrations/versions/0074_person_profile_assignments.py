"""Independent additive business identities and audited project history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0074_person_profile_assignments"
down_revision = "0073_asset_ownership_rentals"
branch_labels = None
depends_on = None


def fk(name: str, table: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(f"{table}.id", ondelete="RESTRICT"), nullable=nullable
    )


def upgrade() -> None:
    op.create_table(
        "person_assignment_states",
        sa.Column(
            "person_id",
            sa.Uuid(),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("is_player", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        fk("updated_by_user_id", "user_accounts"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version >= 1", name="ck_person_assignment_state_version"),
    )
    op.create_table(
        "person_project_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        fk("person_id", "people"),
        fk("project_id", "game_projects"),
        fk("created_by_user_id", "user_accounts"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        fk("ended_by_user_id", "user_accounts", nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(ended_at IS NULL) = (ended_by_user_id IS NULL)",
            name="ck_person_project_assignment_end",
        ),
    )
    op.create_index(
        "uq_person_project_assignments_open",
        "person_project_assignments",
        ["person_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "person_assignment_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        fk("actor_account_id", "user_accounts"),
        fk("person_id", "people"),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "actor_account_id", "idempotency_key", name="uq_person_assignment_operations_actor_key"
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE person_assignment_states, person_project_assignments, person_assignment_operations IN ACCESS EXCLUSIVE MODE"
        )
    )
    if any(
        connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})"))
        for table in (
            "person_assignment_states",
            "person_project_assignments",
            "person_assignment_operations",
        )
    ):
        raise RuntimeError("retained person assignment history prevents downgrade")
    op.drop_table("person_assignment_operations")
    op.drop_table("person_project_assignments")
    op.drop_table("person_assignment_states")
