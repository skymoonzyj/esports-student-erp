from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from threading import Barrier, Event, Lock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, delete, event, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.admissions.models import AdmissionCase, Enrollment
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.evaluations.errors import (
    EvaluationResourceNotFound,
    TeachingReportDraftVersionConflict,
    TeachingReportStateConflict,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.report_schemas import (
    TeachingReportDraftStartInput,
    TeachingReportReviewInput,
    TeachingReportSubmitInput,
)
from app.evaluations.report_service import TeachingReportService
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
)
from app.evaluations.task_models import EvaluationIdempotencyKey
from app.identity.models import (
    GuardianRelationship,
    Person,
    StaffProfile,
    StudentProfile,
)
from app.rbac.models import Permission, UserPermissionOverride, UserRole
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from tests.services.test_teaching_report_access import (
    ReportAccessScenario,
    create_report_access_scenario,
)
from tests.services.test_teaching_reports import NOW, draft_input


@dataclass(frozen=True)
class ConcurrentDraftContext:
    engine: Engine
    scenario: ReportAccessScenario


@dataclass(frozen=True)
class ConcurrentWorkflowContext(ConcurrentDraftContext):
    report_id: UUID
    lock_version: int
    draft_revision: int | None


class CohortLockProbe:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._guard = Lock()
        self._second_attempt = Event()
        self._attempts: dict[int, int] = {}
        self.attempt_count = 0

    @staticmethod
    def _matches(statement: str) -> bool:
        normalized = " ".join(statement.upper().split())
        return "FROM COHORTS" in normalized and "FOR UPDATE" in normalized

    def before(
        self,
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if not self._matches(statement):
            return
        with self._guard:
            self.attempt_count += 1
            self._attempts[id(execution_context)] = self.attempt_count
            if self.attempt_count == 2:
                self._second_attempt.set()

    def after(
        self,
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if (
            self._matches(statement)
            and self._attempts[id(execution_context)] == 1
        ):
            assert self._second_attempt.wait(10), "second action did not contend"

    def install(self) -> None:
        event.listen(self.engine, "before_cursor_execute", self.before)
        event.listen(self.engine, "after_cursor_execute", self.after)

    def remove(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self.before)
        event.remove(self.engine, "after_cursor_execute", self.after)


def _service(db: Session) -> TeachingReportService:
    return TeachingReportService(
        db,
        idempotency=EvaluationIdempotencyService(
            db,
            digest_key=b"r" * 32,
        ),
    )


def _seed_context(engine: Engine) -> ConcurrentDraftContext:
    with Session(engine, expire_on_commit=False) as db:
        scenario = create_report_access_scenario(db)
        db.commit()
    return ConcurrentDraftContext(engine=engine, scenario=scenario)


def _cleanup_context(context: ConcurrentDraftContext) -> None:
    scenario = context.scenario
    account_ids = {account.id for account in scenario.accounts.values()}
    person_ids = {person.id for person in scenario.people.values()}
    actor_keys = {str(account_id) for account_id in account_ids}
    with Session(context.engine) as db, db.begin():
        database_name = db.scalar(select(text("current_database()")))
        assert isinstance(database_name, str) and (
            database_name == "erp_e2e" or database_name.endswith("_test")
        )

        report_ids = set(
            db.scalars(
                select(StudentReport.id).where(
                    StudentReport.enrollment_id == scenario.enrollment.id
                )
            )
        )
        if report_ids:
            db.execute(
                update(StudentReport)
                .where(StudentReport.id.in_(report_ids))
                .values(
                    status="draft",
                    current_version_id=None,
                    published_version_id=None,
                    current_window_grant_id=None,
                    submitted_at=None,
                    correction_due_at=None,
                    published_at=None,
                )
            )
            db.execute(
                delete(StudentReportDraft).where(
                    StudentReportDraft.report_id.in_(report_ids)
                )
            )
            history_tables = (
                (
                    "student_report_reviews",
                    "student_report_reviews_immutable",
                    StudentReportReview,
                ),
                (
                    "student_report_window_grants",
                    "student_report_window_grants_immutable",
                    StudentReportWindowGrant,
                ),
                (
                    "student_report_versions",
                    "student_report_versions_immutable",
                    StudentReportVersion,
                ),
            )
            for table_name, trigger_name, _ in history_tables:
                db.execute(
                    text(f"ALTER TABLE {table_name} DISABLE TRIGGER {trigger_name}")
                )
            try:
                for _, _, model in history_tables:
                    db.execute(delete(model).where(model.report_id.in_(report_ids)))
            finally:
                for table_name, trigger_name, _ in history_tables:
                    db.execute(
                        text(
                            f"ALTER TABLE {table_name} ENABLE TRIGGER {trigger_name}"
                        )
                    )
            db.execute(
                delete(StudentReport).where(StudentReport.id.in_(report_ids))
            )

        db.execute(
            delete(EvaluationIdempotencyKey).where(
                or_(
                    EvaluationIdempotencyKey.actor_key.in_(actor_keys),
                    EvaluationIdempotencyKey.resource_id.in_(report_ids),
                )
            )
        )
        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        db.execute(
            delete(AuditEvent).where(
                or_(
                    AuditEvent.actor_user_id.in_(account_ids),
                    AuditEvent.resource_id.in_(report_ids),
                )
            )
        )
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))

        db.execute(
            delete(CohortStaffAssignmentPermission).where(
                CohortStaffAssignmentPermission.assignment_id
                == scenario.staff_assignment.id
            )
        )
        db.execute(
            delete(UserPermissionOverride).where(
                or_(
                    UserPermissionOverride.user_id.in_(account_ids),
                    UserPermissionOverride.created_by_user_id.in_(account_ids),
                )
            )
        )
        db.execute(
            delete(CoachAssignment).where(
                CoachAssignment.id.in_(
                    {
                        scenario.primary_assignment.id,
                        scenario.assistant_assignment.id,
                    }
                )
            )
        )
        db.execute(
            delete(CohortStaffAssignment).where(
                CohortStaffAssignment.id == scenario.staff_assignment.id
            )
        )
        db.execute(
            delete(GuardianRelationship).where(
                or_(
                    GuardianRelationship.guardian_person_id.in_(person_ids),
                    GuardianRelationship.student_person_id.in_(person_ids),
                )
            )
        )
        db.execute(
            update(AdmissionCase)
            .where(AdmissionCase.id == scenario.enrollment.admission_case_id)
            .values(status="ready", current_enrollment_id=None)
        )
        db.execute(delete(Enrollment).where(Enrollment.id == scenario.enrollment.id))
        db.execute(
            delete(AdmissionCase).where(
                AdmissionCase.id == scenario.enrollment.admission_case_id
            )
        )
        db.execute(
            delete(TeachingClass).where(
                TeachingClass.id == scenario.teaching_class.id
            )
        )
        db.execute(delete(Cohort).where(Cohort.id == scenario.cohort.id))
        db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
        db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
        db.execute(
            delete(StudentProfile).where(StudentProfile.person_id.in_(person_ids))
        )
        db.execute(delete(StaffProfile).where(StaffProfile.person_id.in_(person_ids)))
        db.execute(delete(Person).where(Person.id.in_(person_ids)))


def _seed_with_cleanup[ContextT: ConcurrentDraftContext](
    engine: Engine,
    request: pytest.FixtureRequest,
    seed: Callable[[Engine], ContextT],
) -> ContextT:
    context = seed(engine)
    request.addfinalizer(partial(_cleanup_context, context))
    return context


def _seed_draft_context(engine: Engine) -> ConcurrentWorkflowContext:
    context = _seed_context(engine)
    with Session(engine, expire_on_commit=False) as db:
        created = _service(db).save_draft(
            draft_input(context.scenario, body="并发工作流日报"),
            actor_user_id=context.scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=f"seed-report-draft-{uuid4()}",
            request_id=f"seed-report-draft-{uuid4()}",
        )
        return ConcurrentWorkflowContext(
            engine=engine,
            scenario=context.scenario,
            report_id=created.report.id,
            lock_version=created.report.lock_version,
            draft_revision=created.draft.draft_revision,
        )


def _seed_pending_context(engine: Engine) -> ConcurrentWorkflowContext:
    context = _seed_draft_context(engine)
    assert context.draft_revision is not None
    with Session(engine, expire_on_commit=False) as db:
        submitted = _service(db).submit(
            context.report_id,
            TeachingReportSubmitInput(
                expected_lock_version=context.lock_version,
                expected_draft_revision=context.draft_revision,
            ),
            actor_user_id=context.scenario.accounts["primary"].id,
            occurred_at=NOW,
            idempotency_key=f"seed-report-submit-{uuid4()}",
            request_id=f"seed-report-submit-{uuid4()}",
        )
        return ConcurrentWorkflowContext(
            engine=engine,
            scenario=context.scenario,
            report_id=submitted.report.id,
            lock_version=submitted.report.lock_version,
            draft_revision=None,
        )


def _seed_revision_pending_context(engine: Engine) -> ConcurrentWorkflowContext:
    context = _seed_pending_context(engine)
    with Session(engine, expire_on_commit=False) as db:
        service = _service(db)
        approved = service.review(
            context.report_id,
            TeachingReportReviewInput(
                expected_lock_version=context.lock_version,
                decision="approved",
                reason=None,
            ),
            reviewer_user_id=context.scenario.accounts["admin"].id,
            occurred_at=NOW + timedelta(hours=1),
            idempotency_key=f"seed-report-review-{uuid4()}",
            request_id=f"seed-report-review-{uuid4()}",
        )
        revision = service.start_revision(
            context.report_id,
            TeachingReportDraftStartInput(
                expected_lock_version=approved.report.lock_version,
            ),
            actor_user_id=context.scenario.accounts["primary"].id,
            occurred_at=NOW + timedelta(hours=2),
            idempotency_key=f"seed-report-revision-{uuid4()}",
            request_id=f"seed-report-revision-{uuid4()}",
        )
        submitted = service.submit(
            context.report_id,
            TeachingReportSubmitInput(
                expected_lock_version=revision.report.lock_version,
                expected_draft_revision=revision.draft.draft_revision,
            ),
            actor_user_id=context.scenario.accounts["primary"].id,
            occurred_at=NOW + timedelta(hours=2, minutes=10),
            idempotency_key=f"seed-report-submit-{uuid4()}",
            request_id=f"seed-report-submit-{uuid4()}",
        )
        return ConcurrentWorkflowContext(
            engine=engine,
            scenario=context.scenario,
            report_id=submitted.report.id,
            lock_version=submitted.report.lock_version,
            draft_revision=None,
        )


def _allow_outsider_review(context: ConcurrentWorkflowContext) -> None:
    with Session(context.engine) as db:
        permission_id = db.scalar(
            select(Permission.id).where(Permission.key == "student_reports.review")
        )
        assert permission_id is not None
        db.add(
            UserPermissionOverride(
                user_id=context.scenario.accounts["outsider"].id,
                permission_id=permission_id,
                effect="allow",
                scope_kind="assigned_class",
                scope_id=context.scenario.teaching_class.id,
                valid_from=NOW - timedelta(days=1),
                valid_until=NOW + timedelta(days=30),
                created_by_user_id=context.scenario.accounts["admin"].id,
                grant_reason="并发审核测试授权",
            )
        )
        db.commit()


def _run_concurrent_creates(
    context: ConcurrentDraftContext,
    bodies: tuple[str, str],
) -> tuple[list[tuple[str, UUID | None, bool]], int]:
    start = Barrier(2)
    attempt_lock = Lock()
    second_lock_attempt = Event()
    attempts: dict[int, int] = {}
    attempt_count = 0

    def is_cohort_lock(statement: str) -> bool:
        normalized = " ".join(statement.upper().split())
        return "FROM COHORTS" in normalized and "FOR UPDATE" in normalized

    def before_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        nonlocal attempt_count
        del connection, cursor, parameters, executemany
        if not is_cohort_lock(statement):
            return
        with attempt_lock:
            attempt_count += 1
            attempts[id(execution_context)] = attempt_count
            if attempt_count == 2:
                second_lock_attempt.set()

    def after_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        del connection, cursor, parameters, executemany
        if (
            is_cohort_lock(statement)
            and attempts[id(execution_context)] == 1
        ):
            assert second_lock_attempt.wait(10), "second draft did not contend"

    event.listen(context.engine, "before_cursor_execute", before_cursor_execute)
    event.listen(context.engine, "after_cursor_execute", after_cursor_execute)

    def create(body: str) -> tuple[str, UUID | None, bool]:
        with Session(context.engine, expire_on_commit=False) as db:
            service = TeachingReportService(
                db,
                idempotency=EvaluationIdempotencyService(
                    db,
                    digest_key=b"r" * 32,
                ),
            )
            start.wait(timeout=10)
            try:
                created = service.save_draft(
                    draft_input(context.scenario, body=body),
                    actor_user_id=context.scenario.accounts["primary"].id,
                    occurred_at=NOW,
                    idempotency_key=f"concurrent-report-draft-{uuid4()}",
                    request_id=f"concurrent-report-draft-{uuid4()}",
                )
                return "ok", created.report.id, db.scalar(select(1)) == 1
            except TeachingReportDraftVersionConflict:
                return "conflict", None, db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, bodies, timeout=20))
    finally:
        event.remove(context.engine, "before_cursor_execute", before_cursor_execute)
        event.remove(context.engine, "after_cursor_execute", after_cursor_execute)
    return results, attempt_count


def test_concurrent_identical_creates_merge_into_one_draft(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    context = _seed_with_cleanup(postgres_engine, request, _seed_context)

    results, attempt_count = _run_concurrent_creates(
        context,
        ("并发创建的同一份日报", "并发创建的同一份日报"),
    )

    assert attempt_count == 2
    assert [status for status, _, _ in results] == ["ok", "ok"]
    assert len({report_id for _, report_id, _ in results}) == 1
    assert all(usable for _, _, usable in results)
    with Session(postgres_engine) as db:
        assert db.scalar(
            select(func.count(StudentReport.id)).where(
                StudentReport.enrollment_id == context.scenario.enrollment.id
            )
        ) == 1
        assert db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "student_report.draft.save",
                AuditEvent.resource_id == results[0][1],
            )
        ) == 1
        assert db.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.scope == "teaching_report.draft.save",
                EvaluationIdempotencyKey.actor_key
                == str(context.scenario.accounts["primary"].id),
            )
        ) == 2


def test_concurrent_different_creates_leave_one_conflict_and_a_usable_session(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    context = _seed_with_cleanup(postgres_engine, request, _seed_context)

    results, attempt_count = _run_concurrent_creates(
        context,
        ("并发日报甲", "并发日报乙"),
    )

    assert attempt_count == 2
    assert sorted(status for status, _, _ in results) == ["conflict", "ok"]
    assert all(usable for _, _, usable in results)
    with Session(postgres_engine) as db:
        assert db.scalar(
            select(func.count(StudentReport.id)).where(
                StudentReport.enrollment_id == context.scenario.enrollment.id
            )
        ) == 1


def test_concurrent_double_submit_has_one_version_and_a_usable_conflict_session(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    context = _seed_with_cleanup(postgres_engine, request, _seed_draft_context)
    assert context.draft_revision is not None
    draft_revision = context.draft_revision
    start = Barrier(2)
    probe = CohortLockProbe(postgres_engine)
    probe.install()

    def submit() -> tuple[str, bool]:
        with Session(postgres_engine, expire_on_commit=False) as db:
            start.wait(timeout=10)
            try:
                _service(db).submit(
                    context.report_id,
                    TeachingReportSubmitInput(
                        expected_lock_version=context.lock_version,
                        expected_draft_revision=draft_revision,
                    ),
                    actor_user_id=context.scenario.accounts["primary"].id,
                    occurred_at=NOW,
                    idempotency_key=f"concurrent-report-submit-{uuid4()}",
                    request_id=f"concurrent-report-submit-{uuid4()}",
                )
                return "submitted", db.scalar(select(1)) == 1
            except TeachingReportStateConflict:
                return "conflict", db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2), timeout=20))
    finally:
        probe.remove()

    assert probe.attempt_count == 2
    assert sorted(status for status, _ in results) == ["conflict", "submitted"]
    assert all(usable for _, usable in results)
    with Session(postgres_engine) as db:
        report = db.get(StudentReport, context.report_id)
        assert report is not None and report.status == "pending_review"
        assert db.scalar(
            select(func.count(StudentReportVersion.id)).where(
                StudentReportVersion.report_id == context.report_id
            )
        ) == 1
        assert db.get(StudentReportDraft, context.report_id) is None


def test_two_reviewers_contend_and_only_one_decision_persists(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    context = _seed_with_cleanup(postgres_engine, request, _seed_pending_context)
    _allow_outsider_review(context)
    start = Barrier(2)
    probe = CohortLockProbe(postgres_engine)
    probe.install()
    reviewers = (
        (context.scenario.accounts["admin"].id, "approved", None),
        (
            context.scenario.accounts["outsider"].id,
            "rejected",
            "并发审核驳回原因",
        ),
    )

    def review(spec: tuple[UUID, str, str | None]) -> tuple[str, bool]:
        reviewer_id, decision, reason = spec
        with Session(postgres_engine, expire_on_commit=False) as db:
            start.wait(timeout=10)
            try:
                _service(db).review(
                    context.report_id,
                    TeachingReportReviewInput.model_validate(
                        {
                            "expected_lock_version": context.lock_version,
                            "decision": decision,
                            "reason": reason,
                        }
                    ),
                    reviewer_user_id=reviewer_id,
                    occurred_at=NOW + timedelta(hours=1),
                    idempotency_key=f"concurrent-report-review-{uuid4()}",
                    request_id=f"concurrent-report-review-{uuid4()}",
                )
                return decision, db.scalar(select(1)) == 1
            except TeachingReportStateConflict:
                return "conflict", db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, reviewers, timeout=20))
    finally:
        probe.remove()

    assert probe.attempt_count == 2
    assert [status for status, _ in results].count("conflict") == 1
    assert all(usable for _, usable in results)
    with Session(postgres_engine) as db:
        reviews = list(
            db.scalars(
                select(StudentReportReview).where(
                    StudentReportReview.report_id == context.report_id
                )
            )
        )
        assert len(reviews) == 1
        report = db.get(StudentReport, context.report_id)
        assert report is not None
        assert report.status == (
            "published" if reviews[0].decision == "approved" else "rejected"
        )


def test_review_rechecks_a_concurrent_revocation_before_writing(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    context = _seed_with_cleanup(postgres_engine, request, _seed_pending_context)
    review_reached_lock = Event()
    revocation_committed = Event()
    callback_guard = Lock()
    callback_used = False

    def before_cursor_execute(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: object,
        executemany: bool,
    ) -> None:
        nonlocal callback_used
        del connection, cursor, parameters, execution_context, executemany
        normalized = " ".join(statement.upper().split())
        if "FROM COHORTS" not in normalized or "FOR UPDATE" not in normalized:
            return
        with callback_guard:
            if callback_used:
                return
            callback_used = True
        review_reached_lock.set()
        assert revocation_committed.wait(10), "revocation did not commit"

    event.listen(postgres_engine, "before_cursor_execute", before_cursor_execute)

    def review() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            try:
                _service(db).review(
                    context.report_id,
                    TeachingReportReviewInput(
                        expected_lock_version=context.lock_version,
                        decision="approved",
                        reason=None,
                    ),
                    reviewer_user_id=context.scenario.accounts["admin"].id,
                    occurred_at=NOW + timedelta(hours=1),
                    idempotency_key=f"concurrent-report-review-{uuid4()}",
                    request_id=f"concurrent-report-review-{uuid4()}",
                )
                return "reviewed", db.scalar(select(1)) == 1
            except EvaluationResourceNotFound:
                return "revoked", db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(review)
            assert review_reached_lock.wait(10), "review did not reach lock boundary"
            with Session(postgres_engine) as revoke_db:
                permission_id = revoke_db.scalar(
                    select(Permission.id).where(
                        Permission.key == "student_reports.review"
                    )
                )
                assert permission_id is not None
                revoke_db.add(
                    UserPermissionOverride(
                        user_id=context.scenario.accounts["admin"].id,
                        permission_id=permission_id,
                        effect="deny",
                        scope_kind="enrollment",
                        scope_id=context.scenario.enrollment.id,
                        valid_from=NOW,
                        valid_until=NOW + timedelta(days=30),
                        created_by_user_id=context.scenario.accounts["admin"].id,
                        grant_reason="并发撤销审核权限",
                    )
                )
                revoke_db.commit()
            revocation_committed.set()
            result = future.result(timeout=20)
    finally:
        revocation_committed.set()
        event.remove(postgres_engine, "before_cursor_execute", before_cursor_execute)

    assert callback_used is True
    assert result == ("revoked", True)
    with Session(postgres_engine) as db:
        assert db.scalar(
            select(func.count(StudentReportReview.id)).where(
                StudentReportReview.report_id == context.report_id
            )
        ) == 0
        assert db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "student_report.review",
                AuditEvent.resource_id == context.report_id,
            )
        ) == 0


def test_review_and_new_revision_race_is_serialized_without_losing_publication(
    postgres_engine: Engine,
    request: pytest.FixtureRequest,
) -> None:
    context = _seed_with_cleanup(
        postgres_engine,
        request,
        _seed_revision_pending_context,
    )
    start = Barrier(2)

    def review() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            start.wait(timeout=10)
            _service(db).review(
                context.report_id,
                TeachingReportReviewInput(
                    expected_lock_version=context.lock_version,
                    decision="approved",
                    reason=None,
                ),
                reviewer_user_id=context.scenario.accounts["admin"].id,
                occurred_at=NOW + timedelta(hours=3),
                idempotency_key=f"concurrent-report-review-{uuid4()}",
                request_id=f"concurrent-report-review-{uuid4()}",
            )
            return "reviewed", db.scalar(select(1)) == 1

    def start_revision() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            start.wait(timeout=10)
            try:
                _service(db).start_revision(
                    context.report_id,
                    TeachingReportDraftStartInput(
                        expected_lock_version=context.lock_version,
                    ),
                    actor_user_id=context.scenario.accounts["primary"].id,
                    occurred_at=NOW + timedelta(hours=3),
                    idempotency_key=f"concurrent-report-revision-{uuid4()}",
                    request_id=f"concurrent-report-revision-{uuid4()}",
                )
                return "revision_started", db.scalar(select(1)) == 1
            except TeachingReportStateConflict:
                return "conflict", db.scalar(select(1)) == 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        review_future = pool.submit(review)
        revision_future = pool.submit(start_revision)
        results = [
            review_future.result(timeout=20),
            revision_future.result(timeout=20),
        ]

    assert [status for status, _ in results] == ["reviewed", "conflict"]
    assert all(usable for _, usable in results)
    with Session(postgres_engine) as db:
        report = db.get(StudentReport, context.report_id)
        assert report is not None
        assert report.status == "published"
        assert report.current_version_id == report.published_version_id
        assert db.get(StudentReportDraft, context.report_id) is None
        assert db.scalar(
            select(func.count(StudentReportVersion.id)).where(
                StudentReportVersion.report_id == context.report_id
            )
        ) == 2
