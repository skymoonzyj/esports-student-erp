from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, SQLColumnExpression, and_, case, false, func, or_, select
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.admissions.models import Enrollment
from app.auth.models import UserAccount
from app.core.privacy import contains_phone_or_identity, looks_like_raw_secret
from app.evaluations.access import (
    EvaluationAccessService,
    EvaluationSqlContext,
    EvaluationSqlResource,
)
from app.evaluations.context_service import EvaluationContextService
from app.evaluations.errors import EvaluationOperationDenied, EvaluationResourceNotFound
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.review_models import (
    SystemEvaluationManualDraft,
    SystemEvaluationResultVersion,
)
from app.evaluations.staff_models import CohortStaffAssignment, EvaluationStaffRole
from app.evaluations.task_access import EvaluationTaskAccessService
from app.evaluations.task_models import SystemEvaluationTask, SystemEvaluationTaskMedia
from app.identity.models import Person, StaffProfile, StudentProfile
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import CoachAssignment, Cohort, GameProject, TeachingClass

_WORKING_PERMISSIONS = (
    "evaluation_jobs.match",
    "evaluation_jobs.manual_input",
    "evaluation_jobs.review",
)
_PROCESSING_STATES = frozenset({"uploaded", "identifying", "queued", "processing"})
_MANUAL_STATES = frozenset({"manual_input", "correction_pending", "revision_draft"})
_SENSITIVE_RESULT_KEYS = frozenset(
    {
        "account_id",
        "game_id",
        "id_card",
        "id_number",
        "identity",
        "mobile",
        "nickname",
        "phone",
        "player_id",
        "player_name",
    }
)
_REDACTED_RESULT_TEXT = "已隐藏敏感内容"


@dataclass(frozen=True)
class EvaluationQueryFilters:
    status: str | None = None
    queue: str | None = None
    cohort_id: UUID | None = None
    project_id: UUID | None = None
    class_id: UUID | None = None
    student_id: UUID | None = None
    published_only: bool = False


@dataclass(frozen=True)
class EvaluationResultProjection:
    id: UUID
    version: int
    source: str
    fields: dict[str, Any]
    confidences: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]
    components: tuple[dict[str, Any], ...]
    missing_fields: tuple[str, ...]
    score: Decimal
    grade: str
    advice: str
    adapter_id: str | None
    adapter_version: str | None
    scoring_rule_version: str | None
    result_schema_version: str
    review_decision: str
    reviewed_at: datetime | None
    rejection_reason: str | None
    published_at: datetime | None
    previous_version_id: UUID | None


@dataclass(frozen=True)
class EvaluationAllowedActions:
    match: bool = False
    save_manual_draft: bool = False
    submit_review: bool = False
    create_correction: bool = False
    create_revision: bool = False
    review: bool = False
    archive: bool = False
    reopen: bool = False
    view_preview: bool = False


@dataclass(frozen=True)
class EvaluationTaskProjection:
    id: UUID
    public_number: str
    status: str
    lock_version: int
    created_at: datetime
    student_person_id: UUID | None
    enrollment_id: UUID | None
    cohort_id: UUID | None
    project_id: UUID
    class_id: UUID | None
    identity_summary: str | None
    cohort_name: str | None
    project_name: str
    class_name: str | None
    media_count: int
    current_result: EvaluationResultProjection | None
    published_result: EvaluationResultProjection | None
    result_versions: tuple[EvaluationResultProjection, ...]
    allowed_actions: EvaluationAllowedActions
    allowed_reopen_modes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationTaskPage:
    items: tuple[EvaluationTaskProjection, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class EvaluationQueueCounts:
    upload_available: bool
    processing: int
    pending_match: int
    manual_input: int
    needs_review: int
    rejected: int
    published: int


@dataclass(frozen=True)
class EvaluationTaskMediaAccess:
    task_id: UUID
    media_id: UUID
    slot_key: str
    position: int
    mime_type: str
    size_bytes: int
    width_pixels: int
    height_pixels: int


@dataclass(frozen=True)
class EvaluationWorkspaceMediaSlot:
    key: str
    label: str
    position: int
    required: bool


@dataclass(frozen=True)
class EvaluationWorkspaceProfile:
    mode: str
    adapter_id: str | None
    adapter_version: str | None
    scoring_rule_version: str | None
    result_schema_version: str
    required_fields: tuple[str, ...]
    media_slots: tuple[EvaluationWorkspaceMediaSlot, ...]


@dataclass(frozen=True)
class EvaluationWorkspaceMedia:
    id: UUID
    slot_key: str
    position: int
    mime_type: str
    size_bytes: int
    width_pixels: int
    height_pixels: int


@dataclass(frozen=True)
class EvaluationWorkspaceDraft:
    draft_revision: int
    fields: dict[str, Any]
    confidences: dict[str, Any]
    warnings: tuple[dict[str, Any], ...]
    components: tuple[dict[str, Any], ...]
    missing_fields: tuple[str, ...]
    score: Decimal | None
    grade: str | None
    advice: str | None
    updated_at: datetime


@dataclass(frozen=True)
class EvaluationTaskWorkspace:
    task_id: UUID
    task_version: int
    profile: EvaluationWorkspaceProfile
    media: tuple[EvaluationWorkspaceMedia, ...]
    draft: EvaluationWorkspaceDraft | None


@dataclass(frozen=True)
class EvaluationMatchTarget:
    student_person_id: UUID
    student_name: str
    student_number: str
    enrollment_id: UUID
    cohort_id: UUID
    cohort_name: str
    class_id: UUID
    class_name: str


@dataclass(frozen=True)
class EvaluationMatchTargetPage:
    items: tuple[EvaluationMatchTarget, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class EvaluationTaskFilterOption:
    id: UUID
    label: str


@dataclass(frozen=True)
class EvaluationTaskFilterOptions:
    cohorts: tuple[EvaluationTaskFilterOption, ...]
    projects: tuple[EvaluationTaskFilterOption, ...]
    classes: tuple[EvaluationTaskFilterOption, ...]
    students: tuple[EvaluationTaskFilterOption, ...]


@dataclass(frozen=True)
class _TaskQueryContexts:
    center: EvaluationSqlContext
    match: EvaluationSqlContext
    manual: EvaluationSqlContext
    review: EvaluationSqlContext
    media: EvaluationSqlContext


class EvaluationQueryService:
    """Build privacy-bounded task projections from shared SQL predicates."""

    def __init__(
        self,
        db: Session,
        *,
        access: EvaluationAccessService | None = None,
    ) -> None:
        self.db = db
        self.access = access or EvaluationAccessService(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    @staticmethod
    def _resource() -> EvaluationSqlResource:
        return EvaluationSqlResource(
            student_person_id=cast(
                SQLColumnExpression[UUID],
                SystemEvaluationTask.student_person_id,
            ),
            enrollment_id=cast(
                SQLColumnExpression[UUID],
                SystemEvaluationTask.enrollment_id,
            ),
            cohort_id=cast(
                SQLColumnExpression[UUID],
                SystemEvaluationTask.cohort_id,
            ),
            project_id=SystemEvaluationTask.project_id,
            class_id=cast(
                SQLColumnExpression[UUID],
                SystemEvaluationTask.class_id,
            ),
        )

    def _contexts(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> _TaskQueryContexts:
        return _TaskQueryContexts(
            center=self.access.sql_context(
                account_id=actor_user_id,
                permission="evaluation_center.access",
                occurred_at=occurred_at,
            ),
            match=self.access.sql_context(
                account_id=actor_user_id,
                permission="evaluation_jobs.match",
                occurred_at=occurred_at,
            ),
            manual=self.access.sql_context(
                account_id=actor_user_id,
                permission="evaluation_jobs.manual_input",
                occurred_at=occurred_at,
            ),
            review=self.access.sql_context(
                account_id=actor_user_id,
                permission="evaluation_jobs.review",
                occurred_at=occurred_at,
            ),
            media=self.access.sql_context(
                account_id=actor_user_id,
                permission="evaluation_media.original.read",
                occurred_at=occurred_at,
            ),
        )

    @staticmethod
    def _has_capability(context: EvaluationSqlContext) -> bool:
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

    @staticmethod
    def _target_is_live() -> ColumnElement[bool]:
        target_person = aliased(Person)
        target_student = aliased(StudentProfile)
        return or_(
            SystemEvaluationTask.student_person_id.is_(None),
            select(target_person.id)
            .join(
                target_student,
                target_student.person_id == target_person.id,
            )
            .where(
                target_person.id == SystemEvaluationTask.student_person_id,
                target_person.deleted_at.is_(None),
                target_student.status == "active",
            )
            .exists(),
        )

    @staticmethod
    def _historical_coach_scope(actor_person_id: UUID) -> ColumnElement[bool]:
        enrollment_start = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
            else_=Cohort.starts_at,
        )
        enrollment_end = func.coalesce(
            Enrollment.cancelled_at,
            Enrollment.completed_at,
            case(
                (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
                else_=Cohort.ends_at,
            ),
        )
        return (
            select(CoachAssignment.id)
            .where(
                Enrollment.id == SystemEvaluationTask.enrollment_id,
                Cohort.id == Enrollment.cohort_id,
                CoachAssignment.coach_person_id == actor_person_id,
                CoachAssignment.class_id == SystemEvaluationTask.class_id,
                CoachAssignment.valid_from < enrollment_end,
                or_(
                    CoachAssignment.valid_until.is_(None),
                    CoachAssignment.valid_until > enrollment_start,
                ),
                or_(
                    CoachAssignment.ended_at.is_(None),
                    CoachAssignment.ended_at > enrollment_start,
                ),
                CoachEligibility.eligible_person_predicate(CoachAssignment.coach_person_id),
            )
            .exists()
        )

    @staticmethod
    def _historical_staff_scope(actor_person_id: UUID) -> ColumnElement[bool]:
        enrollment_start = case(
            (Enrollment.schedule_mode == "custom", Enrollment.custom_starts_at),
            else_=Cohort.starts_at,
        )
        enrollment_end = func.coalesce(
            Enrollment.cancelled_at,
            Enrollment.completed_at,
            case(
                (Enrollment.schedule_mode == "custom", Enrollment.custom_ends_at),
                else_=Cohort.ends_at,
            ),
        )
        assignment_scope = or_(
            CohortStaffAssignment.class_id == SystemEvaluationTask.class_id,
            and_(
                CohortStaffAssignment.project_id == SystemEvaluationTask.project_id,
                CohortStaffAssignment.cohort_id == SystemEvaluationTask.cohort_id,
            ),
        )
        eligible_staff = (
            select(StaffProfile.person_id)
            .join(
                Person,
                Person.id == StaffProfile.person_id,
            )
            .where(
                StaffProfile.person_id == actor_person_id,
                Person.deleted_at.is_(None),
            )
            .exists()
        )
        return and_(
            eligible_staff,
            select(CohortStaffAssignment.id)
            .join(
                EvaluationStaffRole,
                EvaluationStaffRole.id == CohortStaffAssignment.role_id,
            )
            .where(
                Enrollment.id == SystemEvaluationTask.enrollment_id,
                Cohort.id == Enrollment.cohort_id,
                CohortStaffAssignment.staff_person_id == actor_person_id,
                CohortStaffAssignment.cohort_id == SystemEvaluationTask.cohort_id,
                assignment_scope,
                EvaluationStaffRole.is_active.is_(True),
                CohortStaffAssignment.valid_from < enrollment_end,
                or_(
                    CohortStaffAssignment.valid_until.is_(None),
                    CohortStaffAssignment.valid_until > enrollment_start,
                ),
                or_(
                    CohortStaffAssignment.ended_at.is_(None),
                    CohortStaffAssignment.ended_at > enrollment_start,
                ),
            )
            .exists(),
        )

    def _account_visibility(
        self,
        *,
        actor_user_id: UUID,
        contexts: _TaskQueryContexts,
    ) -> ColumnElement[bool]:
        account = self.db.get(UserAccount, actor_user_id)
        if account is None or account.status != "active":
            return false()
        actor = self.db.get(Person, account.person_id)
        if actor is None or actor.deleted_at is not None:
            return false()

        resource = self._resource()
        family_ids = contexts.manual.family_student_ids
        relationship: list[ColumnElement[bool]] = [
            SystemEvaluationTask.student_person_id == account.person_id,
            and_(
                SystemEvaluationTask.student_person_id.is_(None),
                SystemEvaluationTask.created_by_user_id == actor_user_id,
            ),
            self._historical_coach_scope(account.person_id),
            self._historical_staff_scope(account.person_id),
        ]
        if family_ids:
            relationship.append(SystemEvaluationTask.student_person_id.in_(family_ids))
        return and_(
            self._target_is_live(),
            contexts.center.allowed(resource),
            or_(
                *relationship,
                contexts.match.allowed(resource),
                contexts.manual.allowed(resource),
                contexts.review.allowed(resource),
            ),
        )

    def _operational_visibility(
        self,
        contexts: _TaskQueryContexts,
    ) -> ColumnElement[bool]:
        resource = self._resource()
        return and_(
            self._target_is_live(),
            contexts.center.allowed(resource),
            or_(
                contexts.match.allowed(resource),
                contexts.manual.allowed(resource),
                contexts.review.allowed(resource),
            ),
        )

    @staticmethod
    def _media_count() -> ColumnElement[int]:
        return (
            select(func.count(SystemEvaluationTaskMedia.id))
            .where(SystemEvaluationTaskMedia.task_id == SystemEvaluationTask.id)
            .correlate(SystemEvaluationTask)
            .scalar_subquery()
        )

    def _statement(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        operational: bool,
    ) -> Select[Any]:
        contexts = self._contexts(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        resource = self._resource()
        target_person = aliased(Person)
        target_student = aliased(StudentProfile)
        visibility = (
            self._operational_visibility(contexts)
            if operational
            else self._account_visibility(
                actor_user_id=actor_user_id,
                contexts=contexts,
            )
        )
        return (
            select(
                SystemEvaluationTask,
                target_person.full_name.label("student_name"),
                target_student.student_number.label("student_number"),
                Cohort.name.label("cohort_name"),
                GameProject.name.label("project_name"),
                TeachingClass.name.label("class_name"),
                self._media_count().label("media_count"),
                contexts.match.allowed(resource).label("can_match"),
                contexts.manual.allowed(resource).label("can_manual"),
                contexts.review.allowed(resource).label("can_review"),
                contexts.media.allowed(resource).label("can_media"),
            )
            .outerjoin(
                target_person,
                target_person.id == SystemEvaluationTask.student_person_id,
            )
            .outerjoin(
                target_student,
                target_student.person_id == target_person.id,
            )
            .outerjoin(Cohort, Cohort.id == SystemEvaluationTask.cohort_id)
            .join(GameProject, GameProject.id == SystemEvaluationTask.project_id)
            .outerjoin(
                TeachingClass,
                TeachingClass.id == SystemEvaluationTask.class_id,
            )
            .where(visibility)
        )

    @staticmethod
    def _apply_filters(
        statement: Select[Any],
        filters: EvaluationQueryFilters,
    ) -> Select[Any]:
        conditions: list[ColumnElement[bool]] = []
        if filters.status is not None:
            conditions.append(SystemEvaluationTask.status == filters.status)
        if filters.queue is not None:
            queue_states = {
                "processing": tuple(sorted(_PROCESSING_STATES)),
                "manual_input": tuple(sorted(_MANUAL_STATES)),
            }.get(filters.queue, (filters.queue,))
            conditions.append(SystemEvaluationTask.status.in_(queue_states))
        if filters.cohort_id is not None:
            conditions.append(SystemEvaluationTask.cohort_id == filters.cohort_id)
        if filters.project_id is not None:
            conditions.append(SystemEvaluationTask.project_id == filters.project_id)
        if filters.class_id is not None:
            conditions.append(SystemEvaluationTask.class_id == filters.class_id)
        if filters.student_id is not None:
            conditions.append(SystemEvaluationTask.student_person_id == filters.student_id)
        if filters.published_only:
            conditions.append(SystemEvaluationTask.published_result_version_id.is_not(None))
        return statement.where(*conditions)

    @staticmethod
    def _mask_identity(name: str | None, student_number: str | None) -> str | None:
        if name is None:
            return None
        masked_name = "*" if len(name) <= 1 else name[0] + "*" * (len(name) - 1)
        number = student_number or ""
        masked_number = "*" * max(len(number) - 4, 0) + number[-4:]
        return f"{masked_name} · {masked_number}" if masked_number else masked_name

    @staticmethod
    def _safe_text(value: str) -> str:
        if contains_phone_or_identity(value) or looks_like_raw_secret(value):
            return _REDACTED_RESULT_TEXT
        return value

    @classmethod
    def _safe_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._safe_text(value)
        if isinstance(value, list):
            return [cls._safe_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._safe_value(item) for key, item in value.items()}
        return value

    @classmethod
    def _safe_fields(cls, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            key: (None if key.casefold() in _SENSITIVE_RESULT_KEYS else cls._safe_value(value))
            for key, value in fields.items()
        }

    @classmethod
    def _result_projection(
        cls,
        result: SystemEvaluationResultVersion | None,
    ) -> EvaluationResultProjection | None:
        if result is None:
            return None
        return EvaluationResultProjection(
            id=result.id,
            version=result.version,
            source=result.source,
            fields=cls._safe_fields(dict(result.fields)),
            confidences=dict(result.confidences),
            warnings=tuple(cls._safe_value(dict(item)) for item in result.warnings),
            components=tuple(cls._safe_value(dict(item)) for item in result.components),
            missing_fields=tuple(result.missing_fields),
            score=result.score,
            grade=result.grade,
            advice=cls._safe_text(result.advice),
            adapter_id=result.adapter_id,
            adapter_version=result.adapter_version,
            scoring_rule_version=result.scoring_rule_version,
            result_schema_version=result.result_schema_version,
            review_decision=result.review_decision,
            reviewed_at=result.reviewed_at,
            rejection_reason=(
                cls._safe_text(result.rejection_reason)
                if result.rejection_reason is not None
                else None
            ),
            published_at=result.published_at,
            previous_version_id=result.previous_version_id,
        )

    def _project_rows(
        self,
        rows: list[Any],
        *,
        actor_user_id: UUID,
    ) -> tuple[EvaluationTaskProjection, ...]:
        result_ids = {
            result_id
            for row in rows
            for result_id in (
                row[0].current_result_version_id,
                row[0].published_result_version_id,
            )
            if result_id is not None
        }
        task_ids = {row[0].id for row in rows}
        task_results = (
            list(
                self.db.scalars(
                    select(SystemEvaluationResultVersion)
                    .where(SystemEvaluationResultVersion.task_id.in_(task_ids))
                    .order_by(
                        SystemEvaluationResultVersion.task_id,
                        SystemEvaluationResultVersion.version,
                    )
                )
            )
            if task_ids
            else []
        )
        results = {result.id: result for result in task_results}
        if result_ids - set(results):
            raise RuntimeError("evaluation task result pointer is missing")
        versions_by_task: dict[UUID, list[SystemEvaluationResultVersion]] = {}
        for result in task_results:
            versions_by_task.setdefault(result.task_id, []).append(result)
        projected: list[EvaluationTaskProjection] = []
        for row in rows:
            task = row[0]
            mapping = row._mapping
            can_match = bool(mapping["can_match"])
            can_manual = bool(mapping["can_manual"])
            can_review = bool(mapping["can_review"])
            can_media = bool(mapping["can_media"])
            current = results.get(task.current_result_version_id)
            published = results.get(task.published_result_version_id)
            current_visible = bool(
                current is not None
                and (
                    current.id == task.published_result_version_id
                    or can_match
                    or can_manual
                    or can_review
                )
            )
            working_visible = can_match or can_manual or can_review
            self_review = bool(current is not None and current.created_by_user_id == actor_user_id)
            reopen_modes: list[str] = []
            if can_match and task.status == "archived":
                if (
                    task.analysis_config_snapshot.get("mode") == "automatic"
                    and task.current_attempt < 3
                ):
                    reopen_modes.append("automatic")
                if current is not None and current.review_decision == "rejected":
                    reopen_modes.append("correction")
            actions = EvaluationAllowedActions(
                match=can_match and task.status == "pending_match",
                save_manual_draft=can_manual
                and task.status in {"manual_input", "correction_pending"},
                submit_review=can_manual and task.status in {"manual_input", "correction_pending"},
                create_correction=can_manual and task.status == "rejected",
                create_revision=can_manual and task.status == "published",
                review=can_review and task.status == "needs_review" and not self_review,
                archive=can_match and task.status in {"failed", "rejected"},
                reopen=bool(reopen_modes),
                view_preview=can_media,
            )
            projected.append(
                EvaluationTaskProjection(
                    id=task.id,
                    public_number=task.public_number,
                    status=task.status,
                    lock_version=task.lock_version,
                    created_at=task.created_at,
                    student_person_id=task.student_person_id,
                    enrollment_id=task.enrollment_id,
                    cohort_id=task.cohort_id,
                    project_id=task.project_id,
                    class_id=task.class_id,
                    identity_summary=self._mask_identity(
                        mapping["student_name"],
                        mapping["student_number"],
                    ),
                    cohort_name=mapping["cohort_name"],
                    project_name=mapping["project_name"],
                    class_name=mapping["class_name"],
                    media_count=int(mapping["media_count"] or 0),
                    current_result=(self._result_projection(current) if current_visible else None),
                    published_result=self._result_projection(published),
                    result_versions=tuple(
                        projection
                        for result in versions_by_task.get(task.id, [])
                        if working_visible or result.review_decision == "approved"
                        if (projection := self._result_projection(result)) is not None
                    ),
                    allowed_actions=actions,
                    allowed_reopen_modes=tuple(reopen_modes),
                )
            )
        return tuple(projected)

    def _list(
        self,
        *,
        actor_user_id: UUID,
        filters: EvaluationQueryFilters,
        page: int,
        page_size: int,
        occurred_at: datetime,
        operational: bool,
    ) -> EvaluationTaskPage:
        self._require_aware(occurred_at)
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("pagination is invalid")
        contexts = self._contexts(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        if operational and not any(
            self._has_capability(context)
            for context in (contexts.match, contexts.manual, contexts.review)
        ):
            raise EvaluationOperationDenied("evaluation operation is denied")
        statement = self._apply_filters(
            self._statement(
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
                operational=operational,
            ),
            filters,
        )
        total = int(
            self.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
            or 0
        )
        rows = list(
            self.db.execute(
                statement.order_by(
                    SystemEvaluationTask.created_at.desc(),
                    SystemEvaluationTask.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return EvaluationTaskPage(
            items=self._project_rows(rows, actor_user_id=actor_user_id),
            total=total,
            page=page,
            page_size=page_size,
        )

    def list_account_tasks(
        self,
        *,
        actor_user_id: UUID,
        filters: EvaluationQueryFilters,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> EvaluationTaskPage:
        return self._list(
            actor_user_id=actor_user_id,
            filters=filters,
            page=page,
            page_size=page_size,
            occurred_at=occurred_at,
            operational=False,
        )

    def list_admin_tasks(
        self,
        *,
        actor_user_id: UUID,
        filters: EvaluationQueryFilters,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> EvaluationTaskPage:
        return self._list(
            actor_user_id=actor_user_id,
            filters=filters,
            page=page,
            page_size=page_size,
            occurred_at=occurred_at,
            operational=True,
        )

    def _get(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        operational: bool,
    ) -> EvaluationTaskProjection:
        if operational:
            contexts = self._contexts(
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
            )
            if not any(
                self._has_capability(context)
                for context in (contexts.match, contexts.manual, contexts.review)
            ):
                raise EvaluationOperationDenied("evaluation operation is denied")
        statement = self._statement(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=operational,
        ).where(SystemEvaluationTask.id == task_id)
        row = self.db.execute(statement.limit(1)).first()
        if row is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        projected = self._project_rows([row], actor_user_id=actor_user_id)
        assert projected
        return projected[0]

    def get_account_task(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskProjection:
        return self._get(
            task_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=False,
        )

    def get_admin_task(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskProjection:
        return self._get(
            task_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=True,
        )

    def _filter_options(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        operational: bool,
    ) -> EvaluationTaskFilterOptions:
        self._require_aware(occurred_at)
        contexts = self._contexts(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        if operational and not any(
            self._has_capability(context)
            for context in (contexts.match, contexts.manual, contexts.review)
        ):
            raise EvaluationOperationDenied("evaluation operation is denied")
        visible = self._statement(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=operational,
        ).subquery("visible_evaluation_task_filter_options")

        def directory(id_column: Any, label_column: Any) -> tuple[EvaluationTaskFilterOption, ...]:
            rows = self.db.execute(
                select(id_column, label_column)
                .where(
                    id_column.is_not(None),
                    label_column.is_not(None),
                )
                .distinct()
                .order_by(label_column, id_column)
            )
            return tuple(
                EvaluationTaskFilterOption(id=row[0], label=row[1])
                for row in rows
            )

        student_rows = self.db.execute(
            select(
                visible.c.student_person_id,
                visible.c.student_name,
                visible.c.student_number,
            )
            .where(
                visible.c.student_person_id.is_not(None),
                visible.c.student_name.is_not(None),
            )
            .distinct()
            .order_by(
                visible.c.student_number,
                visible.c.student_person_id,
            )
        )
        students = tuple(
            EvaluationTaskFilterOption(
                id=row.student_person_id,
                label=masked,
            )
            for row in student_rows
            if (masked := self._mask_identity(row.student_name, row.student_number))
            is not None
        )
        return EvaluationTaskFilterOptions(
            cohorts=directory(visible.c.cohort_id, visible.c.cohort_name),
            projects=directory(visible.c.project_id, visible.c.project_name),
            classes=directory(visible.c.class_id, visible.c.class_name),
            students=students,
        )

    def account_filter_options(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskFilterOptions:
        return self._filter_options(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=False,
        )

    def admin_filter_options(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskFilterOptions:
        return self._filter_options(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=True,
        )

    @classmethod
    def _workspace_profile(
        cls,
        task: SystemEvaluationTask,
    ) -> EvaluationWorkspaceProfile:
        snapshot = task.analysis_config_snapshot
        required_fields = snapshot.get("required_fields")
        raw_slots = snapshot.get("media_slots")
        mode = snapshot.get("mode")
        result_schema_version = snapshot.get("result_schema_version")
        if (
            mode not in {"manual_only", "automatic"}
            or not isinstance(result_schema_version, str)
            or not result_schema_version.strip()
            or not isinstance(required_fields, list)
            or not all(isinstance(item, str) and item.strip() for item in required_fields)
            or len(required_fields) != len(set(required_fields))
            or not isinstance(raw_slots, list)
        ):
            raise RuntimeError("evaluation task profile snapshot is invalid")
        media_slots: list[EvaluationWorkspaceMediaSlot] = []
        for position, raw_slot in enumerate(raw_slots, start=1):
            if (
                not isinstance(raw_slot, dict)
                or set(raw_slot) != {"key", "label", "required"}
                or not isinstance(raw_slot.get("key"), str)
                or not str(raw_slot["key"]).strip()
                or not isinstance(raw_slot.get("label"), str)
                or not str(raw_slot["label"]).strip()
                or not isinstance(raw_slot.get("required"), bool)
                or position > 3
            ):
                raise RuntimeError("evaluation task profile snapshot is invalid")
            media_slots.append(
                EvaluationWorkspaceMediaSlot(
                    key=str(raw_slot["key"]),
                    label=cls._safe_text(str(raw_slot["label"])),
                    position=position,
                    required=bool(raw_slot["required"]),
                )
            )
        optional_versions = (
            snapshot.get("adapter_id"),
            snapshot.get("adapter_version"),
            snapshot.get("scoring_rule_version"),
        )
        if not all(value is None or isinstance(value, str) for value in optional_versions):
            raise RuntimeError("evaluation task profile snapshot is invalid")
        return EvaluationWorkspaceProfile(
            mode=mode,
            adapter_id=cast(str | None, optional_versions[0]),
            adapter_version=cast(str | None, optional_versions[1]),
            scoring_rule_version=cast(str | None, optional_versions[2]),
            result_schema_version=result_schema_version,
            required_fields=tuple(cast(list[str], required_fields)),
            media_slots=tuple(media_slots),
        )

    @classmethod
    def _workspace_draft_projection(
        cls,
        draft: SystemEvaluationManualDraft,
    ) -> EvaluationWorkspaceDraft:
        return EvaluationWorkspaceDraft(
            draft_revision=draft.lock_version,
            fields=cls._safe_fields(dict(draft.fields)),
            confidences=dict(draft.confidences),
            warnings=tuple(cls._safe_value(dict(item)) for item in draft.warnings),
            components=tuple(cls._safe_value(dict(item)) for item in draft.components),
            missing_fields=tuple(draft.missing_fields),
            score=draft.score,
            grade=cls._safe_text(draft.grade) if draft.grade is not None else None,
            advice=(cls._safe_text(draft.advice) if draft.advice is not None else None),
            updated_at=draft.updated_at,
        )

    def _workspace_media(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        operational: bool,
    ) -> tuple[EvaluationWorkspaceMedia, ...]:
        contexts = self._contexts(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        visibility = (
            self._operational_visibility(contexts)
            if operational
            else self._account_visibility(
                actor_user_id=actor_user_id,
                contexts=contexts,
            )
        )
        rows = self.db.execute(
            select(SystemEvaluationTaskMedia, EvaluationMediaBlob)
            .join(
                SystemEvaluationTask,
                SystemEvaluationTask.id == SystemEvaluationTaskMedia.task_id,
            )
            .join(
                EvaluationMediaAsset,
                EvaluationMediaAsset.id == SystemEvaluationTaskMedia.media_id,
            )
            .join(
                EvaluationMediaBlob,
                EvaluationMediaBlob.id == EvaluationMediaAsset.blob_id,
            )
            .where(
                SystemEvaluationTask.id == task_id,
                visibility,
                contexts.media.allowed(self._resource()),
                EvaluationMediaBlob.storage_status == "available",
                EvaluationMediaBlob.validation_status == "validated",
                EvaluationMediaBlob.retention_status == "retained",
            )
            .order_by(
                SystemEvaluationTaskMedia.position,
                SystemEvaluationTaskMedia.id,
            )
        )
        return tuple(
            EvaluationWorkspaceMedia(
                id=task_media.media_id,
                slot_key=task_media.slot_key,
                position=task_media.position,
                mime_type=blob.mime_type,
                size_bytes=blob.size_bytes,
                width_pixels=blob.width_pixels,
                height_pixels=blob.height_pixels,
            )
            for task_media, blob in rows
        )

    def _workspace_draft(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        operational: bool,
    ) -> EvaluationWorkspaceDraft | None:
        contexts = self._contexts(
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
        )
        visibility = (
            self._operational_visibility(contexts)
            if operational
            else self._account_visibility(
                actor_user_id=actor_user_id,
                contexts=contexts,
            )
        )
        draft = self.db.scalar(
            select(SystemEvaluationManualDraft)
            .join(
                SystemEvaluationTask,
                SystemEvaluationTask.id == SystemEvaluationManualDraft.task_id,
            )
            .where(
                SystemEvaluationTask.id == task_id,
                SystemEvaluationTask.status.in_({"manual_input", "correction_pending"}),
                visibility,
                contexts.manual.allowed(self._resource()),
            )
        )
        return self._workspace_draft_projection(draft) if draft is not None else None

    def _get_task_workspace(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        operational: bool,
    ) -> EvaluationTaskWorkspace:
        task_projection = self._get(
            task_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=operational,
        )
        task = self.db.get(SystemEvaluationTask, task_id)
        if task is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        return EvaluationTaskWorkspace(
            task_id=task.id,
            task_version=task_projection.lock_version,
            profile=self._workspace_profile(task),
            media=(
                self._workspace_media(
                    task_id,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                    operational=operational,
                )
                if task_projection.allowed_actions.view_preview
                else ()
            ),
            draft=(
                self._workspace_draft(
                    task_id,
                    actor_user_id=actor_user_id,
                    occurred_at=occurred_at,
                    operational=operational,
                )
                if task_projection.allowed_actions.save_manual_draft
                or task_projection.allowed_actions.submit_review
                else None
            ),
        )

    def get_account_task_workspace(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskWorkspace:
        return self._get_task_workspace(
            task_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=False,
        )

    def get_admin_task_workspace(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskWorkspace:
        return self._get_task_workspace(
            task_id,
            actor_user_id=actor_user_id,
            occurred_at=occurred_at,
            operational=True,
        )

    def list_match_targets(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        query: str,
        page: int,
        page_size: int,
        occurred_at: datetime,
    ) -> EvaluationMatchTargetPage:
        self._require_aware(occurred_at)
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("pagination is invalid")
        task = self.require_action_task(
            task_id,
            actor_user_id=actor_user_id,
            permission="evaluation_jobs.match",
            occurred_at=occurred_at,
        )
        if task.status != "pending_match":
            raise EvaluationResourceNotFound("evaluation resource not found")
        selection = EvaluationTaskAccessService(
            self.db,
            access=self.access,
        ).authorized_match_targets(
            account_id=actor_user_id,
            project_id=task.project_id,
            occurred_at=occurred_at,
        )
        authorized = selection.statement.cte("authorized_evaluation_match_targets")
        statement = (
            select(
                authorized.c.student_person_id,
                Person.full_name.label("student_name"),
                StudentProfile.student_number,
                authorized.c.enrollment_id,
                authorized.c.cohort_id,
                Cohort.name.label("cohort_name"),
                authorized.c.class_id,
                TeachingClass.name.label("class_name"),
            )
            .join(Person, Person.id == authorized.c.student_person_id)
            .join(
                StudentProfile,
                StudentProfile.person_id == authorized.c.student_person_id,
            )
            .join(Cohort, Cohort.id == authorized.c.cohort_id)
            .join(TeachingClass, TeachingClass.id == authorized.c.class_id)
        )
        normalized_query = query.strip()
        if normalized_query:
            escaped = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            statement = statement.where(
                or_(
                    Person.full_name.ilike(pattern, escape="\\"),
                    StudentProfile.student_number.ilike(pattern, escape="\\"),
                )
            )
        total = int(
            self.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
            or 0
        )
        rows = self.db.execute(
            statement.order_by(
                StudentProfile.student_number,
                authorized.c.enrollment_id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings()
        return EvaluationMatchTargetPage(
            items=tuple(
                EvaluationMatchTarget(
                    student_person_id=row["student_person_id"],
                    student_name=row["student_name"],
                    student_number=row["student_number"],
                    enrollment_id=row["enrollment_id"],
                    cohort_id=row["cohort_id"],
                    cohort_name=row["cohort_name"],
                    class_id=row["class_id"],
                    class_name=row["class_name"],
                )
                for row in rows
            ),
            total=total,
            page=page,
            page_size=page_size,
        )

    def require_action_task(
        self,
        task_id: UUID,
        *,
        actor_user_id: UUID,
        permission: str,
        occurred_at: datetime,
        for_update: bool = False,
    ) -> SystemEvaluationTask:
        self._require_aware(occurred_at)
        if permission not in _WORKING_PERMISSIONS:
            raise ValueError("evaluation permission is invalid")
        context = self.access.center_scoped_sql_context(
            account_id=actor_user_id,
            permission=permission,
            occurred_at=occurred_at,
        )
        if not self._has_capability(context):
            raise EvaluationOperationDenied("evaluation operation is denied")
        statement = select(SystemEvaluationTask).where(
            SystemEvaluationTask.id == task_id,
            self._target_is_live(),
            context.allowed(self._resource()),
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        task = self.db.scalar(statement)
        if task is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        return task

    def require_task_media(
        self,
        task_id: UUID,
        media_id: UUID,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationTaskMediaAccess:
        self._require_aware(occurred_at)
        context = self.access.center_scoped_sql_context(
            account_id=actor_user_id,
            permission="evaluation_media.original.read",
            occurred_at=occurred_at,
        )
        if not self._has_capability(context):
            raise EvaluationOperationDenied("evaluation operation is denied")
        row = self.db.execute(
            select(
                SystemEvaluationTaskMedia,
                EvaluationMediaBlob,
            )
            .join(
                SystemEvaluationTask,
                SystemEvaluationTask.id == SystemEvaluationTaskMedia.task_id,
            )
            .join(
                EvaluationMediaAsset,
                EvaluationMediaAsset.id == SystemEvaluationTaskMedia.media_id,
            )
            .join(
                EvaluationMediaBlob,
                EvaluationMediaBlob.id == EvaluationMediaAsset.blob_id,
            )
            .where(
                SystemEvaluationTaskMedia.task_id == task_id,
                SystemEvaluationTaskMedia.media_id == media_id,
                self._target_is_live(),
                context.allowed(self._resource()),
                EvaluationMediaBlob.storage_status == "available",
                EvaluationMediaBlob.validation_status == "validated",
                EvaluationMediaBlob.retention_status == "retained",
            )
            .limit(1)
        ).one_or_none()
        if row is None:
            raise EvaluationResourceNotFound("evaluation resource not found")
        task_media, blob = row
        return EvaluationTaskMediaAccess(
            task_id=task_media.task_id,
            media_id=task_media.media_id,
            slot_key=task_media.slot_key,
            position=task_media.position,
            mime_type=blob.mime_type,
            size_bytes=blob.size_bytes,
            width_pixels=blob.width_pixels,
            height_pixels=blob.height_pixels,
        )

    def dashboard_counts(
        self,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> EvaluationQueueCounts:
        self._require_aware(occurred_at)
        visible = (
            self._statement(
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
                operational=False,
            )
            .with_only_columns(SystemEvaluationTask.status)
            .subquery()
        )
        counts = {
            status: int(count)
            for status, count in self.db.execute(
                select(visible.c.status, func.count()).group_by(visible.c.status)
            )
        }
        return EvaluationQueueCounts(
            upload_available=EvaluationContextService(
                self.db,
                access=self.access,
            ).upload_operation_allowed(
                account_id=actor_user_id,
                occurred_at=occurred_at,
            ),
            processing=sum(counts.get(status, 0) for status in _PROCESSING_STATES),
            pending_match=counts.get("pending_match", 0),
            manual_input=sum(counts.get(status, 0) for status in _MANUAL_STATES),
            needs_review=counts.get("needs_review", 0),
            rejected=counts.get("rejected", 0),
            published=counts.get("published", 0),
        )
