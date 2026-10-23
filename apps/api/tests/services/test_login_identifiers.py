from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.auth.service import AuthService, InvalidCredentials
from app.identity.models import Person
from app.identity.schemas import StaffCreate
from app.identity.service import IdentityService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

PASSWORD = "Valid2026PassAa"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@pytest.fixture
def lookup() -> LookupDigest:
    return LookupDigest(bytes(range(32)))


@pytest.fixture
def auth_service(db: Session, lookup: LookupDigest) -> AuthService:
    return AuthService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=lookup,
        passwords=PasswordService(),
        session_token_pepper=bytes(range(32)),
        session_ttl_minutes=480,
        clock=lambda: datetime(2026, 8, 15, 8, tzinfo=UTC),
    )


def _account_with_identifiers(
    db: Session,
    lookup: LookupDigest,
    *,
    include_email_identifier: bool,
) -> UserAccount:
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=lookup,
    )
    person_summary = identity.create_staff(
        StaffCreate(
            full_name="通用登录测试账号",
            identity_number=make_cn_id("19900102", 402),
            sex="female",
            date_of_birth=date(1990, 1, 2),
            phone="13800138000",
            email="admin@example.com",
            staff_type="administration",
        )
    )
    person = db.get(Person, person_summary.id)
    assert person is not None
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=None,
        email_normalized=None,
        password_hash=PasswordService().hash(PASSWORD),
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    phone = normalize_login_identifier("13800138000")
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind=phone.kind,
            identifier_digest=login_identifier_digest(lookup, phone),
            verified_at=datetime(2026, 8, 15, 7, tzinfo=UTC),
            verification_source="test_verified",
            is_primary=True,
        )
    )
    if include_email_identifier:
        email = normalize_login_identifier("admin@example.com")
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind=email.kind,
                identifier_digest=login_identifier_digest(lookup, email),
                verified_at=datetime(2026, 8, 15, 7, tzinfo=UTC),
                verification_source="test_verified",
                is_primary=False,
            )
        )
    db.flush()
    return account


def test_login_identifier_normalization_and_domains(lookup: LookupDigest) -> None:
    phone = normalize_login_identifier(" 13800138000 ")
    email = normalize_login_identifier(" Admin@Example.COM ")

    assert (phone.kind, phone.value) == ("phone", "13800138000")
    assert (email.kind, email.value) == ("email", "admin@example.com")
    assert login_identifier_digest(lookup, phone) != lookup.digest(phone.value)
    assert login_identifier_digest(lookup, phone) != login_identifier_digest(lookup, email)


@pytest.mark.parametrize("identifier", ["13800138000", "admin@example.com"])
def test_login_accepts_each_verified_identifier(
    db: Session,
    lookup: LookupDigest,
    auth_service: AuthService,
    identifier: str,
) -> None:
    account = _account_with_identifiers(db, lookup, include_email_identifier=True)

    result = auth_service.login(
        identifier=identifier,
        password=PASSWORD,
        ip_address="127.0.0.1",
        request_id="generic-login",
    )

    assert result.context.account.id == account.id


def test_unverified_legacy_email_cannot_login(
    db: Session,
    lookup: LookupDigest,
    auth_service: AuthService,
) -> None:
    _account_with_identifiers(db, lookup, include_email_identifier=False)

    with pytest.raises(InvalidCredentials):
        auth_service.login(
            identifier="admin@example.com",
            password=PASSWORD,
            ip_address="127.0.0.1",
            request_id="legacy-email",
        )
