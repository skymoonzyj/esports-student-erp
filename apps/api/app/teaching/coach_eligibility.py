from __future__ import annotations

from uuid import UUID

from sqlalchemy import ColumnElement, exists, select
from sqlalchemy.orm import InstrumentedAttribute, Session, aliased

from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile
from app.rbac.models import Role, UserRole


class CoachEligibility:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def eligible_person_predicate(
        person_id: ColumnElement[UUID] | InstrumentedAttribute[UUID],
    ) -> ColumnElement[bool]:
        live_person = aliased(Person)
        return exists(
            select(StaffProfile.person_id)
            .join(live_person, live_person.id == StaffProfile.person_id)
            .join(UserAccount, UserAccount.person_id == StaffProfile.person_id)
            .join(UserRole, UserRole.user_id == UserAccount.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                StaffProfile.person_id == person_id,
                live_person.deleted_at.is_(None),
                StaffProfile.staff_type == "coach",
                UserAccount.status == "active",
                Role.key == "coach",
            )
        )

    def ineligibility_reason(self, person_id: UUID) -> str | None:
        live_person = self.db.scalar(
            select(Person.id).where(
                Person.id == person_id,
                Person.deleted_at.is_(None),
            )
        )
        if live_person is None:
            return "person_missing_or_deleted"
        staff_type = self.db.scalar(
            select(StaffProfile.staff_type).where(StaffProfile.person_id == person_id)
        )
        if staff_type != "coach":
            return "not_coach_staff"
        account = self.db.scalar(
            select(UserAccount).where(
                UserAccount.person_id == person_id,
                UserAccount.status == "active",
            )
        )
        if account is None:
            return "account_missing_or_disabled"
        has_role = self.db.scalar(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == account.id, Role.key == "coach")
        )
        if has_role is None:
            return "coach_role_missing"
        return None
