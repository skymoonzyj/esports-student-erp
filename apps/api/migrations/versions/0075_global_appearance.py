"""Add a global singleton appearance and super-administrator-only capability."""

import sqlalchemy as sa
from alembic import op

revision = "0075_global_appearance"
down_revision = "0074_person_profile_assignments"
branch_labels = None
depends_on = None

PERMISSION_ID = "20000000-0000-4000-8000-000000000091"
PERMISSION_KEY = "operations.appearance.manage"


def upgrade() -> None:
    op.create_table(
        "global_appearance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("theme", sa.String(16), nullable=False, server_default="clear"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("id = 1", name="ck_global_appearance_singleton"),
        sa.CheckConstraint("theme IN ('clear', 'command')", name="ck_global_appearance_theme"),
        sa.CheckConstraint("revision >= 1", name="ck_global_appearance_revision"),
    )
    op.execute("INSERT INTO global_appearance (id,theme,revision) VALUES (1,'clear',1)")
    op.execute(
        sa.text(
            "INSERT INTO permissions (id,key,name,sensitive) VALUES (CAST(:id AS uuid),:key,'管理全局系统外观',true)"
        ).bindparams(id=PERMISSION_ID, key=PERMISSION_KEY)
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id,permission_id,scope_kind) SELECT id,CAST(:id AS uuid),'global' FROM roles WHERE key='super_admin'"
        ).bindparams(id=PERMISSION_ID)
    )


def downgrade() -> None:
    # Remove only this migration's default grant. RESTRICT FKs intentionally
    # refuse downgrade if custom grants/overrides would otherwise be discarded.
    op.execute(
        sa.text(
            "DELETE FROM role_permissions rp USING roles r,permissions p WHERE rp.role_id=r.id AND rp.permission_id=p.id AND r.key='super_admin' AND rp.scope_kind='global' AND p.id=CAST(:id AS uuid) AND p.key=:key"
        ).bindparams(id=PERMISSION_ID, key=PERMISSION_KEY)
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE id=CAST(:id AS uuid) AND key=:key").bindparams(
            id=PERMISSION_ID, key=PERMISSION_KEY
        )
    )
    op.drop_table("global_appearance")
