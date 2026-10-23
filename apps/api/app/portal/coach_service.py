from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.admissions.models import Enrollment
from app.core.time import business_date_at
from app.identity.models import Person, StudentProfile
from app.identity.readiness import age_status_for
from app.portal.errors import CoachCohortNotFound, PortalPermissionDenied
from app.portal.schemas import (
    AssignmentType,
    ClassType,
    CoachClassSummary,
    CoachNextCohortsResponse,
    CoachRosterStudent,
    CoachStartOption,
)
from app.rbac.service import RbacDecisionContext, RbacService
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.models import (
    CoachAssignment,
    Cohort,
    GameProject,
    ProfessionalTeam,
    TeachingClass,
)

AssignmentRow = tuple[
    CoachAssignment,
    TeachingClass,
    Cohort,
    GameProject,
    ProfessionalTeam | None,
]


class PortalCoachService:
    _PERMISSIONS = ("teaching.read", "coach_roster.read", "enrollments.read")

    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def _require_capability(
        self,
        *,
        user_id: UUID,
        decisions: RbacDecisionContext,
    ) -> None:
        allowed = self.rbac.account_has_role(user_id, "coach") and all(
            decisions.decide(permission, {"global", "assigned_class"}).allowed
            for permission in self._PERMISSIONS
        )
        if not allowed:
            raise PortalPermissionDenied("coach portal capability is required")

    def _authorized_assignments(
        self,
        *,
        actor_person_id: UUID,
        now: datetime,
        decisions: RbacDecisionContext,
    ) -> list[AssignmentRow]:
        rows = list(
            self.db.execute(
                select(
                    CoachAssignment,
                    TeachingClass,
                    Cohort,
                    GameProject,
                    ProfessionalTeam,
                )
                .join(TeachingClass, TeachingClass.id == CoachAssignment.class_id)
                .join(Cohort, Cohort.id == TeachingClass.cohort_id)
                .join(GameProject, GameProject.id == TeachingClass.project_id)
                .outerjoin(
                    ProfessionalTeam,
                    ProfessionalTeam.id == TeachingClass.professional_team_id,
                )
                .where(
                    CoachAssignment.coach_person_id == actor_person_id,
                    CoachAssignment.valid_from <= now,
                    or_(
                        CoachAssignment.valid_until.is_(None),
                        CoachAssignment.valid_until > now,
                    ),
                    or_(
                        CoachAssignment.ended_at.is_(None),
                        CoachAssignment.ended_at > now,
                    ),
                    Cohort.status.in_(("open", "upcoming")),
                    Cohort.starts_at > now,
                    TeachingClass.is_active.is_(True),
                    CoachEligibility.eligible_person_predicate(
                        CoachAssignment.coach_person_id
                    ),
                )
                .order_by(
                    Cohort.starts_at,
                    Cohort.id,
                    GameProject.sort_order,
                    TeachingClass.name,
                    TeachingClass.id,
                )
            ).tuples()
        )
        tokens_by_class = self.rbac.class_resource_tokens_many(
            actor_person_id=actor_person_id,
            class_ids={row[1].id for row in rows},
            at=now,
        )
        authorized: dict[UUID, AssignmentRow] = {}
        for row in rows:
            assignment, teaching_class, _, _, _ = row
            tokens = tokens_by_class[teaching_class.id]
            if all(
                decisions.decide(permission, tokens).allowed
                for permission in self._PERMISSIONS
            ):
                authorized[assignment.class_id] = row
        return list(authorized.values())

    @staticmethod
    def _option_for_group(
        starts_at: datetime,
        rows: list[AssignmentRow],
    ) -> CoachStartOption:
        cohorts = {row[2].id: row[2] for row in rows}
        anchor = min(cohorts, key=lambda value: value.int)
        ordered_cohorts = sorted(
            cohorts.values(),
            key=lambda cohort: (cohort.name, cohort.id.int),
        )
        return CoachStartOption(
            anchor_cohort_id=anchor,
            starts_at=starts_at,
            cohort_names=[cohort.name for cohort in ordered_cohorts],
            class_count=len(rows),
        )

    def list_next(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        now: datetime,
        anchor_cohort_id: UUID | None,
        page: int,
        page_size: int,
    ) -> CoachNextCohortsResponse:
        decisions = self.rbac.decision_context(user_id)
        self._require_capability(user_id=user_id, decisions=decisions)
        rows = self._authorized_assignments(
            actor_person_id=actor_person_id,
            now=now,
            decisions=decisions,
        )
        if not rows:
            if anchor_cohort_id is not None:
                raise CoachCohortNotFound("coach cohort is not authorized")
            return CoachNextCohortsResponse(
                observed_at=now,
                selected_starts_at=None,
                options=[],
                classes=[],
                items=[],
                page=page,
                page_size=page_size,
                total=0,
            )

        groups: dict[datetime, list[AssignmentRow]] = defaultdict(list)
        for row in rows:
            groups[row[2].starts_at].append(row)
        if anchor_cohort_id is None:
            selected_starts_at = min(groups)
        else:
            selected_rows = [row for row in rows if row[2].id == anchor_cohort_id]
            if not selected_rows:
                raise CoachCohortNotFound("coach cohort is not authorized")
            selected_starts_at = selected_rows[0][2].starts_at

        options = [
            self._option_for_group(starts_at, groups[starts_at])
            for starts_at in sorted(groups)
        ]
        selected = sorted(
            groups[selected_starts_at],
            key=lambda row: (
                row[2].starts_at,
                row[2].id.int,
                row[3].sort_order,
                row[1].name,
                row[1].id.int,
            ),
        )
        selected_class_ids = [row[1].id for row in selected]
        student_counts = {
            class_id: count
            for class_id, count in self.db.execute(
                select(Enrollment.class_id, func.count(Enrollment.id))
                .where(
                    Enrollment.class_id.in_(selected_class_ids),
                    Enrollment.status == "active",
                )
                .group_by(Enrollment.class_id)
            ).tuples()
        }
        classes = [
            CoachClassSummary(
                cohort_id=cohort.id,
                cohort_name=cohort.name,
                starts_at=cohort.starts_at,
                ends_at=cohort.ends_at,
                class_id=teaching_class.id,
                class_name=teaching_class.name,
                project_name=project.name,
                class_type=cast(ClassType, teaching_class.class_type),
                professional_team_name=team.name if team is not None else None,
                assignment_type=cast(AssignmentType, assignment.assignment_type),
                student_count=student_counts.get(teaching_class.id, 0),
            )
            for assignment, teaching_class, cohort, project, team in selected
        ]

        total = self.db.scalar(
            select(func.count(Enrollment.id)).where(
                Enrollment.class_id.in_(selected_class_ids),
                Enrollment.status == "active",
            )
        ) or 0
        roster_rows = list(
            self.db.execute(
                select(
                    Enrollment,
                    Person,
                    StudentProfile,
                    Cohort,
                    TeachingClass,
                    GameProject,
                )
                .join(Person, Person.id == Enrollment.student_person_id)
                .join(StudentProfile, StudentProfile.person_id == Person.id)
                .join(Cohort, Cohort.id == Enrollment.cohort_id)
                .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
                .join(GameProject, GameProject.id == TeachingClass.project_id)
                .where(
                    Enrollment.class_id.in_(selected_class_ids),
                    Enrollment.status == "active",
                )
                .order_by(
                    Cohort.starts_at,
                    Cohort.id,
                    GameProject.sort_order,
                    TeachingClass.name,
                    TeachingClass.id,
                    Person.full_name,
                    Person.id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        selected_by_class = {row[1].id: row for row in selected}
        reference_date = business_date_at(now)
        items: list[CoachRosterStudent] = []
        for (
            enrollment,
            person,
            student,
            cohort,
            teaching_class,
            project,
        ) in roster_rows:
            assignment, _, _, _, team = selected_by_class[teaching_class.id]
            age_status = age_status_for(person.date_of_birth, reference_date)
            items.append(
                CoachRosterStudent(
                    enrollment_id=enrollment.id,
                    student_person_id=person.id,
                    student_name=person.full_name,
                    student_number=student.student_number,
                    age_status=age_status,
                    cohort_id=cohort.id,
                    cohort_name=cohort.name,
                    project_name=project.name,
                    class_id=teaching_class.id,
                    class_name=teaching_class.name,
                    class_type=cast(ClassType, teaching_class.class_type),
                    professional_team_name=team.name if team is not None else None,
                    assignment_type=cast(AssignmentType, assignment.assignment_type),
                    enrollment_status="active",
                )
            )

        return CoachNextCohortsResponse(
            observed_at=now,
            selected_starts_at=selected_starts_at,
            options=options,
            classes=classes,
            items=items,
            page=page,
            page_size=page_size,
            total=total,
        )
