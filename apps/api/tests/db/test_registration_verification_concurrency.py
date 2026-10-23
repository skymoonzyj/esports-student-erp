from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import RegistrationVerificationChallenge
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationInvalidOrExpired,
    VerificationRateLimited,
)
from app.security.encryption import LookupDigest

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
LOOKUP_KEY = bytes(range(32))
VERIFICATION_PEPPER = bytes(reversed(range(32)))


def _service(db: Session, sender: CapturingVerificationSender):
    return RegistrationVerificationService(
        db,
        lookup=LookupDigest(LOOKUP_KEY),
        verification_pepper=VERIFICATION_PEPPER,
        sender=sender,
        app_env="test",
        clock=lambda: NOW,
    )


def test_two_sessions_consuming_one_code_have_exactly_one_success(
    postgres_engine: Engine,
) -> None:
    sender = CapturingVerificationSender(messages=[])
    with Session(postgres_engine) as setup:
        issued = _service(setup, sender).issue(
            identifier_kind="phone",
            identifier="13800008888",
            purpose="registration",
            request_source="127.0.0.1",
        )
    code = sender.code_for(issued.challenge_id)
    barrier = threading.Barrier(2)

    def consume() -> str:
        with Session(postgres_engine) as db:
            barrier.wait(timeout=5)
            try:
                _service(db, CapturingVerificationSender(messages=[])).consume(
                    issued.challenge_id,
                    code,
                )
            except VerificationInvalidOrExpired:
                return "invalid"
            return "success"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = sorted(executor.map(lambda _index: consume(), range(2)))

        assert results == ["invalid", "success"]
        with Session(postgres_engine) as verification:
            stored = verification.scalar(
                select(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.id == issued.challenge_id
                )
            )
            assert stored is not None
            assert stored.consumed_at == NOW
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.id == issued.challenge_id
                )
            )


def test_two_sessions_issuing_same_identifier_respect_resend_limit(
    postgres_engine: Engine,
) -> None:
    phone = "13800008887"
    identifier_digest = registration_identifier_digest(
        LookupDigest(LOOKUP_KEY),
        normalize_registration_identifier("phone", phone),
    )
    barrier = threading.Barrier(2)

    def issue() -> str:
        with Session(postgres_engine) as db:
            barrier.wait(timeout=5)
            try:
                _service(db, CapturingVerificationSender(messages=[])).issue(
                    identifier_kind="phone",
                    identifier=phone,
                    purpose="registration",
                    request_source="127.0.0.1",
                )
            except VerificationRateLimited:
                return "limited"
            return "success"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = sorted(executor.map(lambda _index: issue(), range(2)))

        assert results == ["limited", "success"]
        with Session(postgres_engine) as verification:
            assert (
                verification.scalar(
                    select(func.count(RegistrationVerificationChallenge.id)).where(
                        RegistrationVerificationChallenge.identifier_digest
                        == identifier_digest
                    )
                )
                == 1
            )
    finally:
        with Session(postgres_engine) as cleanup, cleanup.begin():
            cleanup.execute(
                delete(RegistrationVerificationChallenge).where(
                    RegistrationVerificationChallenge.identifier_digest
                    == identifier_digest
                )
            )
