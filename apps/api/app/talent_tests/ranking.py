"""Live internal-training ranks without loading participants' histories."""

import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.talent_tests.access import TalentAccess
from app.talent_tests.catalog import get_rules
from app.talent_tests.errors import TalentNotFound, UnknownCatalogVersion
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from app.talent_tests.ranking_schemas import RankingReason, RankingView
from app.talent_tests.schemas import RuleSnapshot

# All cohort selection, best-whole-round selection and competition ranking is
# database-side. Only the subject's one selected round leaves PostgreSQL.
_RANK_QUERY = text("""
WITH expected AS (
    SELECT * FROM jsonb_to_recordset(CAST(:profile AS jsonb))
    AS x(test_id text, engine text, locale text, input text, ordinal integer)
), rounds AS (
    SELECT s.person_id, p.id AS report_id, s.completed_at,
           sum(r.score) AS score_sum,
           jsonb_agg(jsonb_build_object('test_id', r.test_id, 'score', r.score,
                     'ordinal', e.ordinal) ORDER BY e.ordinal) AS items
    FROM talent_test_sessions s
    JOIN people person ON person.id = s.person_id AND person.deleted_at IS NULL
    JOIN talent_test_reports p ON p.session_id = s.id
    JOIN talent_test_results r ON r.session_id = s.id
    LEFT JOIN expected e ON e.test_id = r.test_id AND e.engine = r.engine_version
        AND e.locale = r.locale AND e.input = r.device_snapshot->>'input_kind'
    WHERE s.status = 'completed' AND s.completed_at IS NOT NULL
      AND s.catalog_version = :catalog AND s.scoring_version = :scoring
      AND s.report_version = :report AND s.rules_snapshot = CAST(:rules AS jsonb)
    GROUP BY s.person_id, p.id, s.completed_at
    HAVING count(*) = :item_count AND count(DISTINCT r.test_id) = :item_count
       AND bool_and(e.test_id IS NOT NULL AND r.quality_flags = '[]'::jsonb)
), choices AS (
    SELECT *, row_number() OVER (
        PARTITION BY person_id ORDER BY score_sum DESC, completed_at ASC, report_id ASC
    ) AS choice FROM rounds
), best AS (
    SELECT person_id, report_id, completed_at, score_sum, items
    FROM choices WHERE choice = 1
), overall AS (
    SELECT *, rank() OVER (ORDER BY score_sum DESC) AS overall_rank,
           count(*) OVER () AS participant_count FROM best
), item_ranks AS (
    SELECT best.person_id, x.test_id, x.score, x.ordinal,
           rank() OVER (PARTITION BY x.test_id ORDER BY x.score DESC) AS rank
    FROM best CROSS JOIN LATERAL jsonb_to_recordset(best.items)
        AS x(test_id text, score integer, ordinal integer)
)
SELECT o.report_id, o.completed_at, o.score_sum, o.overall_rank, o.participant_count,
       jsonb_agg(jsonb_build_object('test_id', i.test_id, 'score', i.score, 'rank', i.rank)
                 ORDER BY i.ordinal) AS items
FROM overall o JOIN item_ranks i ON i.person_id = o.person_id
WHERE o.person_id = :person_id
GROUP BY o.report_id, o.completed_at, o.score_sum, o.overall_rank, o.participant_count
""")


def report_ranking(db: Session, actor: AuthContext, report_id: UUID, now: datetime) -> RankingView:
    # Apply the exact deny-first person visibility predicate used by report reads
    # before touching ranking metadata or computing aggregate participant counts.
    target = db.execute(
        select(
            TalentTestSession.id,
            TalentTestSession.person_id,
            TalentTestSession.catalog_version,
            TalentTestSession.scoring_version,
            TalentTestSession.report_version,
            TalentTestSession.rules_snapshot,
        )
        .join(TalentTestReport, TalentTestReport.session_id == TalentTestSession.id)
        .where(
            TalentTestReport.id == report_id,
            TalentTestSession.status == "completed",
            TalentTestSession.person_id.in_(TalentAccess(db, actor, now).visible_person_ids()),
        )
    ).first()
    if target is None:
        raise TalentNotFound("天赋测试资源不存在")

    def ineligible(reason: RankingReason) -> RankingView:
        return RankingView(
            report_id=report_id, calculated_at=now, status="not_eligible", reason=reason
        )

    try:
        rules = RuleSnapshot.model_validate(target.rules_snapshot)
        known = get_rules(target.catalog_version)
    except (ValidationError, UnknownCatalogVersion):
        return ineligible("incompatible_input")
    if (
        rules.test_ids != known.test_ids
        or set(rules.tests) != set(known.test_ids)
        or rules.catalog_version != target.catalog_version
        or rules.scoring_version != target.scoring_version
        or rules.report_version != target.report_version
    ):
        return ineligible("incompatible_input")
    # One authorized session only; +1 makes extra/duplicate rows fail closed.
    results = db.execute(
        select(
            TalentTestResult.test_id,
            TalentTestResult.engine_version,
            TalentTestResult.locale,
            TalentTestResult.device_snapshot,
            TalentTestResult.quality_flags,
        )
        .where(TalentTestResult.session_id == target.id)
        .limit(len(rules.test_ids) + 1)
    ).all()
    # Match the aggregate's exact empty-JSON-array requirement. Falsey malformed
    # JSON must not authorize fallback ranking from a different valid round.
    if any(row.quality_flags != [] for row in results):
        return ineligible("quality_warning")
    by_test = {row.test_id: row for row in results}
    if len(results) != len(rules.test_ids) or set(by_test) != set(rules.test_ids):
        return ineligible("incomplete_results")
    profile = []
    for ordinal, test_id in enumerate(rules.test_ids):
        row = by_test[test_id]
        device = row.device_snapshot
        kind = device.get("input_kind") if isinstance(device, dict) else None
        if (
            not isinstance(kind, str)
            or kind not in {"mouse", "touch", "pen", "mixed"}
            or row.engine_version != rules.tests[test_id].engine_version
            or row.locale != "zh-CN"
        ):
            return ineligible("incompatible_input")
        profile.append(
            {
                "test_id": test_id,
                "engine": row.engine_version,
                "locale": row.locale,
                "input": kind,
                "ordinal": ordinal,
            }
        )
    ranked = (
        db.execute(
            _RANK_QUERY,
            {
                "profile": json.dumps(profile),
                "rules": json.dumps(target.rules_snapshot),
                "catalog": target.catalog_version,
                "scoring": target.scoring_version,
                "report": target.report_version,
                "item_count": len(rules.test_ids),
                "person_id": target.person_id,
            },
        )
        .mappings()
        .first()
    )
    if ranked is None:
        return ineligible("incomplete_results")
    mean = (Decimal(ranked["score_sum"]) / len(rules.test_ids)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return RankingView(
        report_id=report_id,
        calculated_at=now,
        status="ranked",
        best_report_id=ranked["report_id"],
        best_completed_at=ranked["completed_at"],
        overall_score=float(mean),
        overall_rank=ranked["overall_rank"],
        participant_count=ranked["participant_count"],
        items=ranked["items"],
    )
