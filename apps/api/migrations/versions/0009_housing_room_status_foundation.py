"""Add housing room-status foundation.

Revision ID: 0009_housing_room_status_foundation
Revises: 0008_evaluation_data_media_foundation
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.housing.seed_inventory import (
    CORRECTION_SUMMARY,
    IMPORT_BATCH_ID,
    INITIAL_ROOMS,
    SOURCE_NAME,
    SOURCE_SHA256,
    building_values,
    floor_values,
    room_values,
)

revision: str = "0009_housing_room_status_foundation"
down_revision: str | Sequence[str] | None = "0008_evaluation_data_media_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HOUSING_PERMISSIONS = {
    "housing.read": (
        UUID("20000000-0000-4000-8000-000000000037"),
        "查看房态",
        False,
    ),
    "housing.rooms.manage": (
        UUID("20000000-0000-4000-8000-000000000038"),
        "管理房间目录",
        True,
    ),
}

def _extend_scope_constraints() -> None:
    op.drop_constraint(
        "ck_role_permissions_scope_kind",
        "role_permissions",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        type_="check",
    )
    op.create_check_constraint(
        "ck_role_permissions_scope_kind",
        "role_permissions",
        "scope_kind IN ("
        "'global', 'self', 'family', 'person', 'assigned_class', "
        "'building', 'floor'"
        ")",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        "scope_kind IN ("
        "'global', 'self', 'family', 'person', 'assigned_class', "
        "'cohort', 'project', 'enrollment', 'building', 'floor'"
        ")",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
        "(scope_kind IN ("
        "'person', 'assigned_class', 'cohort', 'project', 'enrollment', "
        "'building', 'floor'"
        ") AND scope_id IS NOT NULL)",
    )


def _create_tables() -> None:
    op.create_table(
        "housing_buildings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint("btrim(code) <> ''", name="ck_housing_buildings_code"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_housing_buildings_name"),
        sa.CheckConstraint(
            "display_order >= 0",
            name="ck_housing_buildings_display_order",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        "ix_housing_buildings_display_order",
        "housing_buildings",
        ["display_order", "code", "id"],
    )

    op.create_table(
        "housing_floors",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("building_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
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
            "number >= -20 AND number <= 200",
            name="ck_housing_floors_number",
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_housing_floors_name"),
        sa.CheckConstraint(
            "display_order >= 0",
            name="ck_housing_floors_display_order",
        ),
        sa.ForeignKeyConstraint(
            ["building_id"],
            ["housing_buildings.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "building_id",
            name="uq_housing_floors_id_building",
        ),
        sa.UniqueConstraint(
            "building_id",
            "number",
            name="uq_housing_floors_building_number",
        ),
        sa.UniqueConstraint(
            "building_id",
            "name",
            name="uq_housing_floors_building_name",
        ),
    )
    op.create_index(
        "ix_housing_floors_display_order",
        "housing_floors",
        ["building_id", "display_order", "number", "id"],
    )

    op.create_table(
        "housing_rooms",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("building_id", sa.UUID(), nullable=False),
        sa.Column("floor_id", sa.UUID(), nullable=False),
        sa.Column("room_number", sa.String(length=32), nullable=False),
        sa.Column("normalized_key", sa.String(length=32), nullable=False),
        sa.Column("room_type", sa.String(length=24), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("area_square_meters", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("service_status", sa.String(length=24), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
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
        sa.CheckConstraint("btrim(room_number) <> ''", name="ck_housing_rooms_number"),
        sa.CheckConstraint("btrim(normalized_key) <> ''", name="ck_housing_rooms_key"),
        sa.CheckConstraint(
            "room_type IN ('single', 'double', 'triple', 'custom', 'functional')",
            name="ck_housing_rooms_type",
        ),
        sa.CheckConstraint(
            "(room_type = 'single' AND capacity = 1) OR "
            "(room_type = 'double' AND capacity = 2) OR "
            "(room_type = 'triple' AND capacity = 3) OR "
            "(room_type = 'custom' AND capacity BETWEEN 1 AND 100) OR "
            "(room_type = 'functional' AND capacity = 0)",
            name="ck_housing_rooms_type_capacity",
        ),
        sa.CheckConstraint(
            "area_square_meters IS NULL OR area_square_meters > 0",
            name="ck_housing_rooms_area",
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending_verification', 'verified')",
            name="ck_housing_rooms_verification",
        ),
        sa.CheckConstraint(
            "service_status IN ('available', 'problem', 'maintenance', 'disabled')",
            name="ck_housing_rooms_service_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_housing_rooms_version"),
        sa.ForeignKeyConstraint(
            ["floor_id", "building_id"],
            ["housing_floors.id", "housing_floors.building_id"],
            name="fk_housing_rooms_floor_building",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "building_id",
            "normalized_key",
            name="uq_housing_rooms_building_key",
        ),
    )
    op.create_index(
        "ix_housing_rooms_floor_number",
        "housing_rooms",
        ["floor_id", "room_number", "id"],
    )
    op.create_index(
        "ix_housing_rooms_verification_status",
        "housing_rooms",
        ["verification_status", "id"],
    )
    op.create_index(
        "ix_housing_rooms_service_status",
        "housing_rooms",
        ["service_status", "id"],
    )
    op.create_index(
        "ix_housing_rooms_type",
        "housing_rooms",
        ["room_type", "id"],
    )

    op.create_table(
        "housing_import_batches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column(
            "correction_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("imported_by_user_id", sa.UUID(), nullable=True),
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
            "btrim(source_name) <> ''",
            name="ck_housing_import_source_name",
        ),
        sa.CheckConstraint(
            "source_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_housing_import_sha",
        ),
        sa.CheckConstraint("row_count >= 0", name="ck_housing_import_row_count"),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_sha256"),
    )


def _seed_permissions(bind: sa.Connection) -> None:
    permission_ids = [values[0] for values in HOUSING_PERMISSIONS.values()]
    permission_keys = list(HOUSING_PERMISSIONS)
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
            "cannot upgrade housing foundation with preexisting permission seeds"
        )

    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    for key, (permission_id, name, sensitive) in HOUSING_PERMISSIONS.items():
        bind.execute(
            permissions.insert().values(
                id=permission_id,
                key=key,
                name=name,
                sensitive=sensitive,
            )
        )

    admin_role_id = bind.scalar(sa.text("SELECT id FROM roles WHERE key = 'admin'"))
    if admin_role_id is None:
        raise RuntimeError("cannot upgrade housing foundation without admin role")
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for permission_id, _, _ in HOUSING_PERMISSIONS.values():
        bind.execute(
            role_permissions.insert().values(
                role_id=admin_role_id,
                permission_id=permission_id,
                scope_kind="global",
            )
        )


def _seed_catalog(bind: sa.Connection) -> None:
    buildings = sa.table(
        "housing_buildings",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("notes", sa.String()),
        sa.column("created_by_user_id", sa.Uuid()),
        sa.column("updated_by_user_id", sa.Uuid()),
    )
    floors = sa.table(
        "housing_floors",
        sa.column("id", sa.Uuid()),
        sa.column("building_id", sa.Uuid()),
        sa.column("number", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("display_order", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("notes", sa.String()),
        sa.column("created_by_user_id", sa.Uuid()),
        sa.column("updated_by_user_id", sa.Uuid()),
    )
    rooms = sa.table(
        "housing_rooms",
        sa.column("id", sa.Uuid()),
        sa.column("building_id", sa.Uuid()),
        sa.column("floor_id", sa.Uuid()),
        sa.column("room_number", sa.String()),
        sa.column("normalized_key", sa.String()),
        sa.column("room_type", sa.String()),
        sa.column("capacity", sa.Integer()),
        sa.column("area_square_meters", sa.Numeric()),
        sa.column("verification_status", sa.String()),
        sa.column("service_status", sa.String()),
        sa.column("notes", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("created_by_user_id", sa.Uuid()),
        sa.column("updated_by_user_id", sa.Uuid()),
    )
    imports = sa.table(
        "housing_import_batches",
        sa.column("id", sa.Uuid()),
        sa.column("source_name", sa.String()),
        sa.column("source_sha256", sa.String()),
        sa.column("row_count", sa.Integer()),
        sa.column("correction_summary", postgresql.JSONB()),
        sa.column("imported_by_user_id", sa.Uuid()),
    )
    bind.execute(buildings.insert(), building_values())
    bind.execute(floors.insert(), floor_values())
    bind.execute(rooms.insert(), room_values())
    bind.execute(
        imports.insert().values(
            id=IMPORT_BATCH_ID,
            source_name=SOURCE_NAME,
            source_sha256=SOURCE_SHA256,
            row_count=len(INITIAL_ROOMS),
            correction_summary=CORRECTION_SUMMARY,
            imported_by_user_id=None,
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _extend_scope_constraints()
    _create_tables()
    _seed_permissions(bind)
    _seed_catalog(bind)


def _expected_catalog_is_unchanged(bind: sa.Connection) -> bool:
    building_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, code, name, display_order, is_active, notes, "
                "created_by_user_id, updated_by_user_id FROM housing_buildings"
            )
        ).tuples()
    )
    expected_buildings = {
        (
            row["id"],
            row["code"],
            row["name"],
            row["display_order"],
            row["is_active"],
            row["notes"],
            row["created_by_user_id"],
            row["updated_by_user_id"],
        )
        for row in building_values()
    }
    floor_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, building_id, number, name, display_order, is_active, notes, "
                "created_by_user_id, updated_by_user_id FROM housing_floors"
            )
        ).tuples()
    )
    expected_floors = {
        (
            row["id"],
            row["building_id"],
            row["number"],
            row["name"],
            row["display_order"],
            row["is_active"],
            row["notes"],
            row["created_by_user_id"],
            row["updated_by_user_id"],
        )
        for row in floor_values()
    }
    room_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, building_id, floor_id, room_number, normalized_key, "
                "room_type, capacity, area_square_meters, verification_status, "
                "service_status, notes, version, created_by_user_id, "
                "updated_by_user_id FROM housing_rooms"
            )
        ).tuples()
    )
    expected_rooms = {
        (
            row["id"],
            row["building_id"],
            row["floor_id"],
            row["room_number"],
            row["normalized_key"],
            row["room_type"],
            row["capacity"],
            row["area_square_meters"],
            row["verification_status"],
            row["service_status"],
            row["notes"],
            row["version"],
            row["created_by_user_id"],
            row["updated_by_user_id"],
        )
        for row in room_values()
    }
    import_rows = list(
        bind.execute(
            sa.text(
                "SELECT id, source_name, source_sha256, row_count, "
                "correction_summary, imported_by_user_id "
                "FROM housing_import_batches"
            )
        ).mappings()
    )
    expected_import = {
        "id": IMPORT_BATCH_ID,
        "source_name": SOURCE_NAME,
        "source_sha256": SOURCE_SHA256,
        "row_count": len(INITIAL_ROOMS),
        "correction_summary": CORRECTION_SUMMARY,
        "imported_by_user_id": None,
    }
    return (
        building_rows == expected_buildings
        and floor_rows == expected_floors
        and room_rows == expected_rooms
        and len(import_rows) == 1
        and dict(import_rows[0]) == expected_import
    )


def _preflight_downgrade(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            "LOCK TABLE housing_import_batches, housing_rooms, housing_floors, "
            "housing_buildings, permissions, role_permissions, roles, "
            "user_permission_overrides, cohort_staff_assignment_permissions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    if not _expected_catalog_is_unchanged(bind):
        raise RuntimeError(
            "cannot downgrade housing foundation with customized housing data"
        )

    permission_ids = [values[0] for values in HOUSING_PERMISSIONS.values()]
    permission_keys = list(HOUSING_PERMISSIONS)
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
    expected_permissions = {
        (permission_id, key, name, sensitive)
        for key, (permission_id, name, sensitive) in HOUSING_PERMISSIONS.items()
    }
    if permission_rows != expected_permissions:
        raise RuntimeError(
            "cannot downgrade housing foundation with customized housing data"
        )

    grant_rows = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id "
                "WHERE permissions.id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": permission_ids},
        ).tuples()
    )
    expected_grants = {
        ("admin", permission_key, "global")
        for permission_key in HOUSING_PERMISSIONS
    }
    invalid_override = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM user_permission_overrides "
            "WHERE scope_kind IN ('building', 'floor') "
            "OR permission_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"ids": permission_ids},
    )
    invalid_role_scope = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM role_permissions "
            "WHERE scope_kind IN ('building', 'floor'))"
        )
    )
    invalid_assignment_permission = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[])))"
        ),
        {"ids": permission_ids},
    )
    if (
        grant_rows != expected_grants
        or invalid_override
        or invalid_role_scope
        or invalid_assignment_permission
    ):
        raise RuntimeError(
            "cannot downgrade housing foundation with customized housing data"
        )


def _restore_scope_constraints() -> None:
    op.drop_constraint(
        "ck_role_permissions_scope_kind",
        "role_permissions",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        type_="check",
    )
    op.drop_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        type_="check",
    )
    op.create_check_constraint(
        "ck_role_permissions_scope_kind",
        "role_permissions",
        "scope_kind IN ('global', 'self', 'family', 'person', 'assigned_class')",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_kind",
        "user_permission_overrides",
        "scope_kind IN ("
        "'global', 'self', 'family', 'person', 'assigned_class', "
        "'cohort', 'project', 'enrollment'"
        ")",
    )
    op.create_check_constraint(
        "ck_permission_overrides_scope_id",
        "user_permission_overrides",
        "(scope_kind IN ('global', 'self', 'family') AND scope_id IS NULL) OR "
        "(scope_kind IN ("
        "'person', 'assigned_class', 'cohort', 'project', 'enrollment'"
        ") AND scope_id IS NOT NULL)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    _preflight_downgrade(bind)

    op.drop_table("housing_import_batches")
    op.drop_table("housing_rooms")
    op.drop_table("housing_floors")
    op.drop_table("housing_buildings")

    permission_ids = [values[0] for values in HOUSING_PERMISSIONS.values()]
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE permission_id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": permission_ids},
    )
    bind.execute(
        sa.text("DELETE FROM permissions WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": permission_ids},
    )
    _restore_scope_constraints()
