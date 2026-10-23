from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.rbac.service import RbacService


def registration_permission_allowed(
    db: Session,
    *,
    account_id: UUID,
    permission: str,
    person_id: UUID | None = None,
) -> bool:
    resource_tokens = {"global"}
    if person_id is not None:
        resource_tokens.add(f"person:{person_id}")
    return RbacService(db).decide(
        user_id=account_id,
        permission=permission,
        resource_tokens=resource_tokens,
    ).allowed
