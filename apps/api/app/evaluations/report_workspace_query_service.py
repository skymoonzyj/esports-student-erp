from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session, aliased

from app.admissions.models import Enrollment
from app.admissions.schedule import effective_schedule
from app.core.time import BUSINESS_TIME_ZONE
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.report_access import TeachingReportAccessService
from app.evaluations.report_models import (
    StudentReportReview,
    StudentReportVersion,
)
from app.evaluations.report_query_service import TeachingReportQueryService
from app.identity.models import Person, StudentProfile
from app.teaching.models import Cohort, GameProject, TeachingClass


@dataclass(frozen=True)
class TeachingReportTargetFilters:
    cohort_id: UUID | None = None
    project_id: UUID | None = None
    class_id: UUID | None = None
    student_id: UUID | None = None
    query: str | None = None
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class TeachingReportTargetItem:
    enrollment_id: UUID
    student_person_id: UUID
    student_name: str
    student_number: str
    cohort_id: UUID
    cohort_name: str
    cohort_starts_at: datetime
    cohort_ends_at: datetime
    project_id: UUID
    project_name: str
    class_id: UUID
    class_name: str


@dataclass(frozen=True)
class TeachingReportCohortFacet:
    id: UUID
    name: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class TeachingReportProjectFacet:
    id: UUID
    name: str
    cohort_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class TeachingReportClassFacet:
    id: UUID
    name: str
    cohort_id: UUID
    project_id: UUID


@dataclass(frozen=True)
class TeachingReportTargetFacets:
    cohorts: tuple[TeachingReportCohortFacet, ...]
    projects: tuple[TeachingReportProjectFacet, ...]
    classes: tuple[TeachingReportClassFacet, ...]


@dataclass(frozen=True)
class TeachingReportTargetPage:
    items: tuple[TeachingReportTargetItem, ...]
    facets: TeachingReportTargetFacets
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class TeachingReportVersionReviewItem:
    decision: str
    reason: str | None
    reviewed_at: datetime


@dataclass(frozen=True)
class TeachingReportVersionItem:
    report_id: UUID
    version_id: UUID
    version: int
    source: str
    body: str
    dimension_values: dict[str, Any]
    submitted_at: datetime
    is_current: bool
    is_published: bool
    review: TeachingReportVersionReviewItem | None


@dataclass(frozen=True)
class TeachingReportVersionPage:
    items: tuple[TeachingReportVersionItem, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class _AuthorizedTarget:
    item: TeachingReportTargetItem
    project_sort_order: int


class TeachingReportWorkspaceQueryService:
    def __init__(
        self,
        db: Session,
        *,
        report_access: TeachingReportAccessService | None = None,
        report_query: TeachingReportQueryService | None = None,
    ) -> None:
        self.db = db
        self.report_access = report_access or TeachingReportAccessService(db)
        self.report_query = report_query or TeachingReportQueryService(
            db,
            report_access=self.report_access,
        )

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    @staticmethod
    def _validate_pagination(*, page: int, page_size: int) -> None:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid report pagination")

    @classmethod
    def _validate_target_filters(
        cls,
        *,
        occurred_at: datetime,
        filters: TeachingReportTargetFilters,
    ) -> str:
        cls._require_aware(occurred_at)
        cls._validate_pagination(page=filters.page, page_size=filters.page_size)
        trimmed = filters.query.strip() if filters.query else ""
        if len(trimmed) > 80:
            raise ValueError("query must be at most 80 characters")
        return trimmed.casefold()

    @classmethod
    def _validate_version_request(
        cls,
        *,
        occurred_at: datetime,
        page: int,
        page_size: int,
    ) -> None:
        cls._require_aware(occurred_at)
        cls._validate_pagination(page=page, page_size=page_size)

    @staticmethod
    def _uuid_key(value: UUID) -> bytes:
        return value.bytes

    @classmethod
    def _target_sort_key(cls, target: _AuthorizedTarget) -> tuple[Any, ...]:
        item = target.item
        return (
            -item.cohort_starts_at.timestamp(),
            cls._uuid_key(item.cohort_id),
            target.project_sort_order,
            item.project_name,
            cls._uuid_key(item.project_id),
            item.class_name,
            cls._uuid_key(item.class_id),
            item.student_name,
            item.student_number,
            cls._uuid_key(item.student_person_id),
            cls._uuid_key(item.enrollment_id),
        )

    def _candidate_rows(self, *, occurred_at: datetime) -> tuple[Any, ...]:
        reporting_window_start = datetime.combine(
            occurred_at.astimezone(BUSINESS_TIME_ZONE).date() - timedelta(days=7),
            time.min,
            tzinfo=BUSINESS_TIME_ZONE,
        )
        statement = (
            select(
                Enrollment,
                Cohort,
                TeachingClass,
                GameProject,
                StudentProfile,
                Person,
            )
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(
                TeachingClass,
                and_(
                    TeachingClass.id == Enrollment.class_id,
                    TeachingClass.cohort_id == Enrollment.cohort_id,
                ),
            )
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .join(StudentProfile, StudentProfile.person_id == Enrollment.student_person_id)
            .join(Person, Person.id == Enrollment.student_person_id)
            .where(
                Enrollment.status.in_(("active", "completed")),
                case(
                    (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
                    else_=Cohort.starts_at,
                )
                <= occurred_at,
                case(
                    (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
                    else_=Cohort.ends_at,
                )
                >= reporting_window_start,
                Cohort.status.in_(("active", "completed")),
                TeachingClass.is_active.is_(True),
                GameProject.is_active.is_(True),
                StudentProfile.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        return tuple(self.db.execute(statement).tuples())

    @staticmethod
    def _query_matches(target: TeachingReportTargetItem, query: str) -> bool:
        if not query:
            return True
        return any(
            query in value.casefold()
            for value in (
                target.student_name,
                target.student_number,
            )
        )

    @classmethod
    def _facets(
        cls,
        authorized: tuple[_AuthorizedTarget, ...],
    ) -> TeachingReportTargetFacets:
        cohorts: dict[UUID, TeachingReportCohortFacet] = {}
        projects: dict[UUID, tuple[TeachingReportProjectFacet, set[UUID]]] = {}
        classes: dict[UUID, TeachingReportClassFacet] = {}
        for target in authorized:
            item = target.item
            cohorts.setdefault(
                item.cohort_id,
                TeachingReportCohortFacet(
                    id=item.cohort_id,
                    name=item.cohort_name,
                    starts_at=item.cohort_starts_at,
                    ends_at=item.cohort_ends_at,
                ),
            )
            project = projects.get(item.project_id)
            if project is None:
                projects[item.project_id] = (
                    TeachingReportProjectFacet(
                        id=item.project_id,
                        name=item.project_name,
                        cohort_ids=(),
                    ),
                    {item.cohort_id},
                )
            else:
                project[1].add(item.cohort_id)
            classes.setdefault(
                item.class_id,
                TeachingReportClassFacet(
                    id=item.class_id,
                    name=item.class_name,
                    cohort_id=item.cohort_id,
                    project_id=item.project_id,
                ),
            )

        project_values: list[TeachingReportProjectFacet] = []
        for project_facet, cohort_ids in projects.values():
            project_values.append(
                TeachingReportProjectFacet(
                    id=project_facet.id,
                    name=project_facet.name,
                    cohort_ids=tuple(sorted(cohort_ids, key=cls._uuid_key)),
                )
            )

        cohort_values = tuple(
            sorted(
                cohorts.values(),
                key=lambda facet: (
                    -facet.starts_at.timestamp(),
                    cls._uuid_key(facet.id),
                ),
            )
        )
        project_sort_orders = {
            target.item.project_id: target.project_sort_order for target in authorized
        }
        project_values.sort(
            key=lambda facet: (
                project_sort_orders[facet.id],
                facet.name,
                cls._uuid_key(facet.id),
            )
        )
        class_values = tuple(
            sorted(
                classes.values(),
                key=lambda facet: (
                    cls._uuid_key(facet.cohort_id),
                    cls._uuid_key(facet.project_id),
                    facet.name,
                    cls._uuid_key(facet.id),
                ),
            )
        )
        return TeachingReportTargetFacets(
            cohorts=cohort_values,
            projects=tuple(project_values),
            classes=class_values,
        )

    def author_targets(
        self,
        *,
        account_id: UUID,
        occurred_at: datetime,
        filters: TeachingReportTargetFilters,
    ) -> TeachingReportTargetPage:
        query = self._validate_target_filters(
            occurred_at=occurred_at,
            filters=filters,
        )
        if not self.report_query.author_capable(
            account_id=account_id,
            occurred_at=occurred_at,
        ):
            raise EvaluationOperationDenied("report author capability is required")

        candidate_rows = self._candidate_rows(occurred_at=occurred_at)
        slots_by_enrollment = self.report_access.author_slots_for_enrollments(
            account_id=account_id,
            enrollment_ids=(row[0].id for row in candidate_rows),
            occurred_at=occurred_at,
        )
        authorized: list[_AuthorizedTarget] = []
        for enrollment, cohort, teaching_class, project, profile, person in candidate_rows:
            slots = slots_by_enrollment.get(enrollment.id)
            if not slots:
                continue
            schedule = effective_schedule(enrollment, cohort)
            authorized.append(
                _AuthorizedTarget(
                    item=TeachingReportTargetItem(
                        enrollment_id=enrollment.id,
                        student_person_id=person.id,
                        student_name=person.full_name,
                        student_number=profile.student_number,
                        cohort_id=cohort.id,
                        cohort_name=cohort.name,
                        cohort_starts_at=schedule.starts_at,
                        cohort_ends_at=schedule.ends_at,
                        project_id=project.id,
                        project_name=project.name,
                        class_id=teaching_class.id,
                        class_name=teaching_class.name,
                    ),
                    project_sort_order=project.sort_order,
                )
            )

        authorized_tuple = tuple(authorized)
        facets = self._facets(authorized_tuple)
        filtered = [
            target
            for target in authorized_tuple
            if (filters.cohort_id is None or target.item.cohort_id == filters.cohort_id)
            and (
                filters.project_id is None
                or target.item.project_id == filters.project_id
            )
            and (filters.class_id is None or target.item.class_id == filters.class_id)
            and (
                filters.student_id is None
                or target.item.student_person_id == filters.student_id
            )
            and self._query_matches(target.item, query)
        ]
        filtered.sort(key=self._target_sort_key)
        total = len(filtered)
        offset = (filters.page - 1) * filters.page_size
        items = tuple(target.item for target in filtered[offset : offset + filters.page_size])
        return TeachingReportTargetPage(
            items=items,
            facets=facets,
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    def report_versions(
        self,
        report_id: UUID,
        *,
        account_id: UUID,
        occurred_at: datetime,
        page: int,
        page_size: int,
    ) -> TeachingReportVersionPage:
        self._validate_version_request(
            occurred_at=occurred_at,
            page=page,
            page_size=page_size,
        )
        author_capable = self.report_query.author_capable(
            account_id=account_id,
            occurred_at=occurred_at,
        )
        reviewer_capable = self.report_query.review_capable(
            account_id=account_id,
            occurred_at=occurred_at,
        )
        if not author_capable and not reviewer_capable:
            raise EvaluationOperationDenied("report author or review capability is required")

        report = None
        if author_capable:
            try:
                report = self.report_query.require_author_report(
                    report_id,
                    account_id=account_id,
                    occurred_at=occurred_at,
                )
            except EvaluationResourceNotFound:
                pass
        if report is None and reviewer_capable:
            try:
                report = self.report_query.require_review_report(
                    report_id,
                    account_id=account_id,
                    occurred_at=occurred_at,
                )
            except EvaluationResourceNotFound:
                pass
        if report is None:
            raise EvaluationResourceNotFound("evaluation resource not found")

        review = aliased(StudentReportReview)
        statement = (
            select(StudentReportVersion, review)
            .outerjoin(
                review,
                and_(
                    review.version_id == StudentReportVersion.id,
                    review.report_id == StudentReportVersion.report_id,
                ),
            )
            .where(StudentReportVersion.report_id == report.id)
        )
        total = self.db.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        ) or 0
        rows = self.db.execute(
            statement.order_by(
                StudentReportVersion.version.desc(),
                StudentReportVersion.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).tuples()
        items = tuple(
            TeachingReportVersionItem(
                report_id=version.report_id,
                version_id=version.id,
                version=version.version,
                source=version.source,
                body=version.body,
                dimension_values=dict(version.dimension_values),
                submitted_at=version.submitted_at,
                is_current=version.id == report.current_version_id,
                is_published=version.id == report.published_version_id,
                review=(
                    TeachingReportVersionReviewItem(
                        decision=review_row.decision,
                        reason=review_row.reason,
                        reviewed_at=review_row.reviewed_at,
                    )
                    if review_row is not None
                    else None
                ),
            )
            for version, review_row in rows
        )
        return TeachingReportVersionPage(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
