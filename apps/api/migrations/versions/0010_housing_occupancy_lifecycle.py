"""Add housing occupancy lifecycle.

Revision ID: 0010_housing_occupancy_lifecycle
Revises: 0009_housing_room_status_foundation
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0010_housing_occupancy_lifecycle"
down_revision: str | Sequence[str] | None = "0009_housing_room_status_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOUSING_READ_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000037")
HOUSING_OCCUPANCY_PERMISSIONS = {
    "housing.stays.manage": (
        UUID("20000000-0000-4000-8000-000000000039"),
        "管理住宿与预约",
        True,
    ),
    "housing.transfers.manage": (
        UUID("20000000-0000-4000-8000-000000000040"),
        "管理调房",
        True,
    ),
    "housing.checkouts.manage": (
        UUID("20000000-0000-4000-8000-000000000041"),
        "管理退房",
        True,
    ),
    "housing.issues.manage": (
        UUID("20000000-0000-4000-8000-000000000042"),
        "管理问题房",
        True,
    ),
}

DEFAULT_GRANTS = tuple(
    ("admin", permission_key, "global")
    for permission_key in HOUSING_OCCUPANCY_PERMISSIONS
) + (
    ("student", "housing.read", "self"),
    ("guardian", "housing.read", "family"),
)


def _role_permission_grants(bind: sa.Connection) -> set[tuple[str, str, str]]:
    return set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id"
            )
        ).tuples()
    )


def _preflight_upgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE permissions, role_permissions, roles "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    permission_ids = [values[0] for values in HOUSING_OCCUPANCY_PERMISSIONS.values()]
    permission_keys = list(HOUSING_OCCUPANCY_PERMISSIONS)
    if bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:ids AS uuid[])) "
            "OR key = ANY(CAST(:keys AS text[])))"
        ),
        {"ids": permission_ids, "keys": permission_keys},
    ):
        raise RuntimeError(
            "cannot upgrade housing occupancy with preexisting permission seeds"
        )

    if _role_permission_grants(bind).intersection(DEFAULT_GRANTS):
        raise RuntimeError(
            "cannot upgrade housing occupancy with preexisting default grants"
        )

    role_keys = set(bind.scalars(sa.text("SELECT key FROM roles")))
    if not {"admin", "student", "guardian"} <= role_keys:
        raise RuntimeError("cannot upgrade housing occupancy without required roles")
    housing_read_id = bind.scalar(
        sa.text("SELECT id FROM permissions WHERE key = 'housing.read'")
    )
    if housing_read_id != HOUSING_READ_PERMISSION_ID:
        raise RuntimeError("cannot upgrade housing occupancy without housing.read seed")


def _create_room_issues_table() -> None:
    op.create_table(
        "housing_room_issues",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("room_id", sa.UUID(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("previous_service_status", sa.String(length=24), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_by_user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.UUID(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(description) <> ''",
            name="ck_housing_room_issues_description",
        ),
        sa.CheckConstraint(
            "previous_service_status IN "
            "('available', 'problem', 'maintenance', 'disabled')",
            name="ck_housing_room_issues_previous_service_status",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved')",
            name="ck_housing_room_issues_status",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND resolved_at IS NULL "
            "AND resolved_by_user_id IS NULL AND resolution_note IS NULL) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND resolved_by_user_id IS NOT NULL "
            "AND resolution_note IS NOT NULL AND btrim(resolution_note) <> '')",
            name="ck_housing_room_issues_resolution",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= reported_at",
            name="ck_housing_room_issues_resolved_at",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["housing_rooms.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reported_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_housing_room_issues_open_room",
        "housing_room_issues",
        ["room_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def _create_stays_table() -> None:
    op.create_table(
        "housing_stays",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("room_id", sa.UUID(), nullable=False),
        sa.Column("stay_purpose", sa.String(length=16), nullable=False),
        sa.Column("duration_mode", sa.String(length=16), nullable=False),
        sa.Column("cohort_id", sa.UUID(), nullable=True),
        sa.Column("visit_reason", sa.String(length=500), nullable=True),
        sa.Column("planned_check_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_check_out_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("room_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("floor_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("building_name_snapshot", sa.String(length=80), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.Column(
            "checkout_requested_actual_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "checkout_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("checkout_requested_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column("checked_in_by_user_id", sa.UUID(), nullable=True),
        sa.Column("checked_out_by_user_id", sa.UUID(), nullable=True),
        sa.Column("cancelled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "stay_purpose IN ('permanent', 'camp', 'temporary')",
            name="ck_housing_stays_purpose",
        ),
        sa.CheckConstraint(
            "duration_mode IN ('long_term', 'fixed_term')",
            name="ck_housing_stays_duration_mode",
        ),
        sa.CheckConstraint(
            "(duration_mode = 'fixed_term' AND planned_check_out_at IS NOT NULL "
            "AND planned_check_out_at > planned_check_in_at) OR "
            "(duration_mode = 'long_term' AND planned_check_out_at IS NULL)",
            name="ck_housing_stays_planned_range",
        ),
        sa.CheckConstraint(
            "stay_purpose <> 'camp' OR cohort_id IS NOT NULL",
            name="ck_housing_stays_camp_cohort",
        ),
        sa.CheckConstraint(
            "stay_purpose <> 'temporary' OR "
            "(duration_mode = 'fixed_term' AND visit_reason IS NOT NULL "
            "AND btrim(visit_reason) <> '')",
            name="ck_housing_stays_temporary_details",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'checked_in', 'checkout_pending', "
            "'checked_out', 'cancelled')",
            name="ck_housing_stays_status",
        ),
        sa.CheckConstraint(
            "(status = 'planned' AND actual_check_in_at IS NULL "
            "AND actual_check_out_at IS NULL AND checked_in_by_user_id IS NULL "
            "AND checked_out_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status IN ('checked_in', 'checkout_pending') "
            "AND actual_check_in_at IS NOT NULL AND actual_check_out_at IS NULL "
            "AND checked_in_by_user_id IS NOT NULL "
            "AND checked_out_by_user_id IS NULL AND cancelled_by_user_id IS NULL "
            "AND cancellation_reason IS NULL) OR "
            "(status = 'checked_out' AND actual_check_in_at IS NOT NULL "
            "AND actual_check_out_at IS NOT NULL "
            "AND checked_in_by_user_id IS NOT NULL "
            "AND checked_out_by_user_id IS NOT NULL "
            "AND cancelled_by_user_id IS NULL AND cancellation_reason IS NULL) OR "
            "(status = 'cancelled' AND actual_check_in_at IS NULL "
            "AND actual_check_out_at IS NULL AND checked_in_by_user_id IS NULL "
            "AND checked_out_by_user_id IS NULL "
            "AND cancelled_by_user_id IS NOT NULL "
            "AND cancellation_reason IS NOT NULL "
            "AND btrim(cancellation_reason) <> '')",
            name="ck_housing_stays_status_fields",
        ),
        sa.CheckConstraint(
            "btrim(room_name_snapshot) <> '' "
            "AND btrim(floor_name_snapshot) <> '' "
            "AND btrim(building_name_snapshot) <> ''",
            name="ck_housing_stays_snapshot_names",
        ),
        sa.CheckConstraint(
            "(actual_check_out_at IS NULL OR "
            "(actual_check_in_at IS NOT NULL "
            "AND actual_check_out_at > actual_check_in_at)) AND "
            "(actual_check_in_at IS NULL OR planned_check_out_at IS NULL OR "
            "actual_check_in_at < planned_check_out_at)",
            name="ck_housing_stays_actual_range",
        ),
        sa.CheckConstraint(
            "(status IN ('planned', 'checked_in', 'cancelled') "
            "AND checkout_requested_actual_at IS NULL "
            "AND checkout_requested_at IS NULL "
            "AND checkout_requested_by_user_id IS NULL) OR "
            "(status IN ('checkout_pending', 'checked_out') AND "
            "((checkout_requested_actual_at IS NULL "
            "AND checkout_requested_at IS NULL "
            "AND checkout_requested_by_user_id IS NULL) OR "
            "(checkout_requested_actual_at IS NOT NULL "
            "AND checkout_requested_at IS NOT NULL "
            "AND checkout_requested_by_user_id IS NOT NULL)))",
            name="ck_housing_stays_checkout_request_fields",
        ),
        sa.CheckConstraint("version >= 1", name="ck_housing_stays_version"),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["housing_rooms.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cohort_id"],
            ["cohorts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checkout_requested_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checked_in_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["checked_out_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    effective_range = (
        "tstzrange(COALESCE(actual_check_in_at, planned_check_in_at), "
        "COALESCE(planned_check_out_at, 'infinity'::timestamptz), '[)')"
    )
    op.execute(
        "ALTER TABLE housing_stays ADD CONSTRAINT "
        "excl_housing_stays_person_active_overlap EXCLUDE USING gist ("
        f"person_id WITH =, {effective_range} WITH &&) "
        "WHERE (status IN ('planned', 'checked_in', 'checkout_pending'))"
    )
    op.create_index(
        "ix_housing_stays_room_status_time",
        "housing_stays",
        [
            "room_id",
            "status",
            "planned_check_in_at",
            "planned_check_out_at",
            "id",
        ],
    )
    op.create_index(
        "ix_housing_stays_person_status_time",
        "housing_stays",
        [
            "person_id",
            "status",
            "planned_check_in_at",
            "planned_check_out_at",
            "id",
        ],
    )
    op.create_index(
        "ix_housing_stays_cohort_status",
        "housing_stays",
        ["cohort_id", "status", "planned_check_in_at", "id"],
    )


def _create_transfers_table() -> None:
    op.create_table(
        "housing_transfers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("from_stay_id", sa.UUID(), nullable=False),
        sa.Column("to_stay_id", sa.UUID(), nullable=False),
        sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("transferred_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_stay_id <> to_stay_id",
            name="ck_housing_transfers_distinct_stays",
        ),
        sa.CheckConstraint(
            "btrim(reason) <> ''",
            name="ck_housing_transfers_reason",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["from_stay_id"],
            ["housing_stays.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_stay_id"],
            ["housing_stays.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["transferred_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_stay_id",
            name="uq_housing_transfers_from_stay",
        ),
        sa.UniqueConstraint(
            "to_stay_id",
            name="uq_housing_transfers_to_stay",
        ),
    )


def _seed_permissions_and_grants(bind: sa.Connection) -> None:
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    for key, (permission_id, name, sensitive) in HOUSING_OCCUPANCY_PERMISSIONS.items():
        bind.execute(
            permissions.insert().values(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )

    role_ids: dict[str, UUID] = {
        key: role_id
        for key, role_id in bind.execute(sa.text("SELECT key, id FROM roles")).tuples()
    }
    permission_ids: dict[str, UUID] = {
        key: permission_id
        for key, permission_id in bind.execute(
            sa.text("SELECT key, id FROM permissions")
        ).tuples()
    }
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for role_key, permission_key, scope_kind in DEFAULT_GRANTS:
        bind.execute(
            role_permissions.insert().values(
                role_id=role_ids[role_key],
                permission_id=permission_ids[permission_key],
                scope_kind=scope_kind,
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    _preflight_upgrade(bind)
    _create_room_issues_table()
    _create_stays_table()
    _create_transfers_table()
    _seed_permissions_and_grants(bind)


def _preflight_downgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE housing_transfers, housing_room_issues, housing_stays, "
            "permissions, role_permissions, roles, user_permission_overrides, "
            "cohort_staff_assignment_permissions IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    has_business_data = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM housing_room_issues) "
            "OR EXISTS (SELECT 1 FROM housing_stays) "
            "OR EXISTS (SELECT 1 FROM housing_transfers)"
        )
    )
    if has_business_data:
        raise RuntimeError("cannot downgrade housing occupancy with business data")

    permission_ids = [values[0] for values in HOUSING_OCCUPANCY_PERMISSIONS.values()]
    permission_keys = list(HOUSING_OCCUPANCY_PERMISSIONS)
    permission_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:ids AS uuid[])) "
                "OR key = ANY(CAST(:keys AS text[]))"
            ),
            {"ids": permission_ids, "keys": permission_keys},
        ).tuples()
    )
    expected_permission_rows = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in (
            HOUSING_OCCUPANCY_PERMISSIONS.items()
        )
    }
    if permission_rows != expected_permission_rows:
        raise RuntimeError("cannot downgrade housing occupancy with customized grants")

    grants = _role_permission_grants(bind)
    expected_new_permission_grants = {
        ("admin", permission_key, "global")
        for permission_key in HOUSING_OCCUPANCY_PERMISSIONS
    }
    actual_new_permission_grants = {
        grant for grant in grants if grant[1] in HOUSING_OCCUPANCY_PERMISSIONS
    }
    if (
        not set(DEFAULT_GRANTS) <= grants
        or actual_new_permission_grants != expected_new_permission_grants
    ):
        raise RuntimeError("cannot downgrade housing occupancy with customized grants")

    invalid_override = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"ids": permission_ids},
    )
    invalid_assignment_permission = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"ids": permission_ids},
    )
    if invalid_override or invalid_assignment_permission:
        raise RuntimeError("cannot downgrade housing occupancy with customized grants")


def _delete_permissions_and_grants(bind: sa.Connection) -> None:
    for role_key, permission_key, scope_kind in DEFAULT_GRANTS:
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions USING roles, permissions "
                "WHERE role_permissions.role_id = roles.id "
                "AND role_permissions.permission_id = permissions.id "
                "AND roles.key = :role_key "
                "AND permissions.key = :permission_key "
                "AND role_permissions.scope_kind = :scope_kind"
            ),
            {
                "role_key": role_key,
                "permission_key": permission_key,
                "scope_kind": scope_kind,
            },
        )
    bind.execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {
            "ids": [
                values[0] for values in HOUSING_OCCUPANCY_PERMISSIONS.values()
            ]
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)
    _delete_permissions_and_grants(bind)

    op.drop_table("housing_transfers")
    op.drop_table("housing_room_issues")
    op.drop_table("housing_stays")
