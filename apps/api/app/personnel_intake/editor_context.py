"""Authorized metadata for correction editors; never loads or decrypts facts."""

from typing import cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.identity.models import Person
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    CoachPersonResponsibility,
    PersonnelIntakeRequest,
)
from app.personnel_intake.schemas import (
    AssistedEditorAction,
    AssistedEditorContext,
    AssistedOption,
    PersonKind,
)
from app.teaching.coach_eligibility import CoachEligibility


class AssistedEditorContextQuery:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, actor: AuthContext, person_id: UUID) -> AssistedEditorContext:
        # Read current account/roles/policy, not caller-carried role labels or a
        # historical intake review/submit grant. Fail before target metadata.
        state = PersonnelIntakeAccess(self.db).snapshot(actor)
        if not state.roles & {"admin", "super_admin"} or not state.allowed(
            "people.update", target_person_id=person_id
        ):
            raise PersonnelIntakeError(
                "personnel_intake_forbidden", status=403, detail="无权办理此人员资料"
            )

        row = self.db.execute(
            select(
                AssistedPersonProfile.version,
                PersonnelIntakeRequest.kind,
                PersonnelIntakeRequest.target_coach_person_ids,
                PersonnelIntakeRequest.target_team_id,
            )
            .select_from(AssistedPersonProfile)
            .join(Person, Person.id == AssistedPersonProfile.person_id)
            .join(
                AssistedApproval,
                and_(
                    AssistedApproval.id == AssistedPersonProfile.current_approval_id,
                    AssistedApproval.person_id == Person.id,
                ),
            )
            .join(
                PersonnelIntakeRequest,
                and_(
                    PersonnelIntakeRequest.id == AssistedApproval.request_id,
                    PersonnelIntakeRequest.person_id == Person.id,
                    PersonnelIntakeRequest.state == "approved",
                    PersonnelIntakeRequest.version == AssistedApproval.revision_version,
                ),
            )
            .where(Person.id == person_id, Person.deleted_at.is_(None))
        ).one_or_none()
        if row is None or row.kind not in {"student", "player", "both"}:
            raise PersonnelIntakeError(
                "personnel_intake_not_found", status=404, detail="未找到可维护的人员资料"
            )

        # Correction preserves current lineage affiliations, whereas coach
        # replacement's writer checks only the target. Do not advertise a
        # correction that an explicit affiliation deny would reject.
        actions: list[AssistedEditorAction] = []
        if state.allowed(
            "people.update",
            target_person_id=person_id,
            coaches=row.target_coach_person_ids,
            team_id=row.target_team_id,
        ):
            actions.append("correct_profile")
        actions.append("replace_coaches")
        current_coach_ids = list(
            self.db.scalars(
                select(CoachPersonResponsibility.coach_person_id)
                .where(
                    CoachPersonResponsibility.person_id == person_id,
                    CoachPersonResponsibility.ended_at.is_(None),
                )
                .order_by(CoachPersonResponsibility.coach_person_id)
            )
        )
        coaches = self.db.execute(
            select(Person.id, Person.full_name)
            .where(Person.id != person_id, CoachEligibility.eligible_person_predicate(Person.id))
            .order_by(Person.full_name, Person.id)
        )
        return AssistedEditorContext(
            person_id=person_id,
            kind=cast(PersonKind, row.kind),
            profile_version=row.version,
            current_coach_person_ids=current_coach_ids,
            eligible_coaches=[AssistedOption(id=pid, label=name) for pid, name in coaches],
            allowed_actions=actions,
        )
