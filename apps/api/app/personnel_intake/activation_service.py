from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.core.time import business_date_at
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.identity.normalization import normalize_email
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.activation_roles import activation_role_keys
from app.personnel_intake.activation_schemas import (
    ActivationCompleted,
    ActivationIssued,
    ActivationPreview,
    ActivationStart,
)
from app.personnel_intake.claims.binding import (
    complete_claim_in_transaction,
    prepare_claim_lifecycle,
    require_activation_binding_in_transaction,
)
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.locking import lock_claim_business_context
from app.personnel_intake.eligibility import AssistedEligibility, AssistedEligibilityPolicy
from app.personnel_intake.errors import CredentialReservationConflict
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.personnel_intake.locks import CredentialReservationPolicy
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.personnel_intake.readiness import AssistedReadinessPolicy
from app.registration.delivery import VerificationSender
from app.registration.models import ChallengePurpose, RegistrationVerificationChallenge
from app.registration.start_limiter import RegistrationStartLimiter
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationConsumed,
    VerificationInvalidOrExpired,
    VerificationRateLimited,
    VerificationTarget,
    lock_registration_credential_keys,
)
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def _invalid() -> VerificationInvalidOrExpired:
    return VerificationInvalidOrExpired("verification is invalid or expired")


@dataclass(frozen=True, repr=False)
class _Proof:
    id: UUID
    person_id: UUID
    approval_id: UUID
    revision_version: int
    challenge_id: UUID
    email_digest: str
    expires_at: datetime
    state: str

    def target(self) -> VerificationTarget:
        return VerificationTarget(
            application_id=None,
            account_id=None,
            identifier_kind="email",
            identifier_digest=self.email_digest,
            purpose="assisted_activation",
        )


class _CompletedReplay(Exception):
    pass


class _DeliveryAttempt:
    def __init__(self, sender: VerificationSender) -> None:
        self.sender = sender
        self.failed = False

    def send_code(
        self, *, challenge_id: UUID, destination: str, code: str, purpose: ChallengePurpose
    ) -> None:
        try:
            self.sender.send_code(
                challenge_id=challenge_id, destination=destination, code=code, purpose=purpose
            )
        except (smtplib.SMTPException, OSError):
            # Retain a consumed challenge to account for the attempted delivery;
            # never retain/log the exception, destination or OTP.
            self.failed = True


class AssistedActivationService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        verification: RegistrationVerificationService,
        token_pepper: bytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(token_pepper) != 32:
            raise ValueError("activation token pepper must be 32 bytes")
        if verification.db is not db:
            raise ValueError("activation verification must share the business Session")
        self.db, self.cipher, self.lookup = db, cipher, lookup
        self.verification, self.token_pepper = verification, token_pepper
        self.clock = clock or (lambda: datetime.now(UTC))
        self.passwords = PasswordService()

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("activation clock must be timezone-aware")
        return now

    def _token_digest(self, token: str) -> str:
        return hmac.new(
            self.token_pepper, f"assisted-activation-session\0{token}".encode(), hashlib.sha256
        ).hexdigest()

    def _proof(self, token: str) -> _Proof:
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise _invalid()
        row = self.db.scalar(
            select(AssistedActivationSession)
            .where(AssistedActivationSession.token_digest == self._token_digest(token))
            .execution_options(populate_existing=True)
        )
        if row is None or row.expires_at <= self._now():
            raise _invalid()
        return _Proof(
            row.id,
            row.person_id,
            row.approval_id,
            row.revision_version,
            row.challenge_id,
            row.email_digest,
            row.expires_at,
            row.state,
        )

    def _lock_person(
        self, person_id: UUID, email_digest: str, *, send_keys: list[int] | None = None
    ) -> Person:
        """Complete credential/quota prefix -> business -> claim/binding/access session.

        Activation session and OTP challenge are deliberately locked by callers
        only after these rows, matching administrator review/revocation.
        """
        snapshot = self.db.get(Person, person_id, populate_existing=True)
        if snapshot is None:
            raise _invalid()
        prepared = prepare_claim_lifecycle(
            self.db,
            person_id=person_id,
            identity_digest=snapshot.identity_digest,
            email_digest=email_digest,
            cipher=self.cipher,
            lookup=self.lookup,
        )
        from app.registration.verification_service import registration_credential_lock_keys

        lock_registration_credential_keys(
            self.db,
            [
                *prepared.credential_keys,
                *registration_credential_lock_keys({email_digest}),
                *(send_keys or []),
            ],
        )
        try:
            lock_claim_business_context(self.db, prepared.context)
            prepared.lock_rows(self.db, cipher=self.cipher, lookup=self.lookup)
        except ProfileClaimError as error:
            raise _invalid() from error
        person = self.db.get(Person, person_id)
        if person is None or person.deleted_at is not None:
            raise _invalid()
        return person

    def _lock_proof(self, proof: _Proof) -> AssistedActivationSession:
        row = self.db.scalar(
            select(AssistedActivationSession)
            .where(AssistedActivationSession.id == proof.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.expires_at <= self._now()
            or (
                row.person_id,
                row.approval_id,
                row.revision_version,
                row.challenge_id,
                row.email_digest,
            )
            != (
                proof.person_id,
                proof.approval_id,
                proof.revision_version,
                proof.challenge_id,
                proof.email_digest,
            )
        ):
            raise _invalid()
        return row

    def _eligible(
        self, person: Person, proof: _Proof | None = None
    ) -> tuple[AssistedEligibility, AssistedApproval, str]:
        profile = self.db.scalar(
            select(AssistedPersonProfile)
            .where(AssistedPersonProfile.person_id == person.id)
            .execution_options(populate_existing=True)
        )
        if profile is None:
            raise _invalid()
        approval = self.db.get(AssistedApproval, profile.current_approval_id)
        if approval is None or (
            proof
            and (
                approval.id != proof.approval_id
                or approval.revision_version != proof.revision_version
            )
        ):
            raise _invalid()
        if person.intake_mode == "pending":
            readiness = AssistedReadinessPolicy(
                self.db, cipher=self.cipher, lookup=self.lookup
            ).evaluate_many([person.id], reference_date=business_date_at(self._now()))[person.id]
            eligible = AssistedEligibility(
                readiness.approval_valid,
                readiness.activation_profile_ready,
                readiness.kind,
                readiness.approval_id,
                readiness.version,
                readiness.activation_missing,
            )
        else:
            eligible = AssistedEligibilityPolicy(
                self.db, cipher=self.cipher, lookup=self.lookup
            ).evaluate_many([person.id], reference_date=business_date_at(self._now()))[person.id]
        if not eligible.eligible or eligible.kind is None:
            raise _invalid()
        if (
            self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id))
            is not None
        ):
            raise _invalid()
        try:
            email = (
                normalize_email(self.cipher.decrypt(person.email_ciphertext, field_name="email"))
                if person.email_ciphertext
                else None
            )
        except EncryptedFieldError as error:
            raise ApprovalEvidenceError() from error
        if email is None or (
            proof
            and not hmac.compare_digest(
                proof.email_digest,
                self.lookup.digest_domain("registration-verification-email", email),
            )
        ):
            raise _invalid()
        if person.intake_mode == "pending" or profile.activation_requirement == "reviewed_claim":
            try:
                require_activation_binding_in_transaction(
                    self.db,
                    person_id=person.id,
                    email_digest=self.lookup.digest_domain(
                        "registration-verification-email", email
                    ),
                    cipher=self.cipher,
                    lookup=self.lookup,
                    now=self._now(),
                )
            except ProfileClaimError as error:
                raise _invalid() from error
        CredentialReservationPolicy(self.db, lookup=self.lookup).require_email_available(
            email=email, person_id=person.id
        )
        return eligible, approval, email

    def _audit(self, action: str, person_id: UUID, *, failed: bool = False) -> None:
        AuditService(self.db).append(
            actor_user_id=None,
            action=f"profile_activation.{action}",
            resource_type="person",
            resource_id=person_id,
            purpose=None,
            outcome="failed" if failed else "allowed",
            request_id=str(uuid4()),
            metadata={},
        )

    def start(self, email: str, request_source: str) -> ActivationIssued:
        email = normalize_email(email)
        digest = self.lookup.digest_domain("registration-verification-email", email)
        RegistrationStartLimiter(
            self.db, pepper=self.verification.verification_pepper, clock=self.clock
        ).record_activation(digest, request_source)
        now = self._now()
        token = secrets.token_urlsafe(32)
        public = ActivationStart(
            resend_available_at=now + timedelta(seconds=60), expires_at=now + timedelta(minutes=10)
        )
        issued = ActivationIssued(public=public, session_token=SecretStr(token))
        with atomic_write_transaction(self.db):
            person_id = self.db.scalar(
                select(Person.id).where(
                    or_(
                        Person.email_digest == self.lookup.digest_domain("person-email", email),
                        Person.email == email,
                    )
                )
            )
            if person_id is None:
                return issued
            try:
                person = self._lock_person(
                    person_id,
                    digest,
                    send_keys=self.verification.issue_limit_lock_keys(
                        identifier_kind="email", identifier=email, request_source=request_source
                    ),
                )
                _, approval, locked_email = self._eligible(person)
                if locked_email != email:
                    return issued
            except (VerificationInvalidOrExpired, CredentialReservationConflict):
                return issued
            except ApprovalEvidenceError:
                self._audit("evidence_failed", person_id, failed=True)
                return issued
            attempt = _DeliveryAttempt(self.verification.sender)
            self.verification.sender = attempt
            try:
                challenge = self.verification.issue_in_transaction(
                    identifier_kind="email",
                    identifier=locked_email,
                    purpose="assisted_activation",
                    request_source=request_source,
                    expires_at_cap=public.expires_at,
                )
            except VerificationRateLimited:
                self._audit("send_limited", person_id, failed=True)
                return issued
            finally:
                self.verification.sender = attempt.sender
            if attempt.failed:
                stored = self.db.get(RegistrationVerificationChallenge, challenge.challenge_id)
                assert stored is not None
                stored.consumed_at = self._now()
                self._audit("delivery_failed", person_id, failed=True)
                return issued
            self.db.add(
                AssistedActivationSession(
                    token_digest=self._token_digest(token),
                    challenge_id=challenge.challenge_id,
                    person_id=person.id,
                    approval_id=approval.id,
                    revision_version=approval.revision_version,
                    email_digest=digest,
                    state="requested",
                    created_at=now,
                    expires_at=challenge.expires_at,
                )
            )
            self.db.flush()
        return issued

    def verify(self, session_token: str, code: SecretStr) -> ActivationPreview:
        proof = self._proof(session_token)
        if proof.state == "completed":
            raise _invalid()
        checked = self.verification.verify_unconsumed(
            proof.challenge_id, code.get_secret_value(), expected_target=proof.target()
        )
        with atomic_write_transaction(self.db):
            person = self._lock_person(proof.person_id, proof.email_digest)
            row = self._lock_proof(proof)
            challenge = self.db.scalar(
                select(RegistrationVerificationChallenge)
                .where(RegistrationVerificationChallenge.id == proof.challenge_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                challenge is None
                or challenge.consumed_at is not None
                or challenge.attempts >= 5
                or challenge.expires_at <= self._now()
                or challenge.expires_at != checked.expires_at
                or challenge.purpose != "assisted_activation"
                or challenge.identifier_kind != "email"
                or challenge.application_id is not None
                or challenge.account_id is not None
                or not hmac.compare_digest(challenge.identifier_digest, proof.email_digest)
            ):
                raise _invalid()
            eligible, _, _ = self._eligible(person, proof)
            row.state, row.verified_at = "verified", row.verified_at or self._now()
            self.db.flush()
            assert eligible.kind is not None
            return ActivationPreview(
                masked_name=person.full_name[:1] + "*" * max(1, len(person.full_name) - 1),
                kind=eligible.kind,
            )

    def finish(
        self, session_token: str, code: SecretStr, password: SecretStr
    ) -> ActivationCompleted:
        password_hash = self.passwords.hash(password.get_secret_value())
        with self.db.no_autoflush:
            proof = self._proof(session_token)
        if proof.state == "completed":
            return ActivationCompleted()
        person: Person | None = None
        row: AssistedActivationSession | None = None

        def prepare() -> VerificationTarget:
            nonlocal person, row
            person = self._lock_person(proof.person_id, proof.email_digest)
            row = self._lock_proof(proof)
            if row.state == "completed":
                raise _CompletedReplay()
            if row.state != "verified":
                raise _invalid()
            return proof.target()

        def create_account(consumed: VerificationConsumed) -> None:
            assert person is not None and row is not None
            eligible, approval, email = self._eligible(person, proof)
            account = UserAccount(
                person_id=person.id,
                password_hash=password_hash,
                status="active",
                force_password_change=False,
                email_normalized=email,
                account_access_origin="assisted_activation",
                assisted_approval_id=approval.id,
            )
            self.db.add(account)
            self.db.flush()
            profile = self.db.get(AssistedPersonProfile, person.id)
            if profile is not None and profile.activation_requirement == "reviewed_claim":
                binding = require_activation_binding_in_transaction(
                    self.db,
                    person_id=person.id,
                    email_digest=proof.email_digest,
                    cipher=self.cipher,
                    lookup=self.lookup,
                    now=consumed.consumed_at,
                )
                complete_claim_in_transaction(
                    self.db,
                    binding_id=binding.id,
                    account_id=account.id,
                    now=consumed.consumed_at,
                )
            self.db.add(
                AccountLoginIdentifier(
                    account_id=account.id,
                    kind="email",
                    identifier_digest=login_identifier_digest(
                        self.lookup, NormalizedLoginIdentifier("email", email)
                    ),
                    verified_at=consumed.consumed_at,
                    verification_source="assisted_activation",
                    is_primary=False,
                )
            )
            from app.rbac.models import Role, UserRole

            assert eligible.kind is not None
            roles = activation_role_keys(eligible.kind)
            role_ids = list(self.db.scalars(select(Role.id).where(Role.key.in_(roles))))
            if len(role_ids) != len(roles):
                raise RuntimeError("activation roles unavailable")
            self.db.add_all(
                UserRole(user_id=account.id, role_id=identifier) for identifier in role_ids
            )
            row.state, row.completed_at = "completed", consumed.consumed_at
            self._audit("completed", person.id)
            self.db.flush()

        try:
            # Reject/count guesses in a separately owned transaction before
            # business locks. The final consume still rechecks under its lock
            # and commits consumption only with successful account creation.
            self.verification.verify_unconsumed(
                proof.challenge_id, code.get_secret_value(), expected_target=proof.target()
            )
            self.verification.consume_with(
                proof.challenge_id,
                code.get_secret_value(),
                expected_purpose="assisted_activation",
                prepare_target=prepare,
                on_success=create_account,
            )
        except _CompletedReplay:
            return ActivationCompleted()
        except VerificationInvalidOrExpired:
            # A concurrent winner can consume after our unlocked snapshot but
            # before either check. Replaying its committed success has no writes.
            with self.db.no_autoflush:
                if self._proof(session_token).state == "completed":
                    return ActivationCompleted()
            raise
        return ActivationCompleted()
