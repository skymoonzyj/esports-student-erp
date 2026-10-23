from __future__ import annotations

from datetime import date, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.admissions.schedule import effective_schedule
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.readiness import AgeStatus, is_minor
from app.portal.errors import FamilyStudentNotFound, PortalPermissionDenied
from app.portal.schemas import (
    ClassType,
    EnrollmentStatus,
    LearningEnrollmentSummary,
    LearningStudentsResponse,
    LearningStudentSummary,
    LearningTeachingResponse,
    PortalAccessSource,
)
from app.rbac.service import RbacDecisionContext, RbacService
from app.teaching.models import Cohort, GameProject, ProfessionalTeam, TeachingClass

EnrollmentRow = tuple[
    Enrollment,
    Cohort,
    TeachingClass,
    GameProject,
    ProfessionalTeam | None,
]


class PortalLearningService:
    _PERMISSIONS = ("people.read", "teaching.read", "enrollments.read")

    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def _capability_sources(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        decisions: RbacDecisionContext,
    ) -> tuple[bool, bool]:
        family = self.rbac.account_has_role(user_id, "guardian") and all(
            decisions.decide(permission, {"global", "family"}).allowed
            for permission in self._PERMISSIONS
        )
        self_access = (
            self.db.get(StudentProfile, actor_person_id) is not None
            and all(
                decisions.decide(
                    permission,
                    {"global", f"self:{actor_person_id}"},
                ).allowed
                for permission in self._PERMISSIONS
            )
        )
        if not family and not self_access:
            raise PortalPermissionDenied("learning portal capability is required")
        return family, self_access

    def _student_allowed(
        self,
        *,
        decisions: RbacDecisionContext,
        resource_tokens: set[str],
    ) -> bool:
        return all(
            decisions.decide(permission, resource_tokens).allowed
            for permission in self._PERMISSIONS
        )

    @staticmethod
    def _enrollment_summary(row: EnrollmentRow) -> LearningEnrollmentSummary:
        enrollment, cohort, teaching_class, project, team = row
        schedule = effective_schedule(enrollment, cohort)
        return LearningEnrollmentSummary(
            enrollment_id=enrollment.id,
            cohort_id=cohort.id,
            cohort_name=cohort.name,
            starts_at=schedule.starts_at,
            ends_at=schedule.ends_at,
            project_name=project.name,
            class_id=teaching_class.id,
            class_name=teaching_class.name,
            class_type=cast(ClassType, teaching_class.class_type),
            professional_team_name=team.name if team is not None else None,
            status=cast(EnrollmentStatus, enrollment.status),
            confirmed_at=enrollment.confirmed_at,
            cancelled_at=enrollment.cancelled_at,
            completed_at=enrollment.completed_at,
        )

    def _candidate_sources(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        reference_date: date,
        decisions: RbacDecisionContext,
    ) -> dict[UUID, PortalAccessSource]:
        family, self_access = self._capability_sources(
            user_id=user_id,
            actor_person_id=actor_person_id,
            decisions=decisions,
        )
        sources: dict[UUID, PortalAccessSource] = {}
        if family:
            for student_id in self.db.scalars(
                select(GuardianRelationship.student_person_id).where(
                    GuardianRelationship.guardian_person_id == actor_person_id,
                    GuardianRelationship.requires_verification.is_(False),
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.valid_from <= reference_date,
                    or_(
                        GuardianRelationship.valid_until.is_(None),
                        GuardianRelationship.valid_until >= reference_date,
                    ),
                )
            ):
                sources[student_id] = "family"
        if self_access:
            sources[actor_person_id] = "self"
        return sources

    def _current_rows(self, student_ids: list[UUID]) -> dict[UUID, EnrollmentRow]:
        if not student_ids:
            return {}
        priority = case(
            (Cohort.status == "active", 0),
            (Cohort.status.in_(("open", "upcoming")), 1),
            else_=2,
        )
        rows = self.db.execute(
            select(
                Enrollment,
                Cohort,
                TeachingClass,
                GameProject,
                ProfessionalTeam,
            )
            .join(Cohort, Cohort.id == Enrollment.cohort_id)
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .join(GameProject, GameProject.id == TeachingClass.project_id)
            .outerjoin(
                ProfessionalTeam,
                ProfessionalTeam.id == TeachingClass.professional_team_id,
            )
            .where(
                Enrollment.student_person_id.in_(student_ids),
                Enrollment.status == "active",
                Cohort.status.in_(("active", "open", "upcoming")),
            )
            .order_by(
                Enrollment.student_person_id,
                priority,
                Cohort.starts_at,
                Enrollment.id,
            )
        ).tuples()
        current: dict[UUID, EnrollmentRow] = {}
        for row in rows:
            current.setdefault(row[0].student_person_id, row)
        return current

    def _authorized_students(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        reference_date: date,
    ) -> list[LearningStudentSummary]:
        decisions = self.rbac.decision_context(user_id)
        sources = self._candidate_sources(
            user_id=user_id,
            actor_person_id=actor_person_id,
            reference_date=reference_date,
            decisions=decisions,
        )
        if not sources:
            return []
        people = {
            person.id: (person, student)
            for person, student in self.db.execute(
                select(Person, StudentProfile)
                .join(StudentProfile, StudentProfile.person_id == Person.id)
                .where(
                    Person.id.in_(list(sources)),
                    Person.deleted_at.is_(None),
                )
            ).tuples()
        }
        tokens_by_student = self.rbac.person_resource_tokens_many(
            actor_person_id=actor_person_id,
            target_person_ids=set(sources),
            reference_date=reference_date,
        )
        allowed_ids = [
            student_id
            for student_id in sources
            if student_id in people
            and self._student_allowed(
                decisions=decisions,
                resource_tokens=tokens_by_student[student_id],
            )
        ]
        current_rows = self._current_rows(allowed_ids)
        items: list[tuple[int, datetime | None, str, int, LearningStudentSummary]] = []
        for student_id in allowed_ids:
            person, student = people[student_id]
            current_row = current_rows.get(student_id)
            current_summary = (
                self._enrollment_summary(current_row)
                if current_row is not None
                else None
            )
            current_priority = 2
            current_start: datetime | None = None
            if current_row is not None:
                current_priority = 0 if current_row[1].status == "active" else 1
                current_start = current_row[1].starts_at
            age_status: AgeStatus = (
                "minor"
                if is_minor(person.date_of_birth, reference_date)
                else "adult"
            )
            items.append(
                (
                    current_priority,
                    current_start,
                    person.full_name,
                    person.id.int,
                    LearningStudentSummary(
                        student_person_id=person.id,
                        student_name=person.full_name,
                        student_number=student.student_number,
                        age_status=age_status,
                        access_source=sources[student_id],
                        current_enrollment=current_summary,
                    ),
                )
            )
        items.sort(
            key=lambda item: (
                item[0],
                item[1] or datetime.max.replace(tzinfo=None),
                item[2],
                item[3],
            )
        )
        return [item[4] for item in items]

    def list_students(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        reference_date: date,
    ) -> LearningStudentsResponse:
        items = self._authorized_students(
            user_id=user_id,
            actor_person_id=actor_person_id,
            reference_date=reference_date,
        )
        return LearningStudentsResponse(items=items, total=len(items))

    def get_teaching(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        student_person_id: UUID,
        reference_date: date,
        page: int,
        page_size: int,
    ) -> LearningTeachingResponse:
        students = self._authorized_students(
            user_id=user_id,
            actor_person_id=actor_person_id,
            reference_date=reference_date,
        )
        student = next(
            (
                candidate
                for candidate in students
                if candidate.student_person_id == student_person_id
            ),
            None,
        )
        if student is None:
            raise FamilyStudentNotFound("learning student is not authorized")

        priority = case(
            (
                and_(Enrollment.status == "active", Cohort.status == "active"),
                0,
            ),
            (
                and_(
                    Enrollment.status == "active",
                    Cohort.status.in_(("open", "upcoming")),
                ),
                1,
            ),
            else_=2,
        )
        total = self.db.scalar(
            select(func.count(Enrollment.id)).where(
                Enrollment.student_person_id == student_person_id
            )
        ) or 0
        rows = list(
            self.db.execute(
                select(
                    Enrollment,
                    Cohort,
                    TeachingClass,
                    GameProject,
                    ProfessionalTeam,
                )
                .join(Cohort, Cohort.id == Enrollment.cohort_id)
                .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
                .join(GameProject, GameProject.id == TeachingClass.project_id)
                .outerjoin(
                    ProfessionalTeam,
                    ProfessionalTeam.id == TeachingClass.professional_team_id,
                )
                .where(Enrollment.student_person_id == student_person_id)
                .order_by(
                    priority,
                    case(
                        (priority.in_((0, 1)), Cohort.starts_at),
                        else_=None,
                    ).asc(),
                    Cohort.starts_at.desc(),
                    Enrollment.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        return LearningTeachingResponse(
            student=student,
            items=[self._enrollment_summary(row) for row in rows],
            page=page,
            page_size=page_size,
            total=total,
        )
