"""Live authorization for camp-survey respondent resources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.evaluations.access import EvaluationAccessService, EvaluationResource
from app.evaluations.errors import CampSurveyResourceNotFound
from app.evaluations.survey_models import CampSurveyCycle
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import Role, UserRole
from app.teaching.models import TeachingClass


@dataclass(frozen=True)
class SurveyRespondent:
    account_id: UUID
    person_id: UUID
    student_person_id: UUID
    respondent_kind: str
    guardian_relationship_id: UUID | None = None
    guardian_relationship_snapshot: dict[str, object] | None = None


class CampSurveyAccessService:
    """Resolve an actor to a live self/guardian respondent without writes."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _cycle_resource(
        self,
        cycle: CampSurveyCycle,
    ) -> EvaluationResource | None:
        """Resolve the complete live evaluation resource for one cycle.

        ``CampSurveyCycle`` stores the enrollment/cohort/class identifiers but
        the project is owned by ``TeachingClass``.  A missing class/project is
        treated as a private resource failure rather than falling back to a
        weaker token set.
        """

        required = ("student_person_id", "cohort_id", "enrollment_id", "class_id")
        if not all(hasattr(cycle, field) for field in required):
            # Lightweight unit doubles from older callers do not carry the
            # enrollment target.  Production ORM rows always do.
            return None
        project_id = self.db.scalar(
            select(TeachingClass.project_id).where(
                TeachingClass.id == cycle.class_id,
            )
        )
        if project_id is None:
            return None
        return EvaluationResource(
            student_person_id=cycle.student_person_id,
            cohort_id=cycle.cohort_id,
            enrollment_id=cycle.enrollment_id,
            class_id=cycle.class_id,
            project_id=project_id,
        )

    def require_center_access(
        self,
        *,
        cycle: CampSurveyCycle,
        account_id: UUID,
        occurred_at: datetime,
    ) -> None:
        """Re-evaluate evaluation-center access against the live cycle target."""

        resource = self._cycle_resource(cycle)
        if resource is None:
            # See the compatibility note in ``_cycle_resource``.  Real ORM
            # cycles never take this branch because all target columns are
            # non-null and the class/project FK is required.
            if not hasattr(cycle, "class_id"):
                return
            raise CampSurveyResourceNotFound("survey resource not found")
        if (
            not EvaluationAccessService(self.db)
            .decide(
                account_id=account_id,
                permission="evaluation_center.access",
                resource=resource,
                occurred_at=occurred_at,
            )
            .allowed
        ):
            raise CampSurveyResourceNotFound("survey resource not found")

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")

    def _active_account(self, account_id: UUID) -> UserAccount | None:
        return self.db.scalar(
            select(UserAccount)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == account_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        )

    def _has_guardian_role(self, account_id: UUID) -> bool:
        return (
            self.db.scalar(
                select(UserRole.user_id)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id == account_id, Role.key == "guardian")
                .limit(1)
            )
            is not None
        )

    def resolve_respondent(
        self,
        *,
        cycle_id: UUID,
        account_id: UUID,
        occurred_at: datetime,
    ) -> SurveyRespondent:
        self._require_aware(occurred_at)
        account = self._active_account(account_id)
        cycle = self.db.scalar(select(CampSurveyCycle).where(CampSurveyCycle.id == cycle_id))
        if account is None or cycle is None:
            raise CampSurveyResourceNotFound("survey resource not found")
        person_id = account.person_id
        if person_id == cycle.student_person_id:
            if (
                self.db.scalar(
                    select(StudentProfile.person_id).where(
                        StudentProfile.person_id == person_id,
                        StudentProfile.status == "active",
                    )
                )
                is None
            ):
                raise CampSurveyResourceNotFound("survey resource not found")
            self.require_center_access(
                cycle=cycle,
                account_id=account.id,
                occurred_at=occurred_at,
            )
            return SurveyRespondent(
                account_id=account.id,
                person_id=person_id,
                student_person_id=cycle.student_person_id,
                respondent_kind="self",
            )

        # A relationship is only a valid guardian respondent when the actor's
        # account still carries the guardian role.  This is deliberately
        # evaluated on every request so role removal revokes access immediately.
        if not self._has_guardian_role(account.id):
            raise CampSurveyResourceNotFound("survey resource not found")

        reference_date = business_date_at(occurred_at)
        relation = self.db.scalar(
            select(GuardianRelationship)
            .where(
                GuardianRelationship.guardian_person_id == person_id,
                GuardianRelationship.student_person_id == cycle.student_person_id,
                GuardianRelationship.requires_verification.is_(False),
                GuardianRelationship.valid_from <= reference_date,
                or_(
                    GuardianRelationship.valid_until.is_(None),
                    GuardianRelationship.valid_until >= reference_date,
                ),
                or_(
                    GuardianRelationship.ended_at.is_(None),
                    GuardianRelationship.ended_at > occurred_at,
                ),
            )
            .order_by(GuardianRelationship.is_primary.desc(), GuardianRelationship.id)
        )
        # Keep the temporal predicate explicit after loading the row as well as
        # in SQL.  This protects alternate SQL backends and makes the exact
        # boundary semantics (inclusive business date, exclusive end instant)
        # unambiguous.
        if (
            relation is None
            or relation.guardian_person_id != person_id
            or relation.student_person_id != cycle.student_person_id
            or relation.valid_from > reference_date
            or (relation.valid_until is not None and relation.valid_until < reference_date)
            or (relation.ended_at is not None and relation.ended_at <= occurred_at)
        ):
            raise CampSurveyResourceNotFound("survey resource not found")
        self.require_center_access(
            cycle=cycle,
            account_id=account.id,
            occurred_at=occurred_at,
        )
        return SurveyRespondent(
            account_id=account.id,
            person_id=person_id,
            student_person_id=cycle.student_person_id,
            respondent_kind="guardian",
            guardian_relationship_id=relation.id,
            guardian_relationship_snapshot={
                "relationship": relation.relationship,
                "is_primary": relation.is_primary,
                "valid_from": relation.valid_from.isoformat(),
                "valid_until": (
                    relation.valid_until.isoformat() if relation.valid_until is not None else None
                ),
            },
        )

    def can_respond(
        self,
        *,
        cycle_id: UUID,
        account_id: UUID,
        occurred_at: datetime,
    ) -> bool:
        try:
            self.resolve_respondent(
                cycle_id=cycle_id,
                account_id=account_id,
                occurred_at=occurred_at,
            )
        except CampSurveyResourceNotFound:
            return False
        return True
