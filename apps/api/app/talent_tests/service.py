from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.talent_tests.access import TalentAccess
from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, get_rules
from app.talent_tests.errors import TalentConflict, TalentForbidden, TalentNotFound
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from app.talent_tests.queries import TalentQueries, find_comparable_baselines
from app.talent_tests.reporting import build_report
from app.talent_tests.schemas import (
    AbandonRequest,
    NormalizedResult,
    PersonDisplay,
    RawResultInput,
    ResultSaveView,
    RuleSnapshot,
    SessionCreateRequest,
    SessionView,
)
from app.talent_tests.scoring import fingerprint, normalize_result

_RAW_INPUT: TypeAdapter[RawResultInput] = TypeAdapter(RawResultInput)


class TalentTestService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _lock_actor(self, actor: AuthContext, now: datetime) -> AuthContext:
        """Match account/role maintenance lock order before evaluating live grants."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        person_id, account_id = actor.person.id, actor.account.id
        person = self.db.scalar(
            select(Person)
            .where(Person.id == person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        account = self.db.scalar(
            select(UserAccount)
            .where(UserAccount.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            person is None
            or person.deleted_at is not None
            or account is None
            or account.person_id != person_id
            or account.status != "active"
        ):
            raise TalentForbidden("无权进行天赋测试")
        current = AuthContext(account=account, person=person, session=actor.session)
        TalentAccess(self.db, current, now).require_take()
        return current

    def _lock_session(self, session_id: UUID, actor: AuthContext) -> TalentTestSession:
        # One mutex for *every* result and abandon. Refresh both the row and its
        # collections: a reused Session can otherwise retain a pre-lock snapshot.
        row = self.db.scalar(
            select(TalentTestSession)
            .where(
                TalentTestSession.id == session_id,
                TalentTestSession.person_id == actor.person.id,
            )
            .with_for_update()
            .options(
                selectinload(TalentTestSession.results), selectinload(TalentTestSession.report)
            )
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise TalentNotFound("天赋测试资源不存在")
        return row

    def _audit(self, action: str, actor: AuthContext, row: TalentTestSession, key: UUID) -> None:
        metadata = {
            "session_id": str(row.id),
            "person_id": str(row.person_id),
            "catalog_version": row.catalog_version,
            "scoring_version": row.scoring_version,
            "operation_key": str(key),
            "result_count": len(row.results),
        }
        if row.report is not None:
            metadata["report_id"] = str(row.report.id)
        AuditService(self.db).append(
            actor_user_id=actor.account.id,
            action=f"talent_tests.{action}",
            resource_type="talent_test_session",
            resource_id=row.id,
            purpose=None,
            outcome="allowed",
            request_id=str(key),
            metadata=metadata,
        )

    @staticmethod
    def _require_digest(existing: str | None, requested: str) -> None:
        if existing != requested:
            raise TalentConflict("操作键已用于不同请求", code="talent_replay_mismatch")

    def start(
        self, *, actor: AuthContext, request_id: UUID, catalog_version: str, now: datetime
    ) -> tuple[SessionView, bool]:
        with atomic_write_transaction(self.db):
            actor = self._lock_actor(actor, now)
            digest = fingerprint(
                SessionCreateRequest(request_id=request_id, catalog_version=catalog_version)
            )
            existing_id = self.db.scalar(
                select(TalentTestSession.id).where(
                    TalentTestSession.person_id == actor.person.id,
                    TalentTestSession.creation_key == request_id,
                )
            )
            # Replays are checked before the current active battery or current
            # catalog; a successful terminal request remains safely replayable.
            if existing_id is not None:
                existing = self._lock_session(existing_id, actor)
                self._require_digest(existing.creation_request_digest, digest)
                return TalentQueries.session_view(existing), False
            rules = get_rules(catalog_version)
            if rules.catalog_version != CURRENT_CATALOG_VERSION:
                raise TalentConflict(
                    "测试目录已更新，请刷新页面后开始新一轮",
                    code="talent_catalog_updated",
                )
            active_id = self.db.scalar(
                select(TalentTestSession.id).where(
                    TalentTestSession.person_id == actor.person.id,
                    TalentTestSession.status == "in_progress",
                )
            )
            if active_id is not None:
                raise TalentConflict(
                    "已有进行中的测试轮次",
                    code="talent_active_session",
                    active_session_id=active_id,
                )
            row = TalentTestSession(
                person_id=actor.person.id,
                created_by_user_id=actor.account.id,
                creation_key=request_id,
                creation_request_digest=digest,
                catalog_version=rules.catalog_version,
                scoring_version=rules.scoring_version,
                report_version=rules.report_version,
                rules_snapshot=rules.model_dump(mode="json"),
                status="in_progress",
                created_at=now,
                updated_at=now,
                results=[],
                report=None,
            )
            self.db.add(row)
            self.db.flush()
            self._audit("started", actor, row, request_id)
            return TalentQueries.session_view(row), True

    def submit(
        self, *, actor: AuthContext, session_id: UUID, value: RawResultInput, now: datetime
    ) -> tuple[ResultSaveView, bool]:
        with atomic_write_transaction(self.db):
            actor = self._lock_actor(actor, now)
            row = self._lock_session(session_id, actor)
            digest = fingerprint(value)
            for existing in row.results:
                if existing.submission_id == value.submission_id:
                    self._require_digest(existing.submission_digest, digest)
                    return ResultSaveView(
                        session=TalentQueries.session_view(row),
                        result=TalentQueries.result_view(existing),
                    ), False
            if any(existing.test_id == value.test_id for existing in row.results):
                raise TalentConflict("本轮已保存该项目", code="talent_test_already_saved")
            if row.status != "in_progress":
                raise TalentConflict("本轮测试已结束", code="talent_session_terminal")
            # Boundary validation protects direct service callers as well as HTTP.
            value = _RAW_INPUT.validate_python(value.model_dump(mode="json"))
            rules = RuleSnapshot.model_validate(row.rules_snapshot)
            normalized = normalize_result(value, rules)
            result = TalentTestResult(
                session=row,
                test_id=value.test_id,
                engine_version=value.engine_version,
                locale=value.locale,
                raw_result=value.raw.model_dump(mode="json"),
                device_snapshot=value.device.model_dump(mode="json"),
                normalized_result=normalized.model_dump(mode="json"),
                score=normalized.score,
                quality_flags=normalized.quality_flags,
                duration_ms=value.duration_ms,
                background_interrupted=value.background_interrupted,
                submission_id=value.submission_id,
                submission_digest=digest,
                created_at=now,
            )
            self.db.add(result)
            row.updated_at = now
            self.db.flush()
            if {saved.test_id for saved in row.results} == set(rules.test_ids):
                results = [
                    NormalizedResult.model_validate(saved.normalized_result)
                    for saved in row.results
                ]
                baselines = find_comparable_baselines(
                    self.db, person_id=actor.person.id, results=results, before=now
                )
                report = build_report(
                    session_id=row.id,
                    person=PersonDisplay(
                        id=actor.person.id,
                        name=actor.person.full_name,
                        nickname=actor.person.nickname,
                    ),
                    completed_at=now,
                    rules=rules,
                    results=results,
                    baselines=baselines,
                )
                self.db.add(
                    TalentTestReport(
                        session=row, report_snapshot=report.model_dump(mode="json"), created_at=now
                    )
                )
                row.status, row.completed_at = "completed", now
                self.db.flush()
                self._audit("completed", actor, row, value.submission_id)
            return ResultSaveView(
                session=TalentQueries.session_view(row), result=TalentQueries.result_view(result)
            ), True

    def abandon(
        self, *, actor: AuthContext, session_id: UUID, request_id: UUID, now: datetime
    ) -> SessionView:
        with atomic_write_transaction(self.db):
            actor = self._lock_actor(actor, now)
            row = self._lock_session(session_id, actor)
            digest = fingerprint(AbandonRequest(request_id=request_id))
            if row.abandon_request_id == request_id:
                self._require_digest(row.abandon_request_digest, digest)
                return TalentQueries.session_view(row)
            if row.status != "in_progress":
                raise TalentConflict("本轮测试已结束", code="talent_session_terminal")
            row.status, row.abandoned_at, row.updated_at = "abandoned", now, now
            row.abandon_request_id, row.abandon_request_digest = request_id, digest
            self.db.flush()
            self._audit("abandoned", actor, row, request_id)
            return TalentQueries.session_view(row)
