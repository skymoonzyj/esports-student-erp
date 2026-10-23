from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService, AuthService
from app.identity.models import Person
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.rbac.models import UserRole
from app.registration.consents import registration_terms_digest
from app.registration.delivery import CapturingVerificationSender
from app.registration.finalization_service import (
    RegistrationFinalizationService,
    RegistrationFinalizationUnavailable,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.matching_service import RegistrationMatch
from app.registration.models import (
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationSession,
    RegistrationVerificationChallenge,
)
from app.registration.review_service import (
    RegistrationReviewNotFound,
    RegistrationReviewService,
)
from app.registration.session_service import (
    RegistrationSessionService,
    RegistrationSessionUnavailable,
)
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
    VerificationTarget,
    VerificationTargetMismatch,
    registration_advisory_lock_key,
    registration_credential_lock_keys,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 17, 8, tzinfo=UTC)
FIELD_KEY = bytes(range(32))
VERIFICATION_KEY = bytes(range(1, 33))
SESSION_KEY = bytes(range(2, 34))
OLD_PASSWORD = "Old2026PassAa"
NEW_PASSWORD = "New2026PassAa"


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
    *,
    now: datetime = NOW,
) -> RegistrationVerificationService:
    return RegistrationVerificationService(
        db,
        lookup=LookupDigest(FIELD_KEY),
        verification_pepper=VERIFICATION_KEY,
        sender=sender,
        app_env="test",
        clock=lambda: now,
    )


def _sessions(db: Session) -> RegistrationSessionService:
    return RegistrationSessionService(
        db,
        token_pepper=SESSION_KEY,
        session_ttl_minutes=480,
        clock=lambda: NOW,
    )


def _finalization(db: Session) -> RegistrationFinalizationService:
    return RegistrationFinalizationService(
        db,
        cipher=EnvelopeCipher(FIELD_KEY),
        lookup=LookupDigest(FIELD_KEY),
        clock=lambda: NOW,
    )


def _review(db: Session) -> RegistrationReviewService:
    return RegistrationReviewService(
        db,
        cipher=EnvelopeCipher(FIELD_KEY),
        lookup=LookupDigest(FIELD_KEY),
        finalization=_finalization(db),
        clock=lambda: NOW,
    )


@dataclass(frozen=True, slots=True)
class ResetRaceScenario:
    person_id: UUID
    account_id: UUID
    application_id: UUID
    registration_session_id: UUID
    registration_challenge_id: UUID
    registration_code: str
    reset_challenge_id: UUID
    reset_code: str
    identifier_digest: str
    identity_digest: str
    phone: str


def _setup_scenario(engine: Engine, *, sequence: int) -> ResetRaceScenario:
    phone = f"13800009{sequence:03d}"
    email = f"recovery-race-{sequence}@example.com"
    identity_number = make_cn_id("19900102", sequence)
    cipher = EnvelopeCipher(FIELD_KEY)
    lookup = LookupDigest(FIELD_KEY)
    with Session(engine) as setup, setup.begin():
        identity = IdentityService(setup, cipher=cipher, lookup=lookup)
        person = identity.create_base_person(
            PersonBaseInput(
                full_name=f"密码重置并发人员{sequence}",
                identity_number=identity_number,
                sex="male",
                date_of_birth=date(1990, 1, 2),
                phone=phone,
                email=email,
            )
        )
        created = AccountService(
            setup,
            identity_service=identity,
            passwords=PasswordService(),
            reference_date_provider=lambda: NOW.date(),
        ).create_account(
            AccountCreate(person_id=person.id, login_phone=phone, email=email)
        )
        account = setup.get(UserAccount, created.account.id)
        assert account is not None
        account.password_hash = PasswordService().hash(OLD_PASSWORD)
        account.force_password_change = False
        identifier = normalize_registration_identifier("phone", phone)
        application = RegistrationApplication(
            primary_purpose="student",
            state="ready_to_finalize",
            verified_identifier_kind="phone",
            verified_identifier_digest=registration_identifier_digest(
                lookup,
                identifier,
            ),
            verified_identifier_ciphertext=cipher.encrypt(
                identifier.value,
                field_name="registration_identifier",
            ),
            password_hash=PasswordService().hash(f"Reg{sequence}PassAa"),
            full_name=person.full_name,
            identity_ciphertext=cipher.encrypt(
                identity_number,
                field_name="registration_identity",
            ),
            identity_digest=lookup.digest(identity_number),
            sex="male",
            date_of_birth=date(1990, 1, 2),
            expires_at=NOW + timedelta(days=30),
        )
        setup.add(application)
        setup.flush()
        terms_digest = registration_terms_digest("2026-08")
        assert terms_digest is not None
        setup.add(
            RegistrationConsentAcceptance(
                registration_application_id=application.id,
                account_id=None,
                consent_type="registration_terms",
                policy_version="2026-08",
                content_digest=terms_digest,
                accepted_at=NOW,
                request_id=f"00000000-0000-4000-8000-{sequence:012d}",
                source="self_registration",
            )
        )
        registration_session = RegistrationSession(
            application_id=application.id,
            token_digest=f"race-registration-token-{sequence}",
            csrf_digest=f"race-registration-csrf-{sequence}",
            expires_at=NOW + timedelta(hours=1),
        )
        setup.add(registration_session)
        setup.flush()
        person_id = person.id
        account_id = account.id
        application_id = application.id
        registration_session_id = registration_session.id
        identifier_digest = application.verified_identifier_digest
        identity_digest = application.identity_digest
        assert identifier_digest is not None
        assert identity_digest is not None

    registration_sender = CapturingVerificationSender(messages=[])
    with Session(engine) as registration_db:
        registration_challenge = _verification(
            registration_db,
            registration_sender,
            now=NOW - timedelta(minutes=2),
        ).issue(
            identifier_kind="phone",
            identifier=phone,
            purpose="registration",
            request_source=f"127.0.1.{sequence % 200 + 1}",
            application_id=application_id,
        )
    reset_sender = CapturingVerificationSender(messages=[])
    with Session(engine) as reset_db:
        reset_challenge = _auth(reset_db).issue_password_reset(
            identifier=phone,
            request_source=f"127.0.2.{sequence % 200 + 1}",
            verification=_verification(reset_db, reset_sender),
        )
    return ResetRaceScenario(
        person_id=person_id,
        account_id=account_id,
        application_id=application_id,
        registration_session_id=registration_session_id,
        registration_challenge_id=registration_challenge.challenge_id,
        registration_code=registration_sender.code_for(
            registration_challenge.challenge_id
        ),
        reset_challenge_id=reset_challenge.challenge_id,
        reset_code=reset_sender.code_for(reset_challenge.challenge_id),
        identifier_digest=identifier_digest,
        identity_digest=identity_digest,
        phone=phone,
    )


def _cleanup_scenario(engine: Engine, scenario: ResetRaceScenario) -> None:
    with Session(engine) as cleanup, cleanup.begin():
        cleanup.execute(
            text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
        )
        cleanup.execute(
            delete(AuditEvent).where(
                (AuditEvent.resource_id == scenario.application_id)
                | (AuditEvent.resource_id == scenario.account_id)
                | (AuditEvent.actor_user_id == scenario.account_id)
            )
        )
        cleanup.execute(
            text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
        )
        cleanup.execute(
            delete(RegistrationVerificationChallenge).where(
                (RegistrationVerificationChallenge.application_id == scenario.application_id)
                | (RegistrationVerificationChallenge.account_id == scenario.account_id)
            )
        )
        cleanup.execute(
            delete(RegistrationConsentAcceptance).where(
                (RegistrationConsentAcceptance.registration_application_id
                 == scenario.application_id)
                | (RegistrationConsentAcceptance.account_id == scenario.account_id)
            )
        )
        cleanup.execute(
            delete(RegistrationSession).where(
                RegistrationSession.application_id == scenario.application_id
            )
        )
        cleanup.execute(
            delete(RegistrationApplication).where(
                RegistrationApplication.id == scenario.application_id
            )
        )
        cleanup.execute(
            delete(AuthSession).where(AuthSession.user_id == scenario.account_id)
        )
        cleanup.execute(delete(UserRole).where(UserRole.user_id == scenario.account_id))
        cleanup.execute(
            delete(AccountLoginIdentifier).where(
                AccountLoginIdentifier.account_id == scenario.account_id
            )
        )
        cleanup.execute(
            delete(UserAccount).where(UserAccount.id == scenario.account_id)
        )
        cleanup.execute(delete(Person).where(Person.id == scenario.person_id))


def _pause_after_advisory_locks(
    connection: Connection,
    *,
    target_keys: set[int],
    owned: threading.Event,
    release: threading.Event,
) -> object:
    acquired_keys: set[int] = set()

    def pause(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" not in statement.lower():
            return
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        acquired_keys.update(
            value
            for value in values
            if isinstance(value, int) and value in target_keys
        )
        if acquired_keys != target_keys or owned.is_set():
            return
        owned.set()
        if not release.wait(timeout=5):
            raise AssertionError("timed out waiting to release advisory locks")

    event.listen(connection, "after_cursor_execute", pause)
    return pause


def _notify_before_advisory_key(
    connection: Connection,
    *,
    target_keys: set[int],
    attempted: threading.Event,
) -> object:
    def notify(
        _connection: object,
        _cursor: object,
        statement: str,
        parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_xact_lock" not in statement.lower():
            return
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        if any(value in target_keys for value in values):
            attempted.set()

    event.listen(connection, "before_cursor_execute", notify)
    return notify


def _complete_reset(db: Session, scenario: ResetRaceScenario) -> None:
    _auth(db).complete_password_reset(
        challenge_id=scenario.reset_challenge_id,
        code=scenario.reset_code,
        new_password=NEW_PASSWORD,
        request_id=(
            "00000000-0000-4000-8000-"
            f"{scenario.application_id.int % 10**12:012d}"
        ),
        verification=_verification(db, CapturingVerificationSender(messages=[])),
    )


def test_late_registration_session_is_revoked_after_password_reset(
    postgres_engine: Engine,
) -> None:
    scenario = _setup_scenario(postgres_engine, sequence=901)
    registration_owned = threading.Event()
    release_registration = threading.Event()
    reset_waiting = threading.Event()
    reset_done = threading.Event()
    application_lock_key = registration_advisory_lock_key(
        "registration-application-identifier",
        scenario.identifier_digest,
    )

    def issue_late_session() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            with db.begin():
                db.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            registration_advisory_lock_key(
                                "registration-application-identifier",
                                scenario.identifier_digest,
                            )
                        )
                    )
                )
                issued = _sessions(db).issue_in_transaction(scenario.application_id)
                registration_owned.set()
                if not release_registration.wait(timeout=5):
                    raise AssertionError("timed out releasing late registration session")
            reusable = db.scalar(select(func.count(RegistrationApplication.id))) >= 1
            return str(issued.context.session.id), reusable

    def reset() -> bool:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = _notify_before_advisory_key(
                connection,
                target_keys={application_lock_key},
                attempted=reset_waiting,
            )
            try:
                _complete_reset(db, scenario)
            finally:
                event.remove(connection, "before_cursor_execute", listener)
            reset_done.set()
            return db.scalar(select(func.count(RegistrationApplication.id))) >= 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            registration_future = executor.submit(issue_late_session)
            assert registration_owned.wait(timeout=5)
            reset_future = executor.submit(reset)
            assert reset_waiting.wait(timeout=5)
            assert reset_done.wait(timeout=0.2) is False
            release_registration.set()
            _session_id, registration_reusable = registration_future.result(timeout=5)
            reset_reusable = reset_future.result(timeout=5)

        assert registration_reusable is True
        assert reset_reusable is True
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            assert application is not None and application.state == "expired"
            assert verification.scalar(
                select(func.count(RegistrationSession.id)).where(
                    RegistrationSession.application_id == scenario.application_id,
                    RegistrationSession.revoked_at.is_(None),
                )
            ) == 0
    finally:
        release_registration.set()
        _cleanup_scenario(postgres_engine, scenario)


@pytest.mark.parametrize("winner", ["challenge", "reset"])
def test_registration_challenge_issue_and_password_reset_share_lock_order(
    postgres_engine: Engine,
    winner: str,
) -> None:
    scenario = _setup_scenario(
        postgres_engine,
        sequence=911 if winner == "challenge" else 913,
    )
    winner_owned = threading.Event()
    release_winner = threading.Event()
    loser_waiting = threading.Event()
    loser_done = threading.Event()
    issue_application_locked = threading.Event()
    identifier_key = registration_advisory_lock_key(
        "identifier",
        scenario.identifier_digest,
    )
    reset_keys = set(
        registration_credential_lock_keys({scenario.identifier_digest})
    )

    def issue_challenge() -> tuple[str, bool]:
        sender = CapturingVerificationSender(messages=[])
        with Session(postgres_engine) as db:
            connection = db.connection()

            def observe_issue(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                normalized = " ".join(statement.casefold().split())
                if isinstance(parameters, dict):
                    values = tuple(parameters.values())
                elif isinstance(parameters, (list, tuple)):
                    values = tuple(parameters)
                else:
                    values = (parameters,)
                if (
                    "pg_advisory_xact_lock" in normalized
                    and identifier_key in values
                    and winner != "challenge"
                ):
                    loser_waiting.set()
                if (
                    "from registration_applications" in normalized
                    and "for update" in normalized
                ):
                    issue_application_locked.set()

            def pause_after_issue_lock(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if winner != "challenge" or winner_owned.is_set():
                    return
                if isinstance(parameters, dict):
                    values = tuple(parameters.values())
                elif isinstance(parameters, (list, tuple)):
                    values = tuple(parameters)
                else:
                    values = (parameters,)
                if (
                    "pg_advisory_xact_lock" in statement.casefold()
                    and identifier_key in values
                ):
                    winner_owned.set()
                    if not release_winner.wait(timeout=5):
                        raise AssertionError("timed out releasing challenge issue")

            event.listen(connection, "before_cursor_execute", observe_issue)
            event.listen(connection, "after_cursor_execute", pause_after_issue_lock)
            try:
                with db.begin_nested():
                    service = _verification(
                        db,
                        sender,
                        now=NOW + timedelta(seconds=61),
                    )
                    service.lock_issue_limits_in_transaction(
                        identifier_kind="phone",
                        identifier=scenario.phone,
                        request_source=(
                            "127.0.5.11"
                            if winner == "challenge"
                            else "127.0.5.13"
                        ),
                    )
                    application = db.scalar(
                        select(RegistrationApplication)
                        .where(
                            RegistrationApplication.id == scenario.application_id
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if application is None or application.state == "expired":
                        outcome = "unavailable"
                    else:
                        service.issue_in_transaction(
                            identifier_kind="phone",
                            identifier=scenario.phone,
                            purpose="registration",
                            request_source=(
                                "127.0.5.11"
                                if winner == "challenge"
                                else "127.0.5.13"
                            ),
                            application_id=scenario.application_id,
                        )
                        outcome = "issued"
                db.commit()
            finally:
                event.remove(connection, "before_cursor_execute", observe_issue)
                event.remove(
                    connection,
                    "after_cursor_execute",
                    pause_after_issue_lock,
                )
            if winner != "challenge":
                loser_done.set()
            return outcome, db.scalar(select(1)) == 1

    def reset_password() -> bool:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = (
                _pause_after_advisory_locks(
                    connection,
                    target_keys=reset_keys,
                    owned=winner_owned,
                    release=release_winner,
                )
                if winner == "reset"
                else _notify_before_advisory_key(
                    connection,
                    target_keys={identifier_key},
                    attempted=loser_waiting,
                )
            )
            try:
                _complete_reset(db, scenario)
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute"
                    if winner == "reset"
                    else "before_cursor_execute",
                    listener,
                )
            if winner != "reset":
                loser_done.set()
            return db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            if winner == "challenge":
                issue_future = executor.submit(issue_challenge)
                assert winner_owned.wait(timeout=5)
                reset_future = executor.submit(reset_password)
            else:
                reset_future = executor.submit(reset_password)
                assert winner_owned.wait(timeout=5)
                issue_future = executor.submit(issue_challenge)
            assert loser_waiting.wait(timeout=5)
            if winner == "reset":
                assert issue_application_locked.is_set() is False
            assert loser_done.wait(timeout=0.2) is False
            release_winner.set()
            issue_outcome, issue_reusable = issue_future.result(timeout=8)
            reset_reusable = reset_future.result(timeout=8)

        assert issue_reusable is True
        assert reset_reusable is True
        assert issue_outcome == (
            "issued" if winner == "challenge" else "unavailable"
        )
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            assert application is not None and application.state == "expired"
    finally:
        release_winner.set()
        _cleanup_scenario(postgres_engine, scenario)


def test_reset_first_invalidates_old_registration_challenge(
    postgres_engine: Engine,
) -> None:
    scenario = _setup_scenario(postgres_engine, sequence=903)
    reset_owned = threading.Event()
    release_reset = threading.Event()
    verification_waiting = threading.Event()
    verification_done = threading.Event()
    application_lock_key = registration_advisory_lock_key(
        "registration-application-identifier",
        scenario.identifier_digest,
    )

    def reset() -> bool:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = _pause_after_advisory_locks(
                connection,
                target_keys=set(
                    registration_credential_lock_keys(
                        {scenario.identifier_digest}
                    )
                ),
                owned=reset_owned,
                release=release_reset,
            )
            try:
                _complete_reset(db, scenario)
            finally:
                event.remove(connection, "after_cursor_execute", listener)
            return db.scalar(select(func.count(RegistrationApplication.id))) >= 1

    def verify_old_challenge() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = _notify_before_advisory_key(
                connection,
                target_keys={application_lock_key},
                attempted=verification_waiting,
            )

            def prepare() -> VerificationTarget:
                db.execute(
                    select(
                        func.pg_advisory_xact_lock(
                            registration_advisory_lock_key(
                                "registration-application-identifier",
                                scenario.identifier_digest,
                            )
                        )
                    )
                )
                application = db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == scenario.application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None or application.state == "expired":
                    raise VerificationTargetMismatch(
                        "registration application is unavailable"
                    )
                return VerificationTarget(
                    application_id=scenario.application_id,
                    account_id=None,
                    identifier_kind="phone",
                    identifier_digest=scenario.identifier_digest,
                    purpose="registration",
                )

            try:
                try:
                    _verification(
                        db,
                        CapturingVerificationSender(messages=[]),
                    ).consume_with(
                        scenario.registration_challenge_id,
                        scenario.registration_code,
                        prepare_target=prepare,
                        on_success=lambda _consumed: _sessions(
                            db
                        ).issue_in_transaction(scenario.application_id),
                    )
                except (
                    RegistrationSessionUnavailable,
                    VerificationInvalidOrExpired,
                    VerificationTargetMismatch,
                ):
                    outcome = "invalid"
                else:
                    outcome = "issued"
            finally:
                event.remove(connection, "before_cursor_execute", listener)
            verification_done.set()
            reusable = db.scalar(select(func.count(RegistrationApplication.id))) >= 1
            return outcome, reusable

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reset_future = executor.submit(reset)
            assert reset_owned.wait(timeout=5)
            verification_future = executor.submit(verify_old_challenge)
            assert verification_waiting.wait(timeout=5)
            assert verification_done.wait(timeout=0.2) is False
            release_reset.set()
            reset_reusable = reset_future.result(timeout=5)
            verification_outcome, verification_reusable = verification_future.result(
                timeout=5
            )

        assert reset_reusable is True
        assert verification_outcome == "invalid"
        assert verification_reusable is True
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            challenge = verification.get(
                RegistrationVerificationChallenge,
                scenario.registration_challenge_id,
            )
            assert application is not None and application.state == "expired"
            assert challenge is not None
            assert challenge.consumed_at is None
            assert challenge.expires_at == NOW
            assert verification.scalar(
                select(func.count(RegistrationSession.id)).where(
                    RegistrationSession.application_id == scenario.application_id,
                    RegistrationSession.revoked_at.is_(None),
                )
            ) == 0
    finally:
        release_reset.set()
        _cleanup_scenario(postgres_engine, scenario)


def test_wrong_purpose_registration_verification_does_not_deadlock_password_reset(
    postgres_engine: Engine,
) -> None:
    scenario = _setup_scenario(postgres_engine, sequence=919)
    reset_challenge_owned = threading.Event()
    release_reset = threading.Event()
    verification_progressed = threading.Event()
    verification_application_key_owned = threading.Event()
    application_lock_key = registration_advisory_lock_key(
        "registration-application-identifier",
        scenario.identifier_digest,
    )

    def reset_password() -> tuple[str, str | None, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()

            def pause_after_reset_challenge_lock(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                normalized = " ".join(statement.casefold().split())
                if isinstance(parameters, dict):
                    values = tuple(parameters.values())
                elif isinstance(parameters, (list, tuple)):
                    values = tuple(parameters)
                else:
                    values = (parameters,)
                if (
                    "from registration_verification_challenges" not in normalized
                    or "for update" not in normalized
                    or scenario.reset_challenge_id not in values
                    or reset_challenge_owned.is_set()
                ):
                    return
                reset_challenge_owned.set()
                if not release_reset.wait(timeout=5):
                    raise AssertionError("timed out releasing password reset challenge")

            event.listen(
                connection,
                "after_cursor_execute",
                pause_after_reset_challenge_lock,
            )
            try:
                try:
                    _complete_reset(db, scenario)
                except DBAPIError as exc:
                    outcome = "database_error"
                    sqlstate = getattr(exc.orig, "sqlstate", None)
                else:
                    outcome = "reset"
                    sqlstate = None
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute",
                    pause_after_reset_challenge_lock,
                )
            return outcome, sqlstate, db.scalar(select(1)) == 1

    def verify_reset_challenge_as_registration() -> tuple[str, str | None, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()

            def observe_application_key(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if "pg_advisory_xact_lock" not in statement.casefold():
                    return
                if isinstance(parameters, dict):
                    values = tuple(parameters.values())
                elif isinstance(parameters, (list, tuple)):
                    values = tuple(parameters)
                else:
                    values = (parameters,)
                if application_lock_key in values:
                    verification_application_key_owned.set()
                    verification_progressed.set()

            def prepare() -> VerificationTarget:
                db.execute(select(func.pg_advisory_xact_lock(application_lock_key)))
                application = db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == scenario.application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None or application.state == "expired":
                    raise VerificationTargetMismatch(
                        "registration application is unavailable"
                    )
                return VerificationTarget(
                    application_id=scenario.application_id,
                    account_id=None,
                    identifier_kind="phone",
                    identifier_digest=scenario.identifier_digest,
                    purpose="registration",
                )

            event.listen(connection, "after_cursor_execute", observe_application_key)
            try:
                try:
                    _verification(
                        db,
                        CapturingVerificationSender(messages=[]),
                    ).consume_with(
                        scenario.reset_challenge_id,
                        scenario.reset_code,
                        expected_purpose="registration",
                        prepare_target=prepare,
                    )
                except VerificationInvalidOrExpired:
                    outcome = "invalid"
                    sqlstate = None
                except DBAPIError as exc:
                    outcome = "database_error"
                    sqlstate = getattr(exc.orig, "sqlstate", None)
                else:
                    outcome = "consumed"
                    sqlstate = None
            finally:
                verification_progressed.set()
                event.remove(
                    connection,
                    "after_cursor_execute",
                    observe_application_key,
                )
            return outcome, sqlstate, db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reset_future = executor.submit(reset_password)
            assert reset_challenge_owned.wait(timeout=5)
            verification_future = executor.submit(
                verify_reset_challenge_as_registration
            )
            assert verification_progressed.wait(timeout=5)
            release_reset.set()
            reset_outcome, reset_sqlstate, reset_reusable = reset_future.result(
                timeout=8
            )
            (
                verification_outcome,
                verification_sqlstate,
                verification_reusable,
            ) = verification_future.result(timeout=8)

        assert (reset_sqlstate, verification_sqlstate) == (None, None)
        assert reset_outcome == "reset"
        assert reset_reusable is True
        assert verification_outcome == "invalid"
        assert verification_reusable is True
        assert verification_application_key_owned.is_set() is False
    finally:
        release_reset.set()
        _cleanup_scenario(postgres_engine, scenario)


def test_finalization_rejects_committed_snapshot_change_before_row_lock(
    postgres_engine: Engine,
) -> None:
    scenario = _setup_scenario(postgres_engine, sequence=909)
    cipher = EnvelopeCipher(FIELD_KEY)
    lookup = LookupDigest(FIELD_KEY)
    replacement = normalize_registration_identifier("phone", "13800009910")
    replacement_digest = registration_identifier_digest(lookup, replacement)
    replacement_ciphertext = cipher.encrypt(
        replacement.value,
        field_name="registration_identifier",
    )
    injected = False

    try:
        with Session(postgres_engine) as db:
            connection = db.connection()

            def replace_identifier_before_row_lock(
                _connection: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                nonlocal injected
                normalized = " ".join(statement.casefold().split())
                if (
                    injected
                    or not normalized.startswith("select")
                    or "from registration_applications" not in normalized
                    or "for update" not in normalized
                ):
                    return
                injected = True
                with Session(postgres_engine) as concurrent, concurrent.begin():
                    result = concurrent.execute(
                        update(RegistrationApplication)
                        .where(
                            RegistrationApplication.id == scenario.application_id
                        )
                        .values(
                            verified_identifier_digest=replacement_digest,
                            verified_identifier_ciphertext=replacement_ciphertext,
                        )
                    )
                    assert result.rowcount == 1

            event.listen(
                connection,
                "before_cursor_execute",
                replace_identifier_before_row_lock,
            )
            try:
                with pytest.raises(RegistrationFinalizationUnavailable):
                    _finalization(db).finalize(
                        scenario.application_id,
                        request_id="finalization-committed-stale-snapshot",
                    )
            finally:
                event.remove(
                    connection,
                    "before_cursor_execute",
                    replace_identifier_before_row_lock,
                )
            assert db.scalar(select(1)) == 1

        assert injected is True
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            assert application is not None
            assert application.state == "ready_to_finalize"
            assert application.verified_identifier_digest == replacement_digest
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.request_id
                    == "finalization-committed-stale-snapshot"
                )
            ) == 0
    finally:
        _cleanup_scenario(postgres_engine, scenario)


@pytest.mark.parametrize("winner", ["review", "reset"])
def test_conflict_review_and_password_reset_share_claim_lock_order(
    postgres_engine: Engine,
    winner: str,
) -> None:
    scenario = _setup_scenario(
        postgres_engine,
        sequence=915 if winner == "review" else 917,
    )
    with Session(postgres_engine) as setup, setup.begin():
        setup.execute(
            update(RegistrationApplication)
            .where(RegistrationApplication.id == scenario.application_id)
            .values(state="conflict", conflict_code="manual_review_required")
        )
    winner_owned = threading.Event()
    release_winner = threading.Event()
    loser_waiting = threading.Event()
    loser_done = threading.Event()
    review_application_locked = threading.Event()
    reset_keys = set(
        registration_credential_lock_keys({scenario.identifier_digest})
    )
    review_keys = set(
        registration_credential_lock_keys(
            {scenario.identifier_digest},
            identity_digests={scenario.identity_digest},
        )
    )

    def resolve_conflict() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()

            def observe_application_lock(
                _connection: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                normalized = " ".join(statement.casefold().split())
                if (
                    "from registration_applications" in normalized
                    and "for update" in normalized
                ):
                    review_application_locked.set()

            event.listen(
                connection,
                "before_cursor_execute",
                observe_application_lock,
            )
            listener = (
                _pause_after_advisory_locks(
                    connection,
                    target_keys=review_keys,
                    owned=winner_owned,
                    release=release_winner,
                )
                if winner == "review"
                else _notify_before_advisory_key(
                    connection,
                    target_keys=reset_keys,
                    attempted=loser_waiting,
                )
            )
            try:
                try:
                    _review(db).resolve(
                        scenario.application_id,
                        candidate_person_id=scenario.person_id,
                        actor_user_id=scenario.account_id,
                        reason="实名字段复核一致允许认领",
                        request_id=f"conflict-review-reset-race-{winner}",
                    )
                except RegistrationReviewNotFound:
                    outcome = "unavailable"
                else:
                    outcome = "finalized"
            finally:
                event.remove(
                    connection,
                    "before_cursor_execute",
                    observe_application_lock,
                )
                event.remove(
                    connection,
                    "after_cursor_execute"
                    if winner == "review"
                    else "before_cursor_execute",
                    listener,
                )
            if winner != "review":
                loser_done.set()
            return outcome, db.scalar(select(1)) == 1

    def reset_password() -> bool:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = (
                _pause_after_advisory_locks(
                    connection,
                    target_keys=reset_keys,
                    owned=winner_owned,
                    release=release_winner,
                )
                if winner == "reset"
                else _notify_before_advisory_key(
                    connection,
                    target_keys=reset_keys,
                    attempted=loser_waiting,
                )
            )
            try:
                _complete_reset(db, scenario)
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute"
                    if winner == "reset"
                    else "before_cursor_execute",
                    listener,
                )
            if winner != "reset":
                loser_done.set()
            return db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            if winner == "review":
                review_future = executor.submit(resolve_conflict)
                assert winner_owned.wait(timeout=5)
                reset_future = executor.submit(reset_password)
            else:
                reset_future = executor.submit(reset_password)
                assert winner_owned.wait(timeout=5)
                review_future = executor.submit(resolve_conflict)
            assert loser_waiting.wait(timeout=5)
            if winner == "reset":
                assert review_application_locked.is_set() is False
            assert loser_done.wait(timeout=0.2) is False
            release_winner.set()
            review_outcome, review_reusable = review_future.result(timeout=8)
            reset_reusable = reset_future.result(timeout=8)

        assert review_reusable is True
        assert reset_reusable is True
        assert review_outcome == (
            "finalized" if winner == "review" else "unavailable"
        )
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            assert application is not None
            assert application.state == (
                "finalized" if winner == "review" else "expired"
            )
    finally:
        release_winner.set()
        _cleanup_scenario(postgres_engine, scenario)


@pytest.mark.parametrize("winner", ["marker", "reset"])
def test_concurrent_conflict_marker_and_password_reset_have_deterministic_winner(
    postgres_engine: Engine,
    winner: str,
) -> None:
    scenario = _setup_scenario(
        postgres_engine,
        sequence=921 if winner == "marker" else 923,
    )
    with Session(postgres_engine) as setup, setup.begin():
        result = setup.execute(
            update(RegistrationApplication)
            .where(RegistrationApplication.id == scenario.application_id)
            .values(state="conflict", conflict_code="split_identifier_match")
        )
        assert result.rowcount == 1
    with Session(postgres_engine) as verification:
        formal_counts_before = (
            verification.scalar(select(func.count(Person.id))),
            verification.scalar(select(func.count(UserAccount.id))),
            verification.scalar(select(func.count(AccountLoginIdentifier.id))),
            verification.scalar(select(func.count()).select_from(UserRole)),
        )
    winner_owned = threading.Event()
    release_winner = threading.Event()
    loser_waiting = threading.Event()
    loser_done = threading.Event()
    marker_application_locked = threading.Event()
    marker_lock_order: list[int] = []
    reset_keys = set(
        registration_credential_lock_keys({scenario.identifier_digest})
    )
    marker_keys = registration_credential_lock_keys(
        {scenario.identifier_digest},
        identity_digests={scenario.identity_digest},
    )

    def mark_conflict() -> tuple[str, str | None, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()

            def observe_marker_progress(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                normalized = " ".join(statement.casefold().split())
                if (
                    "from registration_applications" in normalized
                    and "for update" in normalized
                ):
                    marker_application_locked.set()
                if "pg_advisory_xact_lock" not in normalized:
                    return
                if isinstance(parameters, dict):
                    values = tuple(parameters.values())
                elif isinstance(parameters, (list, tuple)):
                    values = tuple(parameters)
                else:
                    values = (parameters,)
                marker_lock_order.extend(
                    value
                    for value in values
                    if isinstance(value, int) and value in marker_keys
                )

            event.listen(
                connection,
                "after_cursor_execute",
                observe_marker_progress,
            )
            listener = (
                _pause_after_advisory_locks(
                    connection,
                    target_keys=set(marker_keys),
                    owned=winner_owned,
                    release=release_winner,
                )
                if winner == "marker"
                else _notify_before_advisory_key(
                    connection,
                    target_keys=reset_keys,
                    attempted=loser_waiting,
                )
            )
            try:
                try:
                    marked = _finalization(db)._mark_concurrent_conflict(
                        scenario.application_id
                    )
                except DBAPIError as exc:
                    outcome = "database_error"
                    sqlstate = getattr(exc.orig, "sqlstate", None)
                else:
                    outcome = "marked" if marked else "rejected"
                    sqlstate = None
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute",
                    observe_marker_progress,
                )
                event.remove(
                    connection,
                    "after_cursor_execute"
                    if winner == "marker"
                    else "before_cursor_execute",
                    listener,
                )
            if winner != "marker":
                loser_done.set()
            return outcome, sqlstate, db.scalar(select(1)) == 1

    def reset_password() -> tuple[str, str | None, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = (
                _pause_after_advisory_locks(
                    connection,
                    target_keys=reset_keys,
                    owned=winner_owned,
                    release=release_winner,
                )
                if winner == "reset"
                else _notify_before_advisory_key(
                    connection,
                    target_keys=reset_keys,
                    attempted=loser_waiting,
                )
            )
            try:
                try:
                    _complete_reset(db, scenario)
                except DBAPIError as exc:
                    outcome = "database_error"
                    sqlstate = getattr(exc.orig, "sqlstate", None)
                else:
                    outcome = "reset"
                    sqlstate = None
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute"
                    if winner == "reset"
                    else "before_cursor_execute",
                    listener,
                )
            if winner != "reset":
                loser_done.set()
            return outcome, sqlstate, db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            if winner == "marker":
                marker_future = executor.submit(mark_conflict)
                assert winner_owned.wait(timeout=5)
                assert marker_lock_order == marker_keys
                assert marker_application_locked.is_set() is False
                reset_future = executor.submit(reset_password)
            else:
                reset_future = executor.submit(reset_password)
                assert winner_owned.wait(timeout=5)
                marker_future = executor.submit(mark_conflict)
            assert loser_waiting.wait(timeout=5)
            if winner == "reset":
                assert marker_application_locked.is_set() is False
            assert loser_done.wait(timeout=0.2) is False
            release_winner.set()
            marker_outcome, marker_sqlstate, marker_reusable = marker_future.result(
                timeout=8
            )
            reset_outcome, reset_sqlstate, reset_reusable = reset_future.result(
                timeout=8
            )

        assert (marker_sqlstate, reset_sqlstate) == (None, None)
        assert marker_outcome == ("marked" if winner == "marker" else "rejected")
        assert reset_outcome == "reset"
        assert marker_reusable is True
        assert reset_reusable is True
        assert marker_lock_order == marker_keys
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            account = verification.get(UserAccount, scenario.account_id)
            assert application is not None
            assert application.state == "expired"
            assert application.conflict_code == "split_identifier_match"
            assert application.password_hash is None
            assert application.verified_identifier_digest is None
            assert application.identity_digest is None
            assert application.matched_person_id is None
            assert application.finalized_account_id is None
            assert account is not None
            assert PasswordService().verify(NEW_PASSWORD, account.password_hash)
            assert (
                verification.scalar(select(func.count(Person.id))),
                verification.scalar(select(func.count(UserAccount.id))),
                verification.scalar(select(func.count(AccountLoginIdentifier.id))),
                verification.scalar(select(func.count()).select_from(UserRole)),
            ) == formal_counts_before
            assert verification.scalar(
                select(func.count(RegistrationSession.id)).where(
                    RegistrationSession.application_id == scenario.application_id,
                    RegistrationSession.revoked_at.is_(None),
                )
            ) == 0
    finally:
        release_winner.set()
        _cleanup_scenario(postgres_engine, scenario)


def test_password_reset_wins_while_failed_finalization_revalidates_conflict_marker(
    postgres_engine: Engine,
) -> None:
    sequence = 925
    scenario = _setup_scenario(postgres_engine, sequence=sequence)
    with Session(postgres_engine) as verification:
        formal_counts_before = (
            verification.scalar(select(func.count(Person.id))),
            verification.scalar(select(func.count(UserAccount.id))),
            verification.scalar(select(func.count(AccountLoginIdentifier.id))),
            verification.scalar(select(func.count()).select_from(UserRole)),
        )

    marker_entered = threading.Event()
    allow_marker = threading.Event()
    reset_application_locked = threading.Event()
    release_reset = threading.Event()
    first_marker_step: Future[str] = Future()
    reset_keys = set(
        registration_credential_lock_keys({scenario.identifier_digest})
    )
    finalization_request_id = "failed-finalization-marker-revalidation"

    def fail_finalization_into_marker() -> tuple[Exception | None, bool]:
        with Session(postgres_engine) as db:
            service = _finalization(db)

            def force_new_person(
                _snapshot: object,
                _membership: object,
                _people: object,
            ) -> RegistrationMatch:
                return RegistrationMatch("new_person", None, None)

            service.matching.classify_locked_candidates_in_transaction = (  # type: ignore[method-assign]
                force_new_person
            )
            def observe_marker_first_step(
                _connection: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if first_marker_step.done():
                    return
                normalized = " ".join(statement.casefold().split())
                if "pg_advisory_xact_lock" in normalized:
                    first_marker_step.set_result("shared_advisory")
                elif (
                    "from registration_applications" in normalized
                    and "for update" in normalized
                ):
                    first_marker_step.set_result("application_row_lock")

            original_marker = service._mark_concurrent_conflict

            def wrapped_marker(application_id: UUID) -> bool:
                marker_entered.set()
                if not allow_marker.wait(timeout=5):
                    raise AssertionError("timed out waiting to enter conflict marker")
                marker_connection = db.connection()
                event.listen(
                    marker_connection,
                    "before_cursor_execute",
                    observe_marker_first_step,
                )
                try:
                    return original_marker(application_id)
                finally:
                    if not first_marker_step.done():
                        first_marker_step.set_result("returned_without_lock")
                    event.remove(
                        marker_connection,
                        "before_cursor_execute",
                        observe_marker_first_step,
                    )

            service._mark_concurrent_conflict = wrapped_marker  # type: ignore[method-assign]

            error: Exception | None = None
            try:
                service.finalize(
                    scenario.application_id,
                    request_id=finalization_request_id,
                )
            except Exception as exc:  # noqa: BLE001 - exact type is asserted below
                error = exc
            finally:
                allow_marker.set()
            return error, db.scalar(select(1)) == 1

    def reset_password() -> bool:
        with Session(postgres_engine) as db:
            connection = db.connection()
            acquired_reset_keys: set[int] = set()

            def pause_with_reset_application_locked(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                normalized = " ".join(statement.casefold().split())
                if "pg_advisory_xact_lock" in normalized:
                    if isinstance(parameters, dict):
                        values = tuple(parameters.values())
                    elif isinstance(parameters, (list, tuple)):
                        values = tuple(parameters)
                    else:
                        values = (parameters,)
                    acquired_reset_keys.update(
                        value
                        for value in values
                        if isinstance(value, int) and value in reset_keys
                    )
                    return
                if (
                    "from registration_applications" not in normalized
                    or "for update" not in normalized
                ):
                    return
                assert acquired_reset_keys == reset_keys
                reset_application_locked.set()
                if not release_reset.wait(timeout=5):
                    raise AssertionError(
                        "timed out waiting to release password reset application lock"
                    )

            event.listen(
                connection,
                "after_cursor_execute",
                pause_with_reset_application_locked,
            )
            try:
                _complete_reset(db, scenario)
            finally:
                release_reset.set()
                event.remove(
                    connection,
                    "after_cursor_execute",
                    pause_with_reset_application_locked,
                )
            return db.scalar(select(1)) == 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            finalization_future = executor.submit(fail_finalization_into_marker)
            assert marker_entered.wait(timeout=5)
            with Session(postgres_engine) as concurrent_marker, concurrent_marker.begin():
                result = concurrent_marker.execute(
                    update(RegistrationApplication)
                    .where(
                        RegistrationApplication.id == scenario.application_id,
                        RegistrationApplication.state == "ready_to_finalize",
                    )
                    .values(state="conflict", conflict_code="concurrent_claim")
                )
                assert result.rowcount == 1

            reset_future = executor.submit(reset_password)
            assert reset_application_locked.wait(timeout=5)
            allow_marker.set()
            assert first_marker_step.result(timeout=5) == "shared_advisory"
            release_reset.set()
            finalization_error, finalization_reusable = finalization_future.result(
                timeout=8
            )
            reset_reusable = reset_future.result(timeout=8)

        assert type(finalization_error) is RegistrationFinalizationUnavailable
        assert finalization_error.code == "registration_action_unavailable"
        assert finalization_reusable is True
        assert reset_reusable is True
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            account = verification.get(UserAccount, scenario.account_id)
            registration_challenge = verification.get(
                RegistrationVerificationChallenge,
                scenario.registration_challenge_id,
            )
            reset_challenge = verification.get(
                RegistrationVerificationChallenge,
                scenario.reset_challenge_id,
            )
            assert application is not None
            assert application.state == "expired"
            assert application.conflict_code == "concurrent_claim"
            assert application.verified_identifier_kind is None
            assert application.verified_identifier_digest is None
            assert application.verified_identifier_ciphertext is None
            assert application.password_hash is None
            assert application.full_name is None
            assert application.identity_ciphertext is None
            assert application.identity_digest is None
            assert application.sex is None
            assert application.date_of_birth is None
            assert application.review_decision is None
            assert application.reviewed_by_user_id is None
            assert application.review_reason is None
            assert application.reviewed_at is None
            assert application.matched_person_id is None
            assert application.finalized_account_id is None
            assert application.finalization_match_kind is None
            assert application.finalized_at is None
            assert account is not None
            assert PasswordService().verify(NEW_PASSWORD, account.password_hash)
            assert not PasswordService().verify(OLD_PASSWORD, account.password_hash)
            assert (
                verification.scalar(select(func.count(Person.id))),
                verification.scalar(select(func.count(UserAccount.id))),
                verification.scalar(select(func.count(AccountLoginIdentifier.id))),
                verification.scalar(select(func.count()).select_from(UserRole)),
            ) == formal_counts_before
            assert registration_challenge is not None
            assert registration_challenge.consumed_at is None
            assert registration_challenge.expires_at == NOW
            assert reset_challenge is not None
            assert reset_challenge.consumed_at == NOW
            assert verification.scalar(
                select(func.count(RegistrationSession.id)).where(
                    RegistrationSession.application_id == scenario.application_id,
                    RegistrationSession.revoked_at.is_(None),
                )
            ) == 0
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.request_id == finalization_request_id
                )
            ) == 0
    finally:
        allow_marker.set()
        release_reset.set()
        _cleanup_scenario(postgres_engine, scenario)


@pytest.mark.parametrize("winner", ["finalization", "reset"])
def test_finalization_and_password_reset_have_deterministic_winner(
    postgres_engine: Engine,
    winner: str,
) -> None:
    sequence = 905 if winner == "finalization" else 907
    scenario = _setup_scenario(postgres_engine, sequence=sequence)
    winner_owned = threading.Event()
    release_winner = threading.Event()
    loser_waiting = threading.Event()
    loser_done = threading.Event()
    shared_keys = set(
        registration_credential_lock_keys({scenario.identifier_digest})
    )

    def finalize() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = None
            if winner == "finalization":
                listener = _pause_after_advisory_locks(
                    connection,
                    target_keys=set(
                        registration_credential_lock_keys(
                            {scenario.identifier_digest},
                            identity_digests={scenario.identity_digest},
                        )
                    ),
                    owned=winner_owned,
                    release=release_winner,
                )
            else:
                listener = _notify_before_advisory_key(
                    connection,
                    target_keys=shared_keys,
                    attempted=loser_waiting,
                )
            try:
                _finalization(db).finalize(
                    scenario.application_id,
                    request_id=f"finalize-race-{sequence}",
                )
            except RegistrationFinalizationUnavailable:
                outcome = "unavailable"
            else:
                outcome = "finalized"
            finally:
                if listener is not None:
                    event.remove(
                        connection,
                        "after_cursor_execute"
                        if winner == "finalization"
                        else "before_cursor_execute",
                        listener,
                    )
            if winner != "finalization":
                loser_done.set()
            reusable = db.scalar(select(func.count(RegistrationApplication.id))) >= 1
            return outcome, reusable

    def reset() -> bool:
        with Session(postgres_engine) as db:
            connection = db.connection()
            listener = None
            if winner == "reset":
                listener = _pause_after_advisory_locks(
                    connection,
                    target_keys=shared_keys,
                    owned=winner_owned,
                    release=release_winner,
                )
            else:
                listener = _notify_before_advisory_key(
                    connection,
                    target_keys=shared_keys,
                    attempted=loser_waiting,
                )
            try:
                _complete_reset(db, scenario)
            finally:
                if listener is not None:
                    event.remove(
                        connection,
                        "after_cursor_execute"
                        if winner == "reset"
                        else "before_cursor_execute",
                        listener,
                    )
            if winner != "reset":
                loser_done.set()
            return db.scalar(select(func.count(RegistrationApplication.id))) >= 1

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            if winner == "finalization":
                finalization_future = executor.submit(finalize)
                assert winner_owned.wait(timeout=5)
                reset_future = executor.submit(reset)
            else:
                reset_future = executor.submit(reset)
                assert winner_owned.wait(timeout=5)
                finalization_future = executor.submit(finalize)
            assert loser_waiting.wait(timeout=5)
            assert loser_done.wait(timeout=0.2) is False
            release_winner.set()
            finalization_outcome, finalization_reusable = finalization_future.result(
                timeout=5
            )
            reset_reusable = reset_future.result(timeout=5)

        assert finalization_reusable is True
        assert reset_reusable is True
        with Session(postgres_engine) as verification:
            application = verification.get(
                RegistrationApplication,
                scenario.application_id,
            )
            account = verification.get(UserAccount, scenario.account_id)
            assert application is not None
            assert account is not None
            assert PasswordService().verify(NEW_PASSWORD, account.password_hash)
            if winner == "finalization":
                assert finalization_outcome == "finalized"
                assert application.state == "finalized"
                assert application.finalized_account_id == scenario.account_id
            else:
                assert finalization_outcome == "unavailable"
                assert application.state == "expired"
            assert verification.scalar(select(func.count(Person.id))) == 1
            assert verification.scalar(select(func.count(UserAccount.id))) == 1
            assert verification.scalar(
                select(func.count(RegistrationSession.id)).where(
                    RegistrationSession.application_id == scenario.application_id,
                    RegistrationSession.revoked_at.is_(None),
                )
            ) == 0
    finally:
        release_winner.set()
        _cleanup_scenario(postgres_engine, scenario)
