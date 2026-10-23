from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from fastapi import FastAPI, Request, Response
from sqlalchemy import delete, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ProblemError
from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    RegistrationApplication,
    RegistrationSession,
    RegistrationVerificationChallenge,
)
from app.registration.router import verify_registration_contact_change
from app.registration.schemas import RegistrationContactVerifyRequest
from app.registration.session_service import RegistrationContext
from app.registration.verification_service import (
    RegistrationVerificationService,
    registration_advisory_lock_key,
)
from app.security.encryption import EnvelopeCipher, LookupDigest

NOW = datetime(2026, 8, 17, 8, tzinfo=UTC)
FIELD_KEY = bytes(range(32))
VERIFICATION_KEY = bytes(range(1, 33))
ACTIVE_STATES = {
    "contact_verified",
    "identity_completed",
    "pending_guardian",
    "ready_to_finalize",
    "conflict",
}


@dataclass(frozen=True, slots=True)
class ContactChange:
    application_id: UUID
    session_id: UUID
    source: str
    destination: str
    source_digest: str
    destination_digest: str
    source_ciphertext: str
    challenge_id: UUID
    code: str


@dataclass(frozen=True, slots=True)
class ContactSwapScenario:
    changes: tuple[ContactChange, ContactChange]
    expires_at: datetime


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


def _setup_scenario(engine: Engine) -> ContactSwapScenario:
    cipher = EnvelopeCipher(FIELD_KEY)
    lookup = LookupDigest(FIELD_KEY)
    contacts = ("13800008621", "13800008622")
    expires_at = NOW + timedelta(days=30)
    application_ids: list[UUID] = []
    session_ids: list[UUID] = []
    digests: list[str] = []
    ciphertexts: list[str] = []

    with Session(engine) as setup, setup.begin():
        for index, contact in enumerate(contacts, start=1):
            identifier = normalize_registration_identifier("phone", contact)
            digest = registration_identifier_digest(lookup, identifier)
            ciphertext = cipher.encrypt(
                identifier.value,
                field_name="registration_identifier",
            )
            application = RegistrationApplication(
                primary_purpose="student",
                state="identity_completed",
                verified_identifier_kind="phone",
                verified_identifier_digest=digest,
                verified_identifier_ciphertext=ciphertext,
                password_hash=f"contact-swap-password-hash-{index}",
                full_name=f"联系方式交换学员{index}",
                identity_ciphertext=cipher.encrypt(
                    f"contact-swap-identity-{index}",
                    field_name="registration_identity",
                ),
                identity_digest=lookup.digest(f"contact-swap-identity-{index}"),
                sex="male",
                date_of_birth=date(2012, 1, index),
                expires_at=expires_at,
            )
            setup.add(application)
            setup.flush()
            registration_session = RegistrationSession(
                application_id=application.id,
                token_digest=f"contact-swap-token-{index}",
                csrf_digest=f"contact-swap-csrf-{index}",
                expires_at=NOW + timedelta(hours=1),
            )
            setup.add(registration_session)
            setup.flush()
            application_ids.append(application.id)
            session_ids.append(registration_session.id)
            digests.append(digest)
            ciphertexts.append(ciphertext)

    issued: list[tuple[UUID, str]] = []
    for index, destination in enumerate(reversed(contacts), start=1):
        sender = CapturingVerificationSender(messages=[])
        with Session(engine) as challenge_db:
            challenge = _verification(challenge_db, sender).issue(
                identifier_kind="phone",
                identifier=destination,
                purpose="registration",
                request_source=f"127.0.6.{index}",
                application_id=application_ids[index - 1],
            )
        issued.append(
            (challenge.challenge_id, sender.code_for(challenge.challenge_id))
        )

    return ContactSwapScenario(
        changes=(
            ContactChange(
                application_id=application_ids[0],
                session_id=session_ids[0],
                source=contacts[0],
                destination=contacts[1],
                source_digest=digests[0],
                destination_digest=digests[1],
                source_ciphertext=ciphertexts[0],
                challenge_id=issued[0][0],
                code=issued[0][1],
            ),
            ContactChange(
                application_id=application_ids[1],
                session_id=session_ids[1],
                source=contacts[1],
                destination=contacts[0],
                source_digest=digests[1],
                destination_digest=digests[0],
                source_ciphertext=ciphertexts[1],
                challenge_id=issued[1][0],
                code=issued[1][1],
            ),
        ),
        expires_at=expires_at,
    )


def _cleanup_scenario(engine: Engine, scenario: ContactSwapScenario) -> None:
    application_ids = {change.application_id for change in scenario.changes}
    challenge_ids = {change.challenge_id for change in scenario.changes}
    with Session(engine) as cleanup, cleanup.begin():
        cleanup.execute(
            delete(RegistrationVerificationChallenge).where(
                RegistrationVerificationChallenge.id.in_(challenge_ids)
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


def _parameter_values(parameters: object) -> tuple[object, ...]:
    if isinstance(parameters, dict):
        return tuple(parameters.values())
    if isinstance(parameters, (list, tuple)):
        return tuple(parameters)
    return (parameters,)


def _application_row_lock(statement: str, parameters: object, application_id: UUID) -> bool:
    normalized = " ".join(statement.casefold().split())
    values = _parameter_values(parameters)
    return (
        "from registration_applications" in normalized
        and "for update" in normalized
        and (application_id in values or str(application_id) in values)
    )


def test_minor_contact_swap_uses_shared_sorted_locks_without_deadlock(
    postgres_engine: Engine,
    test_settings: Settings,
) -> None:
    scenario = _setup_scenario(postgres_engine)
    application = FastAPI()
    application.state.settings = test_settings
    first_row_locked = threading.Event()
    release_first = threading.Event()
    smaller_key = min(
        registration_advisory_lock_key(
            "registration-application-identifier",
            change.destination_digest,
        )
        for change in scenario.changes
    )
    first = next(
        change
        for change in scenario.changes
        if registration_advisory_lock_key(
            "registration-application-identifier",
            change.destination_digest,
        )
        == smaller_key
    )
    second = next(change for change in scenario.changes if change != first)

    def run_change(
        change: ContactChange,
        *,
        pause_after_own_row: bool,
    ) -> tuple[str, str | None, int | None, str | None, bool]:
        sender = CapturingVerificationSender(messages=[])
        with Session(postgres_engine) as db:
            connection = db.connection()

            def before_cursor_execute(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if pause_after_own_row:
                    return
                if (
                    "pg_advisory_xact_lock" in statement.casefold()
                    and smaller_key in _parameter_values(parameters)
                ):
                    release_first.set()

            def after_cursor_execute(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                _context: object,
                _executemany: bool,
            ) -> None:
                if not _application_row_lock(
                    statement,
                    parameters,
                    change.application_id,
                ):
                    return
                if pause_after_own_row and not first_row_locked.is_set():
                    first_row_locked.set()
                    if not release_first.wait(timeout=5):
                        raise AssertionError("timed out releasing first contact change")
                elif not pause_after_own_row:
                    release_first.set()

            event.listen(connection, "before_cursor_execute", before_cursor_execute)
            event.listen(connection, "after_cursor_execute", after_cursor_execute)
            try:
                stored_application = db.get(
                    RegistrationApplication,
                    change.application_id,
                )
                stored_session = db.get(RegistrationSession, change.session_id)
                assert stored_application is not None
                assert stored_session is not None
                request = Request({"type": "http", "app": application})
                request.state.request_id = f"contact-swap-{change.application_id}"
                try:
                    verify_registration_contact_change(
                        input=RegistrationContactVerifyRequest(
                            challenge_id=change.challenge_id,
                            code=change.code,
                            identifier_kind="phone",
                            identifier=change.destination,
                        ),
                        request=request,
                        response=Response(),
                        context=RegistrationContext(
                            session=stored_session,
                            application=stored_application,
                        ),
                        db=db,
                        service=_verification(db, sender),
                    )
                except ProblemError as exc:
                    outcome = "private_unavailable"
                    sqlstate = None
                    status = exc.status
                    code = exc.code
                except DBAPIError as exc:
                    outcome = "database_error"
                    sqlstate = getattr(exc.orig, "sqlstate", None)
                    status = None
                    code = None
                else:
                    outcome = "changed"
                    sqlstate = None
                    status = 200
                    code = None
            finally:
                event.remove(connection, "before_cursor_execute", before_cursor_execute)
                event.remove(connection, "after_cursor_execute", after_cursor_execute)
            reusable = db.scalar(select(1)) == 1
            return outcome, sqlstate, status, code, reusable

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                first_future = executor.submit(
                    run_change,
                    first,
                    pause_after_own_row=True,
                )
                assert first_row_locked.wait(timeout=5)
                second_future = executor.submit(
                    run_change,
                    second,
                    pause_after_own_row=False,
                )
                results = (
                    first_future.result(timeout=8),
                    second_future.result(timeout=8),
                )
            finally:
                release_first.set()

        assert {result[1] for result in results} == {None}
        assert {result[0] for result in results} == {"private_unavailable"}
        assert {result[2] for result in results} == {409}
        assert {result[3] for result in results} == {
            "registration_action_unavailable"
        }
        assert all(result[4] for result in results)

        with Session(postgres_engine) as verification:
            for change in scenario.changes:
                stored_application = verification.get(
                    RegistrationApplication,
                    change.application_id,
                )
                stored_challenge = verification.get(
                    RegistrationVerificationChallenge,
                    change.challenge_id,
                )
                stored_session = verification.get(
                    RegistrationSession,
                    change.session_id,
                )
                assert stored_application is not None
                assert stored_application.state == "identity_completed"
                assert stored_application.verified_identifier_kind == "phone"
                assert (
                    stored_application.verified_identifier_digest
                    == change.source_digest
                )
                assert (
                    stored_application.verified_identifier_ciphertext
                    == change.source_ciphertext
                )
                assert stored_application.expires_at == scenario.expires_at
                assert stored_challenge is not None
                assert stored_challenge.consumed_at is None
                assert stored_challenge.attempts == 0
                assert stored_session is not None
                assert stored_session.revoked_at is None
                assert verification.scalar(
                    select(func.count(RegistrationApplication.id)).where(
                        RegistrationApplication.verified_identifier_digest
                        == change.source_digest,
                        RegistrationApplication.state.in_(ACTIVE_STATES),
                    )
                ) == 1
    finally:
        release_first.set()
        _cleanup_scenario(postgres_engine, scenario)
