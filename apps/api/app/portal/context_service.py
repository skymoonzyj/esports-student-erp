from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.food_preferences.access import FoodPreferenceAccess
from app.food_preferences.errors import FoodPreferencePermissionDenied
from app.housing.access import HousingAccessService
from app.housing.models import HousingBuilding, HousingFloor
from app.housing.portal_service import HousingPortalService
from app.identity.models import Person, StaffProfile, StudentProfile, VisitorProfile
from app.identity.readiness import age_status_for
from app.portal.schemas import PortalContextResponse, PortalLearningMode
from app.rbac.service import RbacDecisionContext, RbacService
from app.referrals.access import InvitedHousingAccess


class PortalContextService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rbac = RbacService(db)

    def _allows_all(
        self,
        decisions: RbacDecisionContext,
        permissions: tuple[str, ...],
        resource_tokens: set[str],
    ) -> bool:
        return all(
            decisions.decide(permission, resource_tokens).allowed
            for permission in permissions
        )

    def _food_profile_capabilities(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        occurred_at: datetime,
    ) -> tuple[bool, bool]:
        access = FoodPreferenceAccess(
            self.db,
            account_id=user_id,
            occurred_at=occurred_at,
        )

        read_person: Person | None = None
        try:
            read_person = access.require_self("food_preferences.read")
        except FoodPreferencePermissionDenied:
            pass

        update_person: Person | None = None
        try:
            update_person = access.require_self("food_preferences.update")
        except FoodPreferencePermissionDenied:
            pass

        can_read_self = read_person is not None and read_person.id == actor_person_id
        can_update_self = (
            update_person is not None and update_person.id == actor_person_id
        )
        if not (can_read_self and can_update_self):
            return can_read_self, False

        account_projection = self.db.execute(
                select(
                    UserAccount.food_preferences_onboarding_completed_at,
                    UserAccount.self_service_primary_purpose,
                    exists().where(
                    StudentProfile.person_id == UserAccount.person_id
                ).label("has_student_profile"),
                exists().where(StaffProfile.person_id == UserAccount.person_id).label(
                    "has_staff_profile"
                ),
                exists().where(
                    VisitorProfile.person_id == UserAccount.person_id
                ).label("has_visitor_profile"),
            )
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == user_id,
                UserAccount.person_id == actor_person_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        ).one_or_none()
        if account_projection is None:
            return False, False

        completed_at = account_projection[0]
        self_service_purpose = account_projection[1]
        has_profile = any(account_projection[2:])
        requires_profile_onboarding = has_profile or self_service_purpose in {
            "staff_applicant",
            "student",
            "visitor",
        }
        return can_read_self, requires_profile_onboarding and completed_at is None

    def get_context(
        self,
        *,
        user_id: UUID,
        actor_person_id: UUID,
        occurred_at: datetime | None = None,
    ) -> PortalContextResponse:
        observed_at = occurred_at or datetime.now(UTC)
        housing_portal = HousingPortalService(self.db)
        housing_available = housing_portal.has_authorized_subjects(
            user_id=user_id,
            actor_person_id=actor_person_id,
            as_of=observed_at,
        )
        housing_access = HousingAccessService(self.db, user_id, observed_at)
        buildings = list(self.db.scalars(select(HousingBuilding)))
        floors = list(self.db.scalars(select(HousingFloor)))
        housing_management_available = bool(
            housing_access.filter_buildings(buildings, floors)
        )
        decisions = self.rbac.decision_context(user_id, occurred_at=observed_at)
        coach = self.rbac.account_has_role(user_id, "coach") and self._allows_all(
            decisions,
            ("teaching.read", "coach_roster.read", "enrollments.read"),
            {"global", "assigned_class"},
        )
        family = self.rbac.account_has_role(
            user_id,
            "guardian",
        ) and self._allows_all(
            decisions,
            ("people.read", "teaching.read", "enrollments.read"),
            {"global", "family"},
        )
        self_learning = (
            self.db.get(StudentProfile, actor_person_id) is not None
            and self._allows_all(
                decisions,
                ("people.read", "teaching.read", "enrollments.read"),
                {"global", f"self:{actor_person_id}"},
            )
        )
        payments = decisions.decide(
            "payments.read",
            {"global", f"self:{actor_person_id}"},
        ).allowed and not (
            (
                self.rbac.account_has_role(user_id, "super_admin")
                or self.rbac.account_has_role(user_id, "admin")
            )
            and not self.rbac.account_has_role(user_id, "guardian")
            and not self_learning
        )
        food_profile_available, food_preferences_onboarding_required = (
            self._food_profile_capabilities(
                user_id=user_id,
                actor_person_id=actor_person_id,
                occurred_at=observed_at,
            )
        )
        adult_student = self.db.scalar(
            select(Person)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .join(StudentProfile, StudentProfile.person_id == Person.id)
            .where(
                Person.id == actor_person_id,
                Person.deleted_at.is_(None),
                UserAccount.id == user_id,
                UserAccount.status == "active",
            )
        )
        adult_guardian_invitations_available = bool(
            adult_student is not None
            and age_status_for(
                adult_student.date_of_birth,
                business_date_at(observed_at),
            ) == "adult"
        )

        learning_mode: PortalLearningMode | None = None
        if family and self_learning:
            learning_mode = "self_and_family"
        elif family:
            learning_mode = "family"
        elif self_learning:
            learning_mode = "self"

        return PortalContextResponse(
            coach_available=coach,
            learning_available=family or self_learning,
            payments_available=payments,
            housing_management_available=housing_management_available,
            housing_available=housing_available,
            invitations_available=InvitedHousingAccess(self.db).has_sources(
                user_id=user_id, actor_person_id=actor_person_id, as_of=observed_at,
            ),
            adult_guardian_invitations_available=(
                adult_guardian_invitations_available
            ),
            food_profile_available=food_profile_available,
            food_preferences_onboarding_required=food_preferences_onboarding_required,
            learning_mode=learning_mode,
        )
