from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from time import monotonic, sleep
from uuid import UUID

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AccountLoginIdentifier, AuthSession, UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService, AuthService
from app.identity.models import (
    GuardianProfile,
    GuardianRelationship,
    Person,
    StudentProfile,
)
from app.identity.schemas import GuardianCreate
from app.identity.service import (
    GuardianCycle,
    GuardianUnderage,
    IdentityService,
)
from app.rbac.models import UserRole
from app.registration.consents import registration_terms_digest
from app.registration.delivery import CapturingVerificationSender
from app.registration.finalization_service import RegistrationFinalizationService
from app.registration.guardian_service import (
    GuardianInvitationUnavailable,
    GuardianRegistrationService,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.invitation_retention import (
    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
    REDACTED_GUARDIAN_INVITATION_DIGEST,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationSession,
    RegistrationVerificationChallenge,
)
from app.registration.verification_service import (
    RegistrationVerificationService,
    registration_advisory_lock_key,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
KEY = bytes(range(32))
VERIFICATION_KEY = bytes(range(1, 33))
SESSION_KEY = bytes(range(2, 34))
RESET_PASSWORD = "GuardReset26Aa1"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _service(db: Session) -> GuardianRegistrationService:
    cipher = EnvelopeCipher(KEY)
    lookup = LookupDigest(KEY)
    identity = IdentityService(db, cipher=cipher, lookup=lookup)
    return GuardianRegistrationService(
        db,
        cipher=cipher,
        lookup=lookup,
        identity=identity,
        finalization=RegistrationFinalizationService(
            db,
            cipher=cipher,
            lookup=lookup,
            clock=lambda: NOW,
        ),
        sender=CapturingVerificationSender(messages=[]),
        clock=lambda: NOW,
    )


def _auth(db: Session) -> AuthService:
    return AuthService(
        db,
        cipher=EnvelopeCipher(KEY),
        lookup=LookupDigest(KEY),
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
        lookup=LookupDigest(KEY),
        verification_pepper=VERIFICATION_KEY,
        sender=sender,
        app_env="test",
        clock=lambda: NOW,
    )


def _setup_invitation(
    engine: Engine,
    *,
    student_phone: str = "13800008821",
    guardian_phone: str = "13800008823",
) -> tuple[UUID, UUID, UUID]:
    cipher = EnvelopeCipher(KEY)
    lookup = LookupDigest(KEY)
    with Session(engine) as db:
        identity = IdentityService(db, cipher=cipher, lookup=lookup)
        guardian = identity.create_guardian(
            GuardianCreate(
                full_name="并发受邀监护人",
                identity_number=make_cn_id("19800102", 823),
                sex="male",
                date_of_birth=date(1980, 1, 2),
                phone=guardian_phone,
                email="guardian-8823@example.com",
            )
        )
        account_result = AccountService(
            db,
            identity_service=identity,
            passwords=PasswordService(),
            reference_date_provider=lambda: NOW.date(),
        ).create_account(
            AccountCreate(
                person_id=guardian.id,
                login_phone=guardian_phone,
                email="guardian-8823@example.com",
            )
        )
        guardian_account = db.get(UserAccount, account_result.account.id)
        assert guardian_account is not None
        guardian_account.force_password_change = False

        student_identifier = normalize_registration_identifier(
            "phone",
            student_phone,
        )
        application = RegistrationApplication(
            primary_purpose="student",
            state="pending_guardian",
            verified_identifier_kind=student_identifier.kind,
            verified_identifier_digest=registration_identifier_digest(
                lookup,
                student_identifier,
            ),
            verified_identifier_ciphertext=cipher.encrypt(
                student_identifier.value,
                field_name="registration_identifier",
            ),
            password_hash=PasswordService().hash("Guard821RaceAa"),
            full_name="并发待监护学员",
            identity_ciphertext=cipher.encrypt(
                make_cn_id("20120102", 821),
                field_name="registration_identity",
            ),
            identity_digest=lookup.digest(make_cn_id("20120102", 821)),
            sex="male",
            date_of_birth=date(2012, 1, 2),
            expires_at=NOW + timedelta(days=30),
        )
        db.add(application)
        db.flush()
        terms_digest = registration_terms_digest("2026-08")
        assert terms_digest is not None
        db.add(
            RegistrationConsentAcceptance(
                registration_application_id=application.id,
                account_id=None,
                consent_type="registration_terms",
                policy_version="2026-08",
                content_digest=terms_digest,
                accepted_at=NOW,
                request_id="registration-guardian-race-setup",
                source="self_registration",
            )
        )
        db.commit()
        invitation = _service(db).invite(
            application.id,
            identifier_kind="phone",
            identifier=guardian_phone,
            relationship="father",
            is_primary=True,
            request_id="registration-guardian-race-invite",
        )
        return application.id, invitation.id, guardian_account.id


def _setup_adult_invitation(
    engine: Engine,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    cipher = EnvelopeCipher(KEY)
    lookup = LookupDigest(KEY)
    with Session(engine) as db:
        identity = IdentityService(db, cipher=cipher, lookup=lookup)
        student_id = UUID("f7170000-0000-4000-8000-000000000001")
        guardian_id = UUID("f7170000-0000-4000-8000-000000000002")
        student_identity = make_cn_id("19980102", 831)
        guardian_identity = make_cn_id("19800102", 833)
        student = Person(
            id=student_id,
            full_name="并发成年邀请学员",
            identity_ciphertext=cipher.encrypt(
                student_identity,
                field_name="identity_number",
            ),
            identity_digest=lookup.digest(student_identity),
            phone_ciphertext=cipher.encrypt("13800008831", field_name="phone"),
            phone_digest=lookup.digest("13800008831"),
            email_ciphertext=cipher.encrypt(
                "student-8831@example.com",
                field_name="email",
            ),
            email_digest=lookup.digest_domain(
                "person-email",
                "student-8831@example.com",
            ),
            sex="male",
            date_of_birth=date(1998, 1, 2),
            deleted_at=None,
        )
        guardian = Person(
            id=guardian_id,
            full_name="并发成年受邀监护人",
            identity_ciphertext=cipher.encrypt(
                guardian_identity,
                field_name="identity_number",
            ),
            identity_digest=lookup.digest(guardian_identity),
            phone_ciphertext=cipher.encrypt("13800008833", field_name="phone"),
            phone_digest=lookup.digest("13800008833"),
            email_ciphertext=cipher.encrypt(
                "guardian-8833@example.com",
                field_name="email",
            ),
            email_digest=lookup.digest_domain(
                "person-email",
                "guardian-8833@example.com",
            ),
            sex="male",
            date_of_birth=date(1980, 1, 2),
            deleted_at=None,
        )
        db.add_all([student, guardian])
        db.flush()
        db.add_all(
            [
                StudentProfile(
                    person_id=student_id,
                    student_number="REG-GUARDIAN-RACE-831",
                    status="active",
                ),
                StudentProfile(
                    person_id=guardian_id,
                    student_number="REG-GUARDIAN-RACE-833",
                    status="active",
                ),
                GuardianProfile(person_id=guardian_id),
            ]
        )
        db.flush()
        accounts = AccountService(
            db,
            identity_service=identity,
            passwords=PasswordService(),
            reference_date_provider=lambda: NOW.date(),
        )
        student_account = accounts.create_account(
            AccountCreate(
                person_id=student.id,
                login_phone="13800008831",
                email="student-8831@example.com",
            )
        ).account
        guardian_account = accounts.create_account(
            AccountCreate(
                person_id=guardian.id,
                login_phone="13800008833",
                email="guardian-8833@example.com",
            )
        ).account
        for account_id in (student_account.id, guardian_account.id):
            account = db.get(UserAccount, account_id)
            assert account is not None
            account.force_password_change = False
        db.commit()
        invitation = _service(db).invite_for_account(
            student_account.id,
            identifier_kind="phone",
            identifier="13800008833",
            relationship="father",
            is_primary=False,
            request_id="registration-adult-guardian-race-invite",
        )
        return (
            invitation.id,
            student.id,
            student_account.id,
            guardian.id,
            guardian_account.id,
        )


def _setup_exact_claim_invitation(
    engine: Engine,
) -> tuple[UUID, UUID, UUID, UUID]:
    application_id, invitation_id, guardian_account_id = _setup_invitation(engine)
    student_person_id = UUID("00000000-0000-4000-8000-000000000001")
    cipher = EnvelopeCipher(KEY)
    lookup = LookupDigest(KEY)
    with Session(engine) as db, db.begin():
        guardian_account = db.get(UserAccount, guardian_account_id)
        assert guardian_account is not None
        guardian_person_id = guardian_account.person_id
        assert student_person_id.int < guardian_person_id.int
        student_identity = make_cn_id("20120102", 821)
        student = Person(
            id=student_person_id,
            full_name="并发待监护学员",
            identity_ciphertext=cipher.encrypt(
                student_identity,
                field_name="identity_number",
            ),
            identity_digest=lookup.digest(student_identity),
            phone_ciphertext=cipher.encrypt("13800008821", field_name="phone"),
            phone_digest=lookup.digest("13800008821"),
            email_ciphertext=None,
            email_digest=None,
            sex="male",
            date_of_birth=date(2012, 1, 2),
            deleted_at=None,
        )
        db.add(student)
        db.flush()
        db.add_all(
            [
                StudentProfile(
                    person_id=student_person_id,
                    student_number="REG-GUARDIAN-EXACT-821",
                    status="draft",
                ),
                StudentProfile(
                    person_id=guardian_person_id,
                    student_number="REG-GUARDIAN-REVERSE-823",
                    status="active",
                ),
            ]
        )
    return (
        application_id,
        invitation_id,
        student_person_id,
        guardian_account_id,
    )


def _wait_for_postgres_blocker(
    engine: Engine,
    *,
    blocked_pid: int,
    blocker_pid: int,
) -> None:
    deadline = monotonic() + 5
    blocking_pids: list[int] = []
    while monotonic() < deadline:
        with engine.connect() as observer:
            blocking_pids = list(
                observer.scalar(
                    text("SELECT pg_blocking_pids(:blocked_pid)"),
                    {"blocked_pid": blocked_pid},
                )
                or []
            )
        if blocker_pid in blocking_pids:
            return
        sleep(0.02)
    pytest.fail(
        f"accept backend {blocked_pid} was not blocked by direct-add backend "
        f"{blocker_pid}; observed blockers={blocking_pids}"
    )


def _event_backend_pid(connection) -> int:
    return int(connection.connection.driver_connection.info.backend_pid)


def test_accept_and_direct_add_guardian_serialize_and_reuse_winning_relationship(
    postgres_engine: Engine,
) -> None:
    (
        invitation_id,
        student_person_id,
        student_account_id,
        guardian_person_id,
        guardian_account_id,
    ) = _setup_adult_invitation(postgres_engine)
    direct_lock_acquired = threading.Event()
    accept_lock_attempted = threading.Event()
    release_direct = threading.Event()
    accept_done = threading.Event()
    thread_ids: dict[str, int] = {}
    backend_pids: dict[str, int] = {}

    def after_cursor_execute(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if (
            threading.get_ident() == thread_ids.get("direct")
            and "pg_advisory_xact_lock" in statement.casefold()
        ):
            backend_pids["direct"] = _event_backend_pid(_connection)
            direct_lock_acquired.set()
            if not release_direct.wait(timeout=8):
                raise TimeoutError("direct guardian lock was not released by the test")

    def before_cursor_execute(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if (
            threading.get_ident() == thread_ids.get("accept")
            and "pg_advisory_xact_lock" in statement.casefold()
        ):
            backend_pids["accept"] = _event_backend_pid(_connection)
            accept_lock_attempted.set()

    def direct_add() -> UUID:
        thread_ids["direct"] = threading.get_ident()
        with Session(postgres_engine) as db:
            backend_pids["direct"] = int(
                db.scalar(text("SELECT pg_backend_pid()")) or 0
            )
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            relationship = IdentityService(
                db,
                cipher=EnvelopeCipher(KEY),
                lookup=LookupDigest(KEY),
            ).add_guardian(
                student_person_id,
                guardian_person_id,
                relationship="father",
                is_primary=False,
                valid_from=NOW.date(),
                valid_until=None,
            )
            return relationship.relationship_id

    def accept_invitation() -> tuple[UUID, bool]:
        thread_ids["accept"] = threading.get_ident()
        with Session(postgres_engine) as db:
            backend_pids["accept"] = int(
                db.scalar(text("SELECT pg_backend_pid()")) or 0
            )
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                accepted = _service(db).accept(
                    invitation_id,
                    guardian_account_id=guardian_account_id,
                    consent_version="2026-08",
                    request_id="registration-guardian-cross-entry-race",
                )
                return (
                    accepted.relationship_id,
                    db.scalar(
                        select(func.count()).select_from(GuardianRelationship)
                    )
                    is not None,
                )
            finally:
                accept_done.set()

    event.listen(postgres_engine, "after_cursor_execute", after_cursor_execute)
    event.listen(postgres_engine, "before_cursor_execute", before_cursor_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            direct_future = executor.submit(direct_add)
            assert direct_lock_acquired.wait(timeout=5)
            accept_future = executor.submit(accept_invitation)
            assert accept_lock_attempted.wait(timeout=5)
            _wait_for_postgres_blocker(
                postgres_engine,
                blocked_pid=backend_pids["accept"],
                blocker_pid=backend_pids["direct"],
            )
            assert not accept_done.is_set()
            release_direct.set()
            direct_relationship_id = direct_future.result(timeout=12)
            accept_result = accept_future.result(timeout=12)

        assert accept_result == (direct_relationship_id, True)
        with Session(postgres_engine) as verification:
            relationships = list(
                verification.scalars(
                    select(GuardianRelationship).where(
                        GuardianRelationship.student_person_id == student_person_id,
                        GuardianRelationship.guardian_person_id == guardian_person_id,
                    )
                )
            )
            assert [relationship.id for relationship in relationships] == [
                direct_relationship_id
            ]
            invitation = verification.get(
                GuardianRegistrationInvitation,
                invitation_id,
            )
            assert invitation is not None
            assert invitation.status == "accepted"
            assert invitation.accepted_by_person_id == guardian_person_id
            assert invitation.accepted_at == NOW
            assert (
                verification.scalar(
                    select(func.count(RegistrationConsentAcceptance.id)).where(
                        RegistrationConsentAcceptance.account_id
                        == guardian_account_id,
                        RegistrationConsentAcceptance.consent_type
                        == "guardian_relationship",
                    )
                )
                == 1
            )
    finally:
        release_direct.set()
        event.remove(postgres_engine, "after_cursor_execute", after_cursor_execute)
        event.remove(postgres_engine, "before_cursor_execute", before_cursor_execute)
        with Session(postgres_engine) as cleanup, cleanup.begin():
            account_ids = {student_account_id, guardian_account_id}
            person_ids = {student_person_id, guardian_person_id}
            cleanup.execute(
                delete(GuardianRelationship).where(
                    GuardianRelationship.student_person_id.in_(person_ids)
                    | GuardianRelationship.guardian_person_id.in_(person_ids)
                )
            )
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.account_id.in_(account_ids)
                )
            )
            cleanup.execute(
                delete(GuardianRegistrationInvitation).where(
                    GuardianRegistrationInvitation.id == invitation_id
                )
            )
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id.in_(account_ids)
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            cleanup.execute(
                delete(StudentProfile).where(StudentProfile.person_id.in_(person_ids))
            )
            cleanup.execute(
                delete(GuardianProfile).where(GuardianProfile.person_id.in_(person_ids))
            )
            cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_accept_and_reverse_guardian_add_lock_people_in_global_order(
    postgres_engine: Engine,
) -> None:
    (
        invitation_id,
        student_person_id,
        student_account_id,
        guardian_person_id,
        guardian_account_id,
    ) = _setup_adult_invitation(postgres_engine)
    accept_account_locked = threading.Event()
    reverse_people_attempted = threading.Event()
    release_accept = threading.Event()
    thread_ids: dict[str, int] = {}
    backend_pids: dict[str, int] = {}

    def after_cursor_execute(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        lowered = statement.casefold()
        if (
            threading.get_ident() == thread_ids.get("accept")
            and "from user_accounts" in lowered
            and "for update" in lowered
        ):
            backend_pids["accept"] = _event_backend_pid(_connection)
            accept_account_locked.set()
            if not release_accept.wait(timeout=8):
                raise TimeoutError("accept account lock was not released by the test")

    def before_cursor_execute(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        lowered = statement.casefold()
        if (
            threading.get_ident() == thread_ids.get("reverse")
            and "from people" in lowered
            and "for update" in lowered
        ):
            backend_pids["reverse"] = _event_backend_pid(_connection)
            reverse_people_attempted.set()

    def accept_invitation() -> tuple[str, UUID | None, bool]:
        thread_ids["accept"] = threading.get_ident()
        with Session(postgres_engine) as db:
            backend_pids["accept"] = int(
                db.scalar(text("SELECT pg_backend_pid()")) or 0
            )
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                accepted = _service(db).accept(
                    invitation_id,
                    guardian_account_id=guardian_account_id,
                    consent_version="2026-08",
                    request_id="registration-guardian-reverse-race-accept",
                )
                usable = db.scalar(select(func.count()).select_from(Person)) is not None
                return "accepted", accepted.relationship_id, usable
            except BaseException as exc:  # noqa: BLE001 - asserted in parent
                usable = db.scalar(select(func.count()).select_from(Person)) is not None
                return type(exc).__name__, None, usable

    def add_reverse_relationship() -> tuple[str, bool]:
        thread_ids["reverse"] = threading.get_ident()
        with Session(postgres_engine) as db:
            backend_pids["reverse"] = int(
                db.scalar(text("SELECT pg_backend_pid()")) or 0
            )
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                IdentityService(
                    db,
                    cipher=EnvelopeCipher(KEY),
                    lookup=LookupDigest(KEY),
                ).add_guardian(
                    guardian_person_id,
                    student_person_id,
                    relationship="son",
                    is_primary=False,
                    valid_from=NOW.date(),
                    valid_until=None,
                )
            except BaseException as exc:  # noqa: BLE001 - asserted in parent
                usable = (
                    db.scalar(select(func.count()).select_from(GuardianRelationship))
                    is not None
                )
                return type(exc).__name__, usable
        raise AssertionError("reverse guardian relationship unexpectedly succeeded")

    event.listen(postgres_engine, "after_cursor_execute", after_cursor_execute)
    event.listen(postgres_engine, "before_cursor_execute", before_cursor_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            accept_future = executor.submit(accept_invitation)
            assert accept_account_locked.wait(timeout=5)
            reverse_future = executor.submit(add_reverse_relationship)
            assert reverse_people_attempted.wait(timeout=5)
            _wait_for_postgres_blocker(
                postgres_engine,
                blocked_pid=backend_pids["reverse"],
                blocker_pid=backend_pids["accept"],
            )
            release_accept.set()
            accept_result = accept_future.result(timeout=12)
            reverse_result = reverse_future.result(timeout=12)

        assert accept_result[0] == "accepted"
        assert accept_result[1] is not None
        assert accept_result[2] is True
        assert reverse_result == (GuardianCycle.__name__, True)
        with Session(postgres_engine) as verification:
            relationship = verification.scalar(
                select(GuardianRelationship).where(
                    GuardianRelationship.student_person_id == student_person_id,
                    GuardianRelationship.guardian_person_id == guardian_person_id,
                )
            )
            assert relationship is not None
            assert relationship.id == accept_result[1]
            assert (
                verification.scalar(
                    select(func.count(GuardianRelationship.id)).where(
                        GuardianRelationship.student_person_id.in_(
                            {student_person_id, guardian_person_id}
                        ),
                        GuardianRelationship.guardian_person_id.in_(
                            {student_person_id, guardian_person_id}
                        ),
                    )
                )
                == 1
            )
    finally:
        release_accept.set()
        event.remove(postgres_engine, "after_cursor_execute", after_cursor_execute)
        event.remove(postgres_engine, "before_cursor_execute", before_cursor_execute)
        with Session(postgres_engine) as cleanup, cleanup.begin():
            account_ids = {student_account_id, guardian_account_id}
            person_ids = {student_person_id, guardian_person_id}
            cleanup.execute(
                delete(GuardianRelationship).where(
                    GuardianRelationship.student_person_id.in_(person_ids)
                    | GuardianRelationship.guardian_person_id.in_(person_ids)
                )
            )
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.account_id.in_(account_ids)
                )
            )
            cleanup.execute(
                delete(GuardianRegistrationInvitation).where(
                    GuardianRegistrationInvitation.id == invitation_id
                )
            )
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id.in_(account_ids)
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            cleanup.execute(
                delete(StudentProfile).where(StudentProfile.person_id.in_(person_ids))
            )
            cleanup.execute(
                delete(GuardianProfile).where(GuardianProfile.person_id.in_(person_ids))
            )
            cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_exact_claim_accept_and_reverse_add_do_not_deadlock(
    postgres_engine: Engine,
) -> None:
    (
        application_id,
        invitation_id,
        student_person_id,
        guardian_account_id,
    ) = _setup_exact_claim_invitation(postgres_engine)
    with Session(postgres_engine) as lookup_db:
        guardian_account = lookup_db.get(UserAccount, guardian_account_id)
        assert guardian_account is not None
        guardian_person_id = guardian_account.person_id
    accept_people_locked = threading.Event()
    reverse_people_attempted = threading.Event()
    release_accept = threading.Event()
    thread_ids: dict[str, int] = {}
    backend_pids: dict[str, int] = {}

    def after_cursor_execute(
        connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        lowered = statement.casefold()
        if (
            threading.get_ident() == thread_ids.get("accept")
            and "from people" in lowered
            and "for update" in lowered
            and not accept_people_locked.is_set()
        ):
            backend_pids["accept"] = _event_backend_pid(connection)
            accept_people_locked.set()
            if not release_accept.wait(timeout=8):
                raise TimeoutError("exact-claim accept lock was not released")

    def before_cursor_execute(
        connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        lowered = statement.casefold()
        if (
            threading.get_ident() == thread_ids.get("reverse")
            and "from people" in lowered
            and "for update" in lowered
        ):
            backend_pids["reverse"] = _event_backend_pid(connection)
            reverse_people_attempted.set()

    def accept_invitation() -> tuple[str, UUID | None, bool]:
        thread_ids["accept"] = threading.get_ident()
        with Session(postgres_engine) as db, db.begin():
            backend_pids["accept"] = int(
                db.scalar(text("SELECT pg_backend_pid()")) or 0
            )
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                accepted = _service(db).accept(
                    invitation_id,
                    guardian_account_id=guardian_account_id,
                    consent_version="2026-08",
                    request_id="registration-guardian-exact-claim-reverse-race",
                )
                usable = db.scalar(text("SELECT 1")) == 1
                return "accepted", accepted.relationship_id, usable
            except BaseException as exc:  # noqa: BLE001 - asserted in parent
                usable = db.scalar(text("SELECT 1")) == 1
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
                return f"{type(exc).__name__}:{sqlstate}", None, usable

    def add_reverse_relationship() -> tuple[str, bool]:
        thread_ids["reverse"] = threading.get_ident()
        with Session(postgres_engine) as db, db.begin():
            backend_pids["reverse"] = int(
                db.scalar(text("SELECT pg_backend_pid()")) or 0
            )
            db.execute(text("SET LOCAL statement_timeout = '8s'"))
            try:
                IdentityService(
                    db,
                    cipher=EnvelopeCipher(KEY),
                    lookup=LookupDigest(KEY),
                ).add_guardian(
                    guardian_person_id,
                    student_person_id,
                    relationship="son",
                    is_primary=False,
                    valid_from=NOW.date(),
                    valid_until=None,
                )
            except BaseException as exc:  # noqa: BLE001 - asserted in parent
                usable = db.scalar(text("SELECT 1")) == 1
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
                return f"{type(exc).__name__}:{sqlstate}", usable
        raise AssertionError("reverse guardian relationship unexpectedly succeeded")

    event.listen(postgres_engine, "after_cursor_execute", after_cursor_execute)
    event.listen(postgres_engine, "before_cursor_execute", before_cursor_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            accept_future = executor.submit(accept_invitation)
            assert accept_people_locked.wait(timeout=5)
            reverse_future = executor.submit(add_reverse_relationship)
            assert reverse_people_attempted.wait(timeout=5)
            _wait_for_postgres_blocker(
                postgres_engine,
                blocked_pid=backend_pids["reverse"],
                blocker_pid=backend_pids["accept"],
            )
            release_accept.set()
            accept_result = accept_future.result(timeout=12)
            reverse_result = reverse_future.result(timeout=12)

        assert accept_result[0] == "accepted"
        assert accept_result[1] is not None
        assert accept_result[2] is True
        assert reverse_result == (f"{GuardianUnderage.__name__}:None", True)
        assert "40P01" not in reverse_result[0]
        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, application_id)
            assert application is not None
            assert application.state == "finalized"
            assert application.matched_person_id == student_person_id
            assert application.finalized_account_id is not None
            assert application.finalization_match_kind == "claim_existing"
            assert application.finalized_at == NOW

            invitation = verification.get(
                GuardianRegistrationInvitation,
                invitation_id,
            )
            assert invitation is not None
            assert invitation.status == "accepted"
            assert invitation.accepted_by_person_id == guardian_person_id
            assert invitation.accepted_at == NOW

            relationship = verification.scalar(
                select(GuardianRelationship).where(
                    GuardianRelationship.student_person_id == student_person_id,
                    GuardianRelationship.guardian_person_id == guardian_person_id,
                )
            )
            assert relationship is not None
            assert relationship.id == accept_result[1]
            assert relationship.relationship == "father"
            assert relationship.is_primary is True
            assert (
                verification.scalar(
                    select(func.count(GuardianRelationship.id)).where(
                        GuardianRelationship.student_person_id == student_person_id,
                        GuardianRelationship.guardian_person_id == guardian_person_id,
                    )
                )
                == 1
            )
            assert (
                verification.scalar(
                    select(func.count(GuardianRelationship.id)).where(
                        GuardianRelationship.student_person_id == guardian_person_id,
                        GuardianRelationship.guardian_person_id == student_person_id,
                    )
                )
                == 0
            )
            assert (
                verification.scalar(
                    select(func.count(RegistrationConsentAcceptance.id)).where(
                        RegistrationConsentAcceptance.account_id
                        == guardian_account_id,
                        RegistrationConsentAcceptance.consent_type
                        == "guardian_relationship",
                    )
                )
                == 1
            )
            assert verification.get(GuardianProfile, student_person_id) is None
    finally:
        release_accept.set()
        event.remove(postgres_engine, "after_cursor_execute", after_cursor_execute)
        event.remove(postgres_engine, "before_cursor_execute", before_cursor_execute)
        with Session(postgres_engine) as cleanup, cleanup.begin():
            person_ids = {student_person_id, guardian_person_id}
            account_ids = set(
                cleanup.scalars(
                    select(UserAccount.id).where(UserAccount.person_id.in_(person_ids))
                )
            )
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(AuditEvent).where(AuditEvent.resource_id == application_id)
            )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(GuardianRelationship).where(
                    GuardianRelationship.student_person_id.in_(person_ids)
                    | GuardianRelationship.guardian_person_id.in_(person_ids)
                )
            )
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    (RegistrationConsentAcceptance.account_id.in_(account_ids))
                    | (
                        RegistrationConsentAcceptance.registration_application_id
                        == application_id
                    )
                )
            )
            cleanup.execute(
                delete(GuardianRegistrationInvitation).where(
                    GuardianRegistrationInvitation.id == invitation_id
                )
            )
            cleanup.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id.in_(account_ids)
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id == application_id
                )
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            cleanup.execute(
                delete(StudentProfile).where(StudentProfile.person_id.in_(person_ids))
            )
            cleanup.execute(
                delete(GuardianProfile).where(GuardianProfile.person_id.in_(person_ids))
            )
            cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_two_acceptances_are_equivalent_and_create_one_complete_result(
    postgres_engine: Engine,
) -> None:
    application_id, invitation_id, guardian_account_id = _setup_invitation(
        postgres_engine
    )
    barrier = threading.Barrier(2)
    student_person_id: UUID | None = None
    student_account_id: UUID | None = None

    def accept(index: int) -> tuple[str, str, bool, bool]:
        with Session(postgres_engine) as db:
            barrier.wait(timeout=5)
            result = _service(db).accept(
                invitation_id,
                guardian_account_id=guardian_account_id,
                consent_version="2026-08",
                request_id=f"registration-guardian-race-{index}",
            )
            usable = (
                db.scalar(select(func.count()).select_from(RegistrationApplication))
                or 0
            ) >= 1
            return (
                str(result.invitation_id),
                str(result.relationship_id),
                result.student_finalized,
                usable,
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(accept, index) for index in range(2)]
            results = [future.result(timeout=12) for future in futures]

        assert results[0][:3] == results[1][:3]
        assert results[0][2] is True
        assert all(result[3] for result in results)
        with Session(postgres_engine) as verification:
            stored_invitation = verification.get(
                GuardianRegistrationInvitation,
                invitation_id,
            )
            assert stored_invitation is not None
            assert (
                stored_invitation.invited_identifier_digest
                == REDACTED_GUARDIAN_INVITATION_DIGEST
            )
            assert (
                stored_invitation.invited_identifier_ciphertext
                == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
            )
            application = verification.get(RegistrationApplication, application_id)
            assert application is not None and application.state == "finalized"
            assert application.matched_person_id is not None
            assert application.finalized_account_id is not None
            student_person_id = application.matched_person_id
            student_account_id = application.finalized_account_id
            guardian_account = verification.get(UserAccount, guardian_account_id)
            assert guardian_account is not None
            assert verification.scalar(
                select(func.count(GuardianRelationship.id)).where(
                    GuardianRelationship.student_person_id == student_person_id,
                    GuardianRelationship.guardian_person_id
                    == guardian_account.person_id,
                )
            ) == 1
            assert verification.scalar(
                select(func.count(UserAccount.id)).where(
                    UserAccount.person_id == student_person_id
                )
            ) == 1
            consent = verification.scalar(
                select(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.account_id == guardian_account_id,
                    RegistrationConsentAcceptance.consent_type
                    == "guardian_relationship",
                )
            )
            assert verification.scalar(
                select(func.count(RegistrationConsentAcceptance.id)).where(
                    RegistrationConsentAcceptance.account_id == guardian_account_id,
                    RegistrationConsentAcceptance.consent_type
                    == "guardian_relationship",
                )
            ) == 1
            assert consent is not None
            assert consent.policy_version == "2026-08"
            assert len(consent.content_digest) == 64
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            application = cleanup.get(RegistrationApplication, application_id)
            if application is not None:
                student_person_id = application.matched_person_id
                student_account_id = application.finalized_account_id
            guardian_account = cleanup.get(UserAccount, guardian_account_id)
            guardian_person_id = (
                guardian_account.person_id if guardian_account is not None else None
            )
            account_ids = {
                account_id
                for account_id in (guardian_account_id, student_account_id)
                if account_id is not None
            }
            person_ids = {
                person_id
                for person_id in (guardian_person_id, student_person_id)
                if person_id is not None
            }
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(AuditEvent).where(AuditEvent.resource_id == application_id)
            )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            if person_ids:
                cleanup.execute(
                    delete(GuardianRelationship).where(
                        GuardianRelationship.student_person_id.in_(person_ids)
                        | GuardianRelationship.guardian_person_id.in_(person_ids)
                    )
                )
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    (RegistrationConsentAcceptance.registration_application_id
                    == application_id)
                    | RegistrationConsentAcceptance.account_id.in_(account_ids)
                )
            )
            cleanup.execute(
                delete(GuardianRegistrationInvitation).where(
                    GuardianRegistrationInvitation.id == invitation_id
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id == application_id
                )
            )
            if account_ids:
                cleanup.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
                cleanup.execute(
                    delete(AccountLoginIdentifier).where(
                        AccountLoginIdentifier.account_id.in_(account_ids)
                    )
                )
            if person_ids:
                cleanup.execute(
                    delete(StudentProfile).where(StudentProfile.person_id.in_(person_ids))
                )
                cleanup.execute(
                    delete(GuardianProfile).where(
                        GuardianProfile.person_id.in_(person_ids)
                    )
                )
            if account_ids:
                cleanup.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
            if person_ids:
                cleanup.execute(delete(Person).where(Person.id.in_(person_ids)))


def test_password_reset_blocks_guardian_accept_before_application_row_lock(
    postgres_engine: Engine,
) -> None:
    shared_phone = "19990000004"
    application_id, invitation_id, guardian_account_id = _setup_invitation(
        postgres_engine,
        student_phone=shared_phone,
        guardian_phone=shared_phone,
    )
    reset_sender = CapturingVerificationSender(messages=[])
    with Session(postgres_engine) as issue_db:
        reset_challenge = _auth(issue_db).issue_password_reset(
            identifier=shared_phone,
            request_source="127.0.4.22",
            verification=_verification(issue_db, reset_sender),
        )
    with Session(postgres_engine) as lookup_db:
        application = lookup_db.get(RegistrationApplication, application_id)
        assert application is not None
        identifier_digest = application.verified_identifier_digest
        assert identifier_digest is not None
    application_identifier_key = registration_advisory_lock_key(
        "registration-application-identifier",
        identifier_digest,
    )
    identifier_key = registration_advisory_lock_key(
        "identifier",
        identifier_digest,
    )
    assert application_identifier_key < identifier_key

    reset_smaller_lock_owned = threading.Event()
    release_reset = threading.Event()
    accept_smaller_lock_attempted = threading.Event()
    accept_application_locked = threading.Event()
    accept_started = threading.Event()
    accept_observed_locks: list[tuple[str, object]] = []
    thread_ids: dict[str, int] = {}

    def parameters_include_key(parameters: object, key: int) -> bool:
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        return key in values

    def observe_accept_locks(
        _connection,
        _cursor,
        statement: str,
        parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if threading.get_ident() != thread_ids.get("accept"):
            return
        normalized = " ".join(statement.casefold().split())
        if len(accept_observed_locks) < 50:
            accept_observed_locks.append((normalized, parameters))
        if (
            "pg_advisory_xact_lock" in normalized
            and parameters_include_key(
                parameters,
                application_identifier_key,
            )
        ):
            accept_smaller_lock_attempted.set()
        if (
            "from registration_applications" in normalized
            and "for update" in normalized
        ):
            accept_application_locked.set()

    def reset_password() -> tuple[str, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()

            def pause_after_smaller_lock(
                _connection,
                _cursor,
                statement: str,
                parameters,
                _context,
                _executemany: bool,
            ) -> None:
                if (
                    "pg_advisory_xact_lock" in statement.casefold()
                    and parameters_include_key(
                        parameters,
                        application_identifier_key,
                    )
                ):
                    reset_smaller_lock_owned.set()
                    if not release_reset.wait(timeout=8):
                        raise TimeoutError("password reset lock was not released")

            event.listen(connection, "after_cursor_execute", pause_after_smaller_lock)
            try:
                db.execute(text("SET LOCAL statement_timeout = '8s'"))
                _auth(db).complete_password_reset(
                    challenge_id=reset_challenge.challenge_id,
                    code=reset_sender.code_for(reset_challenge.challenge_id),
                    new_password=RESET_PASSWORD,
                    request_id="00000000-0000-4000-8000-000000000422",
                    verification=_verification(db, CapturingVerificationSender(messages=[])),
                )
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute",
                    pause_after_smaller_lock,
                )
            reusable = db.scalar(select(func.count(UserAccount.id))) >= 1
            return "completed", reusable

    def accept_invitation() -> tuple[str, str | None, bool]:
        thread_ids["accept"] = threading.get_ident()
        with Session(postgres_engine) as db:
            accept_started.set()
            try:
                _service(db).accept(
                    invitation_id,
                    guardian_account_id=guardian_account_id,
                    consent_version="2026-08",
                    request_id="registration-guardian-reset-lock-race",
                )
            except GuardianInvitationUnavailable as exc:
                outcome = exc.code
                sqlstate = getattr(getattr(exc.__cause__, "orig", None), "sqlstate", None)
            except BaseException as exc:  # noqa: BLE001 - asserted in parent
                outcome = type(exc).__name__
                sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            else:
                outcome = "accepted"
                sqlstate = None
            reusable = db.scalar(select(func.count(UserAccount.id))) >= 1
            return outcome, sqlstate, reusable

    event.listen(postgres_engine, "before_cursor_execute", observe_accept_locks)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reset_future = executor.submit(reset_password)
            assert reset_smaller_lock_owned.wait(timeout=5)
            accept_future = executor.submit(accept_invitation)
            assert accept_started.wait(timeout=5)
            accept_attempted_before_release = accept_smaller_lock_attempted.wait(
                timeout=5
            )
            application_was_locked_before_shared_key = (
                accept_application_locked.is_set()
            )
            release_reset.set()
            reset_outcome, reset_reusable = reset_future.result(timeout=12)
            accept_outcome, accept_sqlstate, accept_reusable = accept_future.result(
                timeout=12
            )

        assert accept_attempted_before_release, accept_observed_locks
        assert application_was_locked_before_shared_key is False
        assert reset_outcome == "completed"
        assert reset_reusable is True
        assert accept_outcome == "registration_action_unavailable"
        assert accept_sqlstate is None
        assert accept_reusable is True
        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, application_id)
            invitation = verification.get(
                GuardianRegistrationInvitation,
                invitation_id,
            )
            assert application is not None and application.state == "expired"
            assert invitation is not None and invitation.status == "pending"
    finally:
        release_reset.set()
        event.remove(
            postgres_engine,
            "before_cursor_execute",
            observe_accept_locks,
        )
        with Session(postgres_engine) as cleanup, cleanup.begin():
            guardian_account = cleanup.get(UserAccount, guardian_account_id)
            guardian_person_id = (
                guardian_account.person_id if guardian_account is not None else None
            )
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(AuditEvent).where(
                    (AuditEvent.resource_id == application_id)
                    | (AuditEvent.resource_id == guardian_account_id)
                    | (AuditEvent.actor_user_id == guardian_account_id)
                )
            )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    (RegistrationVerificationChallenge.application_id == application_id)
                    | (RegistrationVerificationChallenge.account_id == guardian_account_id)
                )
            )
            cleanup.execute(
                delete(RegistrationSession).where(
                    RegistrationSession.application_id == application_id
                )
            )
            cleanup.execute(
                delete(AuthSession).where(AuthSession.user_id == guardian_account_id)
            )
            cleanup.execute(
                delete(RegistrationConsentAcceptance).where(
                    (RegistrationConsentAcceptance.registration_application_id
                    == application_id)
                    | (
                        RegistrationConsentAcceptance.account_id
                        == guardian_account_id
                    )
                )
            )
            cleanup.execute(
                delete(GuardianRegistrationInvitation).where(
                    GuardianRegistrationInvitation.id == invitation_id
                )
            )
            if guardian_person_id is not None:
                cleanup.execute(
                    delete(GuardianRelationship).where(
                        (GuardianRelationship.student_person_id == guardian_person_id)
                        | (
                            GuardianRelationship.guardian_person_id
                            == guardian_person_id
                        )
                    )
                )
            cleanup.execute(
                delete(UserRole).where(UserRole.user_id == guardian_account_id)
            )
            cleanup.execute(
                delete(AccountLoginIdentifier).where(
                    AccountLoginIdentifier.account_id == guardian_account_id
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id == application_id
                )
            )
            cleanup.execute(
                delete(UserAccount).where(UserAccount.id == guardian_account_id)
            )
            if guardian_person_id is not None:
                cleanup.execute(
                    delete(GuardianProfile).where(
                        GuardianProfile.person_id == guardian_person_id
                    )
                )
                cleanup.execute(
                    delete(StudentProfile).where(
                        StudentProfile.person_id == guardian_person_id
                    )
                )
                cleanup.execute(
                    delete(Person).where(Person.id == guardian_person_id)
                )
