from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.schemas import JobRoleChangeRequest, PermissionOverrideCreate
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import PermissionDelegationDenied, RbacService
from tests.api.conftest import platform_users as _platform_users


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def _manager(db, platform_users):
    actor = platform_users["accounts"]["customer_service"]
    db.execute(delete(UserRole).where(UserRole.user_id == actor.id))
    db.add(UserRole(user_id=actor.id, role_id=ROLES["admin"].id))
    db.flush()
    return actor


@pytest.mark.parametrize(
    "operation", ["assign", "remove_role", "job_change", "add_allow", "add_deny", "remove_allow"]
)
@pytest.mark.parametrize("protected_role", ["finance", "super_admin"])
def test_protected_target_cannot_be_downgraded_or_hidden_before_reset(
    db: Session, platform_users, operation: str, protected_role: str
):
    actor = _manager(db, platform_users)
    target = platform_users["accounts"]["self_student"]
    db.add(UserRole(user_id=target.id, role_id=ROLES[protected_role].id))
    grant = UserPermissionOverride(
        user_id=target.id,
        permission_id=PERMISSIONS["payments.read"].id,
        effect="allow",
        scope_kind="global",
    )
    db.add(grant)
    db.commit()
    service = RbacService(db)
    with pytest.raises(PermissionDelegationDenied):
        if operation == "assign":
            service.assign_role(
                actor_user_id=actor.id, account_id=target.id, role_id=ROLES["customer_service"].id
            )
        elif operation == "remove_role":
            service.remove_role(
                actor_user_id=actor.id,
                account_id=target.id,
                role_id=ROLES[protected_role].id,
                request_id="synthetic-protected-role-test",
            )
        elif operation == "job_change":
            service.change_job_role(
                actor_user_id=actor.id,
                account_id=target.id,
                input=JobRoleChangeRequest(
                    from_role_id=ROLES[protected_role].id,
                    to_role_id=ROLES["customer_service"].id,
                    keep_concurrent_roles=False,
                    expected_role_ids=[ROLES[protected_role].id, ROLES["student"].id],
                ),
                request_id="synthetic-protected-role-test",
            )
        elif operation.startswith("add_"):
            service.add_override(
                actor_user_id=actor.id,
                account_id=target.id,
                input=PermissionOverrideCreate(
                    permission_id=PERMISSIONS["payments.manage"].id,
                    effect="deny" if operation == "add_deny" else "allow",
                    scope_kind="global",
                ),
            )
        else:
            service.remove_override(
                actor_user_id=actor.id,
                account_id=target.id,
                override_id=grant.id,
                before_delete=lambda _: None,
            )
    assert db.get(UserRole, (target.id, ROLES[protected_role].id)) is not None
    assert db.get(UserPermissionOverride, grant.id) is not None


@pytest.mark.parametrize(
    "permission,scope",
    [
        ("payments.read", "self"),
        ("payments.read", "family"),
        ("revenue_share.read_self", "global"),
        ("revenue_share.create_self_referral", "self"),
        ("procurement.manage", "global"),
        ("travel_reports.read", "global"),
    ],
)
def test_personal_financial_and_expense_rights_do_not_protect_ordinary_target(
    db: Session, platform_users, permission: str, scope: str
):
    from app.rbac.account_protection import protected_management_target

    target = platform_users["accounts"]["self_student"]
    db.add(
        UserPermissionOverride(
            user_id=target.id,
            permission_id=PERMISSIONS[permission].id,
            effect="allow",
            scope_kind=scope,
        )
    )
    db.flush()
    assert protected_management_target(db, target.id) is False


@pytest.mark.parametrize(
    "permission",
    [
        "payments.read",
        "payments.manage",
        "refunds.manage",
        "payment_imports.manage",
        "revenue_share.finance",
        "revenue_share.manage",
        "revenue_share.export",
    ],
)
@pytest.mark.parametrize("temporal", ["current", "future", "expired"])
def test_core_financial_structure_ignores_deny_but_excludes_expired(
    db: Session, platform_users, permission: str, temporal: str
):
    from app.rbac.account_protection import protected_management_target

    now = datetime.now(UTC)
    target = platform_users["accounts"]["self_student"]
    db.add_all(
        [
            UserPermissionOverride(
                user_id=target.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
                valid_from=now + timedelta(days=1)
                if temporal == "future"
                else now - timedelta(days=2),
                valid_until=now - timedelta(days=1) if temporal == "expired" else None,
            ),
            UserPermissionOverride(
                user_id=target.id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny",
                scope_kind="global",
            ),
        ]
    )
    db.flush()
    assert protected_management_target(db, target.id, now=now) is (temporal != "expired")


def test_financial_deny_cannot_be_removed_from_self(db: Session, platform_users):
    actor = _manager(db, platform_users)
    deny = UserPermissionOverride(
        user_id=actor.id,
        permission_id=PERMISSIONS["payments.read"].id,
        effect="deny",
        scope_kind="global",
    )
    db.add(deny)
    db.commit()
    with pytest.raises(PermissionDelegationDenied):
        RbacService(db).remove_override(
            actor_user_id=actor.id,
            account_id=actor.id,
            override_id=deny.id,
            before_delete=lambda _: None,
        )
    assert db.get(UserPermissionOverride, deny.id) is not None


def test_second_account_cannot_return_denied_financial_authority(db: Session, platform_users):
    actor = _manager(db, platform_users)
    target = platform_users["accounts"]["self_student"]
    db.add(
        UserPermissionOverride(
            user_id=actor.id,
            permission_id=PERMISSIONS["payments.read"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.commit()
    with pytest.raises(PermissionDelegationDenied):
        RbacService(db).add_override(
            actor_user_id=actor.id,
            account_id=target.id,
            input=PermissionOverrideCreate(
                permission_id=PERMISSIONS["payments.read"].id, effect="allow", scope_kind="global"
            ),
        )
    assert (
        db.scalar(
            select(UserPermissionOverride.id).where(
                UserPermissionOverride.user_id == target.id,
                UserPermissionOverride.permission_id == PERMISSIONS["payments.read"].id,
            )
        )
        is None
    )
