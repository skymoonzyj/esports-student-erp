"""Freeze authorization sources and preserve legacy encrypted envelopes verbatim."""

import sqlalchemy as sa
from alembic import op

revision = "0064_cloud_auth_builtin_import"
down_revision = "0063_cloud_authorization"
branch_labels = None
depends_on = None

TABLES = ("cloud_auth_sessions", "cloud_auth_operations", "cloud_auth_candidates")
TIME = sa.DateTime(timezone=True)
OLD_STATES = "'creating', 'awaiting_confirmation', 'authorized', 'configuring', 'test_pending', 'ready', 'applying', 'applied', 'expired', 'cancelled', 'failed'"
OLD_ACTIONS = "'create_qr', 'poll', 'prepare', 'list_directories', 'select_directory', 'probe', 'apply', 'cleanup', 'unbind'"


def _source_check(*, application=False):
    own = "mode = 'own_app' AND app_revision IS NOT NULL AND app_revision >= 1 AND auth_config_revision = app_revision AND auth_config_id = 'own-app:' || provider"
    builtin = "mode = 'openlist_builtin_import' AND app_revision IS NULL AND auth_config_revision = 1 AND auth_config_id = 'openlist-builtin-import-v1:' || provider"
    if application:
        own += " AND application_provider IS NOT NULL AND application_provider = provider"
        builtin += " AND application_provider IS NULL"
    return (
        "mode IS NOT NULL AND auth_config_id IS NOT NULL AND auth_config_revision IS NOT NULL AND auth_config_revision > 0 AND "
        "protocol_version IS NOT NULL AND protocol_version IN (1, 2) AND (protocol_version = 2 OR mode = 'own_app') AND "
        f"(({own}) OR ({builtin}))"
    )


def upgrade():
    for table in TABLES:
        # No decryption occurs here: v1 identifies the existing envelope format.
        op.add_column(
            table, sa.Column("mode", sa.String(32), nullable=False, server_default="own_app")
        )
        op.add_column(
            table, sa.Column("protocol_version", sa.Integer(), nullable=False, server_default="1")
        )
        op.add_column(table, sa.Column("auth_config_id", sa.String(80)))
        op.add_column(table, sa.Column("auth_config_revision", sa.Integer()))
    op.add_column("cloud_auth_candidates", sa.Column("app_revision", sa.Integer()))
    op.execute("""UPDATE cloud_auth_candidates c SET app_revision=s.app_revision
                  FROM cloud_auth_sessions s WHERE c.session_id=s.id""")
    for table in TABLES:
        op.execute(
            sa.text(
                f"UPDATE {table} SET auth_config_id='own-app:' || provider, auth_config_revision=app_revision"
            )
        )
        op.alter_column(table, "auth_config_id", nullable=False)
        op.alter_column(table, "auth_config_revision", nullable=False)
        op.alter_column(table, "protocol_version", server_default="2")

    op.add_column("cloud_auth_sessions", sa.Column("application_provider", sa.String(16)))
    op.execute("UPDATE cloud_auth_sessions SET application_provider=provider")
    # 0063 used an unnamed FK; discover its actual server-assigned name.
    inspector = sa.inspect(op.get_bind())
    for foreign_key in inspector.get_foreign_keys("cloud_auth_sessions"):
        if foreign_key["constrained_columns"] == ["provider"]:
            op.drop_constraint(foreign_key["name"], "cloud_auth_sessions", type_="foreignkey")
    op.create_foreign_key(
        "fk_cloud_auth_session_application",
        "cloud_auth_sessions",
        "cloud_auth_applications",
        ["application_provider"],
        ["provider"],
        ondelete="RESTRICT",
    )
    for table, prefix in (
        ("cloud_auth_sessions", "session"),
        ("cloud_auth_operations", "operation"),
    ):
        op.drop_constraint(f"ck_cloud_auth_{prefix}_revisions", table, type_="check")
        op.alter_column(table, "app_revision", nullable=True)
        expression = (
            "revision >= 1 AND target_revision >= 0"
            if prefix == "session"
            else "attempts >= 0 AND session_revision >= 1 AND target_revision >= 0"
        )
        op.create_check_constraint(f"ck_cloud_auth_{prefix}_revisions", table, expression)
    for table, prefix in (
        ("cloud_auth_sessions", "session"),
        ("cloud_auth_operations", "operation"),
        ("cloud_auth_candidates", "candidate"),
    ):
        op.create_check_constraint(
            f"ck_cloud_auth_{prefix}_source", table, _source_check(application=prefix == "session")
        )

    op.add_column("cloud_auth_sessions", sa.Column("verified_at", TIME))
    op.add_column("cloud_auth_sessions", sa.Column("token_expires_at", TIME))
    op.add_column("cloud_auth_sessions", sa.Column("expiry_source", sa.String(16)))
    op.create_check_constraint(
        "ck_cloud_auth_session_token_expiry",
        "cloud_auth_sessions",
        "(expiry_source IS NULL AND token_expires_at IS NULL) OR (expiry_source IS NOT NULL AND expiry_source = 'provider' AND token_expires_at IS NOT NULL) OR (expiry_source IS NOT NULL AND expiry_source = 'unreported' AND token_expires_at IS NULL AND provider = 'baidu' AND mode = 'openlist_builtin_import')",
    )
    op.add_column(
        "cloud_auth_operations",
        sa.Column("refresh_phase", sa.String(16), nullable=False, server_default="not_started"),
    )
    op.add_column("cloud_auth_operations", sa.Column("refresh_checkpoint_ciphertext", sa.Text()))
    op.add_column("cloud_auth_operations", sa.Column("import_token_digests_ciphertext", sa.Text()))
    op.create_check_constraint(
        "ck_cloud_auth_operation_refresh_phase",
        "cloud_auth_operations",
        "refresh_phase IN ('not_started', 'sent', 'refreshed', 'verified', 'unknown')",
    )
    op.drop_constraint("ck_cloud_auth_session_state", "cloud_auth_sessions", type_="check")
    op.create_check_constraint(
        "ck_cloud_auth_session_state",
        "cloud_auth_sessions",
        f"state IN ({OLD_STATES}, 'awaiting_import', 'verifying_import')",
    )
    op.drop_constraint("ck_cloud_auth_operation_action", "cloud_auth_operations", type_="check")
    op.create_check_constraint(
        "ck_cloud_auth_operation_action",
        "cloud_auth_operations",
        f"action IN ({OLD_ACTIONS}, 'create_import', 'verify_import')",
    )
    # Existing partial index excludes only terminal states, so both new live
    # states automatically share its provider slot with the legacy mode.


def downgrade():
    bind = op.get_bind()
    # Preflight all retained data before any DDL/data mutation. New own_app v2
    # envelopes also cannot be read safely by the old binary.
    for table in TABLES:
        if bind.scalar(
            sa.text(
                f"SELECT EXISTS(SELECT 1 FROM {table} WHERE mode <> 'own_app' OR protocol_version <> 1)"
            )
        ):
            raise RuntimeError("builtin import downgrade requires no retained new protocol records")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM cloud_auth_sessions WHERE state IN ('awaiting_import','verifying_import') OR verified_at IS NOT NULL OR token_expires_at IS NOT NULL OR expiry_source IS NOT NULL)"
        )
    ):
        raise RuntimeError("builtin import downgrade requires no retained import state")
    if bind.scalar(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM cloud_auth_operations WHERE action IN ('create_import','verify_import') OR refresh_phase <> 'not_started' OR refresh_checkpoint_ciphertext IS NOT NULL OR import_token_digests_ciphertext IS NOT NULL)"
        )
    ):
        raise RuntimeError("builtin import downgrade requires no retained refresh journal")
    for table, prefix in (
        ("cloud_auth_sessions", "session"),
        ("cloud_auth_operations", "operation"),
        ("cloud_auth_candidates", "candidate"),
    ):
        op.drop_constraint(f"ck_cloud_auth_{prefix}_source", table, type_="check")
    op.drop_constraint("ck_cloud_auth_session_token_expiry", "cloud_auth_sessions", type_="check")
    op.drop_constraint(
        "ck_cloud_auth_operation_refresh_phase", "cloud_auth_operations", type_="check"
    )
    op.drop_constraint("ck_cloud_auth_session_state", "cloud_auth_sessions", type_="check")
    op.create_check_constraint(
        "ck_cloud_auth_session_state", "cloud_auth_sessions", f"state IN ({OLD_STATES})"
    )
    op.drop_constraint("ck_cloud_auth_operation_action", "cloud_auth_operations", type_="check")
    op.create_check_constraint(
        "ck_cloud_auth_operation_action", "cloud_auth_operations", f"action IN ({OLD_ACTIONS})"
    )
    for table, prefix in (
        ("cloud_auth_sessions", "session"),
        ("cloud_auth_operations", "operation"),
    ):
        op.drop_constraint(f"ck_cloud_auth_{prefix}_revisions", table, type_="check")
        op.alter_column(table, "app_revision", nullable=False)
        expression = (
            "revision >= 1 AND app_revision >= 1 AND target_revision >= 0"
            if prefix == "session"
            else "attempts >= 0 AND session_revision >= 1 AND app_revision >= 1 AND target_revision >= 0"
        )
        op.create_check_constraint(f"ck_cloud_auth_{prefix}_revisions", table, expression)
    op.drop_constraint(
        "fk_cloud_auth_session_application", "cloud_auth_sessions", type_="foreignkey"
    )
    op.create_foreign_key(
        "cloud_auth_sessions_provider_fkey",
        "cloud_auth_sessions",
        "cloud_auth_applications",
        ["provider"],
        ["provider"],
        ondelete="RESTRICT",
    )
    op.drop_column("cloud_auth_sessions", "application_provider")
    for field in ("verified_at", "token_expires_at", "expiry_source"):
        op.drop_column("cloud_auth_sessions", field)
    for field in (
        "refresh_phase",
        "refresh_checkpoint_ciphertext",
        "import_token_digests_ciphertext",
    ):
        op.drop_column("cloud_auth_operations", field)
    op.drop_column("cloud_auth_candidates", "app_revision")
    for table in TABLES:
        for field in ("mode", "protocol_version", "auth_config_id", "auth_config_revision"):
            op.drop_column(table, field)
