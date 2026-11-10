"""Independent practice lifecycle with shared authorization and occupancy."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction

from . import catalog
from .access import AssessmentAccess
from .catalog import validate_raw, validate_schedule
from .config_schemas import TaskConfig
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
from .models import AssessmentPracticeRun
from .quality import QualityRejected
from .reporting import comparison_key
from .schemas import (
    AttemptView,
    ControlSettings,
    EnvironmentSnapshot,
    PracticeCreate,
    PracticeResult,
    PracticeResultSaveView,
    ResultSubmit,
    validate_settings,
)
from .scoring import practice_result

_SUBMIT_WINDOW = timedelta(minutes=30)


class PracticeAssessmentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _require_digest(existing: str, requested: str) -> None:
        if existing != requested:
            raise AssessmentConflict("操作键已用于不同请求", code="esports_replay_mismatch")

    def _lock_run(self, run_id: UUID, actor: AuthContext) -> AssessmentPracticeRun:
        row = self.db.scalar(
            select(AssessmentPracticeRun)
            .where(
                AssessmentPracticeRun.id == run_id,
                AssessmentPracticeRun.person_id == actor.person.id,
                AssessmentPracticeRun.created_by_user_id == actor.account.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AssessmentNotFound()
        return row

    @staticmethod
    def _saved_view(row: AssessmentPracticeRun) -> PracticeResultSaveView:
        return PracticeResultSaveView(
            result_id=row.id,
            scored=PracticeResult.model_validate(
                {"metrics": row.metrics, "quality_flags": row.quality_flags}
            ),
        )

    def start(
        self,
        actor: AuthContext,
        request: PracticeCreate,
        now: datetime,
    ) -> AttemptView:
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            digest = operation_digest("practice", "start_practice", None, request)
            replay_id = self.db.scalar(
                select(AssessmentPracticeRun.id).where(
                    AssessmentPracticeRun.created_by_user_id == current.account.id,
                    AssessmentPracticeRun.creation_key == request.operation_key,
                )
            )
            if replay_id is not None:
                replay = self._lock_run(replay_id, current)
                self._require_digest(replay.creation_request_digest, digest)
                return attempt_view(replay, now)

            AssessmentAccess(self.db, current, now).require_available(request.suite_id)
            config = (
                catalog.get_task(request.suite_id, request.test_id, request.difficulty)
                if request.attack_mode is None
                else catalog.get_task(
                    request.suite_id,
                    request.test_id,
                    request.difficulty,
                    attack_mode=request.attack_mode,
                )
            )
            if config.catalog_version in ("pc-moba-v4", "pc-moba-v5") and request.attack_mode is None:
                raise AssessmentConflict(
                    "请选择近战或远程人物", code="esports_attack_mode_required"
                )
            validate_settings(config, request.settings, request.environment)
            occupancy = reconcile_account_occupancy(self.db, current.account.id, now)
            if occupancy is not None:
                raise AssessmentConflict(
                    "账号已有正在运行的评测项目",
                    code="esports_active_run",
                    active_session_id=occupancy.session_id,
                )
            row = AssessmentPracticeRun(
                person_id=current.person.id,
                created_by_user_id=current.account.id,
                suite_id=request.suite_id,
                test_id=request.test_id,
                difficulty=request.difficulty,
                seed=secrets.randbits(32),
                config_snapshot=config.model_dump(mode="json"),
                settings_snapshot=request.settings.model_dump(mode="json"),
                environment_snapshot=request.environment.model_dump(mode="json"),
                state="running",
                creation_key=request.operation_key,
                creation_request_digest=digest,
                run_until=now + timedelta(milliseconds=config.duration_ms + 30000),
                submit_until=now + _SUBMIT_WINDOW,
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
            self.db.flush()
            return attempt_view(row, now)

    def submit(
        self,
        actor: AuthContext,
        run_id: UUID,
        request: ResultSubmit,
        now: datetime,
    ) -> PracticeResultSaveView:
        expired = False
        saved: PracticeResultSaveView | None = None
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            digest = operation_digest("practice", "submit_result", run_id, request)
            replay_id = self.db.scalar(
                select(AssessmentPracticeRun.id).where(
                    AssessmentPracticeRun.created_by_user_id == current.account.id,
                    AssessmentPracticeRun.submission_key == request.operation_key,
                )
            )
            if replay_id is not None:
                replay = self._lock_run(replay_id, current)
                if replay.id != run_id:
                    raise AssessmentConflict("操作键已用于另一尝试", code="esports_replay_mismatch")
                self._require_digest(replay.submission_request_digest or "", digest)
                return self._saved_view(replay)

            row = self._lock_run(run_id, current)
            projected = effective_run_state(row.state, row.run_until, row.submit_until, now)
            if projected == "expired":
                row.state = "expired"
                row.updated_at = now
                self.db.flush()
                expired = True
            elif projected not in {"running", "awaiting_submission"}:
                raise AssessmentConflict("该尝试不能提交", code="esports_attempt_terminal")
            else:
                persisted = TaskConfig.model_validate(row.config_snapshot)
                config = catalog.get_task(
                    row.suite_id,
                    row.test_id,
                    row.difficulty,
                    catalog_version=persisted.catalog_version,
                    attack_mode=persisted.params.attack_mode
                    if persisted.params.kind in ("lane_last_hit", "lane_last_hit_towers")
                    else None,
                )
                if persisted != config:
                    raise AssessmentConflict("项目配置已更新", code="esports_catalog_updated")
                require_submission_elapsed(row.created_at, persisted, now)
                raw = validate_raw(persisted, request.raw)
                validate_schedule(persisted, raw, row.seed)
                try:
                    scored = practice_result(persisted, raw, request.quality)
                except QualityRejected as exc:
                    raise AssessmentConflict(
                        "本次数据质量不足，请重新完成该项目",
                        code="esports_quality_rejected",
                    ) from exc
                row.submission_key = request.operation_key
                row.submission_request_digest = digest
                row.raw_result = raw.model_dump(mode="json")
                row.metrics = scored.metrics
                row.quality_flags = list(scored.quality_flags)
                row.comparison_key = comparison_key(
                    persisted,
                    ControlSettings.model_validate(row.settings_snapshot),
                    EnvironmentSnapshot.model_validate(row.environment_snapshot),
                )
                row.state = "submitted"
                row.submitted_at = now
                row.updated_at = now
                self.db.flush()
                saved = self._saved_view(row)
        if expired:
            raise AssessmentExpired()
        assert saved is not None
        return saved

    def interrupt(
        self,
        actor: AuthContext,
        run_id: UUID,
        operation_key: UUID,
        now: datetime,
    ) -> AttemptView:
        result: AttemptView | None = None
        with atomic_write_transaction(self.db):
            current = lock_actor_and_occupancy(self.db, actor, now)
            replay_id = self.db.scalar(
                select(AssessmentPracticeRun.id).where(
                    AssessmentPracticeRun.created_by_user_id == current.account.id,
                    AssessmentPracticeRun.interrupt_key == operation_key,
                )
            )
            if replay_id is not None:
                replay = self._lock_run(replay_id, current)
                if replay.id != run_id:
                    raise AssessmentConflict("操作键已用于另一尝试", code="esports_replay_mismatch")
                return attempt_view(replay, now)

            row = self._lock_run(run_id, current)
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
                raise AssessmentConflict("该尝试不能中断", code="esports_attempt_terminal")
            self.db.flush()
            result = attempt_view(row, now)
        assert result is not None
        return result
