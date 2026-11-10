"""Live, anonymous per-item bests and current-result comparisons."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.assessment_rankings.schemas import (
    ItemRankingGap,
    ItemRankingMetric,
    ItemRankingReason,
    ItemRankingStanding,
    ItemRankingTopRow,
    ItemRankingView,
)
from app.talent_tests.access import TalentAccess
from app.talent_tests.errors import TalentNotFound
from app.talent_tests.item_ranking_keys import build_item_group, missing_item_metadata
from app.talent_tests.models import TalentTestResult, TalentTestSession

# Only one statement calculates current score, person-best, count, ranks, ties,
# and bounded anonymous rows. Histories never leave PostgreSQL. Result validity
# is local to this item; neither round status nor another item enters the key.
_ITEM_RANK_QUERY = text("""
WITH comparable AS (
    SELECT r.id, s.person_id, r.score, r.created_at
    FROM talent_test_results r
    JOIN talent_test_sessions s ON s.id = r.session_id
    WHERE r.test_id = :test_id
      AND s.catalog_version = :catalog_version AND s.scoring_version = :scoring_version
      AND s.rules_snapshot->>'catalog_version' = :catalog_version
      AND s.rules_snapshot->>'scoring_version' = :scoring_version
      AND s.rules_snapshot->'tests'->:test_id = CAST(:rule_json AS jsonb)
      AND r.engine_version = :engine_version AND r.locale = :locale
      AND r.normalized_result->>'scoring_version' = :scoring_version
      AND r.device_snapshot->>'input_kind' = :input_kind
      AND r.quality_flags = '[]'::jsonb AND r.background_interrupted IS FALSE
), target AS (
    SELECT score FROM comparable WHERE id = :result_id AND person_id = :person_id
), choices AS (
    SELECT c.*, row_number() OVER (
        PARTITION BY c.person_id ORDER BY c.score DESC, c.created_at ASC, c.id ASC
    ) AS choice
    FROM comparable c JOIN people p ON p.id = c.person_id
    WHERE p.deleted_at IS NULL AND (
        NOT EXISTS (SELECT 1 FROM user_accounts a WHERE a.person_id = c.person_id)
        OR EXISTS (SELECT 1 FROM user_accounts a
                   WHERE a.person_id = c.person_id AND a.status = 'active')
    )
), best AS (
    SELECT * FROM choices WHERE choice = 1
), ranked AS (
    SELECT *, rank() OVER (ORDER BY score DESC) AS rank,
           count(*) OVER (PARTITION BY score) AS tie_count FROM best
), top_rows AS (
    SELECT * FROM ranked ORDER BY score DESC, created_at ASC, id ASC LIMIT 3
)
SELECT target.score AS current_score,
       (SELECT count(*) FROM best) AS participant_count,
       (SELECT score FROM ranked WHERE person_id = :person_id) AS best_score,
       (SELECT rank FROM ranked WHERE person_id = :person_id) AS best_rank,
       1 + (SELECT count(*) FROM best
            WHERE person_id <> :person_id AND score > target.score) AS current_rank,
       (SELECT jsonb_agg(jsonb_build_object(
            'score', score, 'rank', rank, 'tie_count', tie_count,
            'is_self', person_id = :person_id
        ) ORDER BY score DESC, created_at ASC, id ASC) FROM top_rows) AS top_three
FROM target
""")

_SCORE = ItemRankingMetric(key="score", label="得分", unit="分", better="higher", decimals=0)


class TalentItemRankingService:
    def __init__(self, db: Session, access: TalentAccess) -> None:
        self.db = db
        self.access = access

    def for_result(self, result_id: UUID) -> ItemRankingView:
        target = self.db.execute(
            select(TalentTestResult, TalentTestSession)
            .join(TalentTestSession, TalentTestSession.id == TalentTestResult.session_id)
            .where(TalentTestResult.id == result_id)
        ).first()
        if target is None:
            raise TalentNotFound("天赋测试资源不存在")
        result, session = target
        # Authorization, including the existing partial-round restrictions, must
        # precede metadata inspection and every cohort/aggregate query.
        self.access.require_session(session)
        group_label = "天赋测评 1 · 本项同条件历史最佳"

        def ineligible(reason: ItemRankingReason) -> ItemRankingView:
            return ItemRankingView(
                status="not_eligible",
                reason=reason,
                group_label=group_label,
                calculated_at=self.access.now,
                participant_count=0,
                metrics=[_SCORE],
                current=None,
                best=None,
                top_three=[],
            )

        if result.quality_flags != [] or result.background_interrupted:
            return ineligible("quality_warning")
        group = build_item_group(session, result)
        if group is None:
            return ineligible(
                "missing_comparison_metadata"
                if missing_item_metadata(session, result)
                else "incompatible_input"
            )
        input_labels = {"mouse": "鼠标", "touch": "触屏", "pen": "触控笔", "mixed": "混合输入"}
        group_label = f"天赋测评 1 · {input_labels[group.input_kind]} · 本项同版本同规则"
        ranked = (
            self.db.execute(
                _ITEM_RANK_QUERY,
                {
                    "test_id": group.test_id,
                    "catalog_version": group.catalog_version,
                    "scoring_version": group.scoring_version,
                    "rule_json": group.rule_json,
                    "engine_version": group.engine_version,
                    "locale": group.locale,
                    "input_kind": group.input_kind,
                    "person_id": session.person_id,
                    "result_id": result_id,
                },
            )
            .mappings()
            .first()
        )
        if ranked is None:
            return ineligible("incomplete_result")
        if ranked["best_rank"] is None:
            return ineligible("subject_inactive")
        current_score = ranked["current_score"]
        rows = []
        for row in ranked["top_three"]:
            difference = current_score - row["score"]
            rows.append(
                ItemRankingTopRow(
                    rank=row["rank"],
                    values={"score": row["score"]},
                    is_self=row["is_self"],
                    tie_count=row["tie_count"],
                    gaps=[
                        ItemRankingGap(
                            key="score",
                            amount=abs(difference),
                            state="ahead"
                            if difference > 0
                            else "behind"
                            if difference < 0
                            else "equal",
                        )
                    ],
                )
            )
        return ItemRankingView(
            status="ranked",
            reason=None,
            group_label=group_label,
            calculated_at=self.access.now,
            participant_count=ranked["participant_count"],
            metrics=[_SCORE],
            current=ItemRankingStanding(
                rank=ranked["current_rank"], values={"score": current_score}
            ),
            best=ItemRankingStanding(
                rank=ranked["best_rank"], values={"score": ranked["best_score"]}
            ),
            top_three=rows,
        )
