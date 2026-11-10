"""Add administration's global team-business authority without changing overrides."""

import sqlalchemy as sa
from alembic import op

revision = "0090_administration_team_mgmt"
down_revision = "0089_quick_intake_housing"
branch_labels = None
depends_on = None

TEAM_PERMISSIONS = (
    "professional_teams.manage",
    "team_memberships.read",
    "team_memberships.manage",
    "team_memberships.transfer",
    "team_manager_assignments.manage",
)


def upgrade() -> None:
    connection = op.get_bind()
    for permission in TEAM_PERMISSIONS:
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions(role_id,permission_id,scope_kind) "
                "SELECT r.id,p.id,'global' FROM roles r CROSS JOIN permissions p "
                "WHERE r.key='administration' AND p.key=:permission "
                "ON CONFLICT DO NOTHING"
            ),
            {"permission": permission},
        )


def downgrade() -> None:
    # As in 0088, role_permissions has no grant provenance. Even an identical
    # grant may predate this revision, so a code rollback must not revoke it.
    # Revoking approved business access requires an explicit permission change.
    pass
