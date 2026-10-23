"""Bounded SQL whole-round rankings over formal storage, independent of snapshots."""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.identity.models import Person

from .access import AssessmentAccess
from .catalog import get_suite
from .config_schemas import SuiteConfig
from .errors import AssessmentNotFound
from .models import AssessmentAttempt, AssessmentReport, AssessmentResult, AssessmentSession
from .ranking_schemas import AssessmentRankingView, RankingItem


def _input_group(
    suite_id: str, settings: Any, environment: Any
) -> tuple[str, str, str | None] | None:
    """Only input semantics: suite input, confirmed input, PC FPS raw/relative."""
    if not isinstance(settings, dict) or not isinstance(environment, dict):
        return None
    expected = "touch" if suite_id.startswith("mobile") else "mouse"
    if settings.get("input_kind") != expected or environment.get("input_kind") != expected:
        return None
    mode = settings.get("mouse_mode")
    if suite_id == "pc-fps":
        if mode not in ("raw", "relative"):
            return None
    elif mode is not None:
        return None
    return expected, expected, mode


class AssessmentRankings:
    def __init__(self, db: Session, actor: AuthContext, now: datetime) -> None:
        self.db, self.now = db, now
        self.access = AssessmentAccess(db, actor, now)

    def _ineligible(self, report_id: UUID, reason: str) -> AssessmentRankingView:
        return AssessmentRankingView.model_validate(
            {
                "report_id": report_id,
                "calculated_at": self.now,
                "status": "not_eligible",
                "reason": reason,
            }
        )

    def report(self, report_id: UUID) -> AssessmentRankingView:
        # The exact existing deny-first/private-404 report predicate comes first.
        authorized = self.db.execute(
            select(AssessmentReport, AssessmentSession)
            .join(AssessmentSession, AssessmentSession.id == AssessmentReport.session_id)
            .where(AssessmentReport.id == report_id, self.access.visible_reports())
        ).first()
        if authorized is None:
            raise AssessmentNotFound()
        _, target = authorized
        try:
            suite = SuiteConfig.model_validate(target.catalog_snapshot)
            canonical = get_suite(target.suite_id, catalog_version=suite.catalog_version)
            if suite != canonical or target.catalog_snapshot != canonical.model_dump(mode="json"):
                return self._ineligible(report_id, "incompatible_input")
        except (ValueError, TypeError):
            return self._ineligible(report_id, "incompatible_input")

        rows = self.db.execute(
            select(AssessmentResult, AssessmentAttempt)
            .join(AssessmentAttempt, AssessmentAttempt.id == AssessmentResult.attempt_id)
            .where(AssessmentResult.session_id == target.id)
            .limit(len(suite.test_ids) + 1)
        ).all()
        if len(rows) != len(suite.test_ids) or {r.test_id for r, _ in rows} != set(suite.test_ids):
            return self._ineligible(report_id, "incomplete_results")
        if any(attempt.state != "submitted" for _, attempt in rows):
            return self._ineligible(report_id, "incomplete_results")
        if any(result.quality_flags != [] for result, _ in rows):
            return self._ineligible(report_id, "quality_warning")
        groups = {}
        for result, attempt in rows:
            group = _input_group(
                target.suite_id, attempt.settings_snapshot, attempt.environment_snapshot
            )
            if group is None or attempt.config_snapshot != suite.tasks[result.test_id].model_dump(
                mode="json"
            ):
                return self._ineligible(report_id, "incompatible_input")
            groups[result.test_id] = group

        compatible = []
        for test_id in suite.test_ids:
            input_kind, confirmed_input, mode = groups[test_id]
            compatible.append(
                and_(
                    AssessmentResult.test_id == test_id,
                    AssessmentAttempt.config_snapshot
                    == suite.tasks[test_id].model_dump(mode="json"),
                    AssessmentAttempt.settings_snapshot["input_kind"].astext == input_kind,
                    AssessmentAttempt.environment_snapshot["input_kind"].astext == confirmed_input,
                    AssessmentAttempt.settings_snapshot["mouse_mode"].astext == mode
                    if mode is not None
                    else AssessmentAttempt.settings_snapshot["mouse_mode"].astext.is_(None),
                )
            )

        # Aggregate ALL result rows before selecting a person winner. Incomplete,
        # duplicated, warned or incompatible rounds cannot become complete by filtering.
        rounds = (
            select(
                AssessmentReport.id.label("report_id"),
                AssessmentSession.person_id.label("person_id"),
                AssessmentSession.completed_at.label("completed_at"),
                AssessmentSession.id.label("session_id"),
                func.sum(AssessmentResult.score).label("score_sum"),
            )
            .select_from(AssessmentReport)
            .join(AssessmentSession, AssessmentSession.id == AssessmentReport.session_id)
            .join(Person, Person.id == AssessmentSession.person_id)
            .join(AssessmentResult, AssessmentResult.session_id == AssessmentSession.id)
            .join(AssessmentAttempt, AssessmentAttempt.id == AssessmentResult.attempt_id)
            .where(
                AssessmentSession.suite_id == target.suite_id,
                AssessmentSession.status == "completed",
                AssessmentSession.completed_at.is_not(None),
                AssessmentSession.catalog_snapshot == suite.model_dump(mode="json"),
                Person.deleted_at.is_(None),
            )
            .group_by(AssessmentReport.id, AssessmentSession.id)
            .having(
                func.count(AssessmentResult.id) == len(suite.test_ids),
                func.count(func.distinct(AssessmentResult.test_id)) == len(suite.test_ids),
                func.bool_and(AssessmentResult.quality_flags == []),
                func.bool_and(func.coalesce(or_(*compatible), False)),
                func.bool_and(AssessmentAttempt.state == "submitted"),
            )
            .cte("eligible_rounds")
        )
        choices = select(
            rounds,
            func.row_number()
            .over(
                partition_by=rounds.c.person_id,
                order_by=(rounds.c.score_sum.desc(), rounds.c.completed_at, rounds.c.report_id),
            )
            .label("choice"),
        ).cte("person_choices")
        best = select(choices).where(choices.c.choice == 1).cte("best_rounds")
        overall = select(
            best,
            func.rank().over(order_by=best.c.score_sum.desc()).label("overall_rank"),
            func.count().over().label("participant_count"),
        ).cte("overall_ranks")
        items = (
            select(
                overall,
                AssessmentResult.test_id,
                AssessmentResult.score,
                func.rank()
                .over(
                    partition_by=AssessmentResult.test_id,
                    order_by=AssessmentResult.score.desc(),
                )
                .label("item_rank"),
            )
            .join(AssessmentResult, AssessmentResult.session_id == overall.c.session_id)
            .cte("item_ranks")
        )
        # Only the requested subject's four values leave PostgreSQL. No leaderboard
        # identities, scores or histories are materialized in application memory.
        ranked = (
            self.db.execute(select(items).where(items.c.person_id == target.person_id))
            .mappings()
            .all()
        )
        if not ranked:
            return self._ineligible(report_id, "incomplete_results")
        first = ranked[0]
        by_test = {row["test_id"]: row for row in ranked}
        mean = (Decimal(first["score_sum"]) / Decimal(len(suite.test_ids))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        return AssessmentRankingView(
            report_id=report_id,
            calculated_at=self.now,
            status="ranked",
            best_report_id=first["report_id"],
            best_completed_at=first["completed_at"],
            overall_score=float(mean),
            overall_rank=first["overall_rank"],
            participant_count=first["participant_count"],
            items=tuple(
                RankingItem(
                    test_id=test_id,
                    score=by_test[test_id]["score"],
                    rank=by_test[test_id]["item_rank"],
                )
                for test_id in suite.test_ids
            ),
        )
