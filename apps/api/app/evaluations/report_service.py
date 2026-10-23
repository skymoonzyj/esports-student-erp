from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.evaluations.access import EvaluationAccessService, EvaluationSqlResource
from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationResourceNotFound,
    EvaluationSelfReviewForbidden,
    TeachingReportDraftVersionConflict,
    TeachingReportStateConflict,
    TeachingReportWindowClosed,
)
from app.evaluations.idempotency import EvaluationIdempotencyService
from app.evaluations.report_access import (
    AuthorizedTeachingReportSlot,
    TeachingReportAccessService,
    live_report_target_exists,
)
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportReview,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.report_periods import correction_deadline
from app.evaluations.report_schemas import (
    TeachingReportDraftSave,
    TeachingReportDraftStartInput,
    TeachingReportReopenInput,
    TeachingReportReviewInput,
    TeachingReportSubmitInput,
)
from app.evaluations.report_state import require_report_transition
from app.teaching.models import Cohort


@dataclass(frozen=True)
class TeachingReportDraftResult:
    report: StudentReport
    draft: StudentReportDraft
    slot: AuthorizedTeachingReportSlot
    idempotent: bool = False


@dataclass(frozen=True)
class TeachingReportSubmitted:
    report: StudentReport
    version: StudentReportVersion
    idempotent: bool = False


@dataclass(frozen=True)
class TeachingReportReviewed:
    report: StudentReport
    review: StudentReportReview
    idempotent: bool = False


@dataclass(frozen=True)
class TeachingReportDraftStarted:
    report: StudentReport
    draft: StudentReportDraft
    idempotent: bool = False


@dataclass(frozen=True)
class TeachingReportReopened:
    report: StudentReport
    grant: StudentReportWindowGrant
    idempotent: bool = False


class TeachingReportService:
    def __init__(
        self,
        db: Session,
        *,
        idempotency: EvaluationIdempotencyService,
        access: TeachingReportAccessService | None = None,
        review_access: EvaluationAccessService | None = None,
    ) -> None:
        self.db = db
        self.idempotency = idempotency
        self.access = access or TeachingReportAccessService(db)
        self.review_access = review_access or self.access.access

    @staticmethod
    def _slot(
        slots: tuple[AuthorizedTeachingReportSlot, ...],
        input: TeachingReportDraftSave,
    ) -> AuthorizedTeachingReportSlot:
        for slot in slots:
            if (
                slot.period.kind == input.report_kind
                and slot.period.starts_on == input.period_start_date
            ):
                return slot
        raise EvaluationResourceNotFound("evaluation resource not found")

    def _authorized_report_slot(
        self,
        *,
        report_id: UUID,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedTeachingReportSlot:
        identity = self.db.execute(
            select(
                StudentReport.enrollment_id,
                StudentReport.report_kind,
                StudentReport.period_start_date,
            ).where(
                StudentReport.id == report_id,
                StudentReport.author_user_id == actor_user_id,
            )
        ).one_or_none()
        if identity is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        enrollment_id, report_kind, period_start_date = identity
        for slot in self.access.author_slots(
            account_id=actor_user_id,
            enrollment_id=enrollment_id,
            occurred_at=occurred_at,
        ):
            if (
                slot.report_id == report_id
                and slot.period.kind == report_kind
                and slot.period.starts_on == period_start_date
            ):
                return slot
        raise EvaluationResourceNotFound("evaluation resource not found")

    def _lock_target(self, slot: AuthorizedTeachingReportSlot) -> None:
        cohort_id = self.db.scalar(
            select(Cohort.id)
            .where(Cohort.id == slot.target.cohort_id)
            .with_for_update()
        )
        if cohort_id is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        enrollment_id = self.db.scalar(
            select(Enrollment.id)
            .where(
                Enrollment.id == slot.target.enrollment_id,
                Enrollment.cohort_id == slot.target.cohort_id,
                Enrollment.class_id == slot.target.class_id,
                Enrollment.student_person_id == slot.target.student_person_id,
            )
            .with_for_update()
        )
        if enrollment_id is None:
            raise EvaluationResourceNotFound("evaluation resource not found")

    def _lock_report_target(self, report: StudentReport) -> None:
        cohort_id = self.db.scalar(
            select(Cohort.id)
            .where(Cohort.id == report.cohort_id)
            .with_for_update()
        )
        if cohort_id is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        enrollment_id = self.db.scalar(
            select(Enrollment.id)
            .where(
                Enrollment.id == report.enrollment_id,
                Enrollment.cohort_id == report.cohort_id,
                Enrollment.class_id == report.class_id,
                Enrollment.student_person_id == report.student_person_id,
            )
            .with_for_update()
        )
        if enrollment_id is None:
            raise EvaluationResourceNotFound("evaluation resource not found")

    @staticmethod
    def _idempotency_payload(
        input: TeachingReportDraftSave,
        *,
        report_id: UUID | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "enrollment_id": str(input.enrollment_id),
            "report_kind": input.report_kind,
            "period_start_date": input.period_start_date.isoformat(),
            "body": input.body,
            "dimension_values": input.dimension_values,
            "expected_draft_revision": input.expected_draft_revision,
        }
        if report_id is not None:
            payload["report_id"] = str(report_id)
        return payload

    def _idempotent_result(
        self,
        *,
        resource_id: UUID,
        actor_user_id: UUID,
        slot: AuthorizedTeachingReportSlot,
        requested_report_id: UUID | None,
    ) -> TeachingReportDraftResult:
        report = self.db.scalar(
            select(StudentReport).where(
                StudentReport.id == resource_id,
                StudentReport.enrollment_id == slot.target.enrollment_id,
                StudentReport.author_user_id == actor_user_id,
                StudentReport.report_kind == slot.period.kind,
                StudentReport.period_start_date == slot.period.starts_on,
            )
        )
        if report is None or (
            requested_report_id is not None and report.id != requested_report_id
        ):
            raise EvaluationIdempotencyConflict("idempotent report no longer matches")
        draft = self.db.get(StudentReportDraft, report.id)
        if draft is None:
            raise EvaluationIdempotencyConflict("idempotent draft is unavailable")
        return TeachingReportDraftResult(
            report=report,
            draft=draft,
            slot=slot,
            idempotent=True,
        )

    def _draft_window_allowed(
        self,
        slot: AuthorizedTeachingReportSlot,
        *,
        occurred_at: datetime,
        for_submit: bool,
    ) -> bool:
        if slot.report_id is None:
            return slot.submit_allowed if for_submit else slot.draft_allowed
        report = self.db.get(StudentReport, slot.report_id)
        draft = self.db.get(StudentReportDraft, slot.report_id)
        if report is None or draft is None:
            return slot.submit_allowed if for_submit else slot.draft_allowed
        if report.status == "correction_draft" and draft.draft_kind == "correction":
            return bool(
                report.correction_due_at is not None
                and occurred_at <= report.correction_due_at
            )
        if report.status == "published" and draft.draft_kind == "revision":
            return self._revision_window_allowed(report, occurred_at=occurred_at)
        return slot.submit_allowed if for_submit else slot.draft_allowed

    def save_draft(
        self,
        input: TeachingReportDraftSave,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        idempotency_key: str,
        request_id: str,
        report_id: UUID | None = None,
    ) -> TeachingReportDraftResult:
        preauthorized_slot = self._slot(
            self.access.author_slots(
                account_id=actor_user_id,
                enrollment_id=input.enrollment_id,
                occurred_at=occurred_at,
            ),
            input,
        )
        if not self._draft_window_allowed(
            preauthorized_slot,
            occurred_at=occurred_at,
            for_submit=False,
        ):
            raise TeachingReportWindowClosed("report draft window is closed")
        digests = self.idempotency.prepare(
            scope="teaching_report.draft.save",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload=self._idempotency_payload(input, report_id=report_id),
        )

        with write_transaction(self.db):
            self._lock_target(preauthorized_slot)
            slot = self._slot(
                self.access.author_slots(
                    account_id=actor_user_id,
                    enrollment_id=input.enrollment_id,
                    occurred_at=occurred_at,
                ),
                input,
            )
            if not self._draft_window_allowed(
                slot,
                occurred_at=occurred_at,
                for_submit=False,
            ):
                raise TeachingReportWindowClosed("report draft window is closed")
            existing_resource_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if existing_resource_id is not None:
                return self._idempotent_result(
                    resource_id=existing_resource_id,
                    actor_user_id=actor_user_id,
                    slot=slot,
                    requested_report_id=report_id,
                )

            report = self.db.scalar(
                select(StudentReport)
                .where(
                    StudentReport.enrollment_id == slot.target.enrollment_id,
                    StudentReport.report_kind == slot.period.kind,
                    StudentReport.period_start_date == slot.period.starts_on,
                    StudentReport.author_person_id == slot.author.person_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if report is None:
                if (
                    report_id is not None
                    and self.db.get(StudentReport, report_id) is not None
                ):
                    raise EvaluationResourceNotFound("evaluation resource not found")
                if input.expected_draft_revision != 0:
                    raise TeachingReportDraftVersionConflict(
                        "new report expects draft revision zero"
                    )
                report = StudentReport(
                    id=report_id or uuid4(),
                    enrollment_id=slot.target.enrollment_id,
                    student_person_id=slot.target.student_person_id,
                    cohort_id=slot.target.cohort_id,
                    project_id=slot.target.project_id,
                    class_id=slot.target.class_id,
                    author_person_id=slot.author.person_id,
                    author_user_id=slot.author.user_id,
                    author_source=slot.author.source,
                    coach_assignment_id=slot.author.coach_assignment_id,
                    staff_assignment_id=slot.author.staff_assignment_id,
                    role_key_snapshot=slot.author.role_key,
                    role_name_snapshot=slot.author.role_name,
                    role_category_snapshot=slot.author.role_category,
                    cohort_name_snapshot=slot.target.cohort_name,
                    project_name_snapshot=slot.target.project_name,
                    class_name_snapshot=slot.target.class_name,
                    report_kind=slot.period.kind,
                    period_sequence=slot.period.sequence,
                    period_start_date=slot.period.starts_on,
                    period_end_date=slot.period.ends_on,
                    submission_opens_at=slot.submission_opens_at,
                    initial_submission_deadline=slot.initial_submission_deadline,
                    status="draft",
                    lock_version=1,
                )
                self.db.add(report)
                self.db.flush()
                draft = StudentReportDraft(
                    report_id=report.id,
                    body=input.body,
                    dimension_values=input.dimension_values,
                    draft_kind="initial",
                    draft_revision=1,
                    last_edited_by_user_id=actor_user_id,
                )
                self.db.add(draft)
            else:
                if report_id is not None and report.id != report_id:
                    raise EvaluationResourceNotFound("evaluation resource not found")
                if report.status not in {
                    "draft",
                    "correction_draft",
                    "published",
                }:
                    raise TeachingReportStateConflict("report is not an editable draft")
                stored_draft = self.db.scalar(
                    select(StudentReportDraft)
                    .where(StudentReportDraft.report_id == report.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if stored_draft is None:
                    raise TeachingReportStateConflict("report draft is missing")
                draft = stored_draft
                expected_draft_kind = {
                    "draft": "initial",
                    "correction_draft": "correction",
                    "published": "revision",
                }[report.status]
                if draft.draft_kind != expected_draft_kind:
                    raise TeachingReportStateConflict("report draft kind is invalid")
                if draft.draft_revision != input.expected_draft_revision:
                    if (
                        input.expected_draft_revision == 0
                        and draft.draft_revision == 1
                        and draft.draft_kind == "initial"
                        and draft.body == input.body
                        and draft.dimension_values == input.dimension_values
                    ):
                        self.idempotency.record(
                            digests,
                            resource_id=report.id,
                            occurred_at=occurred_at,
                        )
                        self.db.flush()
                        return TeachingReportDraftResult(
                            report=report,
                            draft=draft,
                            slot=slot,
                            idempotent=True,
                        )
                    raise TeachingReportDraftVersionConflict(
                        "report draft revision changed"
                    )
                draft.body = input.body
                draft.dimension_values = input.dimension_values
                draft.draft_revision += 1
                draft.last_edited_by_user_id = actor_user_id
                draft.updated_at = occurred_at
                report.lock_version += 1
                report.updated_at = occurred_at
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="student_report.draft.save",
                resource_type="student_report",
                resource_id=report.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "enrollment_id": str(report.enrollment_id),
                    "cohort_id": str(report.cohort_id),
                    "project_id": str(report.project_id),
                    "class_id": str(report.class_id),
                    "evaluation_action": "save_draft",
                },
            )
            self.idempotency.record(
                digests,
                resource_id=report.id,
                occurred_at=occurred_at,
            )
            self.db.flush()
            return TeachingReportDraftResult(report=report, draft=draft, slot=slot)

    def _submission_replay(
        self,
        *,
        version_id: UUID,
        report_id: UUID,
        actor_user_id: UUID,
    ) -> TeachingReportSubmitted:
        version = self.db.scalar(
            select(StudentReportVersion).where(
                StudentReportVersion.id == version_id,
                StudentReportVersion.report_id == report_id,
                StudentReportVersion.submitted_by_user_id == actor_user_id,
            )
        )
        report = self.db.scalar(
            select(StudentReport).where(
                StudentReport.id == report_id,
                StudentReport.author_user_id == actor_user_id,
            )
        )
        if version is None or report is None:
            raise EvaluationIdempotencyConflict("idempotent submission is unavailable")
        return TeachingReportSubmitted(
            report=report,
            version=version,
            idempotent=True,
        )

    def submit(
        self,
        report_id: UUID,
        input: TeachingReportSubmitInput,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        idempotency_key: str,
        request_id: str,
    ) -> TeachingReportSubmitted:
        preauthorized_slot = self._authorized_report_slot(
            report_id=report_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        if not self._draft_window_allowed(
            preauthorized_slot,
            occurred_at=occurred_at,
            for_submit=True,
        ):
            raise TeachingReportWindowClosed("report submission window is closed")
        digests = self.idempotency.prepare(
            scope="teaching_report.submit",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "report_id": str(report_id),
                "expected_lock_version": input.expected_lock_version,
                "expected_draft_revision": input.expected_draft_revision,
            },
        )

        with write_transaction(self.db):
            self._lock_target(preauthorized_slot)
            slot = self._authorized_report_slot(
                report_id=report_id,
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
            )
            if not self._draft_window_allowed(
                slot,
                occurred_at=occurred_at,
                for_submit=True,
            ):
                raise TeachingReportWindowClosed("report submission window is closed")
            replay_version_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_version_id is not None:
                return self._submission_replay(
                    version_id=replay_version_id,
                    report_id=report_id,
                    actor_user_id=actor_user_id,
                )
            report = self.db.scalar(
                select(StudentReport)
                .where(
                    StudentReport.id == report_id,
                    StudentReport.enrollment_id == slot.target.enrollment_id,
                    StudentReport.author_user_id == actor_user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if report is None:
                raise EvaluationResourceNotFound("evaluation resource not found")
            if report.status not in {"draft", "correction_draft", "published"}:
                raise TeachingReportStateConflict("report is not an editable draft")
            if report.lock_version != input.expected_lock_version:
                raise TeachingReportStateConflict("report lock version changed")
            draft = self.db.scalar(
                select(StudentReportDraft)
                .where(StudentReportDraft.report_id == report.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            expected_draft_kind = {
                "draft": "initial",
                "correction_draft": "correction",
                "published": "revision",
            }[report.status]
            if draft is None or draft.draft_kind != expected_draft_kind:
                raise TeachingReportStateConflict("report draft is unavailable")
            if draft.draft_revision != input.expected_draft_revision:
                raise TeachingReportDraftVersionConflict(
                    "report draft revision changed"
                )
            next_version = int(
                self.db.scalar(
                    select(
                        func.coalesce(func.max(StudentReportVersion.version), 0) + 1
                    ).where(StudentReportVersion.report_id == report.id)
                )
                or 1
            )
            version = StudentReportVersion(
                report_id=report.id,
                version=next_version,
                source=draft.draft_kind,
                body=draft.body,
                dimension_values=draft.dimension_values,
                submitted_by_user_id=actor_user_id,
                submitted_at=occurred_at,
                previous_version_id=report.current_version_id,
            )
            self.db.add(version)
            self.db.flush()
            target_status = (
                "revision_pending_review"
                if report.status == "published"
                else "pending_review"
            )
            report.status = require_report_transition(report.status, target_status)
            report.current_version_id = version.id
            report.submitted_at = occurred_at
            if draft.draft_kind == "correction":
                report.correction_due_at = None
            report.lock_version += 1
            report.updated_at = occurred_at
            self.db.delete(draft)
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="student_report.submit",
                resource_type="student_report",
                resource_id=report.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "report_id": str(report.id),
                    "enrollment_id": str(report.enrollment_id),
                    "report_kind": report.report_kind,
                    "report_period_start": report.period_start_date.isoformat(),
                    "report_version": version.version,
                    "report_status": report.status,
                    "evaluation_action": "submit",
                },
            )
            self.idempotency.record(
                digests,
                resource_id=version.id,
                occurred_at=occurred_at,
            )
            self.db.flush()
            return TeachingReportSubmitted(report=report, version=version)

    def _authorized_review_report(
        self,
        *,
        report_id: UUID,
        reviewer_user_id: UUID,
        occurred_at: datetime,
        for_update: bool = False,
    ) -> StudentReport:
        if not self.access.context.center_allowed(
            reviewer_user_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationResourceNotFound("evaluation resource not found")
        authorization = self.review_access.center_scoped_sql_context(
            account_id=reviewer_user_id,
            permission="student_reports.review",
            occurred_at=occurred_at,
        )
        statement = select(StudentReport).where(
            StudentReport.id == report_id,
            live_report_target_exists(occurred_at=occurred_at),
            authorization.allowed(
                EvaluationSqlResource(
                    student_person_id=StudentReport.student_person_id,
                    cohort_id=StudentReport.cohort_id,
                    project_id=StudentReport.project_id,
                    class_id=StudentReport.class_id,
                    enrollment_id=StudentReport.enrollment_id,
                )
            ),
        )
        if for_update:
            statement = statement.with_for_update().execution_options(
                populate_existing=True
            )
        report = self.db.scalar(statement)
        if report is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        if authorization.actor_person_id == report.author_person_id:
            raise EvaluationSelfReviewForbidden("report self-review is not allowed")
        return report

    def _review_replay(
        self,
        *,
        review_id: UUID,
        report_id: UUID,
        reviewer_user_id: UUID,
    ) -> TeachingReportReviewed:
        review = self.db.scalar(
            select(StudentReportReview).where(
                StudentReportReview.id == review_id,
                StudentReportReview.report_id == report_id,
                StudentReportReview.reviewed_by_user_id == reviewer_user_id,
            )
        )
        report = self.db.get(StudentReport, report_id)
        if review is None or report is None:
            raise EvaluationIdempotencyConflict("idempotent review is unavailable")
        return TeachingReportReviewed(
            report=report,
            review=review,
            idempotent=True,
        )

    def review(
        self,
        report_id: UUID,
        input: TeachingReportReviewInput,
        *,
        reviewer_user_id: UUID,
        occurred_at: datetime,
        idempotency_key: str,
        request_id: str,
    ) -> TeachingReportReviewed:
        preauthorized_report = self._authorized_review_report(
            report_id=report_id,
            reviewer_user_id=reviewer_user_id,
            occurred_at=occurred_at,
        )
        digests = self.idempotency.prepare(
            scope="teaching_report.review",
            actor_user_id=reviewer_user_id,
            key=idempotency_key,
            payload={
                "report_id": str(report_id),
                "expected_lock_version": input.expected_lock_version,
                "decision": input.decision,
                "reason": input.reason,
            },
        )

        with write_transaction(self.db):
            self._lock_report_target(preauthorized_report)
            report = self._authorized_review_report(
                report_id=report_id,
                reviewer_user_id=reviewer_user_id,
                occurred_at=occurred_at,
                for_update=True,
            )
            replay_review_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_review_id is not None:
                return self._review_replay(
                    review_id=replay_review_id,
                    report_id=report_id,
                    reviewer_user_id=reviewer_user_id,
                )
            if report.status not in {"pending_review", "revision_pending_review"}:
                raise TeachingReportStateConflict("report is not awaiting review")
            if report.lock_version != input.expected_lock_version:
                raise TeachingReportStateConflict("report lock version changed")
            if report.current_version_id is None:
                raise TeachingReportStateConflict("report version is unavailable")
            version = self.db.scalar(
                select(StudentReportVersion)
                .where(
                    StudentReportVersion.id == report.current_version_id,
                    StudentReportVersion.report_id == report.id,
                )
                .with_for_update()
            )
            if version is None:
                raise TeachingReportStateConflict("report version is unavailable")
            source_status = report.status
            target_status = (
                "published" if input.decision == "approved" else "rejected"
            )
            report.status = require_report_transition(source_status, target_status)
            review = StudentReportReview(
                report_id=report.id,
                version_id=version.id,
                decision=input.decision,
                reason=input.reason,
                reviewed_by_user_id=reviewer_user_id,
                reviewed_at=occurred_at,
            )
            self.db.add(review)
            if input.decision == "approved":
                report.published_version_id = version.id
                report.published_at = occurred_at
                report.correction_due_at = None
            else:
                report.correction_due_at = correction_deadline(occurred_at)
            report.lock_version += 1
            report.updated_at = occurred_at
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=reviewer_user_id,
                action="student_report.review",
                resource_type="student_report",
                resource_id=report.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "report_id": str(report.id),
                    "enrollment_id": str(report.enrollment_id),
                    "report_kind": report.report_kind,
                    "report_period_start": report.period_start_date.isoformat(),
                    "report_version": version.version,
                    "review_decision": review.decision,
                    "report_status": report.status,
                    "evaluation_action": (
                        "approve" if review.decision == "approved" else "reject"
                    ),
                },
            )
            self.idempotency.record(
                digests,
                resource_id=review.id,
                occurred_at=occurred_at,
            )
            self.db.flush()
            return TeachingReportReviewed(report=report, review=review)

    def _draft_start_replay(
        self,
        *,
        report_id: UUID,
        actor_user_id: UUID,
        draft_kind: str,
    ) -> TeachingReportDraftStarted:
        report = self.db.scalar(
            select(StudentReport).where(
                StudentReport.id == report_id,
                StudentReport.author_user_id == actor_user_id,
            )
        )
        draft = self.db.get(StudentReportDraft, report_id)
        if report is None or draft is None or draft.draft_kind != draft_kind:
            raise EvaluationIdempotencyConflict("idempotent draft start is unavailable")
        return TeachingReportDraftStarted(
            report=report,
            draft=draft,
            idempotent=True,
        )

    def start_correction(
        self,
        report_id: UUID,
        input: TeachingReportDraftStartInput,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        idempotency_key: str,
        request_id: str,
    ) -> TeachingReportDraftStarted:
        preauthorized_slot = self._authorized_report_slot(
            report_id=report_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        digests = self.idempotency.prepare(
            scope="teaching_report.correction.start",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "report_id": str(report_id),
                "expected_lock_version": input.expected_lock_version,
            },
        )

        with write_transaction(self.db):
            self._lock_target(preauthorized_slot)
            self._authorized_report_slot(
                report_id=report_id,
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
            )
            replay_report_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_report_id is not None:
                if replay_report_id != report_id:
                    raise EvaluationIdempotencyConflict(
                        "idempotent correction no longer matches"
                    )
                return self._draft_start_replay(
                    report_id=report_id,
                    actor_user_id=actor_user_id,
                    draft_kind="correction",
                )
            report = self.db.scalar(
                select(StudentReport)
                .where(
                    StudentReport.id == report_id,
                    StudentReport.author_user_id == actor_user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if report is None:
                raise EvaluationResourceNotFound("evaluation resource not found")
            if report.status != "rejected" or report.current_version_id is None:
                raise TeachingReportStateConflict("report is not awaiting correction")
            if report.lock_version != input.expected_lock_version:
                raise TeachingReportStateConflict("report lock version changed")
            if report.correction_due_at is None or occurred_at > report.correction_due_at:
                raise TeachingReportWindowClosed("report correction window is closed")
            if self.db.get(StudentReportDraft, report.id) is not None:
                raise TeachingReportStateConflict("report draft already exists")
            version = self.db.scalar(
                select(StudentReportVersion).where(
                    StudentReportVersion.id == report.current_version_id,
                    StudentReportVersion.report_id == report.id,
                )
            )
            if version is None:
                raise TeachingReportStateConflict("report version is unavailable")
            draft = StudentReportDraft(
                report_id=report.id,
                body=version.body,
                dimension_values=version.dimension_values,
                draft_kind="correction",
                draft_revision=1,
                last_edited_by_user_id=actor_user_id,
                updated_at=occurred_at,
            )
            self.db.add(draft)
            report.status = require_report_transition(
                report.status,
                "correction_draft",
            )
            report.lock_version += 1
            report.updated_at = occurred_at
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="student_report.correction_draft.start",
                resource_type="student_report",
                resource_id=report.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "report_id": str(report.id),
                    "enrollment_id": str(report.enrollment_id),
                    "report_kind": report.report_kind,
                    "report_period_start": report.period_start_date.isoformat(),
                    "report_version": version.version,
                    "report_status": report.status,
                    "evaluation_action": "create_correction",
                },
            )
            self.idempotency.record(
                digests,
                resource_id=report.id,
                occurred_at=occurred_at,
            )
            self.db.flush()
            return TeachingReportDraftStarted(report=report, draft=draft)

    def _revision_window_allowed(
        self,
        report: StudentReport,
        *,
        occurred_at: datetime,
    ) -> bool:
        if occurred_at <= report.initial_submission_deadline:
            return True
        if report.current_window_grant_id is None:
            return False
        grant = self.db.get(
            StudentReportWindowGrant,
            report.current_window_grant_id,
        )
        return bool(
            grant is not None
            and grant.grant_kind == "revision"
            and grant.opens_at <= occurred_at <= grant.expires_at
        )

    def start_revision(
        self,
        report_id: UUID,
        input: TeachingReportDraftStartInput,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        idempotency_key: str,
        request_id: str,
    ) -> TeachingReportDraftStarted:
        preauthorized_slot = self._authorized_report_slot(
            report_id=report_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        preauthorized_report = self.db.get(StudentReport, report_id)
        if (
            preauthorized_report is None
            or preauthorized_report.status != "published"
            or preauthorized_report.published_version_id is None
        ):
            raise TeachingReportStateConflict("report is not published")
        if not self._revision_window_allowed(
            preauthorized_report,
            occurred_at=occurred_at,
        ):
            raise TeachingReportWindowClosed("report revision window is closed")
        digests = self.idempotency.prepare(
            scope="teaching_report.revision.start",
            actor_user_id=actor_user_id,
            key=idempotency_key,
            payload={
                "report_id": str(report_id),
                "expected_lock_version": input.expected_lock_version,
            },
        )

        with write_transaction(self.db):
            self._lock_target(preauthorized_slot)
            self._authorized_report_slot(
                report_id=report_id,
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
            )
            replay_report_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_report_id is not None:
                if replay_report_id != report_id:
                    raise EvaluationIdempotencyConflict(
                        "idempotent revision no longer matches"
                    )
                return self._draft_start_replay(
                    report_id=report_id,
                    actor_user_id=actor_user_id,
                    draft_kind="revision",
                )
            report = self.db.scalar(
                select(StudentReport)
                .where(
                    StudentReport.id == report_id,
                    StudentReport.author_user_id == actor_user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                report is None
                or report.status != "published"
                or report.published_version_id is None
            ):
                raise TeachingReportStateConflict("report is not published")
            if report.lock_version != input.expected_lock_version:
                raise TeachingReportStateConflict("report lock version changed")
            if not self._revision_window_allowed(report, occurred_at=occurred_at):
                raise TeachingReportWindowClosed("report revision window is closed")
            if self.db.get(StudentReportDraft, report.id) is not None:
                raise TeachingReportStateConflict("report draft already exists")
            version = self.db.scalar(
                select(StudentReportVersion).where(
                    StudentReportVersion.id == report.published_version_id,
                    StudentReportVersion.report_id == report.id,
                )
            )
            if version is None:
                raise TeachingReportStateConflict("published version is unavailable")
            draft = StudentReportDraft(
                report_id=report.id,
                body=version.body,
                dimension_values=version.dimension_values,
                draft_kind="revision",
                draft_revision=1,
                last_edited_by_user_id=actor_user_id,
                updated_at=occurred_at,
            )
            self.db.add(draft)
            report.lock_version += 1
            report.updated_at = occurred_at
            self.db.flush()
            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="student_report.revision.start",
                resource_type="student_report",
                resource_id=report.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "report_id": str(report.id),
                    "enrollment_id": str(report.enrollment_id),
                    "report_kind": report.report_kind,
                    "report_period_start": report.period_start_date.isoformat(),
                    "report_version": version.version,
                    "report_status": report.status,
                    "evaluation_action": "create_revision",
                },
            )
            self.idempotency.record(
                digests,
                resource_id=report.id,
                occurred_at=occurred_at,
            )
            self.db.flush()
            return TeachingReportDraftStarted(report=report, draft=draft)

    def _reopen_replay(
        self,
        *,
        grant_id: UUID,
        report_id: UUID,
        reviewer_user_id: UUID,
    ) -> TeachingReportReopened:
        grant = self.db.scalar(
            select(StudentReportWindowGrant).where(
                StudentReportWindowGrant.id == grant_id,
                StudentReportWindowGrant.report_id == report_id,
                StudentReportWindowGrant.granted_by_user_id == reviewer_user_id,
            )
        )
        report = self.db.get(StudentReport, report_id)
        if grant is None or report is None:
            raise EvaluationIdempotencyConflict("idempotent reopen is unavailable")
        return TeachingReportReopened(
            report=report,
            grant=grant,
            idempotent=True,
        )

    def _require_reopenable(
        self,
        report: StudentReport,
        *,
        grant_kind: str,
        occurred_at: datetime,
    ) -> None:
        if grant_kind == "correction":
            if (
                report.status != "rejected"
                or report.correction_due_at is None
                or occurred_at <= report.correction_due_at
            ):
                raise TeachingReportStateConflict(
                    "report correction is not eligible for reopen"
                )
            return
        if (
            report.status != "published"
            or occurred_at <= report.initial_submission_deadline
        ):
            raise TeachingReportStateConflict(
                "report revision is not eligible for reopen"
            )
        if report.current_window_grant_id is not None:
            grant = self.db.get(
                StudentReportWindowGrant,
                report.current_window_grant_id,
            )
            if (
                grant is not None
                and grant.grant_kind == "revision"
                and grant.opens_at <= occurred_at <= grant.expires_at
            ):
                raise TeachingReportStateConflict(
                    "report revision window is already open"
                )

    def reopen(
        self,
        report_id: UUID,
        input: TeachingReportReopenInput,
        *,
        reviewer_user_id: UUID,
        occurred_at: datetime,
        idempotency_key: str,
        request_id: str,
    ) -> TeachingReportReopened:
        preauthorized_report = self._authorized_review_report(
            report_id=report_id,
            reviewer_user_id=reviewer_user_id,
            occurred_at=occurred_at,
        )
        digests = self.idempotency.prepare(
            scope="teaching_report.reopen",
            actor_user_id=reviewer_user_id,
            key=idempotency_key,
            payload={
                "report_id": str(report_id),
                "expected_lock_version": input.expected_lock_version,
                "grant_kind": input.grant_kind,
                "reason": input.reason,
            },
        )

        with write_transaction(self.db):
            self._lock_report_target(preauthorized_report)
            report = self._authorized_review_report(
                report_id=report_id,
                reviewer_user_id=reviewer_user_id,
                occurred_at=occurred_at,
                for_update=True,
            )
            replay_grant_id = self.idempotency.existing_resource_id(
                digests,
                occurred_at=occurred_at,
            )
            if replay_grant_id is not None:
                return self._reopen_replay(
                    grant_id=replay_grant_id,
                    report_id=report_id,
                    reviewer_user_id=reviewer_user_id,
                )
            if report.lock_version != input.expected_lock_version:
                raise TeachingReportStateConflict("report lock version changed")
            self._require_reopenable(
                report,
                grant_kind=input.grant_kind,
                occurred_at=occurred_at,
            )
            grant = StudentReportWindowGrant(
                report_id=report.id,
                grant_kind=input.grant_kind,
                reason=input.reason,
                opens_at=occurred_at,
                expires_at=correction_deadline(occurred_at),
                granted_by_user_id=reviewer_user_id,
            )
            self.db.add(grant)
            self.db.flush()
            report.current_window_grant_id = grant.id
            if grant.grant_kind == "correction":
                report.correction_due_at = grant.expires_at
            report.lock_version += 1
            report.updated_at = occurred_at
            AuditService(self.db).append(
                actor_user_id=reviewer_user_id,
                action="student_report.reopen",
                resource_type="student_report",
                resource_id=report.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={
                    "report_id": str(report.id),
                    "enrollment_id": str(report.enrollment_id),
                    "report_kind": report.report_kind,
                    "report_period_start": report.period_start_date.isoformat(),
                    "report_status": report.status,
                    "report_window_kind": grant.grant_kind,
                    "evaluation_action": "reopen",
                },
            )
            self.idempotency.record(
                digests,
                resource_id=grant.id,
                occurred_at=occurred_at,
            )
            self.db.flush()
            return TeachingReportReopened(report=report, grant=grant)
