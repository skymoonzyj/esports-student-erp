"""Exact bounded-rational ranking. Display numbers never select a winner."""

from datetime import UTC, datetime
from fractions import Fraction
from typing import Any, cast
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
from app.reactionx.access import ReactionxAccess
from app.reactionx.metrics import MetricResult
from app.reactionx.models import ReactionxResultReceipt, ReactionxRun
from app.reactionx.recovery import denied
from app.reactionx.results import require_report
from app.reactionx.rule_versions import RULES
from app.reactionx.schemas import RunManifest

RANK_QUERY = text("""
WITH comparable AS (
 SELECT r.result_id, s.subject_person_id, r.saved_at,
        r.rank_n1 n1, r.rank_d1 d1, r.rank_sign1 sign1,
        r.rank_n2 n2, r.rank_d2 d2, r.rank_sign2 sign2,
        r.rank_key1 k1, r.rank_key2 k2
 FROM reactionx_result_receipts r JOIN reactionx_runs s ON s.id=r.run_id
 WHERE r.eligible AND r.comparison_key=:comparison_key
), target AS (
 SELECT * FROM comparable WHERE result_id=:result_id AND subject_person_id=:person_id
), choices AS (
 SELECT c.*, row_number() OVER (PARTITION BY c.subject_person_id
   ORDER BY c.k1,c.k2,c.saved_at,c.result_id) choice
 FROM comparable c JOIN people p ON p.id=c.subject_person_id
 WHERE p.deleted_at IS NULL AND (
   NOT EXISTS (SELECT 1 FROM user_accounts a WHERE a.person_id=c.subject_person_id)
   OR EXISTS (SELECT 1 FROM user_accounts a WHERE a.person_id=c.subject_person_id AND a.status='active')
 )
), best AS (
 SELECT * FROM choices WHERE choice=1
), ranked AS (
 SELECT *, rank() OVER (ORDER BY k1,k2) rank,
    count(*) OVER (PARTITION BY k1,k2) tie_count FROM best
), top_rows AS (
 SELECT * FROM ranked ORDER BY k1,k2,saved_at,result_id LIMIT 3
)
SELECT t.n1,t.d1,t.n2,t.d2,
 (SELECT count(*) FROM best) participant_count,
 (SELECT jsonb_build_object('n1',n1,'d1',d1,'n2',n2,'d2',d2,'rank',rank)
    FROM ranked WHERE subject_person_id=:person_id) best,
 1+(SELECT count(*) FROM best b WHERE b.subject_person_id<>:person_id AND (
   CAST(b.n1 AS numeric)*t.d1*b.sign1 < CAST(t.n1 AS numeric)*b.d1*t.sign1 OR (
   CAST(b.n1 AS numeric)*t.d1*b.sign1 = CAST(t.n1 AS numeric)*b.d1*t.sign1 AND
   CAST(b.n2 AS numeric)*t.d2*b.sign2 < CAST(t.n2 AS numeric)*b.d2*t.sign2))) current_rank,
 (SELECT jsonb_agg(jsonb_build_object('n1',n1,'d1',d1,'n2',n2,'d2',d2,
   'rank',rank,'tie_count',tie_count,'is_self',subject_person_id=:person_id)
   ORDER BY k1,k2,saved_at,result_id) FROM top_rows) top_three
FROM target t
""")

_LABELS = {
    "miss_count": "漏击次数",
    "mean_hit_ms": "平均反应时间",
    "mean_group_ms": "平均组耗时",
    "mean_cycle_ms": "平均循环耗时",
    "correct_count": "正确次数",
    "hit_count": "命中次数",
    "survival_ms": "持续时间",
}


class ReactionxRankingService:
    def __init__(self, db: Session, access: ReactionxAccess) -> None:
        self.db, self.access = db, access

    def for_result(self, result_id: UUID) -> ItemRankingView:
        target = self.db.execute(
            select(ReactionxResultReceipt, ReactionxRun)
            .join(ReactionxRun, ReactionxRun.id == ReactionxResultReceipt.run_id)
            .where(ReactionxResultReceipt.result_id == result_id)
        ).first()
        if target is None:
            raise denied(404)
        receipt, run = target
        # Cohort metadata and aggregation only AFTER live report authorization.
        require_report(self.access, run.subject_person_id)
        manifest = RunManifest.model_validate(run.manifest)
        result = MetricResult.model_validate(receipt.metrics)
        keys = RULES[manifest.mode].metric_keys or ("hit_count",)
        metrics = [
            ItemRankingMetric(
                key=key,
                label=_LABELS[key],
                unit="ms" if key.endswith("_ms") else "次",
                better="higher"
                if key in ("correct_count", "hit_count", "survival_ms")
                else "lower",
                decimals=2 if key.startswith("mean_") else 0,
            )
            for key in keys
        ]
        calculated_at = datetime.now(UTC)
        group_label = f"ReactionX · {manifest.mode} · 同版本同规则同布置历史最佳"

        def ineligible(reason: ItemRankingReason) -> ItemRankingView:
            return ItemRankingView(
                status="not_eligible",
                reason=reason,
                group_label=group_label,
                calculated_at=calculated_at,
                participant_count=0,
                metrics=metrics,
                current=None,
                best=None,
                top_three=[],
            )

        if not receipt.eligible:
            return ineligible(
                cast(ItemRankingReason, receipt.ranking_reason or "incomplete_result")
            )
        if (
            not result.eligible
            or tuple(d.key for d in result.dimensions) != keys
            or len(result.dimensions) > 2
            or not receipt.comparison_key
        ):
            return ineligible("missing_comparison_metadata")
        row = (
            self.db.execute(
                RANK_QUERY,
                {
                    "comparison_key": receipt.comparison_key,
                    "result_id": result_id,
                    "person_id": run.subject_person_id,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            return ineligible("incomplete_result")
        if row["best"] is None:
            return ineligible("subject_inactive")

        def exact_values(source: Any) -> dict[str, Fraction]:
            return {
                key: Fraction(source[f"n{i}"], source[f"d{i}"]) for i, key in enumerate(keys, 1)
            }

        def values(source: Any) -> dict[str, int | float]:
            return {
                key: value.numerator if value.denominator == 1 else float(value)
                for key, value in exact_values(source).items()
            }

        current_exact = exact_values(row)
        top = []
        for candidate in row["top_three"]:
            gaps = []
            other = exact_values(candidate)
            for metric in metrics:
                delta = current_exact[metric.key] - other[metric.key]
                ahead = delta > 0 if metric.better == "higher" else delta < 0
                gaps.append(
                    ItemRankingGap(
                        key=metric.key,
                        amount=float(abs(delta)),
                        state="equal" if delta == 0 else "ahead" if ahead else "behind",
                    )
                )
            top.append(
                ItemRankingTopRow(
                    rank=candidate["rank"],
                    values=values(candidate),
                    tie_count=candidate["tie_count"],
                    is_self=candidate["is_self"],
                    gaps=gaps,
                )
            )
        return ItemRankingView(
            status="ranked",
            reason=None,
            group_label=group_label,
            calculated_at=calculated_at,
            participant_count=row["participant_count"],
            metrics=metrics,
            current=ItemRankingStanding(rank=row["current_rank"], values=values(row)),
            best=ItemRankingStanding(rank=row["best"]["rank"], values=values(row["best"])),
            top_three=top,
        )


def exact_order_key(numerator: int, denominator: int) -> int:
    """An exact injective ordering of rationals with denominator <=10000.

    Unequal a/b,c/d differ by at least1/(b*d)>=1e-8. Scaling by1e8 then
    flooring cannot collide/reverse. Ties stay equal. Changing the domain
    requires a versioned proof AND migration, not a larger display precision.
    PostgreSQL generated columns use numeric div, casting before multiplication.
    """
    if (
        type(numerator) is not int
        or type(denominator) is not int
        or not 0 <= numerator <= 9007199254740991
        or not 1 <= denominator <= 10000
    ):
        raise ValueError("unsupported exact ranking domain")
    return numerator * 100000000 // denominator
