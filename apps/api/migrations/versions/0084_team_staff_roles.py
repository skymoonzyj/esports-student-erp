"""Add primary team staff classifications and minimal assistant coach access."""

import sqlalchemy as sa
from alembic import op

revision = "0084_team_staff_roles"
down_revision = "0083_housing_property_changes"
branch_labels = None
depends_on = None

ASSISTANT_ROLE_ID = "10000000-0000-4000-8000-000000000015"
LEGACY_STAFF_TYPES = "'coach','life_teacher','customer_service','administration','other'"
DEFAULT_ASSISTANT_GRANTS = {
    ("team_memberships.read", "assigned_team"),
    ("personnel_intake.housing.read", "global"),
}


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE staff_profiles,roles,permissions,role_permissions,user_roles "
        "IN ACCESS EXCLUSIVE MODE"
    ))
    # Without provenance, an existing identical grant cannot be distinguished
    # from this revision's default on downgrade. Refuse before changing anything.
    if connection.scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM role_permissions rp "
        "JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id "
        "WHERE r.key='coach' AND p.key='team_memberships.read' "
        "AND rp.scope_kind='assigned_team')"
    )):
        raise RuntimeError("cannot migrate pre-existing coach roster grant without provenance")
    op.drop_constraint("ck_staff_profiles_staff_type", "staff_profiles", type_="check")
    op.create_check_constraint(
        "ck_staff_profiles_staff_type", "staff_profiles",
        f"staff_type IN ({LEGACY_STAFF_TYPES},'team_manager','assistant_coach')",
    )
    connection.execute(
        sa.text("INSERT INTO roles(id,key,name) VALUES (CAST(:id AS uuid),'assistant_coach','助教')"),
        {"id": ASSISTANT_ROLE_ID},
    )
    for permission, scope in sorted(DEFAULT_ASSISTANT_GRANTS):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions(role_id,permission_id,scope_kind) "
                "SELECT CAST(:id AS uuid),p.id,:scope FROM permissions p WHERE p.key=:permission"
            ),
            {"id": ASSISTANT_ROLE_ID, "permission": permission, "scope": scope},
        )
    connection.execute(sa.text(
        "INSERT INTO role_permissions(role_id,permission_id,scope_kind) "
        "SELECT r.id,p.id,'assigned_team' FROM roles r CROSS JOIN permissions p "
        "WHERE r.key='coach' AND p.key='team_memberships.read'"
    ))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE staff_profiles,roles,permissions,role_permissions,user_roles "
        "IN ACCESS EXCLUSIVE MODE"
    ))
    if connection.scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM staff_profiles "
        "WHERE staff_type IN ('team_manager','assistant_coach'))"
    )):
        raise RuntimeError("cannot discard new team staff classifications")
    if connection.scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id "
        "WHERE r.key='assistant_coach')"
    )):
        raise RuntimeError("cannot discard assistant coach role assignments")
    grants = connection.execute(sa.text(
        "SELECT p.key,rp.scope_kind FROM role_permissions rp "
        "JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id "
        "WHERE r.key='assistant_coach'"
    ))
    if any(tuple(grant) not in DEFAULT_ASSISTANT_GRANTS for grant in grants):
        raise RuntimeError("cannot discard custom assistant coach permission grants")
    connection.execute(sa.text(
        "DELETE FROM role_permissions WHERE role_id IN "
        "(SELECT id FROM roles WHERE key='assistant_coach')"
    ))
    connection.execute(sa.text(
        "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE key='coach') "
        "AND permission_id IN (SELECT id FROM permissions WHERE key='team_memberships.read') "
        "AND scope_kind='assigned_team'"
    ))
    connection.execute(sa.text("DELETE FROM roles WHERE key='assistant_coach'"))
    op.drop_constraint("ck_staff_profiles_staff_type", "staff_profiles", type_="check")
    op.create_check_constraint(
        "ck_staff_profiles_staff_type", "staff_profiles", f"staff_type IN ({LEGACY_STAFF_TYPES})",
    )
