"""Housing property approval requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0083_housing_property_changes"
down_revision = "0082_housing_room_cards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO roles(id,key,name) VALUES ('10000000-0000-4000-8000-000000000014','property','物业') ON CONFLICT(key) DO NOTHING"
        )
    )
    permissions = [
        ("20000000-0000-4000-8000-000000000094", "housing.changes.submit", "提交房态变更"),
        ("20000000-0000-4000-8000-000000000095", "housing.changes.review", "审核房态变更"),
        ("20000000-0000-4000-8000-000000000096", "housing.keycards.manage", "管理房卡日期"),
        ("20000000-0000-4000-8000-000000000097", "housing.exports.basic", "导出基本住宿清单"),
    ]
    for identifier, key, name in permissions:
        connection.execute(
            sa.text(
                "INSERT INTO permissions(id,key,name,sensitive) VALUES (CAST(:id AS uuid),:key,:name,true) ON CONFLICT(key) DO NOTHING"
            ),
            {"id": identifier, "key": key, "name": name},
        )
    grants = {
        "property": (
            "housing.read",
            "housing.changes.submit",
            "housing.keycards.manage",
            "housing.exports.basic",
        ),
        "administration": (
            "housing.changes.review",
            "housing.keycards.manage",
            "housing.exports.basic",
        ),
        "super_admin": tuple(item[1] for item in permissions),
    }
    for role, keys in grants.items():
        for key in keys:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions(role_id,permission_id,scope_kind) SELECT r.id,p.id,'global' FROM roles r CROSS JOIN permissions p WHERE r.key=:role AND p.key=:permission ON CONFLICT DO NOTHING"
                ),
                {"role": role, "permission": key},
            )
    op.create_table(
        "housing_property_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "room_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("housing_rooms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
        sa.Column("before", postgresql.JSONB(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "submitted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user_accounts.id", ondelete="RESTRICT"),
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_note", sa.Text()),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','withdrawn')",
            name="ck_housing_property_changes_status",
        ),
        sa.CheckConstraint("base_version >= 1", name="ck_housing_property_changes_version"),
        sa.CheckConstraint("btrim(reason) <> ''", name="ck_housing_property_changes_reason"),
        sa.CheckConstraint(
            "(status = 'pending' AND reviewed_at IS NULL AND reviewed_by_user_id IS NULL) OR (status <> 'pending' AND reviewed_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL)",
            name="ck_housing_property_changes_review",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR reviewed_by_user_id <> submitted_by_user_id",
            name="ck_housing_property_changes_not_self",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR (review_note IS NOT NULL AND btrim(review_note) <> '')",
            name="ck_housing_property_changes_rejection",
        ),
    )
    op.create_index(
        "uq_housing_property_changes_pending_room",
        "housing_property_changes",
        ["room_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_housing_property_changes_status_submitted",
        "housing_property_changes",
        ["status", "submitted_at", "id"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE housing_property_changes, roles, permissions, role_permissions, user_roles, user_permission_overrides IN ACCESS EXCLUSIVE MODE"
        )
    )
    permission_keys = (
        "housing.changes.submit",
        "housing.changes.review",
        "housing.keycards.manage",
        "housing.exports.basic",
    )
    expected_grants = {
        ("property", "housing.read", "global"),
        ("property", "housing.changes.submit", "global"),
        ("property", "housing.keycards.manage", "global"),
        ("property", "housing.exports.basic", "global"),
        ("administration", "housing.changes.review", "global"),
        ("administration", "housing.keycards.manage", "global"),
        ("administration", "housing.exports.basic", "global"),
        *[("super_admin", permission, "global") for permission in permission_keys],
    }
    existing_grants = connection.execute(
        sa.text(
            "SELECT r.key,p.key,rp.scope_kind FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE r.key='property' OR p.key IN ('housing.changes.submit','housing.changes.review','housing.keycards.manage','housing.exports.basic')"
        )
    )
    if any(tuple(grant) not in expected_grants for grant in existing_grants):
        raise RuntimeError("cannot discard custom property permission grants")
    if connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id WHERE r.key='property') OR EXISTS(SELECT 1 FROM user_permission_overrides u JOIN permissions p ON p.id=u.permission_id WHERE p.key IN ('housing.changes.submit','housing.changes.review','housing.keycards.manage','housing.exports.basic'))"
        )
    ).scalar():
        raise RuntimeError("cannot discard property role assignments or permission overrides")
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM housing_property_changes)")
    ).scalar():
        raise RuntimeError("cannot discard property approval history")
    op.drop_table("housing_property_changes")
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE key='property') OR permission_id IN (SELECT id FROM permissions WHERE key IN ('housing.changes.submit','housing.changes.review','housing.keycards.manage','housing.exports.basic'))"
        )
    )
    connection.execute(
        sa.text(
            "DELETE FROM permissions WHERE key IN ('housing.changes.submit','housing.changes.review','housing.keycards.manage','housing.exports.basic')"
        )
    )
    connection.execute(sa.text("DELETE FROM roles WHERE key='property'"))
