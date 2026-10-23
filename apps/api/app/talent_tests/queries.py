from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.auth.service import AuthContext
from app.identity.models import Person
from app.talent_tests.access import TalentAccess
from app.talent_tests.catalog import (
    CURRENT_CATALOG_VERSION,
    get_dynamic_vision_rule_for_result,
    get_rules,
)
from app.talent_tests.errors import TalentForbidden, TalentNotFound
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from app.talent_tests.schemas import (
    ContextView,
    DynamicVisionRaw,
    NormalizedResult,
    Page,
    PersonDisplay,
    ReportSnapshot,
    ReportSummary,
    ReportView,
    ResultView,
    RuleSnapshot,
    SessionStatus,
    SessionSummary,
    SessionView,
    TestId,
)

_NON_COMPARABLE_INPUTS = frozenset({"mixed", "unknown"})
_SESSION_STATUS_ADAPTER = TypeAdapter[SessionStatus](SessionStatus)


def find_comparable_baselines(
    db: Session,
    *,
    person_id: UUID,
    results: Sequence[NormalizedResult],
    before: datetime,
) -> dict[str, tuple[UUID, NormalizedResult]]:
    """Select the latest comparable completed result for each current test.

    Each test uses one bounded SQL query. Compatibility is expressed in SQL,
    so an incompatible recent row cannot hide an older compatible baseline and
    no person's result history is loaded for Python-side filtering.
    """

    if before.tzinfo is None or before.utcoffset() is None:
        raise ValueError("before must be timezone-aware")
    baselines: dict[str, tuple[UUID, NormalizedResult]] = {}
    seen: set[TestId] = set()
    for result in results:
        if result.test_id in seen:
            continue
        seen.add(result.test_id)
        if result.quality_flags or result.device.input_kind in _NON_COMPARABLE_INPUTS:
            continue
        comparability_filters: list[ColumnElement[bool]] = []
        if result.test_id == "dynamic-vision":
            if (
                get_dynamic_vision_rule_for_result(result.scoring_version, result.engine_version)
                is None
            ):
                continue
            raw = DynamicVisionRaw.model_validate(result.raw.model_dump())
            comparability_filters.extend(
                (
                    TalentTestResult.raw_result["selected_level"].as_integer()
                    == raw.selected_level,
                    TalentTestResult.raw_result["font_size_px"].as_integer() == raw.font_size_px,
                )
            )
        row = db.execute(
            select(TalentTestReport.id, TalentTestResult.normalized_result)
            .select_from(TalentTestResult)
            .join(
                TalentTestSession,
                TalentTestSession.id == TalentTestResult.session_id,
            )
            .join(
                TalentTestReport,
                TalentTestReport.session_id == TalentTestSession.id,
            )
            .where(
                TalentTestSession.person_id == person_id,
                TalentTestSession.status == "completed",
                TalentTestSession.completed_at.is_not(None),
                TalentTestSession.completed_at < before,
                TalentTestSession.scoring_version == result.scoring_version,
                TalentTestResult.test_id == result.test_id,
                TalentTestResult.engine_version == result.engine_version,
                TalentTestResult.locale == result.locale,
                TalentTestResult.quality_flags == [],
                TalentTestResult.device_snapshot == result.device.model_dump(mode="json"),
                *comparability_filters,
            )
            .order_by(
                TalentTestSession.completed_at.desc(),
                TalentTestReport.id.desc(),
            )
            .limit(1)
        ).first()
        if row is None:
            continue
        report_id, payload = row
        baselines[result.test_id] = (
            report_id,
            NormalizedResult.model_validate(payload),
        )
    return baselines


class TalentQueries:
    def __init__(self, db: Session, actor: AuthContext, now: datetime) -> None:
        self.db = db
        self.actor = actor
        self.now = now
        self.access = TalentAccess(db, actor, now)

    @staticmethod
    def _validate_page(page: int, page_size: int) -> None:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

    @staticmethod
    def _person(person: Person) -> PersonDisplay:
        return PersonDisplay(
            id=person.id,
            name=person.full_name,
            nickname=person.nickname,
        )

    @staticmethod
    def result_view(row: TalentTestResult) -> ResultView:
        normalized = NormalizedResult.model_validate(row.normalized_result)
        return ResultView(
            **normalized.model_dump(),
            id=row.id,
            submission_id=row.submission_id,
            created_at=row.created_at,
        )

    @classmethod
    def session_view(cls, session: TalentTestSession) -> SessionView:
        rules = RuleSnapshot.model_validate(session.rules_snapshot)
        by_test = {result.test_id: result for result in session.results}
        ordered_rows = [by_test[test_id] for test_id in rules.test_ids if test_id in by_test]
        return SessionView(
            id=session.id,
            person_id=session.person_id,
            status=_SESSION_STATUS_ADAPTER.validate_python(session.status),
            created_at=session.created_at,
            completed_at=session.completed_at,
            abandoned_at=session.abandoned_at,
            rules=rules,
            results=[cls.result_view(row) for row in ordered_rows],
            report_id=session.report.id if session.report is not None else None,
        )

    @staticmethod
    def _report_snapshot(report: TalentTestReport) -> ReportSnapshot:
        return ReportSnapshot.model_validate(report.report_snapshot)

    def context(self) -> ContextView:
        try:
            self.access.require_take()
        except TalentForbidden:
            can_take = False
        else:
            can_take = True
        try:
            self.access.require_person(self.actor.person.id)
        except TalentNotFound:
            can_read_self = False
        else:
            can_read_self = True

        can_read_reports = self.db.scalar(self.access.visible_person_ids().limit(1)) is not None
        active_session_id = None
        if can_read_self:
            active_session_id = self.db.scalar(
                select(TalentTestSession.id)
                .where(
                    TalentTestSession.person_id.in_(self.access.visible_person_ids()),
                    TalentTestSession.person_id == self.actor.person.id,
                    TalentTestSession.status == "in_progress",
                )
                .order_by(
                    TalentTestSession.created_at.desc(),
                    TalentTestSession.id.desc(),
                )
                .limit(1)
            )
        return ContextView(
            catalog=get_rules(CURRENT_CATALOG_VERSION),
            can_take=can_take,
            can_read_self=can_read_self,
            can_read_reports=can_read_reports,
            active_session_id=active_session_id,
        )

    def sessions(self, page: int, page_size: int) -> Page[SessionSummary]:
        self._validate_page(page, page_size)
        base = select(TalentTestSession).where(
            TalentTestSession.person_id.in_(self.access.visible_person_ids()),
            TalentTestSession.person_id == self.actor.person.id,
        )
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = list(
            self.db.scalars(
                base.options(
                    selectinload(TalentTestSession.results),
                    selectinload(TalentTestSession.report),
                )
                .order_by(
                    TalentTestSession.created_at.desc(),
                    TalentTestSession.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return Page[SessionSummary](
            items=[
                SessionSummary(
                    id=row.id,
                    status=_SESSION_STATUS_ADAPTER.validate_python(row.status),
                    created_at=row.created_at,
                    completed_at=row.completed_at,
                    abandoned_at=row.abandoned_at,
                    saved_count=len(row.results),
                    required_count=len(RuleSnapshot.model_validate(row.rules_snapshot).test_ids),
                    report_id=row.report.id if row.report is not None else None,
                )
                for row in rows
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    def session(self, session_id: UUID) -> SessionView:
        row = self.db.scalar(
            select(TalentTestSession)
            .where(
                TalentTestSession.id == session_id,
                self.access.visible_session_predicate(),
            )
            .options(
                selectinload(TalentTestSession.results),
                selectinload(TalentTestSession.report),
            )
        )
        if row is None:
            raise TalentNotFound("天赋测试资源不存在")
        return self.session_view(row)

    def subjects(
        self,
        name: str | None,
        page: int,
        page_size: int,
    ) -> Page[PersonDisplay]:
        self._validate_page(page, page_size)
        if name is not None and len(name) > 80:
            raise ValueError("name must not exceed 80 characters")
        completed_report = exists(
            select(TalentTestReport.id)
            .join(
                TalentTestSession,
                TalentTestSession.id == TalentTestReport.session_id,
            )
            .where(
                TalentTestSession.person_id == Person.id,
                TalentTestSession.status == "completed",
            )
        )
        base = select(Person).where(
            Person.id.in_(self.access.visible_person_ids()),
            completed_report,
        )
        if name is not None:
            escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            base = base.where(
                or_(
                    Person.full_name.ilike(pattern, escape="\\"),
                    Person.nickname.ilike(pattern, escape="\\"),
                )
            )
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        people = list(
            self.db.scalars(
                base.order_by(Person.full_name, Person.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return Page[PersonDisplay](
            items=[self._person(person) for person in people],
            total=total,
            page=page,
            page_size=page_size,
        )

    def reports(
        self,
        person_id: UUID | None,
        page: int,
        page_size: int,
    ) -> Page[ReportSummary]:
        self._validate_page(page, page_size)
        if person_id is not None:
            self.access.require_person(person_id)
        base = (
            select(TalentTestReport)
            .join(
                TalentTestSession,
                TalentTestSession.id == TalentTestReport.session_id,
            )
            .where(
                TalentTestSession.person_id.in_(self.access.visible_person_ids()),
                TalentTestSession.status == "completed",
            )
        )
        if person_id is not None:
            base = base.where(TalentTestSession.person_id == person_id)
        total = self.db.scalar(select(func.count()).select_from(base.subquery())) or 0
        rows = list(
            self.db.scalars(
                base.order_by(
                    TalentTestSession.created_at.desc(),
                    TalentTestReport.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        items: list[ReportSummary] = []
        for row in rows:
            snapshot = self._report_snapshot(row)
            items.append(
                ReportSummary(
                    id=row.id,
                    session_id=row.session_id,
                    person=snapshot.person,
                    completed_at=snapshot.completed_at,
                    catalog_version=snapshot.rules.catalog_version,
                    scoring_version=snapshot.rules.scoring_version,
                )
            )
        return Page[ReportSummary](
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    def report(self, report_id: UUID) -> ReportView:
        row = self.db.scalar(
            select(TalentTestReport)
            .join(
                TalentTestSession,
                TalentTestSession.id == TalentTestReport.session_id,
            )
            .where(
                TalentTestReport.id == report_id,
                TalentTestSession.person_id.in_(self.access.visible_person_ids()),
                TalentTestSession.status == "completed",
            )
        )
        if row is None:
            raise TalentNotFound("天赋测试资源不存在")
        snapshot = self._report_snapshot(row)
        return ReportView(id=row.id, **snapshot.model_dump())
