"""Private email-verified claims. No target lookup occurs on the start path."""

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
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.core.transactions import atomic_write_transaction
from app.identity.document_identity import lock_document_identity_keys
from app.identity.models import Person
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.locking import (
    claim_quota_lock_keys,
    discover_claim_lock_context,
    lock_claim_business_context,
)
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimSession
from app.personnel_intake.claims.proof import (
    SUBMISSION_FIELD,
    decode_claim_submission,
    submission_from_input,
    validate_claim_proof,
)
from app.personnel_intake.claims.reservations import expire_claim_reservations_in_transaction
from app.personnel_intake.claims.schemas import (
    ClaimIssued,
    ClaimPublicStatus,
    ClaimStart,
    ClaimStartInput,
)
from app.personnel_intake.eligibility import AssistedEligibilityPolicy
from app.personnel_intake.errors import CredentialReservationConflict
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.personnel_intake.locks import CredentialReservationPolicy
from app.personnel_intake.models import AssistedPersonProfile, PersonnelCredentialReservation
from app.personnel_intake.readiness import AssistedReadinessPolicy
from app.registration.delivery import VerificationSender
from app.registration.models import ChallengePurpose, RegistrationVerificationChallenge
from app.registration.start_limiter import RegistrationStartLimiter
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationConsumed,
    VerificationInvalidOrExpired,
    VerificationTarget,
    lock_registration_credential_keys,
    registration_credential_lock_keys,
)
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest


def _invalid() -> VerificationInvalidOrExpired:
    return VerificationInvalidOrExpired("verification is invalid or expired")


def prune_expired_session_inputs(db: Session, *, now: datetime, limit: int = 200) -> int:
    """Bounded opportunistic redaction. Never deletes immutable claim evidence."""
    if now.tzinfo is None or now.utcoffset() is None or not 1 <= limit <= 10000:
        raise ValueError("invalid session pruning parameters")
    rows = list(
        db.scalars(
            select(ProfileClaimSession)
            .where(
                ProfileClaimSession.expires_at <= now,
                ProfileClaimSession.payload_ciphertext.is_not(None),
            )
            .order_by(ProfileClaimSession.expires_at, ProfileClaimSession.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.payload_ciphertext = None
    db.flush()
    return len(rows)


@dataclass(frozen=True, repr=False)
class _SessionProof:
    id: UUID
    challenge_id: UUID
    payload_digest: str
    identity_digest: str
    email_digest: str
    ciphertext: str
    state: str
    expires_at: datetime
    submission_version: int

    def target(self) -> VerificationTarget:
        return VerificationTarget(
            application_id=None,
            account_id=None,
            identifier_kind="email",
            identifier_digest=self.email_digest,
            purpose="profile_claim",
        )


class _DeliveryAttempt:
    def __init__(self, sender: VerificationSender):
        self.sender, self.failed = sender, False

    def send_code(
        self, *, challenge_id: UUID, destination: str, code: str, purpose: ChallengePurpose
    ) -> None:
        try:
            self.sender.send_code(
                challenge_id=challenge_id, destination=destination, code=code, purpose=purpose
            )
        except (smtplib.SMTPException, OSError):
            self.failed = True  # No provider message, address, OTP or traceback in audit.


class ProfileClaimService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        verification: RegistrationVerificationService,
        token_pepper: bytes,
        clock: Callable[[], datetime] | None = None,
        email_pending_limit: int = 3,
        identity_pending_limit: int = 5,
        global_pending_limit: int = 1000,
    ):
        if len(token_pepper) != 32 or verification.db is not db:
            raise ValueError("claim verification must share Session and a 32-byte token pepper")
        if min(email_pending_limit, identity_pending_limit, global_pending_limit) < 1:
            raise ValueError("pending claim limits must be positive")
        self.db, self.cipher, self.lookup = db, cipher, lookup
        self.verification, self.token_pepper = verification, token_pepper
        self.clock = clock or (lambda: datetime.now(UTC))
        self.email_pending_limit, self.identity_pending_limit = (
            email_pending_limit,
            identity_pending_limit,
        )
        self.global_pending_limit = global_pending_limit

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("claim clock must be timezone-aware")
        return now

    def _token_digest(self, token: str) -> str:
        return hmac.new(
            self.token_pepper, f"profile-claim-session\0{token}".encode(), hashlib.sha256
        ).hexdigest()

    def _audit(self, action: str, resource_id: UUID, *, failed: bool = False) -> None:
        AuditService(self.db).append(
            actor_user_id=None,
            action=f"profile_claim.{action}",
            resource_type="profile_claim",
            resource_id=resource_id,
            purpose=None,
            outcome="failed" if failed else "allowed",
            request_id=str(uuid4()),
            metadata={},
        )

    def start(self, input: ClaimStartInput, request_source: str) -> ClaimIssued:
        value = submission_from_input(input)
        raw = value.canonical()
        email_digest = self.lookup.digest_domain("registration-verification-email", value.email)
        payload_digest = self.lookup.digest_domain(value.domain, raw)
        identity_keys = value.identity_keys(self.lookup)
        now = self._now()
        with atomic_write_transaction(self.db):
            prune_expired_session_inputs(self.db, now=now)
        RegistrationStartLimiter(
            self.db, pepper=self.verification.verification_pepper, clock=self.clock
        ).record_profile_claim(email_digest, request_source)
        token = secrets.token_urlsafe(32)
        attempt = _DeliveryAttempt(self.verification.sender)
        with atomic_write_transaction(self.db):
            # No Person, profile, account or reservation query, regardless of input.
            lock_document_identity_keys(self.db, [identity_keys])
            lock_registration_credential_keys(
                self.db,
                [
                    *registration_credential_lock_keys({email_digest}),
                    *self.verification.issue_limit_lock_keys(
                        identifier_kind="email",
                        identifier=value.email,
                        request_source=request_source,
                    ),
                ],
            )
            previous = list(
                self.db.scalars(
                    select(ProfileClaimSession)
                    .where(
                        ProfileClaimSession.payload_digest == payload_digest,
                        ProfileClaimSession.email_digest == email_digest,
                        ProfileClaimSession.state == "requested",
                        ProfileClaimSession.expires_at > now,
                    )
                    .order_by(ProfileClaimSession.id)
                    .with_for_update()
                )
            )
            self.verification.sender = attempt
            try:
                challenge = self.verification.issue_in_transaction(
                    identifier_kind="email",
                    identifier=value.email,
                    purpose="profile_claim",
                    request_source=request_source,
                )
            finally:
                self.verification.sender = attempt.sender
            if attempt.failed:
                stored = self.db.get(RegistrationVerificationChallenge, challenge.challenge_id)
                assert stored is not None
                stored.consumed_at = now
                self._audit("delivery_failed", challenge.challenge_id, failed=True)
            else:
                for old in self.db.scalars(
                    select(RegistrationVerificationChallenge)
                    .where(
                        RegistrationVerificationChallenge.id.in_([p.challenge_id for p in previous])
                    )
                    .order_by(RegistrationVerificationChallenge.id)
                    .with_for_update()
                ):
                    if old.consumed_at is None:
                        old.expires_at = min(old.expires_at, now)
                self.db.add(
                    ProfileClaimSession(
                        token_digest=self._token_digest(token),
                        challenge_id=challenge.challenge_id,
                        payload_ciphertext=self.cipher.encrypt(raw, field_name=SUBMISSION_FIELD),
                        payload_digest=payload_digest,
                        identity_digest=identity_keys.identity_digest,
                        submission_version=value.version,
                        email_digest=email_digest,
                        state="requested",
                        created_at=now,
                        expires_at=challenge.expires_at,
                    )
                )
                self.db.flush()
        if attempt.failed:
            raise ProfileClaimError(
                code="verification_delivery_failed", status=503, detail="验证码发送失败，请稍后重试"
            )
        return ClaimIssued(
            public=ClaimStart(
                expires_at=challenge.expires_at, resend_available_at=challenge.resend_available_at
            ),
            session_token=SecretStr(token),
        )

    def _proof(self, token: str) -> _SessionProof:
        if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise _invalid()
        row = self.db.scalar(
            select(ProfileClaimSession)
            .where(ProfileClaimSession.token_digest == self._token_digest(token))
            .execution_options(populate_existing=True)
        )
        if row is None or row.expires_at <= self._now() or row.payload_ciphertext is None:
            raise _invalid()
        return _SessionProof(
            row.id,
            row.challenge_id,
            row.payload_digest,
            row.identity_digest,
            row.email_digest,
            row.payload_ciphertext,
            row.state,
            row.expires_at,
            row.submission_version,
        )

    def _lock_session(self, proof: _SessionProof) -> ProfileClaimSession:
        row = self.db.scalar(
            select(ProfileClaimSession)
            .where(ProfileClaimSession.id == proof.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            row is None
            or row.expires_at <= self._now()
            or row.payload_ciphertext is None
            or (
                row.challenge_id,
                row.payload_digest,
                row.identity_digest,
                row.email_digest,
                row.expires_at,
                row.submission_version,
            )
            != (
                proof.challenge_id,
                proof.payload_digest,
                proof.identity_digest,
                proof.email_digest,
                proof.expires_at,
                proof.submission_version,
            )
        ):
            raise _invalid()
        return row

    def _locked_email_claims(self, proof: _SessionProof, *, now: datetime) -> list[ProfileClaim]:
        # Already own this email and all quotas. No preceding lock may be acquired after this point.
        expire_claim_reservations_in_transaction(self.db, email_digest=proof.email_digest, now=now)
        return list(
            self.db.scalars(
                select(ProfileClaim)
                .where(ProfileClaim.email_digest == proof.email_digest)
                .order_by(ProfileClaim.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _require_quota(self, proof: _SessionProof, now: datetime) -> None:
        active = (ProfileClaim.status == "pending", ProfileClaim.expires_at > now)
        for predicate, limit in (
            (ProfileClaim.email_digest == proof.email_digest, self.email_pending_limit),
            (ProfileClaim.identity_digest == proof.identity_digest, self.identity_pending_limit),
            (true(), self.global_pending_limit),
        ):
            count = (
                self.db.scalar(
                    select(func.count()).select_from(ProfileClaim).where(*active, predicate)
                )
                or 0
            )
            if count >= limit:
                oldest = self.db.scalar(
                    select(func.min(ProfileClaim.expires_at)).where(*active, predicate)
                )
                retry = max(1, int((oldest - now).total_seconds()) + 1) if oldest else 60
                raise ProfileClaimError(
                    code="profile_claim_rate_limited",
                    status=429,
                    detail="申请数量已达上限，请稍后重试",
                    retry_after_seconds=retry,
                )

    def _match(self, proof: _SessionProof) -> tuple[UUID | None, UUID | None, str | None]:
        value = decode_claim_submission(
            ciphertext=proof.ciphertext,
            payload_digest=proof.payload_digest,
            identity_digest=proof.identity_digest,
            email_digest=proof.email_digest,
            cipher=self.cipher,
            lookup=self.lookup,
            submission_version=proof.submission_version,
        )
        person = self.db.scalar(
            select(Person).where(Person.identity_digest == proof.identity_digest)
        )
        if (
            person is None
            or person.full_name != value.full_name
            or person.deleted_at is not None
            or person.document_type != value.document_type
            or value.document_type == "passport"
            and (
                not value.issuing_country
                or person.document_issuing_country != value.issuing_country
            )
        ):
            return None, None, "profile_not_matched"
        profile = self.db.get(AssistedPersonProfile, person.id, populate_existing=True)
        if profile is None:
            return person.id, None, "profile_not_eligible"
        has_email = (
            person.email_ciphertext is not None
            or person.email_digest is not None
            or person.email is not None
        )
        if has_email:
            try:
                same_pending_email = bool(
                    person.intake_mode == "pending"
                    and person.email_ciphertext
                    and self.cipher.decrypt(person.email_ciphertext, field_name="email")
                    == value.email
                    and person.email in (None, value.email)
                )
            except EncryptedFieldError:
                return person.id, profile.current_approval_id, "approval_evidence_invalid"
            if not same_pending_email:
                return person.id, profile.current_approval_id, "email_already_reserved"
        if (
            self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id))
            is not None
        ):
            return person.id, profile.current_approval_id, "account_already_exists"
        try:
            if person.intake_mode == "pending":
                readiness = AssistedReadinessPolicy(
                    self.db, cipher=self.cipher, lookup=self.lookup
                ).evaluate_many([person.id], reference_date=business_date_at(self._now()))[
                    person.id
                ]
                return (
                    person.id,
                    readiness.approval_id,
                    None if readiness.completion_allowed else "profile_not_eligible",
                )
            eligible = AssistedEligibilityPolicy(
                self.db, cipher=self.cipher, lookup=self.lookup
            ).evaluate_many([person.id], reference_date=business_date_at(self._now()))[person.id]
        except ApprovalEvidenceError:
            return person.id, profile.current_approval_id, "approval_evidence_invalid"
        if not eligible.eligible or eligible.kind not in {"student", "player", "both"}:
            return person.id, profile.current_approval_id, "profile_not_eligible"
        return person.id, eligible.approval_id, None

    def verify(self, session_token: str, code: SecretStr) -> ClaimPublicStatus:
        proof = self._proof(session_token)
        if proof.state != "requested":
            raise _invalid()
        # Independent committed guess accounting, before any caller business locks.
        self.verification.verify_unconsumed(
            proof.challenge_id, code.get_secret_value(), expected_target=proof.target()
        )
        context = discover_claim_lock_context(
            self.db, identity_digest=proof.identity_digest, cipher=self.cipher, lookup=self.lookup
        )
        row: ProfileClaimSession | None = None
        old_claims: list[ProfileClaim] = []
        result: ClaimPublicStatus | None = None
        email_available = False

        def prepare() -> VerificationTarget:
            nonlocal row, old_claims, email_available
            value = decode_claim_submission(
                ciphertext=proof.ciphertext,
                payload_digest=proof.payload_digest,
                identity_digest=proof.identity_digest,
                email_digest=proof.email_digest,
                cipher=self.cipher,
                lookup=self.lookup,
                submission_version=proof.submission_version,
            )
            lock_document_identity_keys(self.db, [value.identity_keys(self.lookup)])
            lock_registration_credential_keys(
                self.db,
                [
                    *context.credential_keys,
                    *registration_credential_lock_keys(
                        {proof.email_digest}, identity_digests={proof.identity_digest}
                    ),
                    *claim_quota_lock_keys(
                        email_digests={proof.email_digest}, identity_digests={proof.identity_digest}
                    ),
                ],
            )
            lock_claim_business_context(self.db, context)
            old_claims = self._locked_email_claims(proof, now=self._now())
            value = decode_claim_submission(
                ciphertext=proof.ciphertext,
                payload_digest=proof.payload_digest,
                identity_digest=proof.identity_digest,
                email_digest=proof.email_digest,
                cipher=self.cipher,
                lookup=self.lookup,
                submission_version=proof.submission_version,
            )
            try:
                # The shared policy may lazily lock expired claim rows. Finish
                # it BEFORE session/challenge locks, never in on_success.
                matched_person, _, matched_reason = self._match(proof)
                CredentialReservationPolicy(self.db, lookup=self.lookup).require_email_available(
                    email=value.email, person_id=matched_person if matched_reason is None else None
                )
                email_available = True
            except CredentialReservationConflict:
                email_available = False
            row = self._lock_session(proof)
            if row.state != "requested":
                raise _invalid()
            return proof.target()

        def success(consumed: VerificationConsumed) -> None:
            nonlocal result
            assert row is not None
            if (
                consumed.challenge_id != row.challenge_id
                or consumed.purpose != "profile_claim"
                or consumed.identifier_digest != row.email_digest
                or consumed.identifier_kind != "email"
                or consumed.application_id is not None
                or consumed.account_id is not None
                or consumed.consumed_at >= row.expires_at
            ):
                raise _invalid()
            now = consumed.consumed_at  # One immutable timestamp, never another clock call.
            existing = next(
                (
                    c
                    for c in reversed(old_claims)
                    if c.payload_digest == proof.payload_digest
                    and (
                        c.status in {"approved", "completed"}
                        or c.status == "pending"
                        and c.expires_at > now
                    )
                ),
                None,
            )
            if existing is not None:
                validate_claim_proof(self.db, existing, cipher=self.cipher, lookup=self.lookup)
                claim = existing
            else:
                self._require_quota(proof, now)
                target, approval, reason = self._match(proof)
                if reason is None and not email_available:
                    reason = "email_unavailable"
                claim = ProfileClaim(
                    session_id=row.id,
                    challenge_id=row.challenge_id,
                    payload_ciphertext=proof.ciphertext,
                    payload_digest=proof.payload_digest,
                    submission_version=proof.submission_version,
                    identity_digest=proof.identity_digest,
                    email_digest=proof.email_digest,
                    verified_at=now,
                    challenge_consumed_at=now,
                    expires_at=now + timedelta(days=7),
                    target_person_id=target,
                    target_approval_id=approval,
                    private_match_reason=reason,
                    status="pending",
                    created_at=now,
                )
                self.db.add(claim)
                self.db.flush()
                if reason is None:
                    self.db.add(
                        PersonnelCredentialReservation(
                            kind="email",
                            digest=proof.email_digest,
                            request_id=None,
                            profile_claim_id=claim.id,
                            person_id=None,
                            created_at=now,
                        )
                    )
                self._audit("verified", claim.id)
            row.state, row.verified_at, row.claim_id = "verified", now, claim.id
            self.db.flush()
            result = self._public(claim)

        self.verification.consume_with(
            proof.challenge_id,
            code.get_secret_value(),
            expected_purpose="profile_claim",
            prepare_target=prepare,
            on_success=success,
        )
        assert result is not None
        return result

    def status(self, session_token: str) -> ClaimPublicStatus:
        proof = self._proof(session_token)
        if proof.state != "verified":
            raise _invalid()
        with atomic_write_transaction(self.db):
            lock_registration_credential_keys(
                self.db,
                [
                    *registration_credential_lock_keys(
                        {proof.email_digest}, identity_digests={proof.identity_digest}
                    ),
                    *claim_quota_lock_keys(
                        email_digests={proof.email_digest}, identity_digests={proof.identity_digest}
                    ),
                ],
            )
            self._locked_email_claims(proof, now=self._now())
            row = self._lock_session(proof)
            claim = self.db.get(ProfileClaim, row.claim_id) if row.claim_id else None
            challenge = self.db.get(
                RegistrationVerificationChallenge, row.challenge_id, populate_existing=True
            )
            if (
                row.state != "verified"
                or row.verified_at is None
                or claim is None
                or challenge is None
                or (claim.payload_digest, claim.email_digest)
                != (row.payload_digest, row.email_digest)
                or challenge.purpose != "profile_claim"
                or challenge.identifier_kind != "email"
                or challenge.identifier_digest != row.email_digest
                or challenge.application_id is not None
                or challenge.account_id is not None
                or challenge.consumed_at != row.verified_at
                or challenge.consumed_at >= challenge.expires_at
            ):
                raise _invalid()
            validate_claim_proof(self.db, claim, cipher=self.cipher, lookup=self.lookup)
            return self._public(claim)

    def _public(self, claim: ProfileClaim) -> ClaimPublicStatus:
        if claim.status == "pending":
            return ClaimPublicStatus(
                status="expired" if claim.expires_at <= self._now() else "waiting_review"
            )
        if claim.status == "approved":
            return ClaimPublicStatus(status="approved")
        if claim.status == "rejected":
            return ClaimPublicStatus(status="rejected", reason=claim.public_reason)
        if claim.status == "expired":
            return ClaimPublicStatus(status="expired")
        return ClaimPublicStatus(
            status="closed", reason=claim.public_reason if claim.status == "revoked" else None
        )
