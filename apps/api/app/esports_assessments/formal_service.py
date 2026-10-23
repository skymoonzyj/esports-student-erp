"""Transactional formal assessment lifecycle with replay-safe writes."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction

from . import catalog
from .access import AssessmentAccess
from .catalog import validate_raw, validate_schedule
from .config_schemas import SuiteConfig, TestId
from .errors import (
    AssessmentConflict,
    AssessmentExpired,
    AssessmentNotFound,
)
from .lifecycle import (
    attempt_view,
    effective_run_state,
    operation_digest,
    reconcile_account_occupancy,
    require_submission_elapsed,
)
from .locks import lock_actor_and_occupancy
from .models import AssessmentAttempt, AssessmentReport, AssessmentResult, AssessmentSession
from .quality import QualityRejected
from .queries import AssessmentQueries
from .reporting import build_report, comparison_key
from .schemas import (
    AttemptCreate,
    AttemptView,
    ControlSettings,
    EnvironmentSnapshot,
    FormalResultSaveView,
    PersonDisplaySnapshot,
    ReportComparison,
    ReportResult,
    ResultSaveView,
    ResultSubmit,
    SessionCreate,
    SessionView,
    validate_settings,
)
from .scoring import score_result

_RUN_LEASE = timedelta(seconds=90)
_SUBMIT_WINDOW = timedelta(minutes=30)


def _frozen_comparison(
    current: ReportResult, baseline: tuple[UUID, ReportResult] | None, *, allow_delta: bool = False
) -> ReportComparison:
    """Describe actual snapshot differences; only a clean equal pair gets a delta."""
    reasons: list[str] = []
    if current.scored.quality_flags:
        reasons.append("current_quality_warning")
    if baseline is None:
        reasons.append("no_comparable_history")
        report_id, previous = None, None
    else:
        report_id, previous = baseline
        if current.config != previous.config:
            reasons.append("config_changed")
        if current.settings != previous.settings:
            reasons.append("settings_changed")
        if current.environment != previous.environment:
            reasons.append("environment_changed")
        if previous.scored.quality_flags:
            reasons.append("previous_quality_warning")
    return ReportComparison(
        test_id=current.test_id,
        previous_report_id=report_id,
        score_delta=current.scored.score - previous.scored.score
        if allow_delta and previous is not None and not reasons
        else None,
        non_comparable_reasons=tuple(reasons),
    )


class FormalAssessmentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _require_digest(existing: str, requested: str) -> None:
        if existing != requested:
            raise AssessmentConflict(
                "操作键已用于不同请求",
                code="esports_replay_mismatch",
            )

    def _lock_session(self, session_id: UUID, actor: AuthContext) -> AssessmentSession:
        row = self.db.scalar(
            select(AssessmentSession)
            .where(
                AssessmentSession.id == session_id,
                AssessmentSession.person_id == actor.person.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AssessmentNotFound()
        return row

    def _lock_attempt(
        self,
        attempt_id: UUID,
        session_id: UUID,
        actor: AuthContext,
    ) -> AssessmentAttempt:
        row = self.db.scalar(
            select(AssessmentAttempt)
            .where(
                AssessmentAttempt.id == attempt_id,
                AssessmentAttempt.session_id == session_id,
                AssessmentAttempt.created_by_user_id == actor.account.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AssessmentNotFound()
        return row

    def _session_view(
        self,
        actor: AuthContext,
        now: datetime,
        row: AssessmentSession,
    ) -> SessionView:
        return AssessmentQueries(self.db, actor, now).session_view(row)

    def _audit(
        self,
        action: str,
        actor: AuthContext,
        row: AssessmentSession,
        operation_key: UUID,
        *,
        report_id: UUID | None = None,
    ) -> None:
        suite = SuiteConfig.model_validate(row.catalog_snapshot)
        result_count = (
            self.db.scalar(
                select(func.count())
                .select_from(AssessmentResult)
                .where(AssessmentResult.session_id == row.id)
            )
            or 0
        )
        metadata = {
            "session_id": str(row.id),
            "person_id": str(row.person_id),
            "operation_key": str(operation_key),
            "suite_id": row.suite_id,
            "catalog_version": suite.catalog_version,
            "scoring_version": suite.tasks[suite.test_ids[0]].scoring_version,
            "result_count": result_count,
        }
        if report_id is not None:
            metadata["report_id"] = str(report_id)
        AuditService(self.db).append(
            actor_user_id=actor.account.id,
            action=f"esports_assessments.{action}",
            resource_type="esports_assessment_session",
            resource_id=row.id,
            purpose=None,
            outcome="allowed",
            request_id=str(operation_key),
            metadata=metadata,
        )

    @staticmethod
    def _validate_formal_environment(
        suite_id: str,
        settings: ControlSettings,
        environment: EnvironmentSnapshot,
    ) -> None:
        if suite_id != "pc-fps":
            return
        if not environment.pointer_lock_active:
            raise ValueError("PC FPS requires active Pointer Lock")
        if settings.mouse_mode == "raw" and not (
            environment.raw_mouse_requested and environment.raw_mouse_confirmed
        ):
            raise ValueError("raw mouse mode was not confirmed")
        if settings.mouse_mode == "relative" and environment.raw_mouse_confirmed:
            raise ValueError("relative mouse mode conflicts with confirmed raw input")

    def create(
        self,
        actor: AuthContext,
        request: SessionCreate,
        now: datetime,
    ) -> SessionView:
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            digest = operation_digest("formal", "create_session", None, request)
            existing_id = self.db.scalar(
                select(AssessmentSession.id).where(
                    AssessmentSession.created_by_user_id == current.account.id,
                    AssessmentSession.creation_key == request.operation_key,
                )
            )
            if existing_id is not None:
                existing = self._lock_session(existing_id, current)
                self._require_digest(existing.creation_request_digest, digest)
                return self._session_view(current, now, existing)

            AssessmentAccess(self.db, current, now).require_available(request.suite_id)
            active_id = self.db.scalar(
                select(AssessmentSession.id).where(
                    AssessmentSession.person_id == current.person.id,
                    AssessmentSession.suite_id == request.suite_id,
                    AssessmentSession.status == "in_progress",
                )
            )
            if active_id is not None:
                raise AssessmentConflict(
                    "该专项已有进行中的正式轮次",
                    code="esports_active_session",
                    active_session_id=active_id,
                )
            suite = catalog.get_suite(request.suite_id)
            snapshot = suite.model_dump(mode="json")
            row = AssessmentSession(
                person_id=current.person.id,
                created_by_user_id=current.account.id,
                suite_id=request.suite_id,
                creation_key=request.operation_key,
                creation_request_digest=digest,
                catalog_snapshot=snapshot,
                scoring_snapshot=snapshot,
                report_rules_snapshot=snapshot,
                status="in_progress",
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
            self.db.flush()
            self._audit("started", current, row, request.operation_key)
            return self._session_view(current, now, row)

    def start_attempt(
        self,
        actor: AuthContext,
        session_id: UUID,
        request: AttemptCreate,
        now: datetime,
    ) -> AttemptView:
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            digest = operation_digest("formal", "start_attempt", session_id, request)
            replay = self.db.execute(
                select(AssessmentAttempt.id, AssessmentAttempt.session_id).where(
                    AssessmentAttempt.created_by_user_id == current.account.id,
                    AssessmentAttempt.creation_key == request.operation_key,
                )
            ).first()
            if replay is not None:
                replay_id, replay_session_id = replay
                replay_session = self._lock_session(replay_session_id, current)
                replay_row = self._lock_attempt(replay_id, replay_session.id, current)
                if replay_session_id != session_id:
                    raise AssessmentConflict(
                        "操作键已用于另一轮次",
                        code="esports_replay_mismatch",
                    )
                self._require_digest(replay_row.creation_request_digest, digest)
                return attempt_view(replay_row, now)

            session = self._lock_session(session_id, current)
            if session.status != "in_progress":
                raise AssessmentConflict(
                    "正式轮次已结束",
                    code="esports_session_terminal",
                )
            AssessmentAccess(self.db, current, now).require_available(session.suite_id)
            suite = SuiteConfig.model_validate(session.catalog_snapshot)
            if request.test_id not in suite.test_ids:
                raise AssessmentConflict(
                    "项目不属于该正式轮次",
                    code="esports_test_not_in_suite",
                )
            config = suite.tasks[request.test_id]
            validate_settings(config, request.settings, request.environment)
            self._validate_formal_environment(
                session.suite_id, request.settings, request.environment
            )

            occupancy = reconcile_account_occupancy(self.db, current.account.id, now)
            if occupancy is not None:
                raise AssessmentConflict(
                    "账号已有正在运行的评测项目",
                    code="esports_active_run",
                    active_session_id=occupancy.session_id,
                )
            if (
                self.db.scalar(
                    select(AssessmentResult.id).where(
                        AssessmentResult.session_id == session.id,
                        AssessmentResult.test_id == request.test_id,
                    )
                )
                is not None
            ):
                raise AssessmentConflict(
                    "本轮已保存该项目",
                    code="esports_test_already_saved",
                )
            if (
                self.db.scalar(
                    select(AssessmentAttempt.id).where(
                        AssessmentAttempt.session_id == session.id,
                        AssessmentAttempt.test_id == request.test_id,
                        AssessmentAttempt.state.in_(("running", "awaiting_submission")),
                    )
                )
                is not None
            ):
                raise AssessmentConflict(
                    "该项目仍有待处理尝试",
                    code="esports_test_attempt_pending",
                )
            sequence_no = (
                self.db.scalar(
                    select(func.max(AssessmentAttempt.sequence_no)).where(
                        AssessmentAttempt.session_id == session.id,
                        AssessmentAttempt.test_id == request.test_id,
                    )
                )
                or 0
            ) + 1
            row = AssessmentAttempt(
                session_id=session.id,
                created_by_user_id=current.account.id,
                test_id=request.test_id,
                sequence_no=sequence_no,
                seed=secrets.randbits(32),
                config_snapshot=config.model_dump(mode="json"),
                settings_snapshot=request.settings.model_dump(mode="json"),
                environment_snapshot=request.environment.model_dump(mode="json"),
                state="running",
                creation_key=request.operation_key,
                creation_request_digest=digest,
                run_until=now + _RUN_LEASE,
                submit_until=now + _SUBMIT_WINDOW,
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
            self.db.flush()
            return attempt_view(row, now)

    def _result_view(
        self,
        actor: AuthContext,
        now: datetime,
        session: AssessmentSession,
        result: AssessmentResult,
    ) -> FormalResultSaveView:
        return FormalResultSaveView(
            result_id=result.id,
            scored=AssessmentQueries.scored_result(result),
            session=self._session_view(actor, now, session),
        )

    @staticmethod
    def _report_result(result: AssessmentResult, attempt: AssessmentAttempt) -> ReportResult:
        return ReportResult.model_validate(
            {
                "result_id": result.id,
                "test_id": result.test_id,
                "config": attempt.config_snapshot,
                "settings": attempt.settings_snapshot,
                "environment": attempt.environment_snapshot,
                "scored": {
                    "metrics": result.metrics,
                    "score": result.score,
                    "grade": result.grade,
                    "quality_flags": result.quality_flags,
                },
            }
        )

    def _complete_report(
        self,
        actor: AuthContext,
        session: AssessmentSession,
        now: datetime,
        operation_key: UUID,
    ) -> UUID | None:
        suite = SuiteConfig.model_validate(session.catalog_snapshot)
        rows = self.db.execute(
            select(AssessmentResult, AssessmentAttempt)
            .join(AssessmentAttempt, AssessmentAttempt.id == AssessmentResult.attempt_id)
            .where(AssessmentResult.session_id == session.id)
        ).all()
        by_test = {result.test_id: (result, attempt) for result, attempt in rows}
        if set(by_test) != set(suite.test_ids):
            return None
        report_results = tuple(self._report_result(*by_test[test_id]) for test_id in suite.test_ids)
        queries = AssessmentQueries(self.db, actor, now)
        comparisons: list[ReportComparison] = []
        for item, test_id in zip(report_results, suite.test_ids, strict=True):
            baseline = queries.comparable_result(
                session.person_id,
                suite.suite_id,
                test_id,
                comparison_key(item.config, item.settings, item.environment),
                now,
                session.id,
            )
            allow_delta = baseline is not None
            if baseline is None:
                baseline = queries.previous_result(
                    session.person_id, suite.suite_id, test_id, now, session.id
                )
            comparisons.append(_frozen_comparison(item, baseline, allow_delta=allow_delta))
        report_id = uuid4()
        snapshot = build_report(
            session.id,
            PersonDisplaySnapshot(
                person_id=actor.person.id,
                display_name=actor.person.nickname or actor.person.full_name,
            ),
            suite,
            report_results,
            now,
            report_id=report_id,
            comparisons=comparisons,
        )
        self.db.add(
            AssessmentReport(
                id=report_id,
                session_id=session.id,
                report_snapshot=snapshot.model_dump(mode="json"),
                created_at=now,
            )
        )
        session.status = "completed"
        session.completed_at = now
        session.updated_at = now
        self.db.flush()
        self._audit("completed", actor, session, operation_key, report_id=report_id)
        return report_id

    def submit(
        self,
        actor: AuthContext,
        attempt_id: UUID,
        request: ResultSubmit,
        now: datetime,
    ) -> ResultSaveView:
        expired = False
        saved: FormalResultSaveView | None = None
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            digest = operation_digest("formal", "submit_result", attempt_id, request)
            replay = self.db.execute(
                select(
                    AssessmentResult.id,
                    AssessmentResult.attempt_id,
                    AssessmentResult.session_id,
                ).where(
                    AssessmentResult.created_by_user_id == current.account.id,
                    AssessmentResult.submission_key == request.operation_key,
                )
            ).first()
            if replay is not None:
                result_id, replay_attempt_id, replay_session_id = replay
                session = self._lock_session(replay_session_id, current)
                self._lock_attempt(replay_attempt_id, replay_session_id, current)
                result = self.db.scalar(
                    select(AssessmentResult)
                    .where(AssessmentResult.id == result_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                assert result is not None
                if replay_attempt_id != attempt_id:
                    raise AssessmentConflict(
                        "操作键已用于另一尝试",
                        code="esports_replay_mismatch",
                    )
                self._require_digest(result.submission_request_digest, digest)
                return self._result_view(current, now, session, result)

            owner = self.db.execute(
                select(AssessmentAttempt.session_id).where(
                    AssessmentAttempt.id == attempt_id,
                    AssessmentAttempt.created_by_user_id == current.account.id,
                )
            ).scalar_one_or_none()
            if owner is None:
                raise AssessmentNotFound()
            session = self._lock_session(owner, current)
            attempt = self._lock_attempt(attempt_id, owner, current)
            projected = effective_run_state(
                attempt.state, attempt.run_until, attempt.submit_until, now
            )
            if projected == "expired":
                attempt.state = "expired"
                attempt.updated_at = now
                self.db.flush()
                expired = True
            else:
                if session.status != "in_progress":
                    raise AssessmentConflict(
                        "正式轮次已结束",
                        code="esports_session_terminal",
                    )
                if projected not in {"running", "awaiting_submission"}:
                    raise AssessmentConflict(
                        "该尝试不能提交",
                        code="esports_attempt_terminal",
                    )
                config = SuiteConfig.model_validate(session.catalog_snapshot).tasks[
                    cast(TestId, attempt.test_id)
                ]
                require_submission_elapsed(attempt.created_at, config, now)
                raw = validate_raw(config, request.raw)
                validate_schedule(config, raw, attempt.seed)
                try:
                    scored = score_result(config, raw, request.quality)
                except QualityRejected as exc:
                    raise AssessmentConflict(
                        "本次运行质量不符合正式保存要求",
                        code="esports_quality_rejected",
                    ) from exc
                result = AssessmentResult(
                    attempt_id=attempt.id,
                    session_id=session.id,
                    test_id=attempt.test_id,
                    created_by_user_id=current.account.id,
                    submission_key=request.operation_key,
                    submission_request_digest=digest,
                    raw_result=raw.model_dump(mode="json"),
                    metrics=scored.metrics,
                    score=scored.score,
                    grade=scored.grade,
                    quality_flags=list(scored.quality_flags),
                    comparison_key=comparison_key(
                        config,
                        ControlSettings.model_validate(attempt.settings_snapshot),
                        EnvironmentSnapshot.model_validate(attempt.environment_snapshot),
                    ),
                    created_at=now,
                )
                self.db.add(result)
                attempt.state = "submitted"
                attempt.submitted_at = now
                attempt.updated_at = now
                session.updated_at = now
                self.db.flush()
                self._complete_report(current, session, now, request.operation_key)
                saved = self._result_view(current, now, session, result)
        if expired:
            raise AssessmentExpired()
        assert saved is not None
        return saved

    def interrupt(
        self,
        actor: AuthContext,
        attempt_id: UUID,
        operation_key: UUID,
        now: datetime,
    ) -> AttemptView:
        result: AttemptView | None = None
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            replay = self.db.execute(
                select(AssessmentAttempt.id, AssessmentAttempt.session_id).where(
                    AssessmentAttempt.created_by_user_id == current.account.id,
                    AssessmentAttempt.interrupt_key == operation_key,
                )
            ).first()
            if replay is not None:
                replay_id, replay_session_id = replay
                session = self._lock_session(replay_session_id, current)
                row = self._lock_attempt(replay_id, session.id, current)
                if replay_id != attempt_id:
                    raise AssessmentConflict(
                        "操作键已用于另一尝试",
                        code="esports_replay_mismatch",
                    )
                return attempt_view(row, now)

            owner = self.db.scalar(
                select(AssessmentAttempt.session_id).where(
                    AssessmentAttempt.id == attempt_id,
                    AssessmentAttempt.created_by_user_id == current.account.id,
                )
            )
            if owner is None:
                raise AssessmentNotFound()
            session = self._lock_session(owner, current)
            row = self._lock_attempt(attempt_id, session.id, current)
            projected = effective_run_state(row.state, row.run_until, row.submit_until, now)
            if projected == "expired":
                row.state = "expired"
                row.updated_at = now
            elif projected in {"running", "awaiting_submission"}:
                row.state = "interrupted"
                row.interrupt_key = operation_key
                row.interrupted_at = now
                row.updated_at = now
            else:
                raise AssessmentConflict(
                    "该尝试不能中断",
                    code="esports_attempt_terminal",
                )
            self.db.flush()
            result = attempt_view(row, now)
        assert result is not None
        return result

    def abandon(
        self,
        actor: AuthContext,
        session_id: UUID,
        operation_key: UUID,
        now: datetime,
    ) -> SessionView:
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            digest = operation_digest(
                "formal",
                "abandon_session",
                session_id,
                {"operation_key": operation_key},
            )
            replay_id = self.db.scalar(
                select(AssessmentSession.id).where(
                    AssessmentSession.created_by_user_id == current.account.id,
                    AssessmentSession.abandon_key == operation_key,
                )
            )
            if replay_id is not None:
                replay = self._lock_session(replay_id, current)
                if replay.id != session_id:
                    raise AssessmentConflict(
                        "操作键已用于另一轮次",
                        code="esports_replay_mismatch",
                    )
                self._require_digest(cast(str, replay.abandon_request_digest), digest)
                return self._session_view(current, now, replay)

            session = self._lock_session(session_id, current)
            if session.status != "in_progress":
                raise AssessmentConflict(
                    "正式轮次已结束",
                    code="esports_session_terminal",
                )
            attempts = list(
                self.db.scalars(
                    select(AssessmentAttempt)
                    .where(AssessmentAttempt.session_id == session.id)
                    .order_by(AssessmentAttempt.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            for attempt in attempts:
                if attempt.state in {"running", "awaiting_submission", "expired"}:
                    attempt.state = "interrupted"
                    attempt.interrupted_at = now
                    attempt.updated_at = now
            session.status = "abandoned"
            session.abandon_key = operation_key
            session.abandon_request_digest = digest
            session.abandoned_at = now
            session.updated_at = now
            self.db.flush()
            self._audit("abandoned", current, session, operation_key)
            return self._session_view(current, now, session)
