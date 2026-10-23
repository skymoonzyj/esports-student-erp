from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.housing.access import HousingAccessService
from app.housing.models import HousingBuilding, HousingFloor
from app.housing.portal_service import HousingPortalService
from app.identity.models import StudentProfile
from app.portal.schemas import PortalContextResponse, PortalLearningMode
from app.rbac.service import RbacDecisionContext, RbacService


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
            self.rbac.account_has_role(user_id, "admin")
            and not self.rbac.account_has_role(user_id, "guardian")
            and not self_learning
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
            learning_mode=learning_mode,
        )
