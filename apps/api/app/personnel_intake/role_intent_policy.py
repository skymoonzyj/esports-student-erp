"""Narrow direct-intake authority, distinct from account-role delegation."""

from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.errors import PersonnelIntakeError

REQUESTABLE_ROLE_KEYS = frozenset({
    "student", "team_member", "team_manager", "coach", "assistant_coach",
    "administration", "customer_service", "life_teacher", "property", "guardian",
    "staff", "registered_user",
})


def can_request_roles(db: Session, actor: AuthContext) -> bool:
    state = PersonnelIntakeAccess(db).snapshot(actor)
    return bool(state.roles & {"administration", "admin", "super_admin"}
                and state.allowed("personnel_intake.review") and state.allowed("people.read"))


def require_direct_intake(db: Session, actor: AuthContext) -> None:
    if not can_request_roles(db, actor):
        raise PersonnelIntakeError("personnel_intake_forbidden", status=403,
                                   detail="无权直接建立人员档案")
