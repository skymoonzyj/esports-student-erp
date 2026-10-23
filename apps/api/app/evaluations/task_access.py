from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Protocol
from typing import cast as type_cast
from uuid import UUID

from sqlalchemy import Date, Select, case, cast, false, func, or_, select, true, tuple_
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.admissions.models import AdmissionCase, Enrollment
from app.core.time import BUSINESS_TIME_ZONE
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationDecision,
    EvaluationResource,
    EvaluationSqlContext,
    EvaluationSqlResource,
)
from app.evaluations.errors import EvaluationResourceNotFound
from app.identity.models import Person, StudentProfile
from app.teaching.models import Cohort, GameProject, TeachingClass


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")


def _business_day_boundary(value: date, *, end: bool) -> datetime:
    return datetime.combine(
        value,
        time.max if end else time.min,
        tzinfo=BUSINESS_TIME_ZONE,
    )


@dataclass(frozen=True)
class EvaluationUploadWindow:
    starts_at: datetime
    ends_at: datetime

    def contains(self, occurred_at: datetime) -> bool:
        _require_aware(occurred_at)
        return self.starts_at <= occurred_at <= self.ends_at


def upload_window_for_cohort(
    *,
    starts_at: datetime,
    ends_at: datetime,
) -> EvaluationUploadWindow:
    _require_aware(starts_at)
    _require_aware(ends_at)
    local_start = starts_at.astimezone(BUSINESS_TIME_ZONE).date() - timedelta(days=7)
    local_end = ends_at.astimezone(BUSINESS_TIME_ZONE).date() + timedelta(days=7)
    return EvaluationUploadWindow(
        starts_at=_business_day_boundary(local_start, end=False),
        ends_at=_business_day_boundary(local_end, end=True),
    )


@dataclass(frozen=True)
class EvaluationTaskResource:
    student_person_id: UUID | None
    enrollment_id: UUID | None
    cohort_id: UUID | None
    project_id: UUID
    class_id: UUID | None

    def as_evaluation_resource(self) -> EvaluationResource:
        return EvaluationResource(
            student_person_id=self.student_person_id,
            enrollment_id=self.enrollment_id,
            cohort_id=self.cohort_id,
            project_id=self.project_id,
            class_id=self.class_id,
        )


@dataclass(frozen=True)
class AuthorizedEvaluationTarget:
    """A formally enrolled target that the actor may upload for.

    The target is deliberately immutable: callers may carry it across the
    upload flow, but cannot alter the resource or authorization decision that
    was made by the selector.
    """

    student_person_id: UUID
    enrollment_id: UUID
    cohort_id: UUID
    project_id: UUID
    class_id: UUID
    authorization_source: str
    upload_window: EvaluationUploadWindow

    @property
    def resource(self) -> EvaluationTaskResource:
        return EvaluationTaskResource(
            student_person_id=self.student_person_id,
            enrollment_id=self.enrollment_id,
            cohort_id=self.cohort_id,
            project_id=self.project_id,
            class_id=self.class_id,
        )

    @property
    def source(self) -> str:
        """Compatibility alias used by existing task callers."""

        return self.authorization_source

    @property
    def requires_delegated_reason(self) -> bool:
        return self.authorization_source == "administrator"

    @property
    def requires_business_reason(self) -> bool:
        """Compatibility alias for the persisted upload-session field."""

        return self.requires_delegated_reason


def _source_for_target(
    *,
    context: EvaluationSqlContext,
    global_allowed: bool,
    student_person_id: UUID,
    cohort_id: UUID,
    project_id: UUID,
    class_id: UUID,
) -> str | None:
    if context.actor_person_id == student_person_id:
        return "self"
    if student_person_id in context.family_student_ids:
        return "family"
    if class_id in context.assigned_class_ids:
        return "coach"
    if (
        class_id in context.staff_class_scopes
        or (
            cohort_id,
            project_id,
        )
        in context.staff_project_scopes
    ):
        return "staff"
    if global_allowed:
        return "administrator"
    return None


@dataclass(frozen=True)
class AuthorizedUploadTargetSelection:
    """Composable authorized-target query shared by selection and matching."""

    statement: Select[Any]
    context: EvaluationSqlContext
    global_allowed: bool

    def target_from_mapping(
        self,
        row: RowMapping,
    ) -> AuthorizedEvaluationTarget:
        source = _source_for_target(
            context=self.context,
            global_allowed=self.global_allowed,
            student_person_id=row["student_person_id"],
            cohort_id=row["cohort_id"],
            project_id=row["project_id"],
            class_id=row["class_id"],
        )
        if source is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        return AuthorizedEvaluationTarget(
            student_person_id=row["student_person_id"],
            enrollment_id=row["enrollment_id"],
            cohort_id=row["cohort_id"],
            project_id=row["project_id"],
            class_id=row["class_id"],
            authorization_source=source,
            upload_window=upload_window_for_cohort(
                starts_at=row["cohort_starts_at"],
                ends_at=row["cohort_ends_at"],
            ),
        )


class _EvaluationAccess(Protocol):
    def decide(
        self,
        *,
        account_id: UUID,
        permission: str,
        resource: EvaluationResource,
        occurred_at: datetime,
    ) -> EvaluationDecision: ...


class EvaluationTaskAccessService:
    def __init__(
        self,
        db: Session | None = None,
        *,
        access: _EvaluationAccess | None = None,
    ) -> None:
        if access is None:
            if db is None:
                raise TypeError("db or access is required")
            access = EvaluationAccessService(db)
        self.access = access
        self.db = db

    @staticmethod
    def _not_found() -> EvaluationResourceNotFound:
        # Keep all selector failures indistinguishable to callers.  In
        # particular, do not put account, enrollment, or project identifiers
        # in this exception's message.
        return EvaluationResourceNotFound("evaluation resource not found")

    def _sql_context(
        self,
        *,
        account_id: UUID,
        permission: str,
        occurred_at: datetime,
    ) -> EvaluationSqlContext:
        if self.db is None:
            raise TypeError("a database session is required for target selection")
        access = self.access
        if hasattr(access, "sql_context"):
            # ``hasattr`` keeps the existing injected access protocol usable
            # for task_allowed tests while production selection uses the
            # shared SQL context implementation.
            return type_cast(
                EvaluationSqlContext,
                type_cast(Any, access).sql_context(
                    account_id=account_id,
                    permission=permission,
                    occurred_at=occurred_at,
                ),
            )
        return EvaluationAccessService(self.db).sql_context(
            account_id=account_id,
            permission=permission,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _relationship_allowed(
        *,
        context: EvaluationSqlContext,
        global_allowed: bool,
    ) -> ColumnElement[bool]:
        conditions: list[ColumnElement[bool]] = []
        if context.actor_person_id is not None:
            conditions.append(Enrollment.student_person_id == context.actor_person_id)
        if context.family_student_ids:
            conditions.append(Enrollment.student_person_id.in_(context.family_student_ids))
        if context.assigned_class_ids:
            conditions.append(Enrollment.class_id.in_(context.assigned_class_ids))
        if context.staff_class_scopes:
            conditions.append(Enrollment.class_id.in_(context.staff_class_scopes))
        if context.staff_project_scopes:
            conditions.append(
                tuple_(Enrollment.cohort_id, TeachingClass.project_id).in_(
                    context.staff_project_scopes
                )
            )
        if global_allowed:
            conditions.append(true())
        return or_(*conditions) if conditions else false()

    def _authorized_targets(
        self,
        *,
        account_id: UUID,
        project_id: UUID,
        permission: str,
        require_relationship: bool,
        occurred_at: datetime,
    ) -> AuthorizedUploadTargetSelection:
        _require_aware(occurred_at)
        if self.db is None:
            raise TypeError("a database session is required for target selection")
        context = self._sql_context(
            account_id=account_id,
            permission=permission,
            occurred_at=occurred_at,
        )
        global_allowed = bool(self.db.scalar(select(context.allowed(EvaluationSqlResource()))))
        resource = EvaluationSqlResource(
            student_person_id=Enrollment.student_person_id,
            enrollment_id=Enrollment.id,
            cohort_id=Enrollment.cohort_id,
            project_id=TeachingClass.project_id,
            class_id=Enrollment.class_id,
        )
        business_day = occurred_at.astimezone(BUSINESS_TIME_ZONE).date()
        effective_start_at = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
            else_=Cohort.starts_at,
        )
        effective_end_at = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
            else_=Cohort.ends_at,
        )
        cohort_start_day = cast(
            func.timezone(str(BUSINESS_TIME_ZONE), effective_start_at),
            Date,
        )
        cohort_end_day = cast(
            func.timezone(str(BUSINESS_TIME_ZONE), effective_end_at),
            Date,
        )
        conditions: list[ColumnElement[bool]] = [
            context.allowed(resource),
            self._sql_context(
                account_id=account_id,
                permission="evaluation_center.access",
                occurred_at=occurred_at,
            ).allowed(resource),
        ]
        if require_relationship:
            conditions.append(
                self._relationship_allowed(
                    context=context,
                    global_allowed=global_allowed,
                )
            )
        statement = (
            select(
                Enrollment.student_person_id.label("student_person_id"),
                Enrollment.id.label("enrollment_id"),
                Enrollment.cohort_id.label("cohort_id"),
                Enrollment.class_id.label("class_id"),
                TeachingClass.project_id.label("project_id"),
                effective_start_at.label("cohort_starts_at"),
                effective_end_at.label("cohort_ends_at"),
            )
            .join(
                AdmissionCase,
                AdmissionCase.current_enrollment_id == Enrollment.id,
            )
            .join(Person, Person.id == Enrollment.student_person_id)
            .join(StudentProfile, StudentProfile.person_id == Person.id)
            .join(
                TeachingClass,
                (TeachingClass.id == Enrollment.class_id)
                & (TeachingClass.cohort_id == Enrollment.cohort_id),
            )
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .where(
                AdmissionCase.status == "enrolled",
                AdmissionCase.id == Enrollment.admission_case_id,
                AdmissionCase.student_person_id == Enrollment.student_person_id,
                Enrollment.status.in_(("active", "completed")),
                Person.deleted_at.is_(None),
                StudentProfile.status == "active",
                TeachingClass.project_id == project_id,
                TeachingClass.is_active.is_(True),
                GameProject.is_active.is_(True),
                Cohort.status != "archived",
                cohort_start_day <= business_day + timedelta(days=7),
                cohort_end_day >= business_day - timedelta(days=7),
                *conditions,
            )
        )
        return AuthorizedUploadTargetSelection(
            statement=statement,
            context=context,
            global_allowed=global_allowed,
        )

    def authorized_upload_targets(
        self,
        *,
        account_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedUploadTargetSelection:
        """Build the single formal-enrollment selector used by all upload paths."""

        return self._authorized_targets(
            account_id=account_id,
            project_id=project_id,
            permission="evaluation_media.upload",
            require_relationship=True,
            occurred_at=occurred_at,
        )

    def authorized_match_targets(
        self,
        *,
        account_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedUploadTargetSelection:
        """Build the same formal-enrollment selector used by match mutations."""

        return self._authorized_targets(
            account_id=account_id,
            project_id=project_id,
            permission="evaluation_jobs.match",
            require_relationship=False,
            occurred_at=occurred_at,
        )

    def require_match_target(
        self,
        *,
        account_id: UUID,
        enrollment_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedEvaluationTarget:
        """Select one current target under the dedicated match capability."""

        if self.db is None:
            raise TypeError("a database session is required for target selection")
        selection = self.authorized_match_targets(
            account_id=account_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )
        row = (
            self.db.execute(selection.statement.where(Enrollment.id == enrollment_id).limit(1))
            .mappings()
            .first()
        )
        if row is None:
            raise self._not_found()
        return AuthorizedEvaluationTarget(
            student_person_id=row["student_person_id"],
            enrollment_id=row["enrollment_id"],
            cohort_id=row["cohort_id"],
            project_id=row["project_id"],
            class_id=row["class_id"],
            authorization_source="administrator",
            upload_window=upload_window_for_cohort(
                starts_at=row["cohort_starts_at"],
                ends_at=row["cohort_ends_at"],
            ),
        )

    def require_upload_target(
        self,
        *,
        account_id: UUID,
        enrollment_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedEvaluationTarget:
        """Select one formally enrolled, currently authorized upload target.

        All object state and policy state are read before returning.  A miss
        is intentionally collapsed into ``EvaluationResourceNotFound`` so
        callers cannot distinguish missing, private, or ineligible targets.
        """

        _require_aware(occurred_at)
        if self.db is None:
            raise TypeError("a database session is required for target selection")

        selection = self.authorized_upload_targets(
            account_id=account_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )
        row = (
            self.db.execute(selection.statement.where(Enrollment.id == enrollment_id).limit(1))
            .mappings()
            .first()
        )
        if row is None:
            raise self._not_found()
        return selection.target_from_mapping(row)

    def require_upload_capability(
        self,
        *,
        account_id: UUID,
        project_id: UUID,
        occurred_at: datetime,
    ) -> AuthorizedEvaluationTarget:
        """Require at least one current formal target for an unmatched upload."""

        if self.db is None:
            raise TypeError("a database session is required for target selection")
        selection = self.authorized_upload_targets(
            account_id=account_id,
            project_id=project_id,
            occurred_at=occurred_at,
        )
        relationship_row = (
            self.db.execute(
                selection.statement.where(
                    self._relationship_allowed(
                        context=selection.context,
                        global_allowed=False,
                    )
                ).limit(1)
            )
            .mappings()
            .first()
        )
        if relationship_row is not None:
            return selection.target_from_mapping(relationship_row)
        if not selection.global_allowed:
            raise self._not_found()
        global_row = self.db.execute(selection.statement.limit(1)).mappings().first()
        if global_row is None:
            raise self._not_found()
        return selection.target_from_mapping(global_row)

    @staticmethod
    def require_delegated_upload_reason(
        target: AuthorizedEvaluationTarget,
        reason: str | None,
    ) -> str | None:
        """Validate and normalize an administrator delegation reason."""

        if target.authorization_source != "administrator":
            return None
        if reason is None:
            raise ValueError("business reason is required")
        if not isinstance(reason, str):
            raise TypeError("business reason must be a string")
        trimmed = reason.strip()
        if not trimmed:
            raise ValueError("business reason is required")
        if len(trimmed) > 300:
            raise ValueError("business reason must be at most 300 characters")
        return trimmed

    def task_allowed(
        self,
        *,
        account_id: UUID,
        permission: str,
        resource: EvaluationTaskResource,
        occurred_at: datetime,
    ) -> bool:
        center_allowed = self.access.decide(
            account_id=account_id,
            permission="evaluation_center.access",
            resource=resource.as_evaluation_resource(),
            occurred_at=occurred_at,
        ).allowed
        return self.access.decide(
            account_id=account_id,
            permission=permission,
            resource=resource.as_evaluation_resource(),
            occurred_at=occurred_at,
        ).allowed and center_allowed
