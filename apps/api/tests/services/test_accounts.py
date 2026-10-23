from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import (
    AccountContactMismatch,
    AccountService,
    AccountServiceError,
    LoginPhoneAlreadyExists,
    PersonNotReady,
)
from app.core import time as core_time
from app.identity.membership_locks import person_membership_lock_keys
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


def _sql_statement_index(statements: list[tuple[str, object]], predicate) -> int | None:
    return next(
        (
            index
            for index, (statement, parameters) in enumerate(statements)
            if predicate(statement, parameters)
        ),
        None,
    )


def _membership_lock_indices(
    statements: list[tuple[str, object]],
    membership_keys: set[int],
) -> tuple[list[int], set[int]]:
    lock_indices: list[int] = []
    observed_keys: set[int] = set()
    for index, (statement, parameters) in enumerate(statements):
        if "pg_advisory_xact_lock" not in statement or not isinstance(parameters, dict):
            continue
        keys = {
            value for value in parameters.values() if isinstance(value, int) and value in membership_keys
        }
        if keys:
            lock_indices.append(index)
            observed_keys.update(keys)
    return lock_indices, observed_keys


def _people_for_update_indices(statements: list[tuple[str, object]]) -> list[int]:
    return [
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "FROM people" in statement and "FOR UPDATE" in statement
    ]


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


def test_create_account_rejects_credential_drift_after_precomputed_locks(account_service, adult_without_contact, db, monkeypatch):
    import app.auth.service as module
    from app.personnel_intake.errors import CredentialReservationConflict

    original = module.lock_personnel_credentials
    def drift(session, **kwargs):
        session.execute(Person.__table__.update().where(Person.id == adult_without_contact.id).values(
            phone_digest=account_service.identity_service.lookup.digest("13900000111"),
            phone_ciphertext=account_service.identity_service.cipher.encrypt("13900000111", field_name="phone")))
        original(session, **kwargs)
    monkeypatch.setattr(module, "lock_personnel_credentials", drift)
    with pytest.raises(CredentialReservationConflict):
        account_service.create_account(AccountCreate(person_id=adult_without_contact.id,
            login_phone="13900000112", email="drift-account@example.com"))
    assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == adult_without_contact.id)) is None


@pytest.mark.parametrize("valid_code", [True, False])
def test_identifier_drift_fails_closed_without_bypassing_bad_code_count(account_service, adult_without_contact, db, monkeypatch, valid_code):
    import app.auth.service as module
    from app.auth.service import AuthContext, AuthService
    from app.personnel_intake.errors import CredentialReservationConflict
    from app.registration.delivery import CapturingVerificationSender
    from app.registration.models import RegistrationVerificationChallenge
    from app.registration.verification_service import (
        RegistrationVerificationService,
        VerificationInvalidOrExpired,
    )

    identity = account_service.identity_service
    password = "Drift2026PassAa"
    account = UserAccount(person_id=adult_without_contact.id, password_hash=PasswordService().hash(password),
        status="active", force_password_change=False)
    db.add(account)
    db.commit()
    auth = AuthService(db, cipher=identity.cipher, lookup=identity.lookup, passwords=PasswordService(),
        session_token_pepper=bytes(range(32)), session_ttl_minutes=480)
    sender = CapturingVerificationSender(messages=[])
    verification = RegistrationVerificationService(db, lookup=identity.lookup, verification_pepper=bytes(range(32)), sender=sender, app_env="test")
    issued = verification.issue(identifier_kind="email", identifier="drift-login@example.com", purpose="add_login_identifier", request_source="127.0.0.42", account_id=account.id)
    original = module.lock_registration_credential_keys
    def drift(session, keys):
        session.execute(Person.__table__.update().where(Person.id == account.person_id).values(
            phone_digest=identity.lookup.digest("13900000113"),
            phone_ciphertext=identity.cipher.encrypt("13900000113", field_name="phone")))
        original(session, keys)
    monkeypatch.setattr(module, "lock_registration_credential_keys", drift)
    correct = sender.code_for(issued.challenge_id)
    wrong = "000000" if correct != "000000" else "111111"
    with pytest.raises(CredentialReservationConflict if valid_code else VerificationInvalidOrExpired):
        auth.verify_identifier_addition(AuthContext(account=account, person=db.get(Person, account.person_id), session=None),
            challenge_id=issued.challenge_id, code=correct if valid_code else wrong,
            identifier_kind="email", identifier="drift-login@example.com", current_password=password,
            request_id="credential-drift-regression", verification=verification)
    assert db.scalar(select(AccountLoginIdentifier.id).where(AccountLoginIdentifier.account_id == account.id)) is None
    challenge = db.get(RegistrationVerificationChallenge, issued.challenge_id, populate_existing=True)
    assert challenge.consumed_at is None
    assert challenge.attempts == (0 if valid_code else 1)


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
    identity_service: IdentityService,
    adult_without_contact,
    db: Session,
) -> None:
    person = db.get(Person, adult_without_contact.id)
    assert person is not None
    person.nickname = "小成"
    db.flush()
    created = account_service.create_account(
        AccountCreate(
            person_id=adult_without_contact.id,
            login_phone="13800138000",
            email="Adult@Example.com",
        )
    )

    assert created.temporary_password
    assert created.account.masked_primary_identifier == "138****8000"
    assert created.account.nickname == "小成"
    assert created.account.primary_identifier_kind == "phone"
    assert created.account.masked_phone == "138****8000"
    assert created.account.masked_email is None
    assert created.account.verified_identifier_kinds == ["phone"]
    assert created.account.force_password_change is True
    stored = db.get(UserAccount, created.account.id)
    assert stored is not None
    identifier = db.scalar(
        select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == stored.id
        )
    )
    assert identifier is not None
    assert identifier.kind == "phone"
    assert identifier.verification_source == "admin_created"
    assert identifier.is_primary is False
    assert stored.login_phone_digest is None
    assert created.temporary_password not in stored.password_hash
    assert PasswordService().verify(created.temporary_password, stored.password_hash)
    person = db.get(Person, adult_without_contact.id)
    assert person is not None
    assert person.phone_ciphertext is not None
    assert person.email is None
    assert person.email_ciphertext is not None
    assert person.email_digest is not None
    assert identity_service.decrypt_email(person) == "adult@example.com"
    listed = account_service.list_accounts(page=1, page_size=20, status=None)
    assert listed.items == [created.account]
    assert "temporary_password" not in listed.model_dump_json()


def test_create_account_membership_lock_precedes_person_lock_and_contact_write(
    account_service: AccountService,
    adult_without_contact,
    db: Session,
) -> None:
    input = AccountCreate(
        person_id=adult_without_contact.id,
        login_phone="13800138888",
        email="membership-account@example.com",
    )
    person = db.get(Person, adult_without_contact.id)
    assert person is not None
    phone_digest = account_service.identity_service.lookup.digest(input.login_phone)
    email_digest = account_service.identity_service.lookup.digest_domain(
        "person-email",
        str(input.email),
    )
    membership_keys = set(
        person_membership_lock_keys(
            phone_digests={person.phone_digest, phone_digest},
            email_digests={person.email_digest, email_digest},
        )
    )
    statements: list[tuple[str, object]] = []

    def capture_sql(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: object,
    ) -> None:
        statements.append((statement, _parameters))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_sql)
    try:
        account_service.create_account(input)
    finally:
        event.remove(bind, "before_cursor_execute", capture_sql)

    people_for_update_indices = _people_for_update_indices(statements)
    membership_lock_indices, observed_membership_keys = _membership_lock_indices(
        statements,
        membership_keys,
    )
    contact_or_account_write_index = _sql_statement_index(
        statements,
        lambda statement, _parameters: "UPDATE people" in statement
        or "INSERT INTO user_accounts" in statement,
    )
    captured_sql = "\n".join(statement for statement, _parameters in statements)

    assert people_for_update_indices
    assert observed_membership_keys == membership_keys
    assert contact_or_account_write_index is not None
    assert (
        membership_lock_indices[-1]
        < min(people_for_update_indices)
        < contact_or_account_write_index
    )
    assert membership_lock_indices[-1] < contact_or_account_write_index
    assert not [
        index
        for index in people_for_update_indices
        if index < membership_lock_indices[-1]
    ]
    assert input.login_phone not in captured_sql
    assert str(input.email) not in captured_sql
    assert account_service.identity_service.lookup.digest(input.login_phone) not in captured_sql
    assert (
        account_service.identity_service.lookup.digest_domain("person-email", str(input.email))
        not in captured_sql
    )


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


def test_delete_account_soft_deletes_person_and_revokes_sessions(
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
    session = AuthSession(
        user_id=created.account.id,
        token_digest="delete-account-token",
        csrf_digest="delete-account-csrf",
        expires_at=datetime(2026, 8, 21, tzinfo=UTC),
    )
    db.add(session)
    db.flush()

    account_service.delete_account(
        created.account.id,
        actor_user_id=None,
        request_id="service-delete-account",
    )

    stored_account = db.get(UserAccount, created.account.id)
    stored_person = db.get(Person, adult_without_contact.id)
    assert stored_account is not None
    assert stored_account.status == "disabled"
    assert stored_person is not None
    assert stored_person.deleted_at is not None
    assert session.revoked_at is not None
    assert account_service.list_accounts(page=1, page_size=20, status=None).total == 0


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


def test_account_summary_rejects_incomplete_phone_contact(
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
    person = db.get(Person, created.account.person_id)
    assert person is not None
    person.phone_digest = None
    db.flush()

    with pytest.raises(AccountServiceError, match="phone"):
        account_service.list_accounts(page=1, page_size=20, status=None)


def test_account_summary_rejects_identifier_contact_mismatch(
    account_service: AccountService,
    identity_service: IdentityService,
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
    replacement = normalize_login_identifier("13900139000")
    person = db.get(Person, created.account.person_id)
    assert person is not None
    person.phone_ciphertext = identity_service.cipher.encrypt(
        replacement.value,
        field_name="phone",
    )
    person.phone_digest = identity_service.lookup.digest(replacement.value)
    db.add(
        AccountLoginIdentifier(
            account_id=created.account.id,
            kind="phone",
            identifier_digest=login_identifier_digest(
                identity_service.lookup,
                replacement,
            ),
            verified_at=datetime(2026, 8, 15, 8, tzinfo=UTC),
            verification_source="test_verified",
            is_primary=False,
        )
    )
    db.flush()

    with pytest.raises(AccountServiceError, match="phone identifier"):
        account_service.list_accounts(page=1, page_size=20, status=None)


def test_account_list_excludes_soft_deleted_people(
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
    person = db.get(Person, created.account.person_id)
    assert person is not None
    person.deleted_at = datetime(2026, 8, 15, 8, tzinfo=UTC)
    db.flush()

    result = account_service.list_accounts(page=1, page_size=20, status=None)

    assert result.total == 0
    assert result.items == []
