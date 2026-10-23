"""Authorized, cursor-paged history and immutable formal reports."""

import base64
import binascii
import json
import re
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Select, case, func, literal, select, tuple_, union_all
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from app.auth.service import AuthContext
from app.identity.models import Person

from . import catalog
from .access import AssessmentAccess
from .config_schemas import SuiteConfig, SuiteId, TaskConfig, TestId
from .errors import AssessmentForbidden, AssessmentNotFound
from .lifecycle import attempt_view
from .models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from .query_schemas import (
    ActiveRunView,
    AssessmentContextView,
    AssessmentReportSummary,
    AttemptDetailView,
    CursorPage,
    FormalHistoryItem,
    HistoryItem,
    PracticeHistoryItem,
    RecoveryItem,
    ReportFilters,
    SuiteCatalogView,
)
from .schemas import (
    AssessmentReportView,
    ControlSettings,
    EnvironmentSnapshot,
    FormalResultSaveView,
    PracticeResult,
    PracticeResultSaveView,
    ReportResult,
    ScoredResult,
    SessionResultView,
    SessionView,
)

_CHINA = ZoneInfo("Asia/Shanghai")
_CURSOR_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")


def encode_cursor(occurred_at: datetime, row_id: UUID) -> str:
    payload = json.dumps(
        {"time": occurred_at.isoformat(), "id": str(row_id)}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    try:
        if not isinstance(cursor, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", cursor):
            raise ValueError("invalid cursor encoding")
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        if (
            not isinstance(value, dict)
            or set(value) != {"time", "id"}
            or not isinstance(value["time"], str)
            or not _CURSOR_TIME.fullmatch(value["time"])
            or not isinstance(value["id"], str)
            or not _UUID.fullmatch(value["id"])
        ):
            raise ValueError("invalid cursor payload")
        at, row_id = datetime.fromisoformat(value["time"]), UUID(value["id"])
        if at.utcoffset() is None or encode_cursor(at, row_id) != cursor:
            raise ValueError("cursor must use canonical aware time and UUID")
        return at, row_id
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise ValueError("invalid assessment cursor") from exc


class AssessmentQueries:
    def __init__(self, db: Session, actor: AuthContext, now: datetime) -> None:
        self.db, self.actor, self.now = db, actor, now
        self.access = AssessmentAccess(db, actor, now)

    @staticmethod
    def _page_input(cursor: str | None, limit: int) -> tuple[datetime, UUID] | None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return decode_cursor(cursor)

    def _rows(
        self,
        base: Select[Any],
        timestamp: ColumnElement[datetime]
        | InstrumentedAttribute[datetime]
        | InstrumentedAttribute[datetime | None],
        identity: ColumnElement[UUID] | InstrumentedAttribute[UUID],
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[list[Any], int, bool]:
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        if cursor:
            base = base.where(tuple_(timestamp, identity) < cursor)
        rows = list(
            self.db.scalars(base.order_by(timestamp.desc(), identity.desc()).limit(limit + 1))
        )
        return rows[:limit], total, len(rows) > limit

    @staticmethod
    def scored_result(row: AssessmentResult) -> ScoredResult:
        return ScoredResult.model_validate(
            {
                "metrics": row.metrics,
                "score": row.score,
                "grade": row.grade,
                "quality_flags": row.quality_flags,
            }
        )

    def session_view(self, row: AssessmentSession) -> SessionView:
        """Shared authorized serializer for lifecycle and query consumers."""
        self.access.require_session(row)
        suite = SuiteConfig.model_validate(row.catalog_snapshot)
        results = {
            r.test_id: r
            for r in self.db.scalars(
                select(AssessmentResult).where(AssessmentResult.session_id == row.id)
            )
        }
        report_id = self.db.scalar(
            select(AssessmentReport.id).where(AssessmentReport.session_id == row.id)
        )
        return SessionView.model_validate(
            {
                "id": row.id,
                "suite_id": row.suite_id,
                "suite_config": suite,
                "status": row.status,
                "members": suite.test_ids,
                "results": [
                    SessionResultView(
                        result_id=results[t].id,
                        test_id=t,
                        scored=self.scored_result(results[t]),
                    )
                    for t in suite.test_ids
                    if t in results
                ],
                "report_id": report_id,
            }
        )

    def context(self) -> AssessmentContextView:
        can_read_self = (
            self.db.scalar(
                self.access.visible_person_ids().where(Person.id == self.actor.person.id)
            )
            is not None
        )
        can_read_reports = self.db.scalar(self.access.visible_person_ids().limit(1)) is not None
        try:
            self.access.require_take()
            can_take = True
        except AssessmentForbidden:
            can_take = False

        sessions: tuple[SessionView, ...] = ()
        active: ActiveRunView | None = None
        if can_read_self:
            rows = list(
                self.db.scalars(
                    select(AssessmentSession)
                    .where(
                        AssessmentSession.person_id == self.actor.person.id,
                        AssessmentSession.status == "in_progress",
                    )
                    .order_by(AssessmentSession.created_at.desc(), AssessmentSession.id.desc())
                    .limit(4)
                )
            )
            sessions = tuple(self.session_view(row) for row in rows)
            active = self._active_run()
        return AssessmentContextView(
            server_now=self.now,
            can_take=can_take,
            can_read_self=can_read_self,
            can_read_reports=can_read_reports,
            available_suites=self.access.available_suite_ids(),
            own_in_progress_sessions=sessions,
            active_run=active,
        )

    def suites(self) -> SuiteCatalogView:
        return SuiteCatalogView(
            items=tuple(
                catalog.get_suite(suite_id)
                for suite_id in self.access.available_suite_ids()
            )
        )

    def _active_run(self) -> ActiveRunView | None:
        formal = self.db.execute(
            select(AssessmentAttempt, AssessmentSession.suite_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentAttempt.session_id)
            .where(
                AssessmentSession.person_id == self.actor.person.id,
                AssessmentAttempt.created_by_user_id == self.actor.account.id,
                AssessmentAttempt.state.in_(("running", "awaiting_submission")),
                AssessmentAttempt.run_until > self.now,
            )
            .limit(2)
        ).all()
        practice = list(
            self.db.scalars(
                select(AssessmentPracticeRun)
                .where(
                    AssessmentPracticeRun.person_id == self.actor.person.id,
                    AssessmentPracticeRun.created_by_user_id == self.actor.account.id,
                    AssessmentPracticeRun.state.in_(("running", "awaiting_submission")),
                    AssessmentPracticeRun.run_until > self.now,
                )
                .limit(2)
            )
        )
        if len(formal) + len(practice) > 1:
            raise RuntimeError("account has multiple active esports assessment runs")
        if formal:
            row, suite_id = formal[0]
            return ActiveRunView(
                mode="formal",
                attempt_id=row.id,
                session_id=row.session_id,
                suite_id=cast(SuiteId, suite_id),
                test_id=cast(TestId, row.test_id),
                run_until=row.run_until,
                submit_until=row.submit_until,
            )
        if practice:
            row = practice[0]
            return ActiveRunView(
                mode="practice",
                attempt_id=row.id,
                session_id=None,
                suite_id=cast(SuiteId, row.suite_id),
                test_id=cast(TestId, row.test_id),
                run_until=row.run_until,
                submit_until=row.submit_until,
            )
        return None

    def session(self, session_id: UUID) -> SessionView:
        row = self.db.scalar(
            select(AssessmentSession).where(
                AssessmentSession.id == session_id,
                self.access.visible_session_predicate(),
            )
        )
        if row is None:
            raise AssessmentNotFound()
        return self.session_view(row)

    def recovery(self, cursor: str | None = None, limit: int = 20) -> CursorPage[RecoveryItem]:
        position = self._page_input(cursor, limit)
        self.access.require_private_run(self.actor.person.id)
        formal_projected = case(
            (AssessmentAttempt.run_until <= self.now, literal("awaiting_submission")),
            else_=literal("running"),
        )
        formal = (
            select(
                literal("formal").label("mode"),
                AssessmentAttempt.id.label("attempt_id"),
                AssessmentAttempt.session_id.label("session_id"),
                AssessmentSession.suite_id.label("suite_id"),
                AssessmentAttempt.test_id.label("test_id"),
                formal_projected.label("state"),
                AssessmentAttempt.created_at.label("issued_at"),
                AssessmentAttempt.run_until.label("run_until"),
                AssessmentAttempt.submit_until.label("submit_until"),
            )
            .join(AssessmentSession, AssessmentSession.id == AssessmentAttempt.session_id)
            .where(
                AssessmentSession.person_id == self.actor.person.id,
                AssessmentAttempt.created_by_user_id == self.actor.account.id,
                AssessmentAttempt.state.in_(("running", "awaiting_submission")),
                AssessmentAttempt.submit_until > self.now,
            )
        )
        practice_projected = case(
            (AssessmentPracticeRun.run_until <= self.now, literal("awaiting_submission")),
            else_=literal("running"),
        )
        practice = select(
            literal("practice").label("mode"),
            AssessmentPracticeRun.id.label("attempt_id"),
            literal(None, type_=AssessmentPracticeRun.id.type).label("session_id"),
            AssessmentPracticeRun.suite_id.label("suite_id"),
            AssessmentPracticeRun.test_id.label("test_id"),
            practice_projected.label("state"),
            AssessmentPracticeRun.created_at.label("issued_at"),
            AssessmentPracticeRun.run_until.label("run_until"),
            AssessmentPracticeRun.submit_until.label("submit_until"),
        ).where(
            AssessmentPracticeRun.person_id == self.actor.person.id,
            AssessmentPracticeRun.created_by_user_id == self.actor.account.id,
            AssessmentPracticeRun.state.in_(("running", "awaiting_submission")),
            AssessmentPracticeRun.submit_until > self.now,
        )
        combined = union_all(formal, practice).subquery()
        base = select(combined)
        total = self.db.scalar(select(func.count()).select_from(combined)) or 0
        if position:
            base = base.where(tuple_(combined.c.issued_at, combined.c.attempt_id) < position)
        rows = self.db.execute(
            base.order_by(combined.c.issued_at.desc(), combined.c.attempt_id.desc()).limit(
                limit + 1
            )
        ).all()
        page = rows[:limit]
        items = tuple(RecoveryItem.model_validate(dict(row._mapping)) for row in page)
        return CursorPage[RecoveryItem](
            items=items,
            visible_total=total,
            next_cursor=(
                encode_cursor(page[-1].issued_at, page[-1].attempt_id)
                if len(rows) > limit
                else None
            ),
        )

    def detail(self, mode: str, attempt_id: UUID) -> AttemptDetailView:
        if mode == "formal":
            pair = self.db.execute(
                select(AssessmentAttempt, AssessmentSession)
                .join(AssessmentSession, AssessmentSession.id == AssessmentAttempt.session_id)
                .where(AssessmentAttempt.id == attempt_id)
            ).first()
            if pair is None:
                raise AssessmentNotFound()
            row, session = pair
            self.access.require_private_run(session.person_id)
            attempt = attempt_view(row, self.now)
            saved = None
            if row.state == "submitted":
                result = self.db.scalar(
                    select(AssessmentResult).where(AssessmentResult.attempt_id == row.id)
                )
                if result is None:
                    raise RuntimeError("submitted attempt has no result")
                saved = FormalResultSaveView(
                    result_id=result.id,
                    scored=self.scored_result(result),
                    session=self.session_view(session),
                )
            return AttemptDetailView(attempt=attempt, saved=saved)
        if mode == "practice":
            row = self.db.get(AssessmentPracticeRun, attempt_id)
            if row is None:
                raise AssessmentNotFound()
            self.access.require_private_run(row.person_id)
            practice_saved = None
            if row.state == "submitted":
                practice_saved = PracticeResultSaveView(
                    result_id=row.id,
                    scored=PracticeResult.model_validate(
                        {"metrics": row.metrics, "quality_flags": row.quality_flags}
                    ),
                )
            return AttemptDetailView(attempt=attempt_view(row, self.now), saved=practice_saved)
        raise ValueError("mode must be formal or practice")

    def history(
        self, mode: str, cursor: str | None = None, limit: int = 20
    ) -> CursorPage[HistoryItem]:
        position = self._page_input(cursor, limit)
        if mode not in {"formal", "practice"}:
            raise ValueError("mode must be formal or practice")
        self.access.require_private_run(self.actor.person.id)
        if mode == "formal":
            base = select(AssessmentSession).where(
                AssessmentSession.person_id == self.actor.person.id,
                AssessmentSession.person_id.in_(self.access.visible_person_ids()),
            )
            rows, total, more = self._rows(
                base, AssessmentSession.created_at, AssessmentSession.id, position, limit
            )
            items: list[HistoryItem] = [
                FormalHistoryItem(
                    session=self.session_view(row),
                    created_at=row.created_at,
                    completed_at=row.completed_at,
                )
                for row in rows
            ]
            next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if more else None
        else:
            practice_base = select(AssessmentPracticeRun).where(
                AssessmentPracticeRun.person_id == self.actor.person.id,
                AssessmentPracticeRun.person_id.in_(self.access.visible_person_ids()),
                AssessmentPracticeRun.state == "submitted",
            )
            rows, total, more = self._rows(
                practice_base,
                AssessmentPracticeRun.submitted_at,
                AssessmentPracticeRun.id,
                position,
                limit,
            )
            items = [
                PracticeHistoryItem.model_validate(
                    {
                        "attempt_id": row.id,
                        "result_id": row.id,
                        "suite_id": row.suite_id,
                        "test_id": row.test_id,
                        "completed_at": row.submitted_at,
                        "config": row.config_snapshot,
                        "settings": row.settings_snapshot,
                        "environment": row.environment_snapshot,
                        "scored": PracticeResult(
                            metrics=row.metrics, quality_flags=row.quality_flags
                        ),
                    }
                )
                for row in rows
            ]
            next_cursor = encode_cursor(rows[-1].submitted_at, rows[-1].id) if more else None
        return CursorPage[HistoryItem](
            items=tuple(items), visible_total=total, next_cursor=next_cursor
        )

    def reports(
        self,
        filters: ReportFilters | dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> CursorPage[AssessmentReportSummary]:
        position = self._page_input(cursor, limit)
        selectors = ReportFilters.model_validate(filters or {})
        if selectors.person_id is not None:
            self.access.require_person(selectors.person_id)
        base = select(AssessmentReport).join(AssessmentSession).where(self.access.visible_reports())
        if selectors.person_id:
            base = base.where(AssessmentSession.person_id == selectors.person_id)
        if selectors.suite_id:
            base = base.where(AssessmentSession.suite_id == selectors.suite_id)
        if selectors.date_from:
            lower = datetime.combine(selectors.date_from, time.min, _CHINA).astimezone(UTC)
            base = base.where(AssessmentSession.completed_at >= lower)
        if selectors.date_to:
            upper = datetime.combine(
                selectors.date_to + timedelta(days=1), time.min, _CHINA
            ).astimezone(UTC)
            base = base.where(AssessmentSession.completed_at < upper)
        rows, total, more = self._rows(
            base, AssessmentSession.completed_at, AssessmentReport.id, position, limit
        )
        snapshots = [AssessmentReportView.model_validate(r.report_snapshot) for r in rows]
        return CursorPage[AssessmentReportSummary](
            items=tuple(
                AssessmentReportSummary(
                    id=row.id,
                    session_id=row.session_id,
                    suite_id=s.suite_id,
                    person=s.person,
                    completed_at=s.completed_at,
                )
                for row, s in zip(rows, snapshots, strict=True)
            ),
            visible_total=total,
            next_cursor=encode_cursor(snapshots[-1].completed_at, rows[-1].id) if more else None,
        )

    def report(self, report_id: UUID) -> AssessmentReportView:
        row = self.db.scalar(
            select(AssessmentReport)
            .join(AssessmentSession)
            .where(
                AssessmentReport.id == report_id,
                self.access.visible_reports(),
            )
        )
        if row is None:
            raise AssessmentNotFound()
        return AssessmentReportView.model_validate(row.report_snapshot)

    def previous_result(
        self,
        person_id: UUID,
        suite_id: SuiteId,
        test_id: TestId,
        before: datetime,
        exclude_session_id: UUID,
    ) -> tuple[UUID, ReportResult] | None:
        """Latest authorized prior snapshot, including incompatible/flagged results."""
        self.access.require_person(person_id)
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("before must be timezone-aware")
        row = self.db.scalar(
            select(AssessmentReport)
            .join(AssessmentSession, AssessmentReport.session_id == AssessmentSession.id)
            .where(
                self.access.visible_reports(),
                AssessmentSession.person_id == person_id,
                AssessmentSession.suite_id == suite_id,
                AssessmentReport.report_snapshot["results"].contains([{"test_id": test_id}]),
                AssessmentSession.completed_at < before,
                AssessmentSession.id != exclude_session_id,
            )
            .order_by(AssessmentSession.completed_at.desc(), AssessmentReport.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        snapshot = AssessmentReportView.model_validate(row.report_snapshot)
        return row.id, next(result for result in snapshot.results if result.test_id == test_id)

    def comparable_result(
        self,
        person_id: UUID,
        suite_id: SuiteId,
        test_id: TestId,
        comparison_key: str,
        before: datetime,
        exclude_session_id: UUID,
    ) -> tuple[UUID, ReportResult] | None:
        """Latest authorized eligible baseline, with every filter before LIMIT."""
        self.access.require_person(person_id)
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("before must be timezone-aware")
        if not re.fullmatch(r"[0-9a-f]{64}", comparison_key):
            raise ValueError("invalid comparison key")
        row = self.db.execute(
            select(AssessmentReport.id, AssessmentResult, AssessmentAttempt)
            .select_from(AssessmentResult)
            .join(AssessmentAttempt, AssessmentAttempt.id == AssessmentResult.attempt_id)
            .join(AssessmentSession, AssessmentSession.id == AssessmentResult.session_id)
            .join(AssessmentReport, AssessmentReport.session_id == AssessmentSession.id)
            .where(
                self.access.visible_reports(),
                AssessmentSession.person_id == person_id,
                AssessmentSession.suite_id == suite_id,
                AssessmentResult.test_id == test_id,
                AssessmentResult.comparison_key == comparison_key,
                AssessmentResult.quality_flags == [],
                AssessmentSession.completed_at < before,
                AssessmentSession.id != exclude_session_id,
            )
            .order_by(AssessmentSession.completed_at.desc(), AssessmentReport.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        report_id, result, attempt = row
        return cast(UUID, report_id), ReportResult(
            result_id=result.id,
            test_id=cast(TestId, result.test_id),
            config=TaskConfig.model_validate(attempt.config_snapshot),
            settings=ControlSettings.model_validate(attempt.settings_snapshot),
            environment=EnvironmentSnapshot.model_validate(attempt.environment_snapshot),
            scored=self.scored_result(result),
        )
