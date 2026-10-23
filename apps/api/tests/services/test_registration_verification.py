from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.registration.delivery import CapturedVerification, CapturingVerificationSender
from app.registration.models import RegistrationVerificationChallenge
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationDeliveryNotAllowed,
    VerificationInvalidOrExpired,
    VerificationRateLimited,
    VerificationTarget,
)
from app.security.encryption import LookupDigest


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 8, 15, 8, tzinfo=UTC))


@pytest.fixture
def captured_sender() -> CapturingVerificationSender:
    return CapturingVerificationSender(messages=[])


@pytest.fixture
def verification_service(
    db: Session,
    clock: MutableClock,
    captured_sender: CapturingVerificationSender,
) -> RegistrationVerificationService:
    return RegistrationVerificationService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        verification_pepper=bytes(reversed(range(32))),
        sender=captured_sender,
        app_env="test",
        clock=clock,
    )


def _wrong_code(code: str) -> str:
    return "000001" if code == "000000" else "000000"


def test_code_expires_at_ten_minutes(
    verification_service: RegistrationVerificationService,
    captured_sender: CapturingVerificationSender,
    clock: MutableClock,
) -> None:
    issued = verification_service.issue(
        identifier_kind="phone",
        identifier="13800009999",
        purpose="registration",
        request_source="127.0.0.1",
    )
    code = captured_sender.code_for(issued.challenge_id)
    clock.advance(minutes=10)

    with pytest.raises(VerificationInvalidOrExpired):
        verification_service.consume(issued.challenge_id, code)


def test_code_expiry_is_checked_after_target_lock_preparation(
    verification_service: RegistrationVerificationService,
    captured_sender: CapturingVerificationSender,
    clock: MutableClock,
    db: Session,
) -> None:
    issued = verification_service.issue(
        identifier_kind="phone",
        identifier="13800009993",
        purpose="registration",
        request_source="127.0.0.1",
    )
    code = captured_sender.code_for(issued.challenge_id)
    stored = db.get(RegistrationVerificationChallenge, issued.challenge_id)
    assert stored is not None
    clock.advance(minutes=9, seconds=59)

    def prepare_target() -> VerificationTarget:
        clock.advance(seconds=1)
        return VerificationTarget(
            application_id=None,
            account_id=None,
            identifier_kind="phone",
            identifier_digest=stored.identifier_digest,
            purpose="registration",
        )

    with pytest.raises(VerificationInvalidOrExpired):
        verification_service.consume_with(
            issued.challenge_id,
            code,
            prepare_target=prepare_target,
        )


def test_fifth_wrong_attempt_closes_challenge(
    verification_service: RegistrationVerificationService,
    captured_sender: CapturingVerificationSender,
) -> None:
    issued = verification_service.issue(
        identifier_kind="email",
        identifier="new@example.com",
        purpose="registration",
        request_source="127.0.0.1",
    )
    code = captured_sender.code_for(issued.challenge_id)
    wrong_code = _wrong_code(code)

    for _ in range(5):
        with pytest.raises(VerificationInvalidOrExpired):
            verification_service.consume(issued.challenge_id, wrong_code)
    with pytest.raises(VerificationInvalidOrExpired):
        verification_service.consume(issued.challenge_id, code)


def test_valid_code_is_consumed_once(
    verification_service: RegistrationVerificationService,
    captured_sender: CapturingVerificationSender,
) -> None:
    issued = verification_service.issue(
        identifier_kind="phone",
        identifier="13800009998",
        purpose="registration",
        request_source="127.0.0.1",
    )
    code = captured_sender.code_for(issued.challenge_id)

    consumed = verification_service.consume(issued.challenge_id, code)

    assert consumed.challenge_id == issued.challenge_id
    assert consumed.identifier_kind == "phone"
    assert consumed.purpose == "registration"
    assert consumed.identifier_digest not in repr(consumed)
    with pytest.raises(VerificationInvalidOrExpired):
        verification_service.consume(issued.challenge_id, code)


def test_resend_is_blocked_until_sixty_seconds(
    verification_service: RegistrationVerificationService,
    clock: MutableClock,
) -> None:
    issue = {
        "identifier_kind": "phone",
        "identifier": "13800009997",
        "purpose": "registration",
        "request_source": "127.0.0.1",
    }
    verification_service.issue(**issue)
    clock.advance(seconds=59, microseconds=999_999)

    with pytest.raises(VerificationRateLimited):
        verification_service.issue(**issue)

    clock.advance(microseconds=1)
    verification_service.issue(**issue)


def test_identifier_is_limited_to_five_sends_in_twenty_four_hours(
    verification_service: RegistrationVerificationService,
    clock: MutableClock,
) -> None:
    started_at = clock.current
    issue = {
        "identifier_kind": "email",
        "identifier": "limited@example.com",
        "purpose": "registration",
        "request_source": "127.0.0.1",
    }
    for _ in range(5):
        verification_service.issue(**issue)
        clock.advance(seconds=60)

    with pytest.raises(VerificationRateLimited):
        verification_service.issue(**issue)

    clock.current = started_at + timedelta(hours=24)
    verification_service.issue(**issue)


def test_request_source_is_limited_to_twenty_sends_per_hour(
    verification_service: RegistrationVerificationService,
    clock: MutableClock,
) -> None:
    started_at = clock.current
    for sequence in range(20):
        verification_service.issue(
            identifier_kind="phone",
            identifier=f"139{sequence:08d}",
            purpose="registration",
            request_source="198.51.100.8",
        )
        clock.advance(seconds=60)

    with pytest.raises(VerificationRateLimited):
        verification_service.issue(
            identifier_kind="phone",
            identifier="13900000020",
            purpose="registration",
            request_source="198.51.100.8",
        )

    clock.current = started_at + timedelta(hours=1)
    verification_service.issue(
        identifier_kind="phone",
        identifier="19990000006",
        purpose="registration",
        request_source="198.51.100.8",
    )


def test_challenge_persists_only_digests(
    verification_service: RegistrationVerificationService,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    destination = "private@example.com"
    request_source = "203.0.113.10"
    issued = verification_service.issue(
        identifier_kind="email",
        identifier=destination,
        purpose="password_reset",
        request_source=request_source,
    )
    code = captured_sender.code_for(issued.challenge_id)
    stored = db.get(RegistrationVerificationChallenge, issued.challenge_id)

    assert stored is not None
    assert len(stored.code_digest) == 64
    assert code not in stored.code_digest
    assert destination not in stored.identifier_digest
    assert request_source not in stored.request_source_digest
    assert db.scalar(select(func.count(RegistrationVerificationChallenge.id))) == 1


def test_equivalent_ipv6_sources_share_one_rate_limit_digest(
    verification_service: RegistrationVerificationService,
    db: Session,
) -> None:
    first = verification_service.issue(
        identifier_kind="phone",
        identifier="13800009995",
        purpose="registration",
        request_source="2001:0db8:0000:0000:0000:0000:0000:0001",
    )
    second = verification_service.issue(
        identifier_kind="phone",
        identifier="13800009994",
        purpose="registration",
        request_source="2001:db8::1",
    )

    digests = {
        db.get(RegistrationVerificationChallenge, challenge_id).request_source_digest  # type: ignore[union-attr]
        for challenge_id in (first.challenge_id, second.challenge_id)
    }
    assert len(digests) == 1


def test_sender_receives_normalized_destination(
    verification_service: RegistrationVerificationService,
    captured_sender: CapturingVerificationSender,
) -> None:
    issued = verification_service.issue(
        identifier_kind="email",
        identifier=" User@Example.COM ",
        purpose="registration",
        request_source="127.0.0.1",
    )

    message = captured_sender.message_for(issued.challenge_id)
    assert message.destination == "user@example.com"
    assert message.purpose == "registration"


def test_captured_message_repr_hides_destination_and_code() -> None:
    message = CapturedVerification(
        challenge_id=UUID("00000000-0000-0000-0000-000000000001"),
        destination="private@example.com",
        code="123456",
        purpose="registration",
    )

    rendered = repr(message)
    assert message.destination not in rendered
    assert message.code not in rendered


def test_delivery_failure_rolls_back_challenge(
    db: Session,
    clock: MutableClock,
) -> None:
    class FailingSender:
        def send_code(self, **_values: object) -> None:
            raise RuntimeError("delivery unavailable")

    service = RegistrationVerificationService(
        db,
        lookup=LookupDigest(bytes(range(32))),
        verification_pepper=bytes(reversed(range(32))),
        sender=FailingSender(),
        app_env="test",
        clock=clock,
    )

    with pytest.raises(RuntimeError, match="delivery unavailable"):
        service.issue(
            identifier_kind="phone",
            identifier="13800009996",
            purpose="registration",
            request_source="127.0.0.1",
        )

    assert db.scalar(select(func.count(RegistrationVerificationChallenge.id))) == 0


def test_production_rejects_capturing_sender(
    db: Session,
    captured_sender: CapturingVerificationSender,
) -> None:
    with pytest.raises(VerificationDeliveryNotAllowed):
        RegistrationVerificationService(
            db,
            lookup=LookupDigest(bytes(range(32))),
            verification_pepper=bytes(reversed(range(32))),
            sender=captured_sender,
            app_env="production",
        )
