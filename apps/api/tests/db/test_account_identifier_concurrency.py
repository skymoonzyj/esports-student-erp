from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.identifiers import (
    NormalizedLoginIdentifier,
    login_identifier_digest,
)
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.service import (
    AccountIdentifierUnavailable,
    AuthContext,
    AuthService,
)
from app.identity.membership_locks import person_membership_lock_keys
from app.identity.models import Person
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import RegistrationVerificationChallenge
from app.registration.verification_service import RegistrationVerificationService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
FIELD_KEY = bytes(range(32))
VERIFICATION_KEY = bytes(range(1, 33))
SESSION_KEY = bytes(range(2, 34))
TARGET_PHONE = "13800007666"
IDENTIFIER_PASSWORD = "IdentRace2026Aa"


@dataclass(frozen=True)
class _IdentifierAdditionScenario:
    account_id: UUID
    challenge_id: UUID
    code: str
    destination_kind: str
    destination: str


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _auth(db: Session) -> AuthService:
    return AuthService(
        db,
        cipher=EnvelopeCipher(FIELD_KEY),
        lookup=LookupDigest(FIELD_KEY),
        passwords=PasswordService(),
        session_token_pepper=SESSION_KEY,
        session_ttl_minutes=480,
        clock=lambda: NOW,
    )


def _verification(
    db: Session,
    sender: CapturingVerificationSender,
) -> RegistrationVerificationService:
    return RegistrationVerificationService(
        db,
        lookup=LookupDigest(FIELD_KEY),
        verification_pepper=VERIFICATION_KEY,
        sender=sender,
        app_env="test",
        clock=lambda: NOW,
    )


def _setup(engine: Engine) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    with Session(engine) as db:
        for sequence in (621, 623):
            identity = IdentityService(
                db,
                cipher=EnvelopeCipher(FIELD_KEY),
                lookup=LookupDigest(FIELD_KEY),
            )
            email = f"original-{sequence}@example.com"
            person = identity.create_base_person(
                PersonBaseInput(
                    full_name=f"并发标识测试人员{sequence}",
                    identity_number=make_cn_id("19900102", sequence),
                    sex="male",
                    date_of_birth=date(1990, 1, 2),
                    phone=None,
                    email=email,
                )
            )
            account = UserAccount(
                person_id=person.id,
                login_phone_digest=None,
                email_normalized=None,
                password_hash=PasswordService().hash(IDENTIFIER_PASSWORD),
                status="active",
                force_password_change=False,
                failed_login_count=0,
                self_service_primary_purpose="guardian",
            )
            db.add(account)
            db.flush()
            db.add(
                AccountLoginIdentifier(
                    account_id=account.id,
                    kind="email",
                    identifier_digest=login_identifier_digest(
                        LookupDigest(FIELD_KEY),
                        NormalizedLoginIdentifier("email", email),
                    ),
                    verified_at=NOW,
                    verification_source="test_verified",
                    is_primary=True,
                )
            )
            sender = CapturingVerificationSender(messages=[])
            challenge = _verification(db, sender).issue(
                identifier_kind="phone",
                identifier=TARGET_PHONE,
                purpose="add_login_identifier",
                request_source=f"127.0.0.{sequence % 200}",
                account_id=account.id,
            )
            # Separate issue times avoid the resend limiter while preserving one target.
            stored = db.get(RegistrationVerificationChallenge, challenge.challenge_id)
            assert stored is not None
            stored.last_sent_at = NOW - timedelta(minutes=sequence - 620)
            results.append((str(account.id), str(challenge.challenge_id), sender.code_for(challenge.challenge_id)))
        db.commit()
    return results


def _setup_identifier_addition_observer(
    engine: Engine,
    *,
    destination_kind: str,
    destination: str,
) -> _IdentifierAdditionScenario:
    existing_kind = "email" if destination_kind == "phone" else "phone"
    existing_value = (
        "observer-existing@example.com"
        if existing_kind == "email"
        else "13800007652"
    )
    with Session(engine) as db:
        lookup = LookupDigest(FIELD_KEY)
        identity = IdentityService(
            db,
            cipher=EnvelopeCipher(FIELD_KEY),
            lookup=lookup,
        )
        person = identity.create_base_person(
            PersonBaseInput(
                full_name=f"锁序观察{destination_kind}",
                identity_number=make_cn_id("19900102", 652),
                sex="female",
                date_of_birth=date(1990, 1, 2),
                phone=existing_value if existing_kind == "phone" else None,
                email=existing_value if existing_kind == "email" else None,
            )
        )
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=None,
            email_normalized=None,
            password_hash=PasswordService().hash(IDENTIFIER_PASSWORD),
            status="active",
            force_password_change=False,
            failed_login_count=0,
            self_service_primary_purpose="guardian",
        )
        db.add(account)
        db.flush()
        normalized_existing = normalize_registration_identifier(existing_kind, existing_value)
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind=existing_kind,
                identifier_digest=login_identifier_digest(
                    lookup,
                    NormalizedLoginIdentifier(
                        normalized_existing.kind,
                        normalized_existing.value,
                    ),
                ),
                verified_at=NOW,
                verification_source="test_verified",
                is_primary=True,
            )
        )
        sender = CapturingVerificationSender(messages=[])
        issued = _verification(db, sender).issue(
            identifier_kind=destination_kind,
            identifier=destination,
            purpose="add_login_identifier",
            request_source="127.0.0.52",
            account_id=account.id,
        )
        db.commit()
        return _IdentifierAdditionScenario(
            account_id=account.id,
            challenge_id=issued.challenge_id,
            code=sender.code_for(issued.challenge_id),
            destination_kind=destination_kind,
            destination=destination,
        )


def _numeric_parameters(parameters: object) -> tuple[int, ...]:
    if isinstance(parameters, dict):
        return tuple(value for value in parameters.values() if type(value) is int)
    if isinstance(parameters, (list, tuple)):
        return tuple(value for value in parameters if type(value) is int)
    return ()


def _advisory_lock_indices(
    statements: list[tuple[str, tuple[int, ...]]],
    keys: set[int],
) -> tuple[list[int], set[int]]:
    indices: list[int] = []
    observed_keys: set[int] = set()
    for index, (statement, numeric_parameters) in enumerate(statements):
        observed = set(numeric_parameters) & keys
        if "pg_advisory_xact_lock" in statement and observed:
            indices.append(index)
            observed_keys.update(observed)
    return indices, observed_keys


def _people_for_update_indices(statements: list[tuple[str, tuple[int, ...]]]) -> list[int]:
    return [
        index
        for index, (statement, _numeric_parameters) in enumerate(statements)
        if "FROM people" in statement and "FOR UPDATE" in statement
    ]


@pytest.mark.parametrize(
    ("destination_kind", "destination"),
    [
        ("phone", "13800007691"),
        ("email", "observer-target@example.com"),
    ],
)
def test_identifier_addition_membership_locks_follow_the_person_lock(
    postgres_engine: Engine,
    destination_kind: str,
    destination: str,
) -> None:
    scenario = _setup_identifier_addition_observer(
        postgres_engine,
        destination_kind=destination_kind,
        destination=destination,
    )
    try:
        with Session(postgres_engine) as db:
            account = db.get(UserAccount, scenario.account_id)
            assert account is not None
            person = db.get(Person, account.person_id)
            assert person is not None
            auth = _auth(db)
            lookup = LookupDigest(FIELD_KEY)
            normalized_destination = normalize_registration_identifier(
                scenario.destination_kind,
                scenario.destination,
            )
            login_digest = login_identifier_digest(
                lookup,
                NormalizedLoginIdentifier(
                    normalized_destination.kind,
                    normalized_destination.value,
                ),
            )
            destination_phone_digest = (
                lookup.digest(normalized_destination.value)
                if normalized_destination.kind == "phone"
                else None
            )
            destination_email_digest = (
                lookup.digest_domain("person-email", normalized_destination.value)
                if normalized_destination.kind == "email"
                else None
            )
            membership_keys = set(
                person_membership_lock_keys(
                    phone_digests={
                        digest
                        for digest in (person.phone_digest, destination_phone_digest)
                        if digest
                    },
                    email_digests={
                        digest
                        for digest in (person.email_digest, destination_email_digest)
                        if digest
                    },
                )
            )
            account_identifier_keys = {auth._identifier_lock_key(login_digest)}
            statements: list[tuple[str, tuple[int, ...]]] = []

            def capture_sql(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: object,
            ) -> None:
                statements.append((statement, _numeric_parameters(parameters)))

            bind = db.get_bind()
            event.listen(bind, "before_cursor_execute", capture_sql)
            try:
                auth.verify_identifier_addition(
                    AuthContext(
                        session=AuthSession(
                            user_id=account.id,
                            token_digest=f"observer-token-{account.id}",
                            csrf_digest=f"observer-csrf-{account.id}",
                            expires_at=NOW + timedelta(hours=1),
                        ),
                        account=account,
                        person=person,
                    ),
                    challenge_id=scenario.challenge_id,
                    code=scenario.code,
                    identifier_kind=normalized_destination.kind,
                    identifier=normalized_destination.value,
                    current_password=IDENTIFIER_PASSWORD,
                    request_id=f"identifier-lock-order-{destination_kind}",
                    verification=_verification(db, CapturingVerificationSender(messages=[])),
                )
            finally:
                event.remove(bind, "before_cursor_execute", capture_sql)

            account_identifier_lock_indices, observed_account_identifier_keys = (
                _advisory_lock_indices(statements, account_identifier_keys)
            )
            membership_lock_indices, observed_membership_keys = _advisory_lock_indices(
                statements,
                membership_keys,
            )
            people_for_update_indices = _people_for_update_indices(statements)
            account_for_update_indices = [
                index
                for index, (statement, _numeric_parameters) in enumerate(statements)
                if "FROM user_accounts" in statement and "FOR UPDATE" in statement
            ]
            identifier_for_update_indices = [
                index
                for index, (statement, _numeric_parameters) in enumerate(statements)
                if "FROM account_login_identifiers" in statement and "FOR UPDATE" in statement
            ]
            contact_dml_indices = [
                index
                for index, (statement, _numeric_parameters) in enumerate(statements)
                if "UPDATE people" in statement
                or "INSERT INTO account_login_identifiers" in statement
            ]
            captured_sql = "\n".join(statement for statement, _numeric_parameters in statements)
            captured_parameters = repr(
                [numeric_parameters for _statement, numeric_parameters in statements]
            )
            secret_values = {
                normalized_destination.value,
                registration_identifier_digest(lookup, normalized_destination),
                login_digest,
                person.identity_digest,
                *(digest for digest in (person.phone_digest, person.email_digest) if digest),
            }

            assert observed_account_identifier_keys == account_identifier_keys
            assert observed_membership_keys == membership_keys
            assert people_for_update_indices
            assert account_for_update_indices
            assert identifier_for_update_indices
            assert contact_dml_indices
            assert account_identifier_lock_indices[-1] < people_for_update_indices[0]
            assert max(people_for_update_indices) < membership_lock_indices[0]
            assert membership_lock_indices[-1] < min(account_for_update_indices)
            assert membership_lock_indices[-1] < min(identifier_for_update_indices)
            assert membership_lock_indices[-1] < min(contact_dml_indices)
            assert not [
                index
                for index in people_for_update_indices
                if index > membership_lock_indices[-1]
            ]
            assert all(secret not in captured_sql for secret in secret_values)
            assert all(secret not in captured_parameters for secret in secret_values)
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(AuditEvent).where(AuditEvent.resource_id == scenario.account_id)
            )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.id == scenario.challenge_id
                )
            )
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id == scenario.account_id
                )
            )
            person_id = cleanup.scalar(
                select(UserAccount.person_id).where(UserAccount.id == scenario.account_id)
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id == scenario.account_id))
            if person_id is not None:
                cleanup.execute(delete(Person).where(Person.id == person_id))


def test_two_accounts_racing_one_phone_have_one_typed_winner(
    postgres_engine: Engine,
) -> None:
    scenarios = _setup(postgres_engine)
    barrier = threading.Barrier(2)

    def verify(values: tuple[str, str, str]) -> str:
        account_id_raw, challenge_id_raw, code = values
        account_id = UUID(account_id_raw)
        challenge_id = UUID(challenge_id_raw)
        sender = CapturingVerificationSender(messages=[])
        with Session(postgres_engine) as db:
            account = db.get(UserAccount, account_id)
            assert account is not None
            person = db.get(Person, account.person_id)
            assert person is not None
            context = AuthContext(
                session=AuthSession(
                    user_id=account.id,
                    token_digest=f"identifier-race-token-{account.id}",
                    csrf_digest=f"identifier-race-csrf-{account.id}",
                    expires_at=NOW + timedelta(hours=1),
                ),
                account=account,
                person=person,
            )
            barrier.wait(timeout=5)
            try:
                _auth(db).verify_identifier_addition(
                    context,
                    challenge_id=challenge_id,
                    code=code,
                    identifier_kind="phone",
                    identifier=TARGET_PHONE,
                    current_password=IDENTIFIER_PASSWORD,
                    request_id=f"identifier-race-{account_id}",
                    verification=_verification(db, sender),
                )
            except AccountIdentifierUnavailable:
                assert db.scalar(select(func.count(UserAccount.id))) >= 2
                return "unavailable"
            assert db.scalar(select(func.count(UserAccount.id))) >= 2
            return "created"

    account_ids = [UUID(values[0]) for values in scenarios]
    challenge_ids = [UUID(values[1]) for values in scenarios]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(verify, scenarios))

        assert sorted(outcomes) == ["created", "unavailable"]
        target = normalize_registration_identifier("phone", TARGET_PHONE)
        login_digest = login_identifier_digest(
            LookupDigest(FIELD_KEY),
            NormalizedLoginIdentifier(target.kind, target.value),
        )
        with Session(postgres_engine) as verification:
            assert verification.scalar(
                select(func.count(AccountLoginIdentifier.id)).where(
                    AccountLoginIdentifier.identifier_digest == login_digest
                )
            ) == 1
            assert verification.scalar(
                select(func.count(Person.id))
                .join(UserAccount, UserAccount.person_id == Person.id)
                .where(
                    UserAccount.id.in_(account_ids),
                    Person.phone_ciphertext.is_not(None),
                    Person.phone_digest
                    == LookupDigest(FIELD_KEY).digest(TARGET_PHONE),
                )
            ) == 1
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(AuditEvent).where(AuditEvent.resource_id.in_(account_ids))
            )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.id.in_(challenge_ids)
                )
            )
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id.in_(account_ids)
                )
            )
            person_ids = list(
                cleanup.scalars(
                    select(UserAccount.person_id).where(UserAccount.id.in_(account_ids))
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))
