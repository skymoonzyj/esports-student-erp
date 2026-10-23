from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.evaluations.staff_models import CohortStaffAssignment
from app.identity.models import Person
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.errors import (
    ClassNotFound,
    CoachAssignmentAlreadyEnded,
    CoachAssignmentConflict,
    CoachAssignmentNotFound,
    CoachNotEligible,
    CohortArchived,
    PrimaryCoachConflict,
    TeachingServiceError,
)
from app.teaching.models import CoachAssignment, Cohort, TeachingClass
from app.teaching.schemas import CoachAssignmentCreate, CoachAssignmentEndRequest


class CoachService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.eligibility = CoachEligibility(db)

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("assignment datetimes must include an offset")

    @staticmethod
    def _validate_pagination(page: int, page_size: int) -> None:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

    @staticmethod
    def _translate_integrity_error(exc: IntegrityError) -> TeachingServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "excl_coach_assignments_one_primary":
            return PrimaryCoachConflict("primary assignment overlaps")
        if constraint == "excl_coach_assignments_no_duplicate_coach":
            return CoachAssignmentConflict("coach assignment overlaps")
        return TeachingServiceError("coach assignment transaction failed")

    def _locked_class_and_cohort(
        self,
        class_id: UUID,
    ) -> tuple[TeachingClass, Cohort]:
        cohort_id = self.db.scalar(
            select(TeachingClass.cohort_id).where(TeachingClass.id == class_id)
        )
        if cohort_id is None:
            raise ClassNotFound("class not found")
        cohort = self.db.scalar(
            select(Cohort)
            .where(Cohort.id == cohort_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if cohort is None:  # pragma: no cover - protected by the foreign key
            raise ClassNotFound("class parent cohort not found")
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
        return teaching_class, cohort

    def _has_generic_teaching_overlap(
        self,
        *,
        coach_person_id: UUID,
        class_id: UUID,
        valid_from: datetime,
        valid_until: datetime | None,
    ) -> bool:
        requested_end = valid_until or datetime.max.replace(tzinfo=valid_from.tzinfo)
        return (
            self.db.scalar(
                select(CohortStaffAssignment.id)
                .where(
                    CohortStaffAssignment.staff_person_id == coach_person_id,
                    CohortStaffAssignment.class_id == class_id,
                    CohortStaffAssignment.role_category_snapshot == "teaching",
                    CohortStaffAssignment.valid_from < requested_end,
                    or_(
                        CohortStaffAssignment.valid_until.is_(None),
                        CohortStaffAssignment.valid_until > valid_from,
                    ),
                    or_(
                        CohortStaffAssignment.ended_at.is_(None),
                        CohortStaffAssignment.ended_at > valid_from,
                    ),
                )
                .limit(1)
            )
            is not None
        )

    def list_assignments(
        self,
        class_id: UUID,
        *,
        include_ended: bool,
        page: int,
        page_size: int,
    ) -> tuple[list[CoachAssignment], int]:
        self._validate_pagination(page, page_size)
        if self.db.get(TeachingClass, class_id) is None:
            raise ClassNotFound("class not found")
        query = select(CoachAssignment).where(CoachAssignment.class_id == class_id)
        if not include_ended:
            query = query.where(CoachAssignment.ended_at.is_(None))
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        assignments = list(
            self.db.scalars(
                query.order_by(
                    CoachAssignment.valid_from.desc(),
                    CoachAssignment.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return assignments, total

    def create_assignment(
        self,
        class_id: UUID,
        input: CoachAssignmentCreate,
        *,
        actor_user_id: UUID,
    ) -> CoachAssignment:
        try:
            with write_transaction(self.db):
                _, cohort = self._locked_class_and_cohort(class_id)
                if cohort.status == "archived":
                    raise CohortArchived("archived cohort is read-only")
                reason = self.eligibility.ineligibility_reason(input.coach_person_id)
                if reason is not None:
                    raise CoachNotEligible(reason)
                if self._has_generic_teaching_overlap(
                    coach_person_id=input.coach_person_id,
                    class_id=class_id,
                    valid_from=input.valid_from,
                    valid_until=input.valid_until,
                ):
                    raise CoachAssignmentConflict("coach assignment overlaps")
                assignment = CoachAssignment(
                    class_id=class_id,
                    coach_person_id=input.coach_person_id,
                    assignment_type=input.assignment_type,
                    valid_from=input.valid_from,
                    valid_until=input.valid_until,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(assignment)
                self.db.flush()
            return assignment
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def end_assignment(
        self,
        class_id: UUID,
        assignment_id: UUID,
        input: CoachAssignmentEndRequest,
        *,
        actor_user_id: UUID,
        occurred_at: datetime,
    ) -> CoachAssignment:
        self._require_aware(occurred_at)
        with write_transaction(self.db):
            _, cohort = self._locked_class_and_cohort(class_id)
            if cohort.status == "archived":
                raise CohortArchived("archived cohort is read-only")
            assignment = self.db.scalar(
                select(CoachAssignment)
                .where(
                    CoachAssignment.id == assignment_id,
                    CoachAssignment.class_id == class_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if assignment is None:
                raise CoachAssignmentNotFound("coach assignment not found")
            if assignment.ended_at is not None:
                raise CoachAssignmentAlreadyEnded("coach assignment is already ended")
            if occurred_at <= assignment.valid_from:
                raise CoachAssignmentConflict("assignment end must follow its start")
            assignment.ended_at = occurred_at
            assignment.ended_by_user_id = actor_user_id
            assignment.end_reason = input.reason
            assignment.updated_by_user_id = actor_user_id
            self.db.flush()
        return assignment

    def eligible_coaches(
        self,
        class_id: UUID,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[Person], int]:
        self._validate_pagination(page, page_size)
        if self.db.get(TeachingClass, class_id) is None:
            raise ClassNotFound("class not found")
        statement = (
            select(Person)
            .where(
                Person.deleted_at.is_(None),
                self.eligibility.eligible_person_predicate(Person.id),
            )
        )
        if query is not None:
            statement = statement.where(Person.full_name.contains(query.strip(), autoescape=True))
        total = self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        people = list(
            self.db.scalars(
                statement.order_by(Person.full_name, Person.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return people, total
