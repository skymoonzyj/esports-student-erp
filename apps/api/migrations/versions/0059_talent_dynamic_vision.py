"""Allow the versioned dynamic-vision talent result."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_talent_dynamic_vision"
down_revision: str | Sequence[str] | None = "0058_talent_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TEST_IDS = (
    "reactiontime",
    "aim",
    "sequence",
    "number-memory",
    "verbal-memory",
    "chimp",
    "memory",
)
_TEST_IDS = (*_LEGACY_TEST_IDS, "dynamic-vision")


def _test_id_constraint(test_ids: tuple[str, ...]) -> str:
    return "test_id IN (" + ", ".join(f"'{test_id}'" for test_id in test_ids) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_talent_results_test_id",
        "talent_test_results",
        type_="check",
    )
    op.create_check_constraint(
        "ck_talent_results_test_id",
        "talent_test_results",
        _test_id_constraint(_TEST_IDS),
    )


def downgrade() -> None:
    # Match the session -> result -> report write order. Hold these locks from
    # before the data guard through the constraint DDL and transaction commit.
    # Take the DDL-strength mode upfront: a submitter's FOR UPDATE/selectinload
    # reads must not enter and deadlock a later result-table lock upgrade.
    # New-version writers must remain stopped after downgrade releases them.
    op.execute(
        sa.text(
            "LOCK TABLE talent_test_sessions, talent_test_results, talent_test_reports "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    has_new_data = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS("
            "SELECT 1 FROM talent_test_sessions "
            "WHERE catalog_version IS DISTINCT FROM 'talent-training-v1'"
            ") OR EXISTS("
            "SELECT 1 FROM talent_test_results WHERE test_id = 'dynamic-vision'"
            ") OR EXISTS("
            "SELECT 1 FROM talent_test_reports "
            "WHERE report_snapshot->'rules'->>'catalog_version' "
            "IS DISTINCT FROM 'talent-training-v1'"
            ")"
        )
    )
    if has_new_data:
        raise RuntimeError("存在八项天赋测试数据，拒绝降级；未删除任何记录")
    op.drop_constraint(
        "ck_talent_results_test_id",
        "talent_test_results",
        type_="check",
    )
    op.create_check_constraint(
        "ck_talent_results_test_id",
        "talent_test_results",
        _test_id_constraint(_LEGACY_TEST_IDS),
    )
