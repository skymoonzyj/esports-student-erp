"""Structural management protection, evaluated under the caller's participant locks."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.rbac.models import Permission, Role, RolePermission, UserPermissionOverride, UserRole

CORE_FINANCIAL_PROTECTION_KEYS = frozenset(
    {
        "payments.read",
        "payments.manage",
        "refunds.manage",
        "payment_imports.manage",
        "revenue_share.finance",
        "revenue_share.manage",
        "revenue_share.export",
    }
)


def protected_management_target(
    db: Session, account_id: UUID, *, now: datetime | None = None
) -> bool:
    """Do not let denies, future starts, or cached effective access hide a target.

    Personal orders/commissions, family access and expense workflows deliberately
    do not classify an ordinary person as a financial administrator. No commits
    or locks occur here: all mutation callers hold the ordered participant locks.
    """
    occurred_at = now or datetime.now(UTC)
    protected_role = db.scalar(
        select(UserRole.role_id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == account_id, Role.key.in_({"super_admin", "finance"}))
        .limit(1)
    )
    if protected_role is not None:
        return True
    role_allow = db.scalar(
        select(UserRole.role_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            UserRole.user_id == account_id,
            Permission.key.in_(CORE_FINANCIAL_PROTECTION_KEYS),
            RolePermission.scope_kind.not_in({"self", "family"}),
        )
        .limit(1)
    )
    if role_allow is not None:
        return True
    return (
        db.scalar(
            select(UserPermissionOverride.id)
            .join(Permission, Permission.id == UserPermissionOverride.permission_id)
            .where(
                UserPermissionOverride.user_id == account_id,
                Permission.key.in_(CORE_FINANCIAL_PROTECTION_KEYS),
                UserPermissionOverride.effect == "allow",
                UserPermissionOverride.scope_kind.not_in({"self", "family"}),
                or_(
                    UserPermissionOverride.valid_until.is_(None),
                    UserPermissionOverride.valid_until > occurred_at,
                ),
            )
            .limit(1)
        )
        is not None
    )
