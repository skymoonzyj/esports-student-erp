from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.registration.delivery import (
    CapturingVerificationSender,
    TestMailboxVerificationSender,
    VerificationSender,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    ChallengePurpose,
    IdentifierKind,
    RegistrationApplication,
    RegistrationSession,
    RegistrationVerificationChallenge,
)
from app.security.encryption import LookupDigest


class VerificationServiceError(ValueError):
    code = "verification_service_error"


class VerificationInvalidOrExpired(VerificationServiceError):
    code = "verification_invalid_or_expired"


class VerificationRateLimited(VerificationServiceError):
    code = "verification_rate_limited"


class VerificationDeliveryNotAllowed(VerificationServiceError):
    code = "verification_delivery_not_allowed"


class VerificationTargetMismatch(VerificationServiceError):
    code = "verification_target_mismatch"


class VerificationBusy(VerificationServiceError):
    code = "verification_busy"


def registration_advisory_lock_key(scope: str, digest: str) -> int:
    return int.from_bytes(
        hashlib.sha256(f"{scope}\x00{digest}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )


def registration_credential_lock_keys(
    identifier_digests: set[str],
    *,
    identity_digests: set[str] | None = None,
) -> list[int]:
    keys = {
        registration_advisory_lock_key(scope, digest)
        for digest in identifier_digests
        for scope in ("identifier", "registration-application-identifier")
    }
    keys.update(
        registration_advisory_lock_key("identity", digest)
        for digest in identity_digests or set()
    )
    return sorted(keys)


def lock_registration_credential_keys(db: Session, keys: list[int]) -> None:
    for key in sorted(set(keys)):
        db.execute(select(func.pg_advisory_xact_lock(key)))


def lock_password_credentials_in_transaction(db: Session, account_id: UUID) -> None:
    """Serialize password issuance/writes before any credential or row locks.

    Identifier locks alone do not serialize recovery through two different
    verified contacts, or issuance against ordinary/admin password changes.
    """
    key = registration_advisory_lock_key("account-password-credentials", str(account_id))
    lock_registration_credential_keys(db, [key])


def invalidate_password_reset_challenges_in_transaction(
    db: Session, *, account_id: UUID, invalidated_at: datetime
) -> None:
    """Expire every unused recovery proof under the account credential lock."""
    db.execute(
        update(RegistrationVerificationChallenge)
        .where(
            RegistrationVerificationChallenge.account_id == account_id,
            RegistrationVerificationChallenge.purpose == "password_reset",
            RegistrationVerificationChallenge.consumed_at.is_(None),
            RegistrationVerificationChallenge.expires_at > invalidated_at,
        )
        .values(expires_at=invalidated_at)
        .execution_options(synchronize_session=False)
    )


@dataclass(frozen=True, slots=True)
class VerificationIssued:
    challenge_id: UUID
    expires_at: datetime
    resend_available_at: datetime


@dataclass(frozen=True, slots=True)
class VerificationChecked:
    challenge_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerificationConsumed:
    challenge_id: UUID
    application_id: UUID | None
    account_id: UUID | None
    identifier_kind: IdentifierKind
    identifier_digest: str = field(repr=False)
    purpose: ChallengePurpose
    consumed_at: datetime


@dataclass(frozen=True, slots=True)
class VerificationTarget:
    application_id: UUID | None
    account_id: UUID | None
    identifier_kind: IdentifierKind
    identifier_digest: str = field(repr=False)
    purpose: ChallengePurpose


class RegistrationVerificationService:
    def __init__(
        self,
        db: Session,
        *,
        lookup: LookupDigest,
        verification_pepper: bytes,
        sender: VerificationSender,
        app_env: Literal["development", "test", "production"],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(verification_pepper) != 32:
            raise ValueError("verification pepper must be 32 bytes")
        if app_env == "production" and isinstance(
            sender,
            (CapturingVerificationSender, TestMailboxVerificationSender),
        ):
            raise VerificationDeliveryNotAllowed(
                "capturing verification sender is not allowed in production"
            )
        self.db = db
        self.lookup = lookup
        self.verification_pepper = verification_pepper
        self.sender = sender
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("verification clock must be timezone-aware")
        return value

    def _digest(self, domain: str, value: str) -> str:
        material = f"{domain}\x00{value}".encode()
        return hmac.new(
            self.verification_pepper,
            material,
            hashlib.sha256,
        ).hexdigest()

    def _request_source_digest(self, source: str) -> str:
        try:
            normalized = str(ipaddress.ip_address(source.strip()))
        except ValueError:
            normalized = "unknown"
        return self._digest("registration-verification-source", normalized)

    def _code_digest(self, challenge_id: UUID, code: str) -> str:
        return self._digest(
            "registration-verification-code",
            f"{challenge_id}\x00{code}",
        )

    def issue_limit_lock_keys(
        self,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        request_source: str,
    ) -> list[int]:
        normalized = normalize_registration_identifier(identifier_kind, identifier)
        identifier_digest = registration_identifier_digest(self.lookup, normalized)
        source_digest = self._request_source_digest(request_source)
        return sorted({
            registration_advisory_lock_key("identifier", identifier_digest),
            registration_advisory_lock_key("source", source_digest),
        })

    def _lock_issue_limits(
        self,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        request_source: str,
    ) -> None:
        lock_registration_credential_keys(self.db, self.issue_limit_lock_keys(
            identifier_kind=identifier_kind, identifier=identifier, request_source=request_source,
        ))

    def lock_issue_limits_in_transaction(
        self,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        request_source: str,
    ) -> None:
        self._lock_issue_limits(
            identifier_kind=identifier_kind,
            identifier=identifier,
            request_source=request_source,
        )

    def issue(
        self,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        purpose: ChallengePurpose,
        request_source: str,
        application_id: UUID | None = None,
        account_id: UUID | None = None,
        expires_at_cap: datetime | None = None,
    ) -> VerificationIssued:
        with write_transaction(self.db):
            return self.issue_in_transaction(
                identifier_kind=identifier_kind,
                identifier=identifier,
                purpose=purpose,
                request_source=request_source,
                application_id=application_id,
                account_id=account_id,
                expires_at_cap=expires_at_cap,
            )

    def issue_in_transaction(
        self,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        purpose: ChallengePurpose,
        request_source: str,
        application_id: UUID | None = None,
        account_id: UUID | None = None,
        expires_at_cap: datetime | None = None,
    ) -> VerificationIssued:
        normalized = normalize_registration_identifier(identifier_kind, identifier)
        identifier_digest = registration_identifier_digest(self.lookup, normalized)
        source_digest = self._request_source_digest(request_source)
        now = self._now()
        challenge_id = uuid4()
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = now + timedelta(minutes=10)
        resend_available_at = now + timedelta(seconds=60)
        if expires_at_cap is not None:
            if expires_at_cap.tzinfo is None or expires_at_cap.utcoffset() is None:
                raise ValueError("verification expiry cap must be timezone-aware")
            if expires_at_cap <= now:
                raise VerificationInvalidOrExpired(
                    "verification target is invalid or expired"
                )
            expires_at = min(expires_at, expires_at_cap)
            resend_available_at = min(resend_available_at, expires_at)

        self._lock_issue_limits(
            identifier_kind=identifier_kind,
            identifier=identifier,
            request_source=request_source,
        )
        last_sent_at = self.db.scalar(
            select(func.max(RegistrationVerificationChallenge.last_sent_at)).where(
                RegistrationVerificationChallenge.identifier_digest == identifier_digest
            )
        )
        if last_sent_at is not None and last_sent_at > now - timedelta(seconds=60):
            raise VerificationRateLimited("verification resend is rate limited")

        identifier_sends = self.db.scalar(
            select(func.count(RegistrationVerificationChallenge.id)).where(
                RegistrationVerificationChallenge.identifier_digest
                == identifier_digest,
                RegistrationVerificationChallenge.last_sent_at
                > now - timedelta(hours=24),
            )
        )
        if (identifier_sends or 0) >= 5:
            raise VerificationRateLimited("verification identifier is rate limited")

        source_sends = self.db.scalar(
            select(func.count(RegistrationVerificationChallenge.id)).where(
                RegistrationVerificationChallenge.request_source_digest == source_digest,
                RegistrationVerificationChallenge.last_sent_at > now - timedelta(hours=1),
            )
        )
        if (source_sends or 0) >= 20:
            raise VerificationRateLimited("verification source is rate limited")

        challenge = RegistrationVerificationChallenge(
            id=challenge_id,
            application_id=application_id,
            account_id=account_id,
            purpose=purpose,
            identifier_kind=identifier_kind,
            identifier_digest=identifier_digest,
            code_digest=self._code_digest(challenge_id, code),
            request_source_digest=source_digest,
            expires_at=expires_at,
            attempts=0,
            last_sent_at=now,
        )
        self.db.add(challenge)
        self.db.flush()
        self.sender.send_code(
            challenge_id=challenge_id,
            destination=normalized.value,
            code=code,
            purpose=purpose,
        )

        return VerificationIssued(
            challenge_id=challenge_id,
            expires_at=expires_at,
            resend_available_at=resend_available_at,
        )

    def consume(self, challenge_id: UUID, code: str) -> VerificationConsumed:
        return self.consume_with(challenge_id, code)

    def verify_unconsumed(
        self,
        challenge_id: UUID,
        code: str,
        *,
        expected_target: VerificationTarget,
    ) -> VerificationChecked:
        """Check in an independent transaction; failed guesses survive caller rollback.

        Only committed challenges are eligible. Never enlist in the caller's
        connection/savepoint or commit its work. NOWAIT prevents self-deadlock if
        a caller already holds this challenge; callers must retry after releasing
        business locks. Success is proof of the OTP only, not target eligibility:
        the activation caller must subsequently lock/recheck its current facts.
        """
        bind = self.db.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
        if not isinstance(engine, Engine):
            raise TypeError("verification requires a bound Engine")
        checked = None
        try:
            with Session(engine) as owned, owned.begin():
                challenge = owned.scalar(
                    select(RegistrationVerificationChallenge)
                    .where(RegistrationVerificationChallenge.id == challenge_id)
                    .with_for_update(nowait=True)
                )
                now = self._now()
                if (
                    challenge is not None
                    and challenge.consumed_at is None
                    and challenge.expires_at > now
                    and challenge.attempts < 5
                ):
                    if (
                        challenge.application_id != expected_target.application_id
                        or challenge.account_id != expected_target.account_id
                        or challenge.identifier_kind != expected_target.identifier_kind
                        or challenge.purpose != expected_target.purpose
                        or not hmac.compare_digest(
                            challenge.identifier_digest, expected_target.identifier_digest
                        )
                    ):
                        raise VerificationTargetMismatch("verification target mismatch")
                    if hmac.compare_digest(
                        challenge.code_digest, self._code_digest(challenge.id, code)
                    ):
                        checked = VerificationChecked(challenge.id, challenge.expires_at)
                    else:
                        challenge.attempts += 1
        except OperationalError as error:
            if getattr(error.orig, "sqlstate", None) == "55P03":
                raise VerificationBusy("verification is busy; retry later") from error
            raise
        except PoolTimeoutError as error:
            raise VerificationBusy("verification is busy; retry later") from error
        if checked is None:
            raise VerificationInvalidOrExpired("verification is invalid or expired")
        return checked

    def consume_with(
        self,
        challenge_id: UUID,
        code: str,
        *,
        expected_target: VerificationTarget | None = None,
        expected_purpose: ChallengePurpose | None = None,
        prepare_target: Callable[[], VerificationTarget] | None = None,
        on_success: Callable[[VerificationConsumed], None] | None = None,
    ) -> VerificationConsumed:
        if expected_target is not None and prepare_target is not None:
            raise ValueError("verification target must have one source")
        consumed: VerificationConsumed | None = None
        with write_transaction(self.db):
            if expected_purpose is not None:
                challenge_snapshot = self.db.execute(
                    select(
                        RegistrationVerificationChallenge.purpose,
                        RegistrationVerificationChallenge.consumed_at,
                        RegistrationVerificationChallenge.expires_at,
                        RegistrationVerificationChallenge.attempts,
                    ).where(
                        RegistrationVerificationChallenge.id == challenge_id
                    )
                ).one_or_none()
                if (
                    challenge_snapshot is None
                    or challenge_snapshot.purpose != expected_purpose
                    or challenge_snapshot.consumed_at is not None
                    or challenge_snapshot.expires_at <= self._now()
                    or challenge_snapshot.attempts >= 5
                ):
                    raise VerificationInvalidOrExpired(
                        "verification is invalid or expired"
                    )
            active_target = (
                prepare_target() if prepare_target is not None else expected_target
            )
            now = self._now()
            challenge = self.db.scalar(
                select(RegistrationVerificationChallenge)
                .where(RegistrationVerificationChallenge.id == challenge_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                challenge is not None
                and challenge.consumed_at is None
                and challenge.expires_at > now
                and challenge.attempts < 5
            ):
                if (
                    expected_purpose is not None
                    and challenge.purpose != expected_purpose
                ):
                    raise VerificationInvalidOrExpired(
                        "verification is invalid or expired"
                    )
                if active_target is not None and (
                    challenge.application_id != active_target.application_id
                    or challenge.account_id != active_target.account_id
                    or challenge.identifier_kind != active_target.identifier_kind
                    or challenge.purpose != active_target.purpose
                    or not hmac.compare_digest(
                        challenge.identifier_digest,
                        active_target.identifier_digest,
                    )
                ):
                    raise VerificationTargetMismatch("verification target mismatch")
                candidate_digest = self._code_digest(challenge.id, code)
                if hmac.compare_digest(challenge.code_digest, candidate_digest):
                    challenge.consumed_at = now
                    consumed = VerificationConsumed(
                        challenge_id=challenge.id,
                        application_id=challenge.application_id,
                        account_id=challenge.account_id,
                        identifier_kind=cast(IdentifierKind, challenge.identifier_kind),
                        identifier_digest=challenge.identifier_digest,
                        purpose=cast(ChallengePurpose, challenge.purpose),
                        consumed_at=now,
                    )
                    if on_success is not None:
                        on_success(consumed)
                else:
                    challenge.attempts += 1
        if consumed is None:
            raise VerificationInvalidOrExpired("verification is invalid or expired")
        return consumed

    def invalidate_registration_credentials_in_transaction(
        self,
        *,
        account_id: UUID,
        application_ids: set[UUID],
        identifier_digests: set[str],
        invalidated_at: datetime,
    ) -> None:
        if identifier_digests:
            self.db.execute(
                update(RegistrationVerificationChallenge)
                .where(
                    RegistrationVerificationChallenge.purpose == "registration",
                    RegistrationVerificationChallenge.identifier_digest.in_(
                        identifier_digests
                    ),
                    RegistrationVerificationChallenge.consumed_at.is_(None),
                    RegistrationVerificationChallenge.expires_at > invalidated_at,
                )
                .values(expires_at=invalidated_at)
                .execution_options(synchronize_session=False)
            )
        scoped_application_ids = select(RegistrationApplication.id).where(
            or_(
                RegistrationApplication.finalized_account_id == account_id,
                RegistrationApplication.id.in_(application_ids),
                RegistrationApplication.verified_identifier_digest.in_(
                    identifier_digests
                ),
            )
        )
        self.db.execute(
            update(RegistrationSession)
            .where(
                RegistrationSession.application_id.in_(scoped_application_ids),
                RegistrationSession.revoked_at.is_(None),
            )
            .values(revoked_at=invalidated_at)
            .execution_options(synchronize_session=False)
        )
