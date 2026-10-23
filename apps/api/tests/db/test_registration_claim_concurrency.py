from __future__ import annotations

import hashlib
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
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.identity.membership_locks import person_membership_advisory_lock_key
from app.identity.models import Person, StudentProfile
from app.identity.schemas import PersonBaseInput, PersonUpdate, StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import UserRole
from app.registration.consents import registration_terms_digest
from app.registration.finalization_service import (
    RegistrationConflictPendingReview,
    RegistrationFinalizationService,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    IdentifierKind,
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationSession,
)
from app.registration.review_service import (
    RegistrationReviewNotFound,
    RegistrationReviewService,
)
from app.registration.verification_service import registration_credential_lock_keys
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)


def _statement_locks_advisory_key(
    statement: str,
    parameters: object,
    expected_key: int,
) -> bool:
    if "pg_advisory_xact_lock" not in statement.casefold():
        return False
    if isinstance(parameters, dict):
        values = parameters.values()
    elif isinstance(parameters, (list, tuple)):
        values = parameters
    else:
        values = (parameters,)
    return expected_key in values


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _service(db: Session) -> RegistrationFinalizationService:
    return RegistrationFinalizationService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
        clock=lambda: NOW,
    )


def _review_service(db: Session) -> RegistrationReviewService:
    return RegistrationReviewService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(reversed(range(32)))),
        finalization=_service(db),
        clock=lambda: NOW,
    )


def _setup_application(
    engine: Engine,
    *,
    identity_number: str | None = None,
    phone: str = "13800008001",
    identifier_kind: IdentifierKind = "phone",
    identifier_value: str | None = None,
    full_name: str = "并发注册学员",
) -> UUID:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    identity_number = identity_number or make_cn_id("19900102", 801)
    verified_identifier = identifier_value or phone
    identifier = normalize_registration_identifier(identifier_kind, verified_identifier)
    with Session(engine) as db, db.begin():
        application = RegistrationApplication(
            primary_purpose="student",
            state="ready_to_finalize",
            verified_identifier_kind=identifier_kind,
            verified_identifier_digest=registration_identifier_digest(
                lookup,
                identifier,
            ),
            verified_identifier_ciphertext=cipher.encrypt(
                verified_identifier,
                field_name="registration_identifier",
            ),
            password_hash=PasswordService().hash("Strong801PassAa"),
            full_name=full_name,
            identity_ciphertext=cipher.encrypt(
                identity_number,
                field_name="registration_identity",
            ),
            identity_digest=lookup.digest(identity_number),
            sex="male",
            date_of_birth=date(1990, 1, 2),
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
                request_id="00000000-0000-4000-8000-000000000801",
                source="self_registration",
            )
        )
        application_id = application.id
    return application_id


def test_two_sessions_finalizing_one_application_are_idempotent(
    postgres_engine: Engine,
) -> None:
    application_id = _setup_application(postgres_engine)
    stale_snapshot_captured = threading.Event()
    release_stale_finalizer = threading.Event()

    def finalize_with_stale_snapshot() -> tuple[str, str, bool]:
        with Session(postgres_engine) as db:
            connection = db.connection()
            snapshot_paused = False

            def pause_after_claim_snapshot(
                _connection: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                nonlocal snapshot_paused
                normalized = " ".join(statement.casefold().split())
                if (
                    snapshot_paused
                    or "from registration_applications" not in normalized
                    or "for update" in normalized
                ):
                    return
                snapshot_paused = True
                stale_snapshot_captured.set()
                assert release_stale_finalizer.wait(timeout=5)

            event.listen(connection, "after_cursor_execute", pause_after_claim_snapshot)
            try:
                result = _service(db).finalize(
                    application_id,
                    request_id="00000000-0000-4000-8000-000000000000",
                )
            finally:
                event.remove(
                    connection,
                    "after_cursor_execute",
                    pause_after_claim_snapshot,
                )
            usable = db.scalar(select(func.count()).select_from(RegistrationApplication)) >= 1
            return str(result.person_id), str(result.account_id), usable

    def finalize_winner() -> tuple[str, str, bool]:
        with Session(postgres_engine) as db:
            result = _service(db).finalize(
                application_id,
                request_id="00000000-0000-4000-8000-000000000001",
            )
            usable = db.scalar(select(func.count()).select_from(RegistrationApplication)) >= 1
            return str(result.person_id), str(result.account_id), usable

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            stale_future = executor.submit(finalize_with_stale_snapshot)
            assert stale_snapshot_captured.wait(timeout=5)
            winner_future = executor.submit(finalize_winner)
            winner_result = winner_future.result(timeout=8)
            release_stale_finalizer.set()
            stale_result = stale_future.result(timeout=8)

        assert stale_result[:2] == winner_result[:2]
        assert stale_result[2] is True
        assert winner_result[2] is True
        with Session(postgres_engine) as verification:
            stored = verification.get(RegistrationApplication, application_id)
            assert stored is not None and stored.state == "finalized"
            assert verification.scalar(select(func.count(UserAccount.id))) == 1
            assert verification.scalar(select(func.count(Person.id))) == 1
            assert verification.scalar(select(func.count(StudentProfile.person_id))) == 1
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "registration.finalize",
                    AuditEvent.resource_id == application_id,
                )
            ) == 1
    finally:
        release_stale_finalizer.set()
        with Session(postgres_engine) as cleanup, cleanup.begin():
            stored = cleanup.get(RegistrationApplication, application_id)
            account_id = stored.finalized_account_id if stored is not None else None
            person_id = stored.matched_person_id if stored is not None else None
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
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.account_id == account_id
                )
            )
            cleanup.execute(
                delete(RegistrationSession).where(
                    RegistrationSession.application_id == application_id
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id == application_id
                )
            )
            if account_id is not None:
                cleanup.execute(delete(UserRole).where(UserRole.user_id == account_id))
                cleanup.execute(
                    delete(AccountLoginIdentifier).where(
                        AccountLoginIdentifier.account_id == account_id
                    )
                )
            if person_id is not None:
                cleanup.execute(
                    delete(StudentProfile).where(
                        StudentProfile.person_id == person_id
                    )
                )
            if account_id is not None:
                cleanup.execute(
                    delete(UserAccount).where(UserAccount.id == account_id)
                )
            if person_id is not None:
                cleanup.execute(delete(Person).where(Person.id == person_id))


def test_two_applications_competing_for_one_person_yield_one_conflict(
    postgres_engine: Engine,
) -> None:
    identity_number = make_cn_id("19900102", 803)
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    with postgres_engine.begin() as connection:
        connection.execute(
            text("DROP INDEX uq_registration_applications_active_identity")
        )
    person_id: UUID | None = None
    application_ids: list[UUID] = []
    try:
        with Session(postgres_engine) as setup:
            person = IdentityService(setup, cipher=cipher, lookup=lookup).create_base_person(
                PersonBaseInput(
                    full_name="并发待认领人员",
                    identity_number=identity_number,
                    sex="male",
                    date_of_birth=date(1990, 1, 2),
                    phone=None,
                    email=None,
                )
            )
            person_id = person.id
        application_ids = [
            _setup_application(
                postgres_engine,
                identity_number=identity_number,
                phone="13800008003",
                full_name="并发待认领人员",
            ),
            _setup_application(
                postgres_engine,
                identity_number=identity_number,
                phone="13800008005",
                full_name="并发待认领人员",
            ),
        ]
        barrier = threading.Barrier(2)

        def finalize(application_id: UUID) -> tuple[str, bool]:
            with Session(postgres_engine) as db:
                barrier.wait(timeout=5)
                try:
                    _service(db).finalize(
                        application_id,
                        request_id=f"00000000-0000-4000-8000-{application_id.int % 10**12:012d}",
                    )
                except RegistrationConflictPendingReview:
                    outcome = "conflict"
                else:
                    outcome = "finalized"
                usable = db.scalar(select(func.count()).select_from(RegistrationApplication)) >= 2
                return outcome, usable

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = sorted(executor.map(finalize, application_ids))

        assert results == [("conflict", True), ("finalized", True)]
        with Session(postgres_engine) as verification:
            states = set(
                verification.scalars(
                    select(RegistrationApplication.state).where(
                        RegistrationApplication.id.in_(application_ids)
                    )
                )
            )
            assert states == {"conflict", "finalized"}
            assert verification.scalar(
                select(func.count(UserAccount.id)).where(
                    UserAccount.person_id == person_id
                )
            ) == 1
            assert verification.scalar(
                select(func.count(Person.id)).where(Person.id == person_id)
            ) == 1
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id.in_(application_ids),
                    AuditEvent.action == "registration.finalize",
                )
            ) == 1
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            account_id = cleanup.scalar(
                select(UserAccount.id).where(UserAccount.person_id == person_id)
            )
            cleanup.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            if application_ids:
                cleanup.execute(
                    delete(AuditEvent).where(
                        AuditEvent.resource_id.in_(application_ids)
                    )
                )
            cleanup.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            if account_id is not None:
                cleanup.execute(
                    delete(RegistrationConsentAcceptance).where(
                        RegistrationConsentAcceptance.account_id == account_id
                    )
                )
            if application_ids:
                cleanup.execute(
                    delete(RegistrationConsentAcceptance).where(
                        RegistrationConsentAcceptance.registration_application_id.in_(
                            application_ids
                        )
                    )
                )
                cleanup.execute(
                    delete(RegistrationSession).where(
                        RegistrationSession.application_id.in_(application_ids)
                    )
                )
                cleanup.execute(
                    delete(RegistrationApplication).where(
                        RegistrationApplication.id.in_(application_ids)
                    )
                )
            if account_id is not None:
                cleanup.execute(delete(UserRole).where(UserRole.user_id == account_id))
                cleanup.execute(
                    delete(AccountLoginIdentifier).where(
                        AccountLoginIdentifier.account_id == account_id
                    )
                )
            if person_id is not None:
                cleanup.execute(
                    delete(StudentProfile).where(
                        StudentProfile.person_id == person_id
                    )
                )
            if account_id is not None:
                cleanup.execute(
                    delete(UserAccount).where(UserAccount.id == account_id)
                )
            if person_id is not None:
                cleanup.execute(delete(Person).where(Person.id == person_id))
        with postgres_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_registration_applications_active_identity "
                    "ON registration_applications (identity_digest) "
                    "WHERE identity_digest IS NOT NULL AND state IN "
                    "('draft', 'contact_verified', 'identity_completed', "
                    "'pending_guardian', 'ready_to_finalize', 'conflict')"
                )
            )


def test_finalization_rechecks_candidates_after_generic_contact_update_commits(
    postgres_engine: Engine,
) -> None:
    """A writer that wins the contact predicate prevents a stale new-person claim."""

    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    phone = "13800008801"
    application_id = _setup_application(
        postgres_engine,
        identity_number=make_cn_id("19900102", 881),
        phone=phone,
        full_name="正式开通候选漂移学员",
    )
    with Session(postgres_engine) as setup, setup.begin():
        denied = _person(
            cipher=cipher,
            lookup=lookup,
            full_name="正式开通并发联系人",
            identity_number=make_cn_id("19900102", 883),
            phone=None,
        )
        setup.add(denied)
        setup.flush()
        denied_person_id = denied.id
    target_membership_key = person_membership_advisory_lock_key(
        "phone",
        lookup.digest(phone),
    )
    writer_membership_locked = threading.Event()
    finalization_membership_attempted = threading.Event()
    release_writer = threading.Event()
    try:
        with Session(postgres_engine) as baseline:
            formal_counts_before = (
                baseline.scalar(select(func.count()).select_from(Person)),
                baseline.scalar(select(func.count()).select_from(UserAccount)),
                baseline.scalar(select(func.count()).select_from(UserRole)),
            )
            finalize_audit_count_before = baseline.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == application_id,
                    AuditEvent.action == "registration.finalize",
                )
            )

        def update_denied_person() -> bool:
            with Session(postgres_engine) as db:
                connection = db.connection()
                paused = False

                def pause_after_membership_lock(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal paused
                    if paused or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    paused = True
                    writer_membership_locked.set()
                    assert release_writer.wait(timeout=5)

                event.listen(
                    connection,
                    "after_cursor_execute",
                    pause_after_membership_lock,
                )
                try:
                    IdentityService(db, cipher=cipher, lookup=lookup).update_person(
                        denied_person_id,
                        PersonUpdate(phone=phone),
                        actor_user_id=None,
                        request_id="00000000-0000-4000-8000-000000000881",
                    )
                finally:
                    event.remove(
                        connection,
                        "after_cursor_execute",
                        pause_after_membership_lock,
                    )
                return db.scalar(select(1)) == 1

        def finalize_application() -> tuple[str, bool]:
            with Session(postgres_engine) as db:
                connection = db.connection()

                def observe_membership_attempt(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    if _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        finalization_membership_attempted.set()

                event.listen(
                    connection,
                    "before_cursor_execute",
                    observe_membership_attempt,
                )
                try:
                    try:
                        _service(db).finalize(
                            application_id,
                            request_id="00000000-0000-4000-8000-000000000882",
                        )
                    except RegistrationConflictPendingReview:
                        outcome = "conflict"
                    else:
                        outcome = "finalized"
                finally:
                    event.remove(
                        connection,
                        "before_cursor_execute",
                        observe_membership_attempt,
                    )
                return outcome, db.scalar(select(1)) == 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(update_denied_person)
            assert writer_membership_locked.wait(timeout=5)
            finalization_future = executor.submit(finalize_application)
            assert finalization_membership_attempted.wait(timeout=5)
            release_writer.set()
            assert writer_future.result(timeout=8) is True
            assert finalization_future.result(timeout=8) == ("conflict", True)

        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, application_id)
            denied = verification.get(Person, denied_person_id)
            assert application is not None
            assert denied is not None
            assert application.state == "conflict"
            assert application.conflict_code == "concurrent_claim"
            assert application.matched_person_id is None
            assert application.finalized_account_id is None
            assert denied.phone_digest == lookup.digest(phone)
            assert (
                verification.scalar(select(func.count()).select_from(Person)),
                verification.scalar(select(func.count()).select_from(UserAccount)),
                verification.scalar(select(func.count()).select_from(UserRole)),
            ) == formal_counts_before
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == application_id,
                    AuditEvent.action == "registration.finalize",
                )
            ) == finalize_audit_count_before
    finally:
        release_writer.set()
        with Session(postgres_engine) as cleanup, cleanup.begin():
            application = cleanup.get(RegistrationApplication, application_id)
            created_account_id = (
                application.finalized_account_id if application is not None else None
            )
            created_person_id = (
                application.matched_person_id if application is not None else None
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
                delete(RegistrationConsentAcceptance).where(
                    RegistrationConsentAcceptance.registration_application_id
                    == application_id
                )
            )
            if created_account_id is not None:
                cleanup.execute(
                    delete(RegistrationConsentAcceptance).where(
                        RegistrationConsentAcceptance.account_id == created_account_id
                    )
                )
            cleanup.execute(
                delete(RegistrationSession).where(
                    RegistrationSession.application_id == application_id
                )
            )
            cleanup.execute(
                delete(RegistrationApplication).where(
                    RegistrationApplication.id == application_id
                )
            )
            if created_account_id is not None:
                cleanup.execute(
                    delete(UserRole).where(UserRole.user_id == created_account_id)
                )
                cleanup.execute(
                    delete(AccountLoginIdentifier).where(
                        AccountLoginIdentifier.account_id == created_account_id
                    )
                )
                cleanup.execute(
                    delete(UserAccount).where(UserAccount.id == created_account_id)
                )
            if (
                created_person_id is not None
                and created_person_id != denied_person_id
            ):
                cleanup.execute(
                    delete(StudentProfile).where(
                        StudentProfile.person_id == created_person_id
                    )
                )
                cleanup.execute(
                    delete(Person).where(Person.id == created_person_id)
                )
            cleanup.execute(delete(Person).where(Person.id == denied_person_id))


@dataclass(frozen=True, slots=True)
class RejectScopeRaceScenario:
    application_id: UUID
    registration_session_id: UUID
    candidate_person_id: UUID
    denied_person_id: UUID
    reviewer_person_id: UUID
    reviewer_account_id: UUID
    person_ids: tuple[UUID, ...]
    phone: str


def _mapped_snapshot(row: object) -> dict[str, object]:
    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.columns  # type: ignore[attr-defined]
    }


def _snapshot_matches(row: object, snapshot: dict[str, object]) -> bool:
    return all(getattr(row, key) == value for key, value in snapshot.items())


def _person(
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    full_name: str,
    identity_number: str,
    phone: str | None,
) -> Person:
    normalized = normalize_registration_identifier("phone", phone) if phone else None
    return Person(
        full_name=full_name,
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="identity_number",
        ),
        identity_digest=lookup.digest(identity_number),
        phone_ciphertext=(
            cipher.encrypt(normalized.value, field_name="phone")
            if normalized is not None
            else None
        ),
        phone_digest=(lookup.digest(normalized.value) if normalized is not None else None),
        email=None,
        email_ciphertext=None,
        email_digest=None,
        sex="male",
        date_of_birth=date(1990, 1, 2),
    )


def _setup_reject_scope_race(engine: Engine) -> RejectScopeRaceScenario:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    phone = "13800008911"
    identity_number = make_cn_id("19900102", 911)
    application_id = _setup_application(
        engine,
        identity_number=identity_number,
        phone=phone,
        full_name="并发审核候选人",
    )
    with Session(engine) as db, db.begin():
        application = db.get(RegistrationApplication, application_id)
        assert application is not None
        application.state = "conflict"
        application.conflict_code = "manual_review_required"
        registration_session = RegistrationSession(
            application_id=application_id,
            token_digest=hashlib.sha256(
                f"reject-scope-session:{application_id}".encode()
            ).hexdigest(),
            csrf_digest=hashlib.sha256(
                f"reject-scope-csrf:{application_id}".encode()
            ).hexdigest(),
            expires_at=NOW + timedelta(hours=8),
        )
        candidate = _person(
            cipher=cipher,
            lookup=lookup,
            full_name="并发审核候选人",
            identity_number=identity_number,
            phone=None,
        )
        denied = _person(
            cipher=cipher,
            lookup=lookup,
            full_name="范围禁止候选人",
            identity_number=make_cn_id("19900102", 913),
            phone=None,
        )
        reviewer = _person(
            cipher=cipher,
            lookup=lookup,
            full_name="并发审核管理员",
            identity_number=make_cn_id("19900102", 915),
            phone=None,
        )
        db.add_all([registration_session, candidate, denied, reviewer])
        db.flush()
        account = UserAccount(
            person_id=reviewer.id,
            login_phone_digest=None,
            email_normalized=None,
            password_hash=PasswordService().hash("Strong915PassAa"),
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind="phone",
                identifier_digest=hashlib.sha256(
                    f"reject-scope-reviewer:{account.id}".encode()
                ).hexdigest(),
                verified_at=NOW,
                verification_source="test",
                is_primary=True,
            )
        )
        db.flush()
        return RejectScopeRaceScenario(
            application_id=application_id,
            registration_session_id=registration_session.id,
            candidate_person_id=candidate.id,
            denied_person_id=denied.id,
            reviewer_person_id=reviewer.id,
            reviewer_account_id=account.id,
            person_ids=(candidate.id, denied.id, reviewer.id),
            phone=phone,
        )


def _cleanup_reject_scope_race(engine: Engine, scenario: RejectScopeRaceScenario) -> None:
    with Session(engine) as db, db.begin():
        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        db.execute(
            delete(AuditEvent).where(
                AuditEvent.resource_id == scenario.application_id
            )
        )
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        db.execute(
            delete(RegistrationConsentAcceptance).where(
                RegistrationConsentAcceptance.registration_application_id
                == scenario.application_id
            )
        )
        db.execute(
            delete(RegistrationSession).where(
                RegistrationSession.application_id == scenario.application_id
            )
        )
        db.execute(
            delete(RegistrationApplication).where(
                RegistrationApplication.id == scenario.application_id
            )
        )
        db.execute(delete(UserAccount).where(UserAccount.id == scenario.reviewer_account_id))
        db.execute(delete(Person).where(Person.id.in_(scenario.person_ids)))


def test_reject_rechecks_denied_contact_candidate_after_generic_update_commits(
    postgres_engine: Engine,
) -> None:
    """A winning generic contact update must make reject fail with zero writes.

    The generic update owns the target contact-membership key before it commits.
    Reject must wait at that same predicate fence; after the update commits it
    must discover the newly contact-matching, denied person and roll back all
    registration mutations.
    """

    scenario = _setup_reject_scope_race(postgres_engine)
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    target_membership_key = person_membership_advisory_lock_key(
        "phone",
        lookup.digest(scenario.phone),
    )
    writer_membership_locked = threading.Event()
    release_writer = threading.Event()
    reject_membership_attempted = threading.Event()
    try:
        with Session(postgres_engine) as baseline:
            application = baseline.get(RegistrationApplication, scenario.application_id)
            registration_session = baseline.get(
                RegistrationSession,
                scenario.registration_session_id,
            )
            assert application is not None
            assert registration_session is not None
            application_before = _mapped_snapshot(application)
            session_before = _mapped_snapshot(registration_session)
            audit_count_before = baseline.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.application_id
                )
            )

        def update_denied_person() -> bool:
            with Session(postgres_engine) as db:
                connection = db.connection()
                paused = False

                def pause_after_membership_lock(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal paused
                    if paused or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    paused = True
                    writer_membership_locked.set()
                    assert release_writer.wait(timeout=5)

                event.listen(
                    connection,
                    "after_cursor_execute",
                    pause_after_membership_lock,
                )
                try:
                    IdentityService(db, cipher=cipher, lookup=lookup).update_person(
                        scenario.denied_person_id,
                        PersonUpdate(phone=scenario.phone),
                        actor_user_id=None,
                        request_id="00000000-0000-4000-8000-000000000911",
                    )
                finally:
                    event.remove(
                        connection,
                        "after_cursor_execute",
                        pause_after_membership_lock,
                    )
                return db.scalar(select(func.count()).select_from(Person)) >= 3

        def reject_application() -> tuple[str, bool, list[str]]:
            with Session(postgres_engine) as db:
                connection = db.connection()
                lock_order: list[str] = []

                def observe_lock_order(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    _parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    normalized = " ".join(statement.casefold().split())
                    if "pg_advisory_xact_lock" in normalized:
                        lock_order.append("advisory")
                    elif (
                        "from registration_applications" in normalized
                        and "for update" in normalized
                    ):
                        lock_order.append("application")
                    elif "from people" in normalized and "for update" in normalized:
                        lock_order.append("person")
                    if _statement_locks_advisory_key(
                        statement,
                        _parameters,
                        target_membership_key,
                    ):
                        lock_order.append("membership")
                        reject_membership_attempted.set()

                event.listen(connection, "before_cursor_execute", observe_lock_order)
                try:
                    try:
                        _review_service(db).reject(
                            scenario.application_id,
                            actor_user_id=scenario.reviewer_account_id,
                            reason="人员范围发生并发变化时不得继续驳回",
                            request_id="00000000-0000-4000-8000-000000000912",
                            candidate_allowed=lambda person_ids: (
                                scenario.denied_person_id not in person_ids
                            ),
                        )
                    except RegistrationReviewNotFound:
                        outcome = "not_found"
                    else:
                        outcome = "rejected"
                finally:
                    event.remove(connection, "before_cursor_execute", observe_lock_order)
                usable = db.scalar(select(func.count()).select_from(Person)) >= 3
                return outcome, usable, lock_order

        with ThreadPoolExecutor(max_workers=2) as executor:
            writer_future = executor.submit(update_denied_person)
            assert writer_membership_locked.wait(timeout=5)
            reject_future = executor.submit(reject_application)
            assert reject_membership_attempted.wait(timeout=5)
            release_writer.set()
            assert writer_future.result(timeout=8) is True
            outcome, reject_session_usable, lock_order = reject_future.result(timeout=8)

        assert outcome == "not_found"
        assert reject_session_usable is True
        assert lock_order.index("advisory") < lock_order.index("application")
        assert lock_order.index("application") < lock_order.index("person")
        assert lock_order.index("person") < lock_order.index("membership")
        assert max(
            index for index, lock_kind in enumerate(lock_order) if lock_kind == "person"
        ) < lock_order.index("membership")
        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, scenario.application_id)
            registration_session = verification.get(
                RegistrationSession,
                scenario.registration_session_id,
            )
            denied = verification.get(Person, scenario.denied_person_id)
            assert application is not None
            assert registration_session is not None
            assert denied is not None
            assert _mapped_snapshot(application) == application_before
            assert _mapped_snapshot(registration_session) == session_before
            assert denied.phone_digest == lookup.digest(scenario.phone)
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.application_id
                )
            ) == audit_count_before
    finally:
        release_writer.set()
        _cleanup_reject_scope_race(postgres_engine, scenario)


def test_reject_serializes_late_generic_contact_update_until_after_rejection(
    postgres_engine: Engine,
) -> None:
    """A late matching contact update waits for the reject membership fence."""

    scenario = _setup_reject_scope_race(postgres_engine)
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    target_membership_key = person_membership_advisory_lock_key(
        "phone",
        lookup.digest(scenario.phone),
    )
    reject_membership_locked = threading.Event()
    release_reject = threading.Event()
    writer_membership_attempted = threading.Event()
    try:
        def reject_application() -> tuple[str, bool]:
            with Session(postgres_engine) as db:
                connection = db.connection()
                paused = False

                def pause_after_membership_lock(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal paused
                    if paused or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    paused = True
                    reject_membership_locked.set()
                    assert release_reject.wait(timeout=5)

                event.listen(
                    connection,
                    "after_cursor_execute",
                    pause_after_membership_lock,
                )
                try:
                    _review_service(db).reject(
                        scenario.application_id,
                        actor_user_id=scenario.reviewer_account_id,
                        reason="人员范围稳定时允许完成驳回",
                        request_id="00000000-0000-4000-8000-000000000913",
                        candidate_allowed=lambda person_ids: (
                            scenario.denied_person_id not in person_ids
                        ),
                    )
                finally:
                    event.remove(
                        connection,
                        "after_cursor_execute",
                        pause_after_membership_lock,
                    )
                usable = db.scalar(select(func.count()).select_from(Person)) >= 3
                return "rejected", usable

        def update_denied_person() -> bool:
            with Session(postgres_engine) as db:
                connection = db.connection()
                attempted = False

                def observe_membership_attempt(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal attempted
                    if attempted or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    attempted = True
                    writer_membership_attempted.set()

                event.listen(
                    connection,
                    "before_cursor_execute",
                    observe_membership_attempt,
                )
                try:
                    IdentityService(db, cipher=cipher, lookup=lookup).update_person(
                        scenario.denied_person_id,
                        PersonUpdate(phone=scenario.phone),
                        actor_user_id=None,
                        request_id="00000000-0000-4000-8000-000000000914",
                    )
                finally:
                    event.remove(
                        connection,
                        "before_cursor_execute",
                        observe_membership_attempt,
                    )
                return db.scalar(select(func.count()).select_from(Person)) >= 3

        with ThreadPoolExecutor(max_workers=2) as executor:
            reject_future = executor.submit(reject_application)
            assert reject_membership_locked.wait(timeout=5)
            writer_future = executor.submit(update_denied_person)
            assert writer_membership_attempted.wait(timeout=5)
            release_reject.set()
            assert reject_future.result(timeout=8) == ("rejected", True)
            assert writer_future.result(timeout=8) is True

        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, scenario.application_id)
            registration_session = verification.get(
                RegistrationSession,
                scenario.registration_session_id,
            )
            denied = verification.get(Person, scenario.denied_person_id)
            assert application is not None
            assert registration_session is not None
            assert denied is not None
            assert application.state == "rejected"
            assert registration_session.revoked_at == NOW
            assert denied.phone_digest == lookup.digest(scenario.phone)
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.application_id,
                    AuditEvent.action == "registration.conflict.reject",
                )
            ) == 1
    finally:
        release_reject.set()
        _cleanup_reject_scope_race(postgres_engine, scenario)


def test_reject_does_not_block_unrelated_person_update(
    postgres_engine: Engine,
) -> None:
    """The reject candidate barrier must not serialize unrelated person writes."""

    scenario = _setup_reject_scope_race(postgres_engine)
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    target_membership_key = person_membership_advisory_lock_key(
        "phone",
        lookup.digest(scenario.phone),
    )
    reject_membership_locked = threading.Event()
    release_reject = threading.Event()
    try:
        def reject_application() -> bool:
            with Session(postgres_engine) as db:
                connection = db.connection()
                paused = False

                def pause_after_membership_lock(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal paused
                    if paused or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    paused = True
                    reject_membership_locked.set()
                    assert release_reject.wait(timeout=5)

                event.listen(
                    connection,
                    "after_cursor_execute",
                    pause_after_membership_lock,
                )
                try:
                    _review_service(db).reject(
                        scenario.application_id,
                        actor_user_id=scenario.reviewer_account_id,
                        reason="仅稳定候选集合可以完成驳回",
                        request_id="00000000-0000-4000-8000-000000000915",
                        candidate_allowed=lambda person_ids: (
                            scenario.denied_person_id not in person_ids
                        ),
                    )
                finally:
                    event.remove(
                        connection,
                        "after_cursor_execute",
                        pause_after_membership_lock,
                    )
                return db.scalar(select(func.count()).select_from(Person)) >= 3

        def update_unrelated_person() -> bool:
            with Session(postgres_engine) as db:
                IdentityService(db, cipher=cipher, lookup=lookup).update_person(
                    scenario.reviewer_person_id,
                    PersonUpdate(full_name="不相关人员更新完成"),
                    actor_user_id=None,
                    request_id="00000000-0000-4000-8000-000000000916",
                )
                return db.scalar(select(func.count()).select_from(Person)) >= 3

        with ThreadPoolExecutor(max_workers=2) as executor:
            reject_future = executor.submit(reject_application)
            assert reject_membership_locked.wait(timeout=5)
            update_future = executor.submit(update_unrelated_person)
            try:
                assert update_future.result(timeout=1) is True
            finally:
                release_reject.set()
            assert reject_future.result(timeout=8) is True

        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, scenario.application_id)
            reviewer = verification.get(Person, scenario.reviewer_person_id)
            assert application is not None
            assert reviewer is not None
            assert application.state == "rejected"
            assert reviewer.full_name == "不相关人员更新完成"
    finally:
        release_reject.set()
        _cleanup_reject_scope_race(postgres_engine, scenario)


@dataclass(frozen=True, slots=True)
class GenericCreateRejectRaceScenario:
    application_id: UUID
    registration_session_id: UUID
    candidate_person_id: UUID
    reviewer_person_id: UUID
    reviewer_account_id: UUID
    person_ids: tuple[UUID, ...]
    identifier_kind: IdentifierKind
    identifier_value: str
    student_number: str


def _setup_generic_create_reject_race(
    engine: Engine,
    *,
    identifier_kind: IdentifierKind,
    identifier_value: str,
    student_number: str,
) -> GenericCreateRejectRaceScenario:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    identity_number = make_cn_id("19900102", 981)
    application_id = _setup_application(
        engine,
        identity_number=identity_number,
        identifier_kind=identifier_kind,
        identifier_value=identifier_value,
        full_name="并发新建审核候选人",
    )
    with Session(engine) as db, db.begin():
        application = db.get(RegistrationApplication, application_id)
        assert application is not None
        application.state = "conflict"
        application.conflict_code = "manual_review_required"
        registration_session = RegistrationSession(
            application_id=application_id,
            token_digest=hashlib.sha256(
                f"generic-create-session:{application_id}".encode()
            ).hexdigest(),
            csrf_digest=hashlib.sha256(
                f"generic-create-csrf:{application_id}".encode()
            ).hexdigest(),
            expires_at=NOW + timedelta(hours=8),
        )
        candidate = _person(
            cipher=cipher,
            lookup=lookup,
            full_name="并发新建审核候选人",
            identity_number=identity_number,
            phone=None,
        )
        reviewer = _person(
            cipher=cipher,
            lookup=lookup,
            full_name="并发新建审核管理员",
            identity_number=make_cn_id("19900102", 983),
            phone=None,
        )
        db.add_all([registration_session, candidate, reviewer])
        db.flush()
        account = UserAccount(
            person_id=reviewer.id,
            login_phone_digest=None,
            email_normalized=None,
            password_hash=PasswordService().hash("Strong983PassAa"),
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind="phone",
                identifier_digest=hashlib.sha256(
                    f"generic-create-reviewer:{account.id}".encode()
                ).hexdigest(),
                verified_at=NOW,
                verification_source="test",
                is_primary=True,
            )
        )
        db.flush()
        return GenericCreateRejectRaceScenario(
            application_id=application_id,
            registration_session_id=registration_session.id,
            candidate_person_id=candidate.id,
            reviewer_person_id=reviewer.id,
            reviewer_account_id=account.id,
            person_ids=(candidate.id, reviewer.id),
            identifier_kind=identifier_kind,
            identifier_value=identifier_value,
            student_number=student_number,
        )


def _generic_create_membership_key(
    scenario: GenericCreateRejectRaceScenario,
    lookup: LookupDigest,
) -> int:
    identifier = normalize_registration_identifier(
        scenario.identifier_kind,
        scenario.identifier_value,
    )
    contact_digest = (
        lookup.digest(identifier.value)
        if identifier.kind == "phone"
        else lookup.digest_domain("person-email", identifier.value)
    )
    return person_membership_advisory_lock_key(identifier.kind, contact_digest)


def _matching_student_input(
    scenario: GenericCreateRejectRaceScenario,
    *,
    identity_sequence: int,
) -> StudentCreate:
    return StudentCreate(
        full_name="新建范围候选人员",
        identity_number=make_cn_id("19900102", identity_sequence),
        sex="male",
        date_of_birth=date(1990, 1, 2),
        phone=(
            scenario.identifier_value
            if scenario.identifier_kind == "phone"
            else None
        ),
        email=(
            scenario.identifier_value
            if scenario.identifier_kind == "email"
            else None
        ),
        student_number=scenario.student_number,
    )


def _cleanup_generic_create_reject_race(
    engine: Engine,
    scenario: GenericCreateRejectRaceScenario,
) -> None:
    with Session(engine) as db, db.begin():
        created_person_ids = list(
            db.scalars(
                select(StudentProfile.person_id).where(
                    StudentProfile.student_number == scenario.student_number
                )
            )
        )
        db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        db.execute(
            delete(AuditEvent).where(
                AuditEvent.resource_id == scenario.application_id
            )
        )
        db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
        db.execute(
            delete(RegistrationConsentAcceptance).where(
                RegistrationConsentAcceptance.registration_application_id
                == scenario.application_id
            )
        )
        db.execute(
            delete(RegistrationSession).where(
                RegistrationSession.application_id == scenario.application_id
            )
        )
        db.execute(
            delete(RegistrationApplication).where(
                RegistrationApplication.id == scenario.application_id
            )
        )
        db.execute(
            delete(AccountLoginIdentifier).where(
                AccountLoginIdentifier.account_id == scenario.reviewer_account_id
            )
        )
        db.execute(delete(UserAccount).where(UserAccount.id == scenario.reviewer_account_id))
        if created_person_ids:
            db.execute(
                delete(StudentProfile).where(
                    StudentProfile.person_id.in_(created_person_ids)
                )
            )
        db.execute(
            delete(Person).where(
                Person.id.in_([*scenario.person_ids, *created_person_ids])
            )
        )


def _only_original_candidate(
    scenario: GenericCreateRejectRaceScenario,
    person_ids: list[UUID],
) -> bool:
    return set(person_ids) == {scenario.candidate_person_id}


@pytest.mark.parametrize(
    ("identifier_kind", "identifier_value", "identity_sequence"),
    [
        ("phone", "13800007981", 985),
        ("email", "generic-create-observer@example.com", 987),
    ],
    ids=("phone", "email"),
)
def test_reject_rechecks_new_generic_student_after_membership_writer_commits(
    postgres_engine: Engine,
    identifier_kind: IdentifierKind,
    identifier_value: str,
    identity_sequence: int,
) -> None:
    scenario = _setup_generic_create_reject_race(
        postgres_engine,
        identifier_kind=identifier_kind,
        identifier_value=identifier_value,
        student_number=f"GC-WRITER-{identifier_kind}",
    )
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    target_membership_key = _generic_create_membership_key(scenario, lookup)
    # Email writers now meet at the earlier shared credential fence. Keep the
    # winner paused at membership and all post-race state/audit assertions.
    attempt_keys = {target_membership_key}
    if identifier_kind == "email":
        attempt_keys.update(registration_credential_lock_keys({
            registration_identifier_digest(lookup, normalize_registration_identifier("email", identifier_value)),
        }))
    writer_membership_locked = threading.Event()
    release_writer = threading.Event()
    reject_membership_attempted = threading.Event()
    try:
        with Session(postgres_engine) as baseline:
            application = baseline.get(RegistrationApplication, scenario.application_id)
            registration_session = baseline.get(
                RegistrationSession,
                scenario.registration_session_id,
            )
            assert application is not None
            assert registration_session is not None
            application_before = _mapped_snapshot(application)
            session_before = _mapped_snapshot(registration_session)
            audit_count_before = baseline.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.application_id
                )
            )

        def create_matching_student() -> tuple[UUID, bool]:
            with Session(postgres_engine) as db:
                connection = db.connection()
                paused = False

                def pause_after_membership_lock(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal paused
                    if paused or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    paused = True
                    writer_membership_locked.set()
                    assert release_writer.wait(timeout=5)

                event.listen(
                    connection,
                    "after_cursor_execute",
                    pause_after_membership_lock,
                )
                try:
                    created = IdentityService(
                        db,
                        cipher=cipher,
                        lookup=lookup,
                    ).create_student(
                        _matching_student_input(
                            scenario,
                            identity_sequence=identity_sequence,
                        )
                    )
                finally:
                    event.remove(
                        connection,
                        "after_cursor_execute",
                        pause_after_membership_lock,
                    )
                return created.id, db.scalar(select(1)) == 1

        def reject_application() -> tuple[str, bool]:
            with Session(postgres_engine) as db:
                connection = db.connection()
                attempted = False

                def observe_membership_attempt(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal attempted
                    if attempted or not any(
                        _statement_locks_advisory_key(statement, parameters, key)
                        for key in attempt_keys
                    ):
                        return
                    attempted = True
                    reject_membership_attempted.set()

                event.listen(
                    connection,
                    "before_cursor_execute",
                    observe_membership_attempt,
                )
                try:
                    try:
                        _review_service(db).reject(
                            scenario.application_id,
                            actor_user_id=scenario.reviewer_account_id,
                            reason="新增候选在并发审核中必须重新核验",
                            request_id="00000000-0000-4000-8000-000000000981",
                            candidate_allowed=lambda person_ids: _only_original_candidate(
                                scenario,
                                person_ids,
                            ),
                        )
                    except RegistrationReviewNotFound:
                        outcome = "not_found"
                    else:
                        outcome = "rejected"
                finally:
                    event.remove(
                        connection,
                        "before_cursor_execute",
                        observe_membership_attempt,
                    )
                return outcome, db.scalar(select(1)) == 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                writer_future = executor.submit(create_matching_student)
                assert writer_membership_locked.wait(timeout=5)
                reject_future = executor.submit(reject_application)
                assert reject_membership_attempted.wait(timeout=5)
                release_writer.set()
                created_person_id, writer_session_usable = writer_future.result(timeout=8)
                outcome, reject_session_usable = reject_future.result(timeout=8)
            finally:
                release_writer.set()

        assert writer_session_usable is True
        assert reject_session_usable is True
        assert outcome == "not_found"
        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, scenario.application_id)
            registration_session = verification.get(
                RegistrationSession,
                scenario.registration_session_id,
            )
            created_profile = verification.scalar(
                select(StudentProfile).where(
                    StudentProfile.student_number == scenario.student_number
                )
            )
            assert application is not None
            assert registration_session is not None
            assert created_profile is not None
            assert created_profile.person_id == created_person_id
            assert _snapshot_matches(application, application_before)
            assert _snapshot_matches(registration_session, session_before)
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.application_id
                )
            ) == audit_count_before
    finally:
        release_writer.set()
        _cleanup_generic_create_reject_race(postgres_engine, scenario)


@pytest.mark.parametrize(
    ("identifier_kind", "identifier_value", "identity_sequence"),
    [
        ("phone", "13800007983", 989),
        ("email", "generic-create-late@example.com", 991),
    ],
    ids=("phone", "email"),
)
def test_reject_serializes_late_generic_student_until_after_rejection(
    postgres_engine: Engine,
    identifier_kind: IdentifierKind,
    identifier_value: str,
    identity_sequence: int,
) -> None:
    scenario = _setup_generic_create_reject_race(
        postgres_engine,
        identifier_kind=identifier_kind,
        identifier_value=identifier_value,
        student_number=f"GC-REJECT-{identifier_kind}",
    )
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(reversed(range(32))))
    target_membership_key = _generic_create_membership_key(scenario, lookup)
    attempt_keys = {target_membership_key}
    if identifier_kind == "email":
        attempt_keys.update(registration_credential_lock_keys({
            registration_identifier_digest(lookup, normalize_registration_identifier("email", identifier_value)),
        }))
    reject_membership_locked = threading.Event()
    release_reject = threading.Event()
    writer_membership_attempted = threading.Event()
    try:
        def reject_application() -> bool:
            with Session(postgres_engine) as db:
                connection = db.connection()
                paused = False

                def pause_after_membership_lock(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal paused
                    if paused or not _statement_locks_advisory_key(
                        statement,
                        parameters,
                        target_membership_key,
                    ):
                        return
                    paused = True
                    reject_membership_locked.set()
                    assert release_reject.wait(timeout=5)

                event.listen(
                    connection,
                    "after_cursor_execute",
                    pause_after_membership_lock,
                )
                try:
                    _review_service(db).reject(
                        scenario.application_id,
                        actor_user_id=scenario.reviewer_account_id,
                        reason="审核先完成后新增候选可以继续创建",
                        request_id="00000000-0000-4000-8000-000000000982",
                        candidate_allowed=lambda person_ids: _only_original_candidate(
                            scenario,
                            person_ids,
                        ),
                    )
                finally:
                    event.remove(
                        connection,
                        "after_cursor_execute",
                        pause_after_membership_lock,
                    )
                return db.scalar(select(1)) == 1

        def create_matching_student() -> tuple[UUID, bool]:
            with Session(postgres_engine) as db:
                connection = db.connection()
                attempted = False

                def observe_membership_attempt(
                    _connection: object,
                    _cursor: object,
                    statement: str,
                    parameters: object,
                    _context: object,
                    _executemany: bool,
                ) -> None:
                    nonlocal attempted
                    if attempted or not any(
                        _statement_locks_advisory_key(statement, parameters, key)
                        for key in attempt_keys
                    ):
                        return
                    attempted = True
                    writer_membership_attempted.set()

                event.listen(
                    connection,
                    "before_cursor_execute",
                    observe_membership_attempt,
                )
                try:
                    created = IdentityService(
                        db,
                        cipher=cipher,
                        lookup=lookup,
                    ).create_student(
                        _matching_student_input(
                            scenario,
                            identity_sequence=identity_sequence,
                        )
                    )
                finally:
                    event.remove(
                        connection,
                        "before_cursor_execute",
                        observe_membership_attempt,
                    )
                return created.id, db.scalar(select(1)) == 1

        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                reject_future = executor.submit(reject_application)
                assert reject_membership_locked.wait(timeout=5)
                writer_future = executor.submit(create_matching_student)
                assert writer_membership_attempted.wait(timeout=5)
                assert not writer_future.done()
                release_reject.set()
                assert reject_future.result(timeout=8) is True
                created_person_id, writer_session_usable = writer_future.result(timeout=8)
            finally:
                release_reject.set()

        assert writer_session_usable is True
        with Session(postgres_engine) as verification:
            application = verification.get(RegistrationApplication, scenario.application_id)
            registration_session = verification.get(
                RegistrationSession,
                scenario.registration_session_id,
            )
            created_profile = verification.scalar(
                select(StudentProfile).where(
                    StudentProfile.student_number == scenario.student_number
                )
            )
            assert application is not None
            assert registration_session is not None
            assert created_profile is not None
            assert created_profile.person_id == created_person_id
            assert application.state == "rejected"
            assert registration_session.revoked_at == NOW
            assert verification.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.resource_id == scenario.application_id,
                    AuditEvent.action == "registration.conflict.reject",
                )
            ) == 1
    finally:
        release_reject.set()
        _cleanup_generic_create_reject_race(postgres_engine, scenario)
