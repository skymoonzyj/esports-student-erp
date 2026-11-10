"""Permit typed HKID without rewriting legacy identity or approval evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0077_hk_identity_documents"
down_revision = "0076_asset_batch_hardware"
branch_labels = None
depends_on = None


def _checks(hkid: bool) -> None:
    domestic = "('cn_id','hk_id')" if hkid else "('cn_id')"
    pending = "('passport','hk_id')" if hkid else "('passport')"
    types = "('cn_id','passport','hk_id')" if hkid else "('cn_id','passport')"
    checks = {
        "ck_people_document_type": f"document_type IN {types}",
        "ck_people_document_keys": "(document_type='passport' AND passport_number_digest IS NOT NULL AND (document_issuing_country IS NULL OR document_issuing_country ~ '^[A-Z]{2}$')) OR "
        f"(document_type IN {domestic} AND passport_number_digest IS NULL AND document_issuing_country IS NULL)",
        "ck_people_required_identity_facts": f"(intake_mode='pending' AND document_type IN {pending}) OR (sex IS NOT NULL AND date_of_birth IS NOT NULL AND (document_type IN {domestic} OR document_issuing_country IS NOT NULL))",
    }
    for name, expression in checks.items():
        op.drop_constraint(name, "people", type_="check")
        op.create_check_constraint(name, "people", expression)


def upgrade() -> None:
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    _checks(True)


def downgrade() -> None:
    op.execute("LOCK TABLE people IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM people WHERE document_type='hk_id')")
    ):
        raise RuntimeError("HKID downgrade would discard business facts")
    _checks(False)
