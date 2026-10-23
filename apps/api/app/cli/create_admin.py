from __future__ import annotations

import argparse
import base64
import getpass
from datetime import date
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.identity.schemas import StaffCreate
from app.identity.service import IdentityService
from app.rbac.models import Role, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


class InitialAdminExists(ValueError):
    pass


def create_initial_admin(
    db: Session,
    *,
    profile: StaffCreate,
    password: str,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> UUID:
    passwords.validate(password)
    with write_transaction(db):
        db.execute(text("SELECT pg_advisory_xact_lock(7262026001)"))
        existing = db.scalar(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.key == "super_admin")
            .limit(1)
        )
        if existing is not None:
            raise InitialAdminExists("a super administrator account already exists")

        identity = IdentityService(db, cipher=cipher, lookup=lookup)
        person = identity.create_staff(profile)
        accounts = AccountService(
            db,
            identity_service=identity,
            passwords=passwords,
        )
        created = accounts.create_account(
            AccountCreate(
                person_id=person.id,
                login_phone=profile.phone,
                email=profile.email,
            )
        )
        account = db.get(UserAccount, created.account.id)
        super_admin_role = db.scalar(select(Role).where(Role.key == "super_admin"))
        if account is None or super_admin_role is None:
            raise RuntimeError("RBAC seed migration must run before creating an administrator")
        account.password_hash = passwords.hash(password)
        account.force_password_change = False
        db.add(UserRole(user_id=account.id, role_id=super_admin_role.id))
        db.flush()
        account_id = account.id
    return account_id


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create the first ERP super administrator")
    parser.add_argument("--name", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--identity-number", required=True)
    parser.add_argument("--sex", choices=("male", "female"), required=True)
    parser.add_argument("--date-of-birth", type=date.fromisoformat, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    password = getpass.getpass("超级管理员密码：")
    confirmation = getpass.getpass("再次输入超级管理员密码：")
    if password != confirmation:
        raise SystemExit("两次输入的密码不一致")
    settings = get_settings()
    settings.validate_runtime_secrets()
    profile = StaffCreate(
        full_name=arguments.name,
        identity_number=arguments.identity_number,
        sex=arguments.sex,
        date_of_birth=arguments.date_of_birth,
        phone=arguments.phone,
        email=arguments.email,
        staff_type="administration",
    )
    with SessionLocal() as db:
        account_id = create_initial_admin(
            db,
            profile=profile,
            password=password,
            cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
    print(f"超级管理员账号已创建：{account_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
