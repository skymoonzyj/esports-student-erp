from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.create_admin import InitialAdminExists, create_initial_admin
from app.identity.schemas import StaffCreate
from app.rbac.models import Role, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

ADMIN = StaffCreate(
    full_name="首位管理员",
    identity_number="11010519491231002X",
    sex="female",
    date_of_birth=date(1949, 12, 31),
    phone="13800138000",
    email="admin@example.com",
    staff_type="administration",
)


def test_create_initial_admin_is_atomic_and_uses_entered_password(db: Session) -> None:
    account_id = create_initial_admin(
        db,
        profile=ADMIN,
        password="InitAdmin2026Aa",
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
        passwords=PasswordService(),
    )

    account = db.get(UserAccount, account_id)
    assert account is not None
    assert PasswordService().verify("InitAdmin2026Aa", account.password_hash)
    assert account.force_password_change is False
    assert (
        db.scalar(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == account_id)
        )
        == "super_admin"
    )


def test_create_initial_admin_refuses_when_admin_exists(db: Session) -> None:
    arguments = {
        "profile": ADMIN,
        "password": "InitAdmin2026Aa",
        "cipher": EnvelopeCipher(bytes(range(32))),
        "lookup": LookupDigest(bytes(reversed(range(32)))),
        "passwords": PasswordService(),
    }
    create_initial_admin(db, **arguments)

    with pytest.raises(InitialAdminExists):
        create_initial_admin(db, **arguments)
