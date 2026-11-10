"""Index valid item comparison conditions without changing historical scores."""

import sqlalchemy as sa
from alembic import op

revision = "0079_talent_item_ranking_indexes"
down_revision = "0078_hk_mo_permit_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_talent_results_item_conditions",
        "talent_test_results",
        [
            "test_id",
            "engine_version",
            "locale",
            sa.text("(device_snapshot->>'input_kind')"),
            sa.text("(normalized_result->>'scoring_version')"),
            "session_id",
        ],
        postgresql_where=sa.text("quality_flags = '[]'::jsonb AND background_interrupted IS FALSE"),
    )


def downgrade() -> None:
    op.drop_index("ix_talent_results_item_conditions", table_name="talent_test_results")
