from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, aliased

from app.admissions.models import Enrollment
from app.auth.models import UserAccount
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationResource,
    EvaluationSqlContext,
    EvaluationSqlResource,
)
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.report_access import (
    AuthorizedTeachingReportSlot,
    TeachingReportAccessService,
    live_report_target_exists,
)
from app.evaluations.report_models import (
    StudentReport,
    StudentReportDraft,
    StudentReportVersion,
    StudentReportWindowGrant,
)
from app.evaluations.staff_models import (
    CohortStaffAssignment,
    CohortStaffAssignmentPermission,
    EvaluationStaffRole,
)
from app.identity.models import Person, StudentProfile
from app.rbac.models import Permission
from app.rbac.service import RbacService
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass


@dataclass(frozen=True)
class TeachingReportAuthorWorkspaceItem:
    slot: AuthorizedTeachingReportSlot
    draft_body: str | None
    draft_dimension_values: dict[str, Any] | None
    draft_revision: int | None


@dataclass(frozen=True)
class PublishedTeachingReport:
    report_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str
    report_kind: str
    period_sequence: int
    period_start_date: date
    period_end_date: date
    author_name: str
    role_name: str
    version_id: UUID
    version: int
    body: str
    dimension_values: dict[str, Any]
    published_at: datetime


@dataclass(frozen=True)
class PublishedTeachingReportPage:
    items: tuple[PublishedTeachingReport, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class TeachingReportReviewQueueItem:
    report_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str
    report_kind: str
    period_sequence: int
    period_start_date: date
    period_end_date: date
    status: str
    lock_version: int
    author_name: str
    role_name: str
    version_id: UUID
    version: int
    source: str
    body: str
    dimension_values: dict[str, Any]
    submitted_at: datetime


@dataclass(frozen=True)
class TeachingReportReviewQueuePage:
    items: tuple[TeachingReportReviewQueueItem, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class TeachingReportCapabilities:
    author_available: bool
    reviewer_available: bool
    family_available: bool


class TeachingReportQueryService:
    def __init__(
        self,
        db: Session,
        *,
        report_access: TeachingReportAccessService | None = None,
        evaluation_access: EvaluationAccessService | None = None,
        context: EvaluationContextService | None = None,
        rbac: RbacService | None = None,
    ) -> None:
        self.db = db
        self.report_access = report_access or TeachingReportAccessService(db)
        self.evaluation_access = evaluation_access or EvaluationAccessService(db)
        self.context = context or EvaluationContextService(db)
        self.rbac = rbac or RbacService(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    @staticmethod
    def _authorization_capable(context: EvaluationSqlContext) -> bool:
        return bool(
            context.account_active
            and not any(
                rule.effect == "deny" and rule.scope_kind == "global"
                for rule in context.overrides
            )
            and (
                context.role_rules
                or any(rule.effect == "allow" for rule in context.overrides)
                or context.staff_project_scopes
                or context.staff_class_scopes
            )
        )

    def _family_identity_capable(
        self,
        *,
        account_id: UUID,
        actor_person_id: UUID | None,
    ) -> bool:
        if actor_person_id is None:
            return False
        return (
            self.db.get(StudentProfile, actor_person_id) is not None
            or self.rbac.account_has_role(account_id, "guardian")
        )

    def _family_student_ids(
        self,
        *,
        account_id: UUID,
        authorization: EvaluationSqlContext,
    ) -> frozenset[UUID]:
        actor_person_id = authorization.actor_person_id
        if actor_person_id is None:
            return frozenset()
        student_ids: set[UUID] = set()
        if self.db.get(StudentProfile, actor_person_id) is not None:
            student_ids.add(actor_person_id)
        if self.rbac.account_has_role(account_id, "guardian"):
            student_ids.update(authorization.family_student_ids)
        return frozenset(student_ids)

    def author_capable(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        self._require_aware(occurred_at)
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            return False
        authorization = self.evaluation_access.sql_context(
            account_id=account_id,
            permission="student_reports.write",
            occurred_at=occurred_at,
        )
        if not authorization.account_active or any(
            rule.effect == "deny" and rule.scope_kind == "global"
            for rule in authorization.overrides
        ):
            return False
        if self._authorization_capable(authorization):
            return True
        if authorization.actor_person_id is None:
            return False
        implicit_primary = self.db.scalar(
            select(CoachAssignment.id)
            .where(
                CoachAssignment.coach_person_id == authorization.actor_person_id,
                CoachAssignment.assignment_type == "primary",
                CoachAssignment.valid_from <= occurred_at,
                CoachEligibility.eligible_person_predicate(
                    CoachAssignment.coach_person_id
                ),
            )
            .limit(1)
        )
        if implicit_primary is not None:
            return True
        return (
            self.db.scalar(
                select(CohortStaffAssignment.id)
                .join(
                    CohortStaffAssignmentPermission,
                    CohortStaffAssignmentPermission.assignment_id
                    == CohortStaffAssignment.id,
                )
                .join(
                    Permission,
                    Permission.id == CohortStaffAssignmentPermission.permission_id,
                )
                .join(
                    EvaluationStaffRole,
                    EvaluationStaffRole.id == CohortStaffAssignment.role_id,
                )
                .where(
                    CohortStaffAssignment.staff_person_id
                    == authorization.actor_person_id,
                    Permission.key == "student_reports.write",
                    EvaluationStaffRole.is_active.is_(True),
                    CohortStaffAssignment.valid_from <= occurred_at,
                )
                .limit(1)
            )
            is not None
        )

    def family_capable(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        self._require_aware(occurred_at)
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            return False
        authorizations = tuple(
            self.evaluation_access.sql_context(
                account_id=account_id,
                permission=permission,
                occurred_at=occurred_at,
            )
            for permission in ("teaching.read", "enrollments.read")
        )
        return self._family_identity_capable(
            account_id=account_id,
            actor_person_id=authorizations[0].actor_person_id,
        ) and all(self._authorization_capable(context) for context in authorizations)

    def capabilities(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> TeachingReportCapabilities:
        self._require_aware(occurred_at)
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            raise EvaluationOperationDenied("evaluation center access is required")

        enrollment_ids = tuple(
            self.db.scalars(
                select(Enrollment.id)
                .where(
                    Enrollment.status.in_(("active", "completed")),
                    Enrollment.confirmed_at <= occurred_at,
                )
                .order_by(Enrollment.confirmed_at, Enrollment.id)
            )
        )
        try:
            slots_by_enrollment = self.report_access.author_slots_for_enrollments(
                account_id=account_id,
                enrollment_ids=enrollment_ids,
                occurred_at=occurred_at,
            )
        except EvaluationResourceNotFound:
            slots_by_enrollment = {}
        author_available = self._any_slot_writable(
            tuple(
                slot
                for slots in slots_by_enrollment.values()
                for slot in slots
            ),
            occurred_at=occurred_at,
        )

        reviewer_available = self.review_queue(
            account_id=account_id,
            occurred_at=occurred_at,
            cohort_id=None,
            project_id=None,
            class_id=None,
            page=1,
            page_size=1,
        ).total > 0

        account = self.db.get(UserAccount, account_id)
        if account is None or not self._family_identity_capable(
            account_id=account_id,
            actor_person_id=account.person_id,
        ):
            # A staff-only account cannot have family report rows. Avoid
            # loading three permission contexts for that impossible branch;
            # student/guardian reads still evaluate all live resource gates.
            return TeachingReportCapabilities(
                author_available=author_available,
                reviewer_available=reviewer_available,
                family_available=False,
            )

        teaching_authorization = self.evaluation_access.center_scoped_sql_context(
            account_id=account_id,
            permission="teaching.read",
            occurred_at=occurred_at,
        )
        enrollment_authorization = self.evaluation_access.sql_context(
            account_id=account_id,
            permission="enrollments.read",
            occurred_at=occurred_at,
        )
        resource = EvaluationSqlResource(
            student_person_id=StudentReport.student_person_id,
            cohort_id=StudentReport.cohort_id,
            project_id=StudentReport.project_id,
            class_id=StudentReport.class_id,
            enrollment_id=StudentReport.enrollment_id,
        )
        family_student_ids = self._family_student_ids(
            account_id=account_id,
            authorization=teaching_authorization,
        )
        family_available = bool(family_student_ids) and (
            self.db.scalar(
                select(StudentReport.id)
                .where(
                    StudentReport.student_person_id.in_(family_student_ids),
                    StudentReport.published_version_id.is_not(None),
                    live_report_target_exists(occurred_at=occurred_at),
                    teaching_authorization.allowed(resource),
                    enrollment_authorization.allowed(resource),
                )
                .limit(1)
            )
            is not None
        )
        return TeachingReportCapabilities(
            author_available=author_available,
            reviewer_available=reviewer_available,
            family_available=family_available,
        )

    @staticmethod
    def _slot_writable(
        slot: AuthorizedTeachingReportSlot,
        *,
        report: StudentReport | None,
        draft: StudentReportDraft | None,
        grant: StudentReportWindowGrant | None,
        occurred_at: datetime,
    ) -> bool:
        if slot.report_id is None:
            return slot.draft_allowed
        if report is None:
            return False
        if draft is not None:
            if report.status == "draft":
                return slot.draft_allowed
            if report.status == "correction_draft":
                return bool(
                    report.correction_due_at is not None
                    and occurred_at <= report.correction_due_at
                )
            if report.status == "published" and draft.draft_kind == "revision":
                if occurred_at <= report.initial_submission_deadline:
                    return True
                return bool(
                    grant is not None
                    and grant.grant_kind == "revision"
                    and grant.opens_at <= occurred_at <= grant.expires_at
                )
            return False
        if report.status == "rejected":
            return bool(
                report.correction_due_at is not None
                and occurred_at <= report.correction_due_at
            )
        if report.status == "published":
            if occurred_at <= report.initial_submission_deadline:
                return True
            return bool(
                grant is not None
                and grant.grant_kind == "revision"
                and grant.opens_at <= occurred_at <= grant.expires_at
            )
        return False

    def _any_slot_writable(
        self,
        slots: tuple[AuthorizedTeachingReportSlot, ...],
        *,
        occurred_at: datetime,
    ) -> bool:
        if any(slot.report_id is None and slot.draft_allowed for slot in slots):
            return True
        report_ids = {
            slot.report_id for slot in slots if slot.report_id is not None
        }
        if not report_ids:
            return False
        reports = {
            report.id: report
            for report in self.db.scalars(
                select(StudentReport).where(StudentReport.id.in_(report_ids))
            )
        }
        drafts = {
            draft.report_id: draft
            for draft in self.db.scalars(
                select(StudentReportDraft).where(
                    StudentReportDraft.report_id.in_(report_ids)
                )
            )
        }
        grant_ids = {
            report.current_window_grant_id
            for report in reports.values()
            if report.current_window_grant_id is not None
        }
        grants = (
            {
                grant.id: grant
                for grant in self.db.scalars(
                    select(StudentReportWindowGrant).where(
                        StudentReportWindowGrant.id.in_(grant_ids)
                    )
                )
            }
            if grant_ids
            else {}
        )
        for slot in slots:
            report = (
                reports.get(slot.report_id)
                if slot.report_id is not None
                else None
            )
            grant = (
                grants.get(report.current_window_grant_id)
                if report is not None and report.current_window_grant_id is not None
                else None
            )
            if self._slot_writable(
                slot,
                report=report,
                draft=(
                    drafts.get(slot.report_id)
                    if slot.report_id is not None
                    else None
                ),
                grant=grant,
                occurred_at=occurred_at,
            ):
                return True
        return False

    def review_capable(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        authorization = self.evaluation_access.center_scoped_sql_context(
            account_id=account_id,
            permission="student_reports.review",
            occurred_at=occurred_at,
        )
        return self._authorization_capable(authorization)

    def workspace_capable(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        """Return whether the account may enter a teaching-report workspace.

        This is deliberately separate from :meth:`capabilities`.  The latter
        reports whether there is a live slot or review item to work on, while
        the evaluation-center entry should remain available to an authorized
        administrator before the first cohort has been enrolled.
        """
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            return False
        for permission in ("student_reports.write", "student_reports.review"):
            authorization = self.evaluation_access.sql_context(
                account_id=account_id,
                permission=permission,
                occurred_at=occurred_at,
            )
            if not self._authorization_capable(authorization):
                continue
            if any(
                rule.effect == "allow" and rule.scope_kind == "global"
                for rule in (*authorization.role_rules, *authorization.overrides)
            ):
                return True
        return False

    def require_author_report(
        self,
        report_id: UUID,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> StudentReport:
        if not self.author_capable(
            account_id=account_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationOperationDenied("report author capability is required")
        enrollment_id = self.db.scalar(
            select(StudentReport.enrollment_id).where(
                StudentReport.id == report_id,
                StudentReport.author_user_id == account_id,
            )
        )
        if enrollment_id is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        workspace = self.author_workspace(
            account_id=account_id,
            enrollment_id=enrollment_id,
            occurred_at=occurred_at,
        )
        if not any(item.slot.report_id == report_id for item in workspace):
            raise EvaluationResourceNotFound("evaluation resource not found")
        report = self.db.get(StudentReport, report_id)
        if report is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        return report

    def require_review_report(
        self,
        report_id: UUID,
        *,
        account_id: UUID,
        occurred_at: datetime,
    ) -> StudentReport:
        if not self.review_capable(
            account_id=account_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationOperationDenied("report review capability is required")
        authorization = self.evaluation_access.center_scoped_sql_context(
            account_id=account_id,
            permission="student_reports.review",
            occurred_at=occurred_at,
        )
        report = self.db.scalar(
            select(StudentReport).where(
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
        )
        if report is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        return report

    def author_workspace(
        self,
        *,
        account_id: UUID,
        enrollment_id: UUID,
        occurred_at: datetime,
    ) -> tuple[TeachingReportAuthorWorkspaceItem, ...]:
        slots = self.report_access.author_slots(
            account_id=account_id,
            enrollment_id=enrollment_id,
            occurred_at=occurred_at,
        )
        report_ids = {slot.report_id for slot in slots if slot.report_id is not None}
        drafts = (
            {
                draft.report_id: draft
                for draft in self.db.scalars(
                    select(StudentReportDraft).where(
                        StudentReportDraft.report_id.in_(report_ids)
                    )
                )
            }
            if report_ids
            else {}
        )
        return tuple(
            TeachingReportAuthorWorkspaceItem(
                slot=slot,
                draft_body=(
                    drafts[slot.report_id].body
                    if slot.report_id is not None and slot.report_id in drafts
                    else None
                ),
                draft_dimension_values=(
                    drafts[slot.report_id].dimension_values
                    if slot.report_id is not None and slot.report_id in drafts
                    else None
                ),
                draft_revision=(
                    drafts[slot.report_id].draft_revision
                    if slot.report_id is not None and slot.report_id in drafts
                    else None
                ),
            )
            for slot in slots
        )

    def review_queue(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
        cohort_id: UUID | None,
        project_id: UUID | None,
        class_id: UUID | None,
        page: int,
        page_size: int,
    ) -> TeachingReportReviewQueuePage:
        self._require_aware(occurred_at)
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid report pagination")
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            raise EvaluationOperationDenied("evaluation center access is required")
        authorization = self.evaluation_access.center_scoped_sql_context(
            account_id=account_id,
            permission="student_reports.review",
            occurred_at=occurred_at,
        )
        current = aliased(StudentReportVersion)
        author = aliased(Person)
        statement = (
            select(StudentReport, current, author)
            .join(
                current,
                and_(
                    current.id == StudentReport.current_version_id,
                    current.report_id == StudentReport.id,
                ),
            )
            .join(author, author.id == StudentReport.author_person_id)
            .where(
                StudentReport.status.in_(
                    ("pending_review", "revision_pending_review")
                ),
                StudentReport.author_user_id != account_id,
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
        )
        if cohort_id is not None:
            statement = statement.where(StudentReport.cohort_id == cohort_id)
        if project_id is not None:
            statement = statement.where(StudentReport.project_id == project_id)
        if class_id is not None:
            statement = statement.where(StudentReport.class_id == class_id)
        total = self.db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        rows = self.db.execute(
            statement.order_by(
                StudentReport.submitted_at,
                StudentReport.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).tuples()
        return TeachingReportReviewQueuePage(
            items=tuple(
                TeachingReportReviewQueueItem(
                    report_id=report.id,
                    enrollment_id=report.enrollment_id,
                    cohort_id=report.cohort_id,
                    cohort_name=report.cohort_name_snapshot,
                    project_id=report.project_id,
                    project_name=report.project_name_snapshot,
                    class_id=report.class_id,
                    class_name=report.class_name_snapshot,
                    report_kind=report.report_kind,
                    period_sequence=report.period_sequence,
                    period_start_date=report.period_start_date,
                    period_end_date=report.period_end_date,
                    status=report.status,
                    lock_version=report.lock_version,
                    author_name=author_person.full_name,
                    role_name=report.role_name_snapshot,
                    version_id=version.id,
                    version=version.version,
                    source=version.source,
                    body=version.body,
                    dimension_values=version.dimension_values,
                    submitted_at=version.submitted_at,
                )
                for report, version, author_person in rows
            ),
            total=total,
            page=page,
            page_size=page_size,
        )

    def _require_family_read(
        self,
        *,
        account_id: UUID,
        student_person_id: UUID,
        occurred_at: datetime,
        enrollment_id: UUID | None = None,
    ) -> None:
        if not self.context.center_allowed(account_id, occurred_at=occurred_at):
            raise EvaluationOperationDenied("evaluation center access is required")
        if not self.family_capable(
            account_id=account_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationOperationDenied("family report read capability is required")
        family_authorization = self.evaluation_access.sql_context(
            account_id=account_id,
            permission="teaching.read",
            occurred_at=occurred_at,
        )
        if student_person_id not in self._family_student_ids(
            account_id=account_id,
            authorization=family_authorization,
        ):
            raise EvaluationResourceNotFound("evaluation resource not found")
        student_exists = self.db.scalar(
            select(StudentProfile.person_id)
            .join(Person, Person.id == StudentProfile.person_id)
            .where(
                StudentProfile.person_id == student_person_id,
                StudentProfile.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        if student_exists is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        resource = EvaluationResource(student_person_id=student_person_id)
        if not all(
            self.evaluation_access.decide(
                account_id=account_id,
                permission=permission,
                resource=resource,
                occurred_at=occurred_at,
            ).allowed
            for permission in ("evaluation_center.access", "teaching.read", "enrollments.read")
        ):
            raise EvaluationResourceNotFound("evaluation resource not found")
        if enrollment_id is None:
            return
        effective_start_at = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
            else_=Cohort.starts_at,
        )
        effective_end_at = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
            else_=Cohort.ends_at,
        )
        target = self.db.execute(
            select(Enrollment.cohort_id, Enrollment.class_id, TeachingClass.project_id)
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(
                TeachingClass,
                (TeachingClass.id == Enrollment.class_id)
                & (TeachingClass.cohort_id == Enrollment.cohort_id),
            )
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(
                Enrollment.id == enrollment_id,
                Enrollment.student_person_id == student_person_id,
                Enrollment.status.in_(("active", "completed")),
                effective_start_at <= occurred_at,
                effective_end_at >= occurred_at,
                Cohort.status.in_(("active", "completed")),
                TeachingClass.is_active.is_(True),
                GameProject.is_active.is_(True),
            )
        ).one_or_none()
        if target is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        scoped_resource = EvaluationResource(
            student_person_id=student_person_id,
            enrollment_id=enrollment_id,
            cohort_id=target.cohort_id,
            project_id=target.project_id,
            class_id=target.class_id,
        )
        if not all(
            self.evaluation_access.decide(
                account_id=account_id,
                permission=permission,
                resource=scoped_resource,
                occurred_at=occurred_at,
            ).allowed
            for permission in ("evaluation_center.access", "teaching.read", "enrollments.read")
        ):
            raise EvaluationResourceNotFound("evaluation resource not found")

    def published_for_student(
        self,
        *,
        account_id: UUID,
        student_person_id: UUID,
        occurred_at: datetime,
        page: int,
        page_size: int,
        enrollment_id: UUID | None = None,
    ) -> PublishedTeachingReportPage:
        self._require_aware(occurred_at)
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid report pagination")
        self._require_family_read(
            account_id=account_id,
            student_person_id=student_person_id,
            occurred_at=occurred_at,
            enrollment_id=enrollment_id,
        )
        teaching_authorization = self.evaluation_access.center_scoped_sql_context(
            account_id=account_id,
            permission="teaching.read",
            occurred_at=occurred_at,
        )
        enrollment_authorization = self.evaluation_access.sql_context(
            account_id=account_id,
            permission="enrollments.read",
            occurred_at=occurred_at,
        )
        scoped_resource = EvaluationSqlResource(
            student_person_id=StudentReport.student_person_id,
            cohort_id=StudentReport.cohort_id,
            project_id=StudentReport.project_id,
            class_id=StudentReport.class_id,
            enrollment_id=StudentReport.enrollment_id,
        )
        published = aliased(StudentReportVersion)
        author = aliased(Person)
        statement = (
            select(StudentReport, published, author)
            .join(
                published,
                and_(
                    published.id == StudentReport.published_version_id,
                    published.report_id == StudentReport.id,
                ),
            )
            .join(author, author.id == StudentReport.author_person_id)
            .where(
                StudentReport.student_person_id == student_person_id,
                StudentReport.published_version_id.is_not(None),
                StudentReport.published_at.is_not(None),
                live_report_target_exists(occurred_at=occurred_at),
                teaching_authorization.allowed(scoped_resource),
                enrollment_authorization.allowed(scoped_resource),
            )
        )
        if enrollment_id is not None:
            statement = statement.where(
                StudentReport.enrollment_id == enrollment_id
            )
        total = self.db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        rows = self.db.execute(
            statement.order_by(
                StudentReport.published_at.desc(),
                StudentReport.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).tuples()
        items: list[PublishedTeachingReport] = []
        for report, version, author_person in rows:
            if report.published_at is None:
                raise AssertionError("published report is missing its publication time")
            items.append(
                PublishedTeachingReport(
                    report_id=report.id,
                    enrollment_id=report.enrollment_id,
                    cohort_id=report.cohort_id,
                    cohort_name=report.cohort_name_snapshot,
                    project_id=report.project_id,
                    project_name=report.project_name_snapshot,
                    class_id=report.class_id,
                    class_name=report.class_name_snapshot,
                    report_kind=report.report_kind,
                    period_sequence=report.period_sequence,
                    period_start_date=report.period_start_date,
                    period_end_date=report.period_end_date,
                    author_name=author_person.full_name,
                    role_name=report.role_name_snapshot,
                    version_id=version.id,
                    version=version.version,
                    body=version.body,
                    dimension_values=version.dimension_values,
                    published_at=report.published_at,
                )
            )
        return PublishedTeachingReportPage(
            items=tuple(items),
            total=total,
            page=page,
            page_size=page_size,
        )
