from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import (
    AccountContactMismatch,
    AccountService,
    LoginPhoneAlreadyExists,
    PersonNotReady,
)
from app.core import time as core_time
from app.identity.models import Person
from app.identity.schemas import StudentCreate
from app.identity.service import IdentityService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 7, 26, 15, 59, 59, tzinfo=UTC), date(2026, 7, 26)),
        (datetime(2026, 7, 26, 16, 0, tzinfo=UTC), date(2026, 7, 27)),
    ],
)
def test_business_date_at_uses_shanghai_date_boundary(
    value: datetime,
    expected: date,
) -> None:
    assert core_time.business_date_at(value) == expected


def test_business_date_at_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        core_time.business_date_at(datetime(2026, 7, 26, 16, 0))  # noqa: DTZ001


@pytest.fixture
def identity_service(db: Session) -> IdentityService:
    return IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
    )


@pytest.fixture
def account_service(db: Session, identity_service: IdentityService) -> AccountService:
    return AccountService(
        db,
        identity_service=identity_service,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 7, 26),
    )


@pytest.fixture
def adult_without_contact(identity_service: IdentityService):
    return identity_service.create_student(
        StudentCreate(
            full_name="成年学员",
            identity_number=make_cn_id("19950102", 102),
            sex="female",
            date_of_birth=date(1995, 1, 2),
            student_number="ACCOUNT-001",
        )
    )


@pytest.fixture
def unready_minor(identity_service: IdentityService):
    return identity_service.create_student(
        StudentCreate(
            full_name="未就绪学员",
            identity_number=make_cn_id("20150102", 104),
            sex="female",
            date_of_birth=date(2015, 1, 2),
            student_number="ACCOUNT-MINOR-001",
        )
    )


def test_account_requires_ready_person_and_unique_phone(
    account_service: AccountService,
    unready_minor,
) -> None:
    with pytest.raises(PersonNotReady) as error:
        account_service.create_account(
            AccountCreate(
                person_id=unready_minor.id,
                login_phone="13800138000",
                email="minor@example.com",
            )
        )

    assert error.value.code == "person_not_ready"
    assert error.value.missing == ["guardian"]


def test_create_account_populates_missing_contact_and_returns_password_once(
    account_service: AccountService,
    adult_without_contact,
    db: Session,
) -> None:
    created = account_service.create_account(
        AccountCreate(
            person_id=adult_without_contact.id,
            login_phone="13800138000",
            email="Adult@Example.com",
        )
    )

    assert created.temporary_password
    assert created.account.masked_login_phone == "138****8000"
    assert created.account.email == "adult@example.com"
    assert created.account.force_password_change is True
    stored = db.get(UserAccount, created.account.id)
    assert stored is not None
    assert created.temporary_password not in stored.password_hash
    assert PasswordService().verify(created.temporary_password, stored.password_hash)
    person = db.get(Person, adult_without_contact.id)
    assert person is not None
    assert person.phone_ciphertext is not None
    assert person.email == "adult@example.com"
    listed = account_service.list_accounts(page=1, page_size=20, status=None)
    assert listed.items == [created.account]
    assert "temporary_password" not in listed.model_dump_json()


def test_account_contact_mismatch_requires_person_edit_first(
    account_service: AccountService,
    identity_service: IdentityService,
) -> None:
    person = identity_service.create_student(
        StudentCreate(
            full_name="已有联系方式学员",
            identity_number=make_cn_id("19940102", 102),
            sex="female",
            date_of_birth=date(1994, 1, 2),
            student_number="ACCOUNT-002",
            phone="13900139000",
            email="existing@example.com",
        )
    )

    with pytest.raises(AccountContactMismatch) as error:
        account_service.create_account(
            AccountCreate(
                person_id=person.id,
                login_phone="13800138000",
                email="existing@example.com",
            )
        )
    assert error.value.code == "account_contact_mismatch"


def test_login_phone_is_unique_across_accounts(
    account_service: AccountService,
    identity_service: IdentityService,
    adult_without_contact,
) -> None:
    account_service.create_account(
        AccountCreate(
            person_id=adult_without_contact.id,
            login_phone="13800138000",
            email="first@example.com",
        )
    )
    second = identity_service.create_student(
        StudentCreate(
            full_name="第二位成年学员",
            identity_number=make_cn_id("19930102", 104),
            sex="female",
            date_of_birth=date(1993, 1, 2),
            student_number="ACCOUNT-003",
        )
    )

    with pytest.raises(LoginPhoneAlreadyExists):
        account_service.create_account(
            AccountCreate(
                person_id=second.id,
                login_phone="13800138000",
                email="second@example.com",
            )
        )


def test_disable_enable_and_reset_password(
    account_service: AccountService,
    adult_without_contact,
    db: Session,
) -> None:
    created = account_service.create_account(
        AccountCreate(
            person_id=adult_without_contact.id,
            login_phone="13800138000",
            email="adult@example.com",
        )
    )
    account_service.disable_account(
        created.account.id,
        actor_user_id=None,
        request_id="service-test",
    )
    assert db.get(UserAccount, created.account.id).status == "disabled"  # type: ignore[union-attr]
    account_service.enable_account(created.account.id)
    assert db.get(UserAccount, created.account.id).status == "active"  # type: ignore[union-attr]

    reset = account_service.reset_password(created.account.id)
    stored = db.get(UserAccount, created.account.id)
    assert stored is not None
    assert reset.temporary_password != created.temporary_password
    assert PasswordService().verify(created.temporary_password, stored.password_hash) is False
    assert PasswordService().verify(reset.temporary_password, stored.password_hash) is True
    assert stored.force_password_change is True


def test_account_list_status_filter(
    account_service: AccountService,
    adult_without_contact,
) -> None:
    created = account_service.create_account(
        AccountCreate(
            person_id=adult_without_contact.id,
            login_phone="13800138000",
            email="adult@example.com",
        )
    )
    account_service.disable_account(
        created.account.id,
        actor_user_id=None,
        request_id="service-test",
    )

    assert account_service.list_accounts(page=1, page_size=20, status="active").total == 0
    assert account_service.list_accounts(page=1, page_size=20, status="disabled").total == 1
