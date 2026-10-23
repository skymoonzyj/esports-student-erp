"""Preserve historical stays with orthogonal immutable void evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0070_housing_history_void"
down_revision = "0069_pending_profile_intake"
branch_labels = None
depends_on = None

VOID_PERMISSION_ID = "20000000-0000-4000-8000-000000000090"
VOID_PERMISSION_KEY = "housing.history.void"


def upgrade() -> None:
    op.add_column(
        "housing_stays", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("housing_stays", sa.Column("voided_by_user_id", sa.UUID(), nullable=True))
    op.add_column("housing_stays", sa.Column("void_reason_ciphertext", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_housing_stays_void_actor",
        "housing_stays",
        "user_accounts",
        ["voided_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_housing_stays_void_fact",
        "housing_stays",
        "(voided_at IS NULL AND voided_by_user_id IS NULL AND void_reason_ciphertext IS NULL) "
        "OR (voided_at IS NOT NULL AND voided_by_user_id IS NOT NULL "
        "AND void_reason_ciphertext IS NOT NULL AND status = 'checked_out')",
    )
    op.create_table(
        "housing_stay_void_operations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("actor_account_id", sa.UUID(), nullable=False),
        sa.Column("idempotency_key", sa.UUID(), nullable=False),
        sa.Column("stay_id", sa.UUID(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["actor_account_id"],
            ["user_accounts.id"],
            ondelete="RESTRICT",
            name="fk_housing_stay_void_actor",
        ),
        sa.ForeignKeyConstraint(
            ["stay_id"], ["housing_stays.id"], ondelete="RESTRICT", name="fk_housing_stay_void_stay"
        ),
        sa.UniqueConstraint(
            "actor_account_id", "idempotency_key", name="uq_housing_stay_void_actor_key"
        ),
        sa.UniqueConstraint("stay_id", name="uq_housing_stay_void_stay"),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_housing_stay_void_fingerprint"
        ),
        sa.CheckConstraint("result_version >= 1", name="ck_housing_stay_void_result_version"),
    )
    op.execute("""
        CREATE FUNCTION enforce_housing_stay_void_fact_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.voided_at IS NOT NULL THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'housing stay void evidence is immutable' USING ERRCODE='23514';
                END IF;
                IF ROW(NEW.voided_at,NEW.voided_by_user_id,NEW.void_reason_ciphertext)
                   IS DISTINCT FROM ROW(OLD.voided_at,OLD.voided_by_user_id,OLD.void_reason_ciphertext) THEN
                    RAISE EXCEPTION 'housing stay void evidence is immutable' USING ERRCODE='23514';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_housing_stays_void_fact_immutable
        BEFORE UPDATE OR DELETE ON housing_stays FOR EACH ROW
        EXECUTE FUNCTION enforce_housing_stay_void_fact_immutable()
    """)
    op.execute("""
        CREATE FUNCTION enforce_housing_stay_void_operation_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'housing stay void operation is immutable' USING ERRCODE='23514';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_housing_stay_void_operations_immutable
        BEFORE UPDATE OR DELETE ON housing_stay_void_operations FOR EACH ROW
        EXECUTE FUNCTION enforce_housing_stay_void_operation_immutable()
    """)
    op.execute(
        sa.text(
            "INSERT INTO permissions (id,key,name,sensitive) "
            "VALUES (CAST(:id AS uuid),:key,'作废历史入住记录',true)"
        ).bindparams(id=VOID_PERMISSION_ID, key=VOID_PERMISSION_KEY)
    )
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role_id,permission_id,scope_kind) "
            "SELECT id,CAST(:id AS uuid),'global' FROM roles WHERE key='super_admin'"
        ).bindparams(id=VOID_PERMISSION_ID)
    )


def downgrade() -> None:
    # Lock before checking so concurrent evidence cannot arrive between the
    # refusal check and destructive DDL. PostgreSQL holds these until commit.
    op.execute("LOCK TABLE housing_stays, housing_stay_void_operations IN ACCESS EXCLUSIVE MODE")
    connection = op.get_bind()
    if connection.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM housing_stays WHERE voided_at IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM housing_stay_void_operations)"
        )
    ):
        raise RuntimeError("cannot downgrade while housing void evidence exists")
    # Remove only this exact default seed. Custom grants/overrides must not be
    # erased: their RESTRICT FKs refuse the permission deletion and roll back
    # the entire downgrade, including this default grant deletion.
    op.execute(
        sa.text(
            "DELETE FROM role_permissions rp USING roles r,permissions p "
            "WHERE rp.role_id=r.id AND rp.permission_id=p.id "
            "AND r.key='super_admin' AND rp.scope_kind='global' "
            "AND p.id=CAST(:id AS uuid) AND p.key=:key"
        ).bindparams(id=VOID_PERMISSION_ID, key=VOID_PERMISSION_KEY)
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE id=CAST(:id AS uuid) AND key=:key").bindparams(
            id=VOID_PERMISSION_ID, key=VOID_PERMISSION_KEY
        )
    )
    op.execute(
        "DROP TRIGGER trg_housing_stay_void_operations_immutable ON housing_stay_void_operations"
    )
    op.execute("DROP FUNCTION enforce_housing_stay_void_operation_immutable()")
    op.execute("DROP TRIGGER trg_housing_stays_void_fact_immutable ON housing_stays")
    op.execute("DROP FUNCTION enforce_housing_stay_void_fact_immutable()")
    op.drop_table("housing_stay_void_operations")
    op.drop_constraint("ck_housing_stays_void_fact", "housing_stays", type_="check")
    op.drop_constraint("fk_housing_stays_void_actor", "housing_stays", type_="foreignkey")
    op.drop_column("housing_stays", "void_reason_ciphertext")
    op.drop_column("housing_stays", "voided_by_user_id")
    op.drop_column("housing_stays", "voided_at")
