"""Immutable discovery and prelocked realization of personnel staff evidence."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.role_grant_models import ActivationRolePlan
from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
from app.security.encryption import LookupDigest
from app.teaching.models import ProfessionalTeam
from app.team_management.errors import TeamServiceError
from app.team_management.person_staff_models import (
    PersonTeamStaffAuthorization,
    PersonTeamStaffPosition,
)
from app.team_management.person_staff_service import (
    PreparedPersonStaffLocks,
    authentic_authorization,
    require_staff_manager,
)
from app.team_management.staff_service import STAFF_ROLE_KEYS, _assign_staff_in_transaction
from app.team_management.write_support import require_active_team, require_candidate


def _invalid() -> PersonnelIntakeError:
    return PersonnelIntakeError("activation_staff_invalidated", status=409,
        detail="队伍工作人员授权已失效，请联系管理员重新确认")


@dataclass(frozen=True)
class PreparedStaffActivation:
    locks: PreparedPersonStaffLocks

    @classmethod
    def discover(cls, db: Session, person_id: UUID) -> "PreparedStaffActivation":
        return cls(PreparedPersonStaffLocks.discover(db, person_id))

    def assert_unchanged(self, db: Session) -> None:
        if self != self.discover(db, self.locks.person_id):
            raise PersonnelIntakeError("activation_staff_retry", status=409,
                detail="工作人员岗位或授权已更新，请重试激活")


def realize_staff_positions_in_transaction(
    db: Session, *, prepared: PreparedStaffActivation, account: UserAccount,
    lookup: LookupDigest, occurred_at: datetime,
) -> None:
    """All upstream locks and the position rows are owned by activation already."""
    if account.person_id != prepared.locks.person_id:
        raise _invalid()
    positions = list(db.scalars(select(PersonTeamStaffPosition).where(
        PersonTeamStaffPosition.person_id == account.person_id,
        PersonTeamStaffPosition.ended_at.is_(None)).order_by(PersonTeamStaffPosition.id)))
    roles = ActivationRoleService(db, lookup=lookup)
    current = latest_plan(db, account.person_id)
    from app.personnel_intake.role_intents import RoleIntentService, pending_intents
    intent = pending_intents(db, {account.person_id}).get(account.person_id)
    intent_service = RoleIntentService(db, lookup=lookup)
    checked = []
    try:
        for position in positions:
            proof = db.scalar(select(PersonTeamStaffAuthorization).where(
                PersonTeamStaffAuthorization.position_id == position.id,
                PersonTeamStaffAuthorization.version == position.version))
            if (position.account_assignment_id is not None or proof is None
                    or not authentic_authorization(lookup, position, proof)):
                raise _invalid()
            if intent_service.pending_position(intent, position, proof):
                continue
            original = db.get(ActivationRolePlan, proof.role_plan_id) if proof.role_plan_id else None
            key = STAFF_ROLE_KEYS[position.staff_role]
            if (original is None or current is None or original.person_id != account.person_id
                    or key not in original.role_keys or key not in current.role_keys):
                raise _invalid()
            roles.authenticate(original)
            roles.authenticate(current)
            roles.validate(original.authorizer_user_id, account.person_id, [key])
            authorizer = db.get(UserAccount, proof.authorizer_user_id)
            person = db.get(Person, authorizer.person_id) if authorizer else None
            if authorizer is None or person is None:
                raise _invalid()
            require_staff_manager(db, AuthContext(account=authorizer, person=person, session=None), position.team_id)  # type: ignore[arg-type]
            team = db.get(ProfessionalTeam, position.team_id)
            if team is None:
                raise _invalid()
            require_active_team(db, team)
            require_candidate(db, role_key=key, account_id=account.id)
            checked.append((position, proof))
        for position, proof in checked:
            assignment = _assign_staff_in_transaction(db, account_id=account.id,
                team_id=position.team_id, staff_role=position.staff_role,
                actor_user_id=proof.authorizer_user_id, occurred_at=occurred_at)
            position.account_assignment_id = assignment.id
        db.flush()
    except TeamServiceError as exc:
        raise _invalid() from exc
