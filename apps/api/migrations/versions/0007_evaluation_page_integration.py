"""Add evaluation page entry permission and bridge tables.

Revision ID: 0007_evaluation_page_integration
Revises: 0006_coach_family_portal
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "0007_evaluation_page_integration"
down_revision: str | Sequence[str] | None = "0006_coach_family_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVALUATION_ACCESS_PERMISSION_ID = UUID("20000000-0000-4000-8000-000000000026")
EVALUATION_ACCESS_GRANTS = (
    ("admin", "evaluation_center.access", "global"),
    ("coach", "evaluation_center.access", "global"),
    ("life_teacher", "evaluation_center.access", "global"),
    ("staff", "evaluation_center.access", "global"),
    ("guardian", "evaluation_center.access", "global"),
    ("student", "evaluation_center.access", "global"),
)


def _evaluation_grants(bind: sa.Connection) -> set[tuple[str, str]]:
    return set(
        bind.execute(
            sa.text(
                "SELECT roles.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "WHERE role_permissions.permission_id = :permission_id "
                "FOR UPDATE OF role_permissions"
            ),
            {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
        ).tuples()
    )


def upgrade() -> None:
    bind = op.get_bind()
    permission_exists = bind.scalar(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM permissions "
            "WHERE id = :permission_id OR key = 'evaluation_center.access')"
        ),
        {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
    )
    if permission_exists:
        raise RuntimeError(
            "cannot upgrade evaluation page with preexisting evaluation access permission"
        )

    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("sensitive", sa.Boolean()),
    )
    bind.execute(
        permissions.insert().values(
            id=EVALUATION_ACCESS_PERMISSION_ID,
            key="evaluation_center.access",
            name="进入评价中心",
            sensitive=False,
        )
    )

    role_ids: dict[str, UUID] = {
        key: role_id
        for key, role_id in bind.execute(sa.text("SELECT key, id FROM roles")).tuples()
    }
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("scope_kind", sa.String()),
    )
    for role_key, _permission_key, scope_kind in EVALUATION_ACCESS_GRANTS:
        bind.execute(
            role_permissions.insert().values(
                role_id=role_ids[role_key],
                permission_id=EVALUATION_ACCESS_PERMISSION_ID,
                scope_kind=scope_kind,
            )
        )

    op.create_table(
        "evaluation_bridge_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("state_ciphertext", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "mode IN ('standalone', 'embedded')",
            name="ck_evaluation_bridge_requests_mode",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_bridge_requests_expiry",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_evaluation_bridge_requests_expiry",
        "evaluation_bridge_requests",
        ["expires_at", "id"],
    )
    op.create_table(
        "evaluation_bridge_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("login_request_id", sa.Uuid(), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_evaluation_bridge_tickets_expiry",
        ),
        sa.ForeignKeyConstraint(
            ["login_request_id"],
            ["evaluation_bridge_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["auth_session_id"],
            ["auth_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("login_request_id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index(
        "ix_evaluation_bridge_tickets_expiry",
        "evaluation_bridge_tickets",
        ["expires_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    permission_row = bind.execute(
        sa.text(
            "SELECT id, key, name, sensitive FROM permissions "
            "WHERE id = :permission_id FOR UPDATE"
        ),
        {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
    ).mappings().one_or_none()
    if permission_row is None or dict(permission_row) != {
        "id": EVALUATION_ACCESS_PERMISSION_ID,
        "key": "evaluation_center.access",
        "name": "进入评价中心",
        "sensitive": False,
    }:
        raise RuntimeError("cannot downgrade evaluation page with modified permission seed")

    override_id = bind.scalar(
        sa.text(
            "SELECT id FROM user_permission_overrides "
            "WHERE permission_id = :permission_id LIMIT 1 FOR UPDATE"
        ),
        {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
    )
    if override_id is not None:
        raise RuntimeError(
            "cannot downgrade evaluation page with account permission overrides"
        )

    expected_grants = {
        (role_key, scope_kind)
        for role_key, _permission_key, scope_kind in EVALUATION_ACCESS_GRANTS
    }
    if _evaluation_grants(bind) != expected_grants:
        raise RuntimeError("cannot downgrade evaluation page with customized grants")

    op.drop_index(
        "ix_evaluation_bridge_tickets_expiry",
        table_name="evaluation_bridge_tickets",
    )
    op.drop_table("evaluation_bridge_tickets")
    op.drop_index(
        "ix_evaluation_bridge_requests_expiry",
        table_name="evaluation_bridge_requests",
    )
    op.drop_table("evaluation_bridge_requests")

    for role_key, permission_key, scope_kind in EVALUATION_ACCESS_GRANTS:
        deleted_grant = bind.execute(
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
        if deleted_grant.rowcount != 1:
            raise RuntimeError("cannot downgrade evaluation page with customized grants")
    deleted_permission = bind.execute(
        sa.text(
            "DELETE FROM permissions WHERE id = :permission_id "
            "AND key = 'evaluation_center.access' "
            "AND name = '进入评价中心' AND sensitive IS FALSE"
        ),
        {"permission_id": EVALUATION_ACCESS_PERMISSION_ID},
    )
    if deleted_permission.rowcount != 1:
        raise RuntimeError("cannot downgrade evaluation page with modified permission seed")
