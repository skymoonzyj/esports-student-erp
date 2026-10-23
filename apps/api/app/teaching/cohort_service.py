from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, and_, case, exists, false, func, or_, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.admissions.enrollment_service import complete_active_enrollments_for_cohort
from app.admissions.models import Enrollment
from app.audit.service import AuditService
from app.core.transactions import write_transaction
from app.teaching.access import TeachingVisibility
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.errors import (
    ClassHasActiveEnrollments,
    ClassNotFound,
    CohortArchived,
    CohortNotFound,
    CohortScheduleLocked,
    DuplicateClass,
    EliteTeamRequired,
    InactiveProfessionalTeam,
    InactiveProject,
    InvalidCohortTransitionError,
    PrimaryCoachRequired,
    ProfessionalTeamNotFound,
    ProjectNotFound,
    ProjectTeamMismatch,
    StandardClassTeamForbidden,
    TeachingServiceError,
)
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    CohortStatusEvent,
    GameProject,
    ProfessionalTeam,
    TeachingClass,
)
from app.teaching.schemas import (
    CohortCreate,
    CohortUpdate,
    TeachingClassCreate,
    TeachingClassUpdate,
)
from app.teaching.state import (
    CohortStatus,
    InvalidCohortTransition,
    TransitionTrigger,
    due_transition,
    require_transition,
)

_SCHEDULE_FIELDS = frozenset(
    {
        "starts_at",
        "ends_at",
        "default_check_in_at",
        "default_check_out_at",
        "auto_start_enabled",
        "auto_complete_enabled",
    }
)
_ACTIVE_SCHEDULE_FIELDS = frozenset(
    {"ends_at", "default_check_out_at", "auto_complete_enabled"}
)


class CohortService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cohort datetimes must include an offset")

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> None:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

    @staticmethod
    def _visibility_predicate(
        visibility: TeachingVisibility,
    ) -> ColumnElement[bool]:
        any_class = exists(
            select(TeachingClass.id).where(TeachingClass.cohort_id == Cohort.id)
        )
        if visibility.global_access:
            if not visibility.excluded_class_ids:
                return ~false()
            visible_class = exists(
                select(TeachingClass.id).where(
                    TeachingClass.cohort_id == Cohort.id,
                    TeachingClass.id.not_in(visibility.excluded_class_ids),
                )
            )
            return visible_class | ~any_class
        if not visibility.class_ids:
            return false()
        return exists(
            select(TeachingClass.id).where(
                TeachingClass.cohort_id == Cohort.id,
                TeachingClass.id.in_(visibility.class_ids),
            )
        )

    def create_cohort(
        self,
        input: CohortCreate,
        *,
        actor_user_id: UUID,
    ) -> Cohort:
        with write_transaction(self.db):
            cohort = Cohort(
                name=input.name,
                starts_at=input.starts_at,
                ends_at=input.ends_at,
                default_check_in_at=input.default_check_in_at,
                default_check_out_at=input.default_check_out_at,
                status="draft",
                auto_start_enabled=input.auto_start_enabled,
                auto_complete_enabled=input.auto_complete_enabled,
                created_by_user_id=actor_user_id,
            )
            self.db.add(cohort)
            self.db.flush()
        return cohort

    def list_cohorts(
        self,
        *,
        name: str | None,
        status: CohortStatus | None,
        date_from: datetime | None,
        date_to: datetime | None,
        visibility: TeachingVisibility,
        page: int,
        page_size: int,
    ) -> tuple[list[Cohort], int]:
        self._validate_pagination(page, page_size)
        if date_from is not None:
            self._require_aware(date_from)
        if date_to is not None:
            self._require_aware(date_to)
        query = select(Cohort).where(self._visibility_predicate(visibility))
        if name is not None:
            query = query.where(Cohort.name == name.strip())
        if status is not None:
            query = query.where(Cohort.status == status)
        if date_from is not None:
            query = query.where(Cohort.starts_at >= date_from)
        if date_to is not None:
            query = query.where(Cohort.starts_at < date_to)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        cohorts = list(
            self.db.scalars(
                query.order_by(Cohort.starts_at.desc(), Cohort.name, Cohort.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return cohorts, total

    def get_cohort(
        self,
        cohort_id: UUID,
        *,
        visibility: TeachingVisibility,
    ) -> Cohort:
        cohort = self.db.scalar(
            select(Cohort).where(
                Cohort.id == cohort_id,
                self._visibility_predicate(visibility),
            )
        )
        if cohort is None:
            raise CohortNotFound("cohort not found")
        return cohort

    def _locked_cohort(self, cohort_id: UUID) -> Cohort:
        cohort = self.db.scalar(
            select(Cohort).where(Cohort.id == cohort_id).with_for_update()
        )
        if cohort is None:
            raise CohortNotFound("cohort not found")
        return cohort

    @staticmethod
    def _validate_schedule(
        *,
        starts_at: datetime,
        ends_at: datetime,
        default_check_in_at: datetime,
        default_check_out_at: datetime,
    ) -> None:
        for value in (starts_at, ends_at, default_check_in_at, default_check_out_at):
            CohortService._require_aware(value)
        if ends_at <= starts_at:
            raise ValueError("ends_at must be after starts_at")
        if default_check_out_at < default_check_in_at:
            raise ValueError("default_check_out_at must not precede default_check_in_at")

    def update_cohort(
        self,
        cohort_id: UUID,
        input: CohortUpdate,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
        request_id: str,
    ) -> Cohort:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            cohort = self._locked_cohort(cohort_id)
            current_status = cast(CohortStatus, cohort.status)
            changed_fields = {
                field
                for field in input.model_fields_set
                if getattr(input, field) is not None
            }
            schedule_fields = changed_fields & _SCHEDULE_FIELDS
            if current_status == "archived":
                raise CohortScheduleLocked("archived cohort is read-only")
            if current_status == "active" and not schedule_fields <= _ACTIVE_SCHEDULE_FIELDS:
                raise CohortScheduleLocked("active cohort schedule field is locked")
            if current_status == "completed" and schedule_fields:
                raise CohortScheduleLocked("completed cohort schedule is locked")
            if (
                current_status == "active"
                and input.ends_at is not None
                and input.ends_at <= occurred_at
            ):
                raise CohortScheduleLocked("active cohort end must remain in the future")

            starts_at = input.starts_at or cohort.starts_at
            ends_at = input.ends_at or cohort.ends_at
            check_in = input.default_check_in_at or cohort.default_check_in_at
            check_out = input.default_check_out_at or cohort.default_check_out_at
            self._validate_schedule(
                starts_at=starts_at,
                ends_at=ends_at,
                default_check_in_at=check_in,
                default_check_out_at=check_out,
            )
            for field in changed_fields:
                setattr(cohort, field, getattr(input, field))
            cohort.updated_by_user_id = actor_user_id
            self.db.flush()
            self._reconcile_locked(
                cohort,
                now=occurred_at,
                trigger="request_reconciliation",
                request_id=request_id,
            )
            self.db.flush()
        return cohort

    def _missing_primary_query(
        self,
        cohort_id: UUID,
        *,
        at: datetime,
        visibility: TeachingVisibility | None = None,
    ) -> Select[tuple[UUID]]:
        eligible_primary = exists(
            select(CoachAssignment.id).where(
                CoachAssignment.class_id == TeachingClass.id,
                CoachAssignment.assignment_type == "primary",
                CoachAssignment.valid_from <= at,
                or_(
                    CoachAssignment.valid_until.is_(None),
                    CoachAssignment.valid_until > at,
                ),
                or_(CoachAssignment.ended_at.is_(None), CoachAssignment.ended_at > at),
                CoachEligibility.eligible_person_predicate(
                    CoachAssignment.coach_person_id
                ),
            )
        )
        query = select(TeachingClass.id).where(
            TeachingClass.cohort_id == cohort_id,
            TeachingClass.is_active.is_(True),
            ~eligible_primary,
        )
        if visibility is not None:
            query = query.where(self._class_visibility_predicate(visibility))
        return query

    def enabled_class_count(
        self,
        cohort_id: UUID,
        *,
        visibility: TeachingVisibility | None = None,
    ) -> int:
        query = select(func.count(TeachingClass.id)).where(
            TeachingClass.cohort_id == cohort_id,
            TeachingClass.is_active.is_(True),
        )
        if visibility is not None:
            query = query.where(self._class_visibility_predicate(visibility))
        return self.db.scalar(query) or 0

    def missing_primary_class_count(
        self,
        cohort_id: UUID,
        *,
        at: datetime,
        visibility: TeachingVisibility | None = None,
    ) -> int:
        self._require_aware(at)
        query = self._missing_primary_query(cohort_id, at=at, visibility=visibility)
        return self.db.scalar(select(func.count()).select_from(query.subquery())) or 0

    def _transition_locked(
        self,
        cohort: Cohort,
        *,
        target: CohortStatus,
        trigger: TransitionTrigger,
        actor_user_id: UUID | None,
        occurred_at: datetime,
        request_id: str,
    ) -> None:
        current = cast(CohortStatus, cohort.status)
        try:
            require_transition(current, target)
        except InvalidCohortTransition as exc:
            raise InvalidCohortTransitionError(str(exc)) from exc
        if current == "draft" and target == "open":
            class_ids = list(
                self.db.scalars(
                    select(TeachingClass.id)
                    .where(
                        TeachingClass.cohort_id == cohort.id,
                        TeachingClass.is_active.is_(True),
                    )
                    .with_for_update()
                )
            )
            if class_ids and self.missing_primary_class_count(cohort.id, at=occurred_at):
                raise PrimaryCoachRequired("every active class requires a primary coach")
        cohort.status = target
        cohort.updated_by_user_id = actor_user_id
        event = CohortStatusEvent(
            cohort_id=cohort.id,
            old_status=current,
            new_status=target,
            trigger=trigger,
            actor_user_id=actor_user_id,
            request_id=request_id,
            occurred_at=occurred_at,
        )
        self.db.add(event)
        if target == "completed":
            complete_active_enrollments_for_cohort(
                self.db,
                cohort_id=cohort.id,
                occurred_at=occurred_at,
                actor_user_id=actor_user_id,
                request_id=request_id,
            )
        self.db.flush()
        AuditService(self.db).append(
            actor_user_id=actor_user_id,
            action="teaching.cohort.transition",
            resource_type="cohort",
            resource_id=cohort.id,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
            metadata={
                "cohort_id": str(cohort.id),
                "old_status": current,
                "new_status": target,
                "transition_trigger": trigger,
            },
        )

    def transition(
        self,
        cohort_id: UUID,
        *,
        target: CohortStatus,
        trigger: TransitionTrigger,
        actor_user_id: UUID | None,
        occurred_at: datetime,
        request_id: str,
    ) -> Cohort:
        self._require_aware(occurred_at)
        if (trigger == "manual") != (actor_user_id is not None):
            raise ValueError("manual transitions require an actor; automatic transitions forbid one")
        with write_transaction(self.db):
            cohort = self._locked_cohort(cohort_id)
            self._transition_locked(
                cohort,
                target=target,
                trigger=trigger,
                actor_user_id=actor_user_id,
                occurred_at=occurred_at,
                request_id=request_id,
            )
            self.db.flush()
        return cohort

    def _reconcile_locked(
        self,
        cohort: Cohort,
        *,
        now: datetime,
        trigger: TransitionTrigger,
        request_id: str,
    ) -> bool:
        target = due_transition(
            status=cast(CohortStatus, cohort.status),
            starts_at=cohort.starts_at,
            ends_at=cohort.ends_at,
            auto_start_enabled=cohort.auto_start_enabled,
            auto_complete_enabled=cohort.auto_complete_enabled,
            now=now,
        )
        if target is None:
            return False
        self._transition_locked(
            cohort,
            target=target,
            trigger=trigger,
            actor_user_id=None,
            occurred_at=now,
            request_id=request_id,
        )
        return True

    def reconcile_due_cohort(
        self,
        cohort_id: UUID,
        *,
        now: datetime,
        trigger: TransitionTrigger,
        request_id: str,
    ) -> bool:
        self._require_aware(now)
        if trigger == "manual":
            raise ValueError("reconciliation trigger must be automatic")
        with write_transaction(self.db):
            cohort = self._locked_cohort(cohort_id)
            changed = self._reconcile_locked(
                cohort,
                now=now,
                trigger=trigger,
                request_id=request_id,
            )
            self.db.flush()
        return changed

    def reconcile_due_batch_for_request(
        self,
        *,
        now: datetime,
        request_id: str,
        limit: int = 50,
        visibility: TeachingVisibility | None = None,
    ) -> int:
        self._require_aware(now)
        if limit < 1:
            raise ValueError("limit must be positive")
        with write_transaction(self.db):
            due_at = case(
                (Cohort.status == "active", Cohort.ends_at),
                else_=Cohort.starts_at,
            )
            candidate_query = select(Cohort).where(
                or_(
                    and_(
                        Cohort.auto_start_enabled.is_(True),
                        Cohort.status.in_(("open", "upcoming")),
                        Cohort.starts_at <= now,
                    ),
                    and_(
                        Cohort.auto_complete_enabled.is_(True),
                        Cohort.status == "active",
                        Cohort.ends_at <= now,
                    ),
                )
            )
            if visibility is not None:
                candidate_query = candidate_query.where(
                    self._visibility_predicate(visibility)
                )
            candidates = list(
                self.db.scalars(
                    candidate_query.order_by(due_at, Cohort.status, Cohort.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            changed = sum(
                self._reconcile_locked(
                    cohort,
                    now=now,
                    trigger="request_reconciliation",
                    request_id=request_id,
                )
                for cohort in candidates
            )
            self.db.flush()
        return changed

    def list_status_events(
        self,
        cohort_id: UUID,
        *,
        visibility: TeachingVisibility,
        page: int,
        page_size: int,
    ) -> tuple[list[CohortStatusEvent], int]:
        self._validate_pagination(page, page_size)
        self.get_cohort(cohort_id, visibility=visibility)
        query = select(CohortStatusEvent).where(
            CohortStatusEvent.cohort_id == cohort_id
        )
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        events = list(
            self.db.scalars(
                query.order_by(
                    CohortStatusEvent.occurred_at.desc(),
                    CohortStatusEvent.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return events, total

    @staticmethod
    def _translate_class_integrity_error(exc: IntegrityError) -> TeachingServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_teaching_classes_cohort_name":
            return DuplicateClass("class name already exists in cohort")
        if constraint == "ck_teaching_classes_type_team":
            return TeachingServiceError("class type and team are inconsistent")
        if constraint == "fk_teaching_classes_team_project":
            return ProjectTeamMismatch("professional team belongs to another project")
        return TeachingServiceError("teaching class transaction failed")

    def _validate_class_team(
        self,
        *,
        class_type: str,
        project_id: UUID,
        professional_team_id: UUID | None,
    ) -> None:
        if class_type == "standard":
            if professional_team_id is not None:
                raise StandardClassTeamForbidden("standard class cannot have a team")
            return
        if professional_team_id is None:
            raise EliteTeamRequired("elite class requires a professional team")
        team = self.db.scalar(
            select(ProfessionalTeam)
            .where(ProfessionalTeam.id == professional_team_id)
            .with_for_update()
        )
        if team is None:
            raise ProfessionalTeamNotFound("professional team not found")
        if not team.is_active:
            raise InactiveProfessionalTeam("professional team is inactive")
        if team.project_id != project_id:
            raise ProjectTeamMismatch("professional team belongs to another project")

    def create_class(
        self,
        input: TeachingClassCreate,
        *,
        actor_user_id: UUID,
    ) -> TeachingClass:
        try:
            with write_transaction(self.db):
                cohort = self._locked_cohort(input.cohort_id)
                if cohort.status == "archived":
                    raise CohortArchived("archived cohort is read-only")
                project = self.db.scalar(
                    select(GameProject)
                    .where(GameProject.id == input.project_id)
                    .with_for_update()
                )
                if project is None:
                    raise ProjectNotFound("project not found")
                if not project.is_active:
                    raise InactiveProject("project is inactive")
                self._validate_class_team(
                    class_type=input.class_type,
                    project_id=input.project_id,
                    professional_team_id=input.professional_team_id,
                )
                teaching_class = TeachingClass(
                    cohort_id=cohort.id,
                    project_id=project.id,
                    name=input.name,
                    class_type=input.class_type,
                    professional_team_id=input.professional_team_id,
                    capacity=input.capacity,
                    default_tuition_cents=input.default_tuition_cents,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(teaching_class)
                self.db.flush()
            return teaching_class
        except IntegrityError as exc:
            raise self._translate_class_integrity_error(exc) from exc

    def update_class(
        self,
        class_id: UUID,
        input: TeachingClassUpdate,
        *,
        actor_user_id: UUID,
    ) -> TeachingClass:
        try:
            with write_transaction(self.db):
                cohort_id = self.db.scalar(
                    select(TeachingClass.cohort_id).where(TeachingClass.id == class_id)
                )
                if cohort_id is None:
                    raise ClassNotFound("class not found")
                cohort = self._locked_cohort(cohort_id)
                teaching_class = self.db.scalar(
                    select(TeachingClass)
                    .where(
                        TeachingClass.id == class_id,
                        TeachingClass.cohort_id == cohort.id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if teaching_class is None:
                    raise ClassNotFound("class not found")
                if cohort.status == "archived":
                    raise CohortArchived("archived cohort is read-only")
                if input.is_active is False and teaching_class.is_active:
                    active_enrollments = list(
                        self.db.scalars(
                            select(Enrollment.id)
                            .where(
                                Enrollment.class_id == teaching_class.id,
                                Enrollment.status == "active",
                            )
                            .order_by(Enrollment.id)
                            .with_for_update()
                        )
                    )
                    if active_enrollments:
                        raise ClassHasActiveEnrollments(
                            "class has active enrollments"
                        )
                class_type = input.class_type or teaching_class.class_type
                professional_team_id = (
                    input.professional_team_id
                    if "professional_team_id" in input.model_fields_set
                    else teaching_class.professional_team_id
                )
                self._validate_class_team(
                    class_type=class_type,
                    project_id=teaching_class.project_id,
                    professional_team_id=professional_team_id,
                )
                if input.name is not None:
                    teaching_class.name = input.name
                if input.class_type is not None:
                    teaching_class.class_type = input.class_type
                if "professional_team_id" in input.model_fields_set:
                    teaching_class.professional_team_id = input.professional_team_id
                if input.capacity is not None:
                    teaching_class.capacity = input.capacity
                if input.default_tuition_cents is not None:
                    teaching_class.default_tuition_cents = input.default_tuition_cents
                if input.is_active is not None:
                    teaching_class.is_active = input.is_active
                teaching_class.updated_by_user_id = actor_user_id
                self.db.flush()
            return teaching_class
        except IntegrityError as exc:
            raise self._translate_class_integrity_error(exc) from exc

    @staticmethod
    def _class_visibility_predicate(
        visibility: TeachingVisibility,
    ) -> ColumnElement[bool]:
        if visibility.global_access:
            if visibility.excluded_class_ids:
                return TeachingClass.id.not_in(visibility.excluded_class_ids)
            return true()
        if not visibility.class_ids:
            return false()
        return TeachingClass.id.in_(visibility.class_ids)

    def get_class(
        self,
        class_id: UUID,
        *,
        visibility: TeachingVisibility,
    ) -> TeachingClass:
        teaching_class = self.db.scalar(
            select(TeachingClass).where(
                TeachingClass.id == class_id,
                self._class_visibility_predicate(visibility),
            )
        )
        if teaching_class is None:
            raise ClassNotFound("class not found")
        return teaching_class

    def list_classes(
        self,
        *,
        cohort_id: UUID | None,
        project_id: UUID | None,
        name: str | None,
        is_active: bool | None,
        visibility: TeachingVisibility,
        page: int,
        page_size: int,
    ) -> tuple[list[TeachingClass], int]:
        self._validate_pagination(page, page_size)
        query = select(TeachingClass).where(
            self._class_visibility_predicate(visibility)
        )
        if cohort_id is not None:
            query = query.where(TeachingClass.cohort_id == cohort_id)
        if project_id is not None:
            query = query.where(TeachingClass.project_id == project_id)
        if name is not None:
            query = query.where(TeachingClass.name == name.strip())
        if is_active is not None:
            query = query.where(TeachingClass.is_active.is_(is_active))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        classes = list(
            self.db.scalars(
                query.order_by(
                    TeachingClass.is_active.desc(),
                    TeachingClass.name,
                    TeachingClass.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return classes, total
