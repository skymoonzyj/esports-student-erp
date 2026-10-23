from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity.models import Person
from app.teaching.coach_eligibility import CoachEligibility
from app.teaching.errors import (
    CoachNotEligible,
    ProjectCoachConfigurationInvalid,
    ProjectNotFound,
)
from app.teaching.models import GameProject, ProjectCoachAssignment


class ProjectCoachService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.eligibility = CoachEligibility(db)

    def _project(self, project_id: UUID, *, lock: bool = False) -> GameProject:
        statement = select(GameProject).where(GameProject.id == project_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        project = self.db.scalar(statement)
        if project is None:
            raise ProjectNotFound("project not found")
        return project

    def configuration(
        self,
        project_id: UUID,
    ) -> tuple[GameProject, list[tuple[Person, bool]]]:
        project = self._project(project_id)
        assigned_ids = set(
            self.db.scalars(
                select(ProjectCoachAssignment.coach_person_id).where(
                    ProjectCoachAssignment.project_id == project_id,
                    ProjectCoachAssignment.is_active.is_(True),
                )
            )
        )
        coaches = list(
            self.db.scalars(
                select(Person)
                .where(
                    Person.deleted_at.is_(None),
                    self.eligibility.eligible_person_predicate(Person.id),
                )
                .order_by(Person.full_name, Person.id)
            )
        )
        return project, [(coach, coach.id in assigned_ids) for coach in coaches]

    def replace_assignments(
        self,
        project_id: UUID,
        coach_person_ids: list[UUID],
        *,
        actor_user_id: UUID,
    ) -> tuple[GameProject, list[tuple[Person, bool]]]:
        if len(coach_person_ids) != len(set(coach_person_ids)):
            raise ProjectCoachConfigurationInvalid("duplicate coach person ids")
        self._project(project_id, lock=True)
        requested_ids = set(coach_person_ids)
        for coach_person_id in requested_ids:
            reason = self.eligibility.ineligibility_reason(coach_person_id)
            if reason is not None:
                raise CoachNotEligible(reason)

        existing = list(
            self.db.scalars(
                select(ProjectCoachAssignment)
                .where(ProjectCoachAssignment.project_id == project_id)
                .with_for_update()
            )
        )
        existing_by_coach = {assignment.coach_person_id: assignment for assignment in existing}
        for assignment in existing:
            should_be_active = assignment.coach_person_id in requested_ids
            if assignment.is_active != should_be_active:
                assignment.is_active = should_be_active
                assignment.updated_by_user_id = actor_user_id
        for coach_person_id in requested_ids - set(existing_by_coach):
            self.db.add(
                ProjectCoachAssignment(
                    project_id=project_id,
                    coach_person_id=coach_person_id,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                    updated_by_user_id=actor_user_id,
                )
            )
        self.db.flush()
        return self.configuration(project_id)
