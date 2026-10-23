from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.housing.access import HousingAccessService
from app.housing.api_support import LockedHousingAuthorizationDenied
from app.housing.models import HousingRoom, HousingStay
from app.identity.models import Person
from app.rbac.models import Role, UserRole


def require_history_void_authority(db: Session, actor: AuthContext, room: HousingRoom) -> None:
    """Re-read current authority; a cached AuthContext is never an entitlement."""
    account = db.execute(
        select(UserAccount.person_id, UserAccount.status, UserAccount.force_password_change).where(
            UserAccount.id == actor.account.id
        )
    ).one_or_none()
    if (
        account is None
        or account.person_id != actor.person.id
        or account.status != "active"
        or account.force_password_change
    ):
        raise LockedHousingAuthorizationDenied(not_found=False)
    if db.scalar(select(Person.deleted_at).where(Person.id == account.person_id)) is not None:
        raise LockedHousingAuthorizationDenied(not_found=False)
    access = HousingAccessService(db, actor.account.id, datetime.now(UTC))
    if not access.can_read_room(room):
        raise LockedHousingAuthorizationDenied(not_found=True)
    actual_superadmin = db.scalar(
        select(UserRole.user_id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == actor.account.id, Role.key == "super_admin")
    )
    if actual_superadmin is None or not access.can(
        "housing.history.void", access.room_tokens(room)
    ):
        raise LockedHousingAuthorizationDenied(not_found=False)


def can_void_history(db: Session, actor: AuthContext, room: HousingRoom, stay: HousingStay) -> bool:
    if stay.room_id != room.id or stay.status != "checked_out" or stay.voided_at is not None:
        return False
    try:
        require_history_void_authority(db, actor, room)
    except LockedHousingAuthorizationDenied:
        return False
    return True
