"""Trusted claim binding checks. Callers own lifecycle locks; helpers never commit.

Activation callers must prelock binding/claim BEFORE activation session/challenge.
This module never acquires preceding credential, membership or Person locks.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.core.time import business_date_at
from app.identity.models import Person
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.locking import (
    ClaimLockContext,
    claim_quota_lock_keys,
    discover_claim_lock_context,
)
from app.personnel_intake.claims.models import (
    ProfileClaim,
    ProfileClaimBinding,
    ProfileClaimSession,
)
from app.personnel_intake.claims.proof import validate_claim_proof
from app.personnel_intake.eligibility import AssistedEligibilityPolicy
from app.personnel_intake.guardian_records import authenticated_approval_facts
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelCredentialReservation,
)
from app.personnel_intake.readiness import AssistedReadinessPolicy
from app.registration.verification_service import registration_credential_lock_keys
from app.security.encryption import EnvelopeCipher, LookupDigest


def discover_claim_lifecycle_rows(
    db: Session, *, person_id: UUID | None, identity_digest: str, email_digest: str | None = None
) -> list[ProfileClaim]:
    """READ ONLY: discover target/input competitors AND their same-email expiry rows.

    Caller merges returned keyset with all old/new credentials and actor/guardian
    discovery, locks that complete sorted set once, then rechecks this row set and
    version/status fingerprints before taking claim/session locks. Drift is 409,
    not permission to append more earlier-order locks.
    """
    seed = list(
        db.scalars(
            select(ProfileClaim)
            .where(
                or_(
                    ProfileClaim.identity_digest == identity_digest,
                    ProfileClaim.email_digest == email_digest if email_digest else false(),
                    ProfileClaim.target_person_id == person_id if person_id else false(),
                )
            )
            .execution_options(populate_existing=True)
        )
    )
    emails = {row.email_digest for row in seed}
    return list(
        db.scalars(
            select(ProfileClaim)
            .where(
                or_(
                    ProfileClaim.id.in_([row.id for row in seed]),
                    ProfileClaim.email_digest.in_(emails),
                )
            )
            .order_by(ProfileClaim.id)
            .execution_options(populate_existing=True)
        )
    )


def claim_lifecycle_lock_keys(rows: list[ProfileClaim]) -> list[int]:
    """Return keys only, never acquire them after a caller's earlier prefix."""
    emails = {row.email_digest for row in rows}
    identities = {row.identity_digest for row in rows}
    return sorted(
        set(
            registration_credential_lock_keys(emails, identity_digests=identities)
            + claim_quota_lock_keys(email_digests=emails, identity_digests=identities)
        )
    )


def _lifecycle_fingerprint(rows: list[ProfileClaim]) -> dict[UUID, tuple[object, ...]]:
    return {
        row.id: (
            row.version,
            row.status,
            row.target_person_id,
            row.target_approval_id,
            row.email_digest,
            row.identity_digest,
        )
        for row in rows
    }


@dataclass
class PreparedClaimLifecycle:
    """Read-only discovery retained across the caller's complete lock prefix."""

    context: ClaimLockContext
    rows: list[ProfileClaim]
    fingerprint: dict[UUID, tuple[object, ...]]
    email_digest: str | None

    @property
    def credential_keys(self) -> list[int]:
        return self.context.credential_keys + (
            claim_lifecycle_lock_keys(self.rows) if self.rows else []
        )

    def affected_rows(self, rows: list[ProfileClaim]) -> list[ProfileClaim]:
        """Select mutation scope, not the wider email-adjacent locking scope.

        All discovered rows must still be fingerprinted/prelocked. A historical
        shared email does not authorize changes to another person's evidence;
        only this stable target and its old/new identity facts are affected.
        """
        person_id = self.context.stable_person_id
        identities = {self.context.identity_digest}
        original = self.context.people.get(person_id) if person_id is not None else None
        if original is not None and isinstance(original[0], str):
            identities.add(original[0])
        return [
            row
            for row in rows
            if (person_id is not None and row.target_person_id == person_id)
            or row.identity_digest in identities
        ]

    def lock_rows(
        self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest
    ) -> list[ProfileClaim]:
        # The caller has merged credentials and memberships, then locked the
        # context's requests, complete graph, sorted people/accounts/directory.
        current = discover_claim_lock_context(
            db,
            identity_digest=self.context.identity_digest,
            stable_person_id=self.context.stable_person_id,
            cipher=cipher,
            lookup=lookup,
        )
        rows = discover_claim_lifecycle_rows(
            db,
            person_id=self.context.stable_person_id,
            identity_digest=self.context.identity_digest,
            email_digest=self.email_digest,
        )
        if current != self.context or _lifecycle_fingerprint(rows) != self.fingerprint:
            raise ProfileClaimError(code="profile_claim_retry", detail="资料正在更新，请重试")
        rows = list(
            db.scalars(
                select(ProfileClaim)
                .where(ProfileClaim.id.in_(self.fingerprint))
                .order_by(ProfileClaim.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if _lifecycle_fingerprint(rows) != self.fingerprint:
            raise ProfileClaimError(code="profile_claim_retry", detail="资料正在更新，请重试")
        list(
            db.scalars(
                select(ProfileClaimBinding)
                .where(ProfileClaimBinding.claim_id.in_(self.fingerprint))
                .order_by(ProfileClaimBinding.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        list(
            db.scalars(
                select(ProfileClaimSession)
                .where(
                    or_(
                        ProfileClaimSession.claim_id.in_(self.fingerprint),
                        ProfileClaimSession.id.in_([row.session_id for row in rows]),
                    )
                )
                .order_by(ProfileClaimSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        return rows


def prepare_claim_lifecycle(
    db: Session,
    *,
    person_id: UUID,
    identity_digest: str,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    email_digest: str | None = None,
) -> PreparedClaimLifecycle:
    context = discover_claim_lock_context(
        db,
        identity_digest=identity_digest,
        stable_person_id=person_id,
        cipher=cipher,
        lookup=lookup,
    )
    rows = discover_claim_lifecycle_rows(
        db,
        person_id=person_id,
        identity_digest=identity_digest,
        email_digest=email_digest,
    )
    return PreparedClaimLifecycle(context, rows, _lifecycle_fingerprint(rows), email_digest)


def complete_claim_in_transaction(
    db: Session, *, binding_id: UUID, account_id: UUID, now: datetime
) -> None:
    """Complete already-prelocked claim with the account/OTP transaction, never commit."""
    binding = db.get(ProfileClaimBinding, binding_id)
    account = db.get(UserAccount, account_id)
    claim = db.get(ProfileClaim, binding.claim_id) if binding else None
    if (
        binding is None
        or binding.revoked_at is not None
        or account is None
        or claim is None
        or account.person_id != binding.person_id
        or claim.target_person_id != binding.person_id
        or account.account_access_origin != "assisted_activation"
    ):
        raise ProfileClaimError()
    if claim.status == "completed" and claim.completed_account_id == account_id:
        return
    if claim.status != "approved":
        raise ProfileClaimError()
    before = claim.version
    claim.status, claim.version = "completed", before + 1
    claim.completed_account_id, claim.completed_at = account_id, now
    AuditService(db).append(
        actor_user_id=None,
        action="profile_claim.completed",
        resource_type="profile_claim",
        resource_id=claim.id,
        purpose=None,
        outcome="allowed",
        request_id=str(uuid4()),
        metadata={"old_version": before, "new_version": claim.version},
    )
    db.flush()


def invalidate_prelocked_pending_claims_in_transaction(
    db: Session,
    *,
    claims: list[ProfileClaim],
    occurred_at: datetime,
    actor_user_id: UUID | None = None,
) -> None:
    """Only mutate supplied prelocked pending rows; preserve all original proofs.

    Caller owns complete merged credentials/quotas, business, claim and session
    locks. No discovery, lock acquisition or activation invalidation occurs here.
    Generic identity/contact changes must call this BEFORE old activation
    invalidate_in_transaction takes its final challenge locks. Trusted same-email
    successors must not invalidate an approved binding with this helper.
    """
    for row in claims:
        if row.status != "pending":
            continue
        before = row.version
        row.status, row.version = "invalidated", row.version + 1
        for reservation in db.scalars(
            select(PersonnelCredentialReservation).where(
                PersonnelCredentialReservation.profile_claim_id == row.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        ):
            reservation.ended_at = occurred_at
        AuditService(db).append(
            actor_user_id=actor_user_id,
            action="profile_claim.invalidated",
            resource_type="profile_claim",
            resource_id=row.id,
            purpose=None,
            outcome="allowed",
            request_id=str(uuid4()),
            metadata={"old_version": before, "new_version": row.version},
        )
    db.flush()


def require_binding_ownership_in_transaction(
    db: Session,
    *,
    person_id: UUID,
    email_digest: str,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    now: datetime | None = None,
) -> ProfileClaimBinding:
    """Authenticate current email and trusted binding lineage without readiness policy."""
    binding = db.scalar(
        select(ProfileClaimBinding)
        .where(ProfileClaimBinding.person_id == person_id, ProfileClaimBinding.revoked_at.is_(None))
        .execution_options(populate_existing=True)
    )
    profile = db.get(AssistedPersonProfile, person_id, populate_existing=True)
    person = db.get(Person, person_id, populate_existing=True)
    if (
        binding is None
        or profile is None
        or person is None
        or person.deleted_at
        or binding.email_digest != email_digest
        or not person.email_ciphertext
        or profile.activation_requirement != "reviewed_claim"
    ):
        raise ProfileClaimError()
    email = cipher.decrypt(person.email_ciphertext, field_name="email")
    if lookup.digest_domain("registration-verification-email", email) != email_digest:
        raise ProfileClaimError()
    claim = db.get(ProfileClaim, binding.claim_id, populate_existing=True)
    if (
        claim is None
        or claim.status != "approved"
        or claim.target_person_id != person_id
        or claim.email_digest != email_digest
    ):
        raise ProfileClaimError()
    submitted = validate_claim_proof(db, claim, cipher=cipher, lookup=lookup)
    if submitted.email != email:
        raise ProfileClaimError()
    cursor = db.get(AssistedApproval, profile.current_approval_id)
    seen = set()
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        own, _ = authenticated_approval_facts(cursor, cipher=cipher, lookup=lookup)
        if own.get("email") != email:
            raise ProfileClaimError()
        if cursor.id == binding.approval_id:
            # Reuse the existing authenticated request/revision lineage checks.
            # Only the live guardian readiness outcome is irrelevant to undoing
            # an email that this binding still owns.
            provenance = AssistedEligibilityPolicy(
                db, cipher=cipher, lookup=lookup
            ).evaluate_many_with_provenance(
                [person_id],
                reference_date=business_date_at(now or datetime.now(UTC)),
                original_approvals={person_id: binding.approval_id},
            )[person_id]
            if not provenance.approved or provenance.missing not in {(), ("guardian",)}:
                raise ProfileClaimError()
            return binding
        cursor = (
            db.get(AssistedApproval, cursor.supersedes_approval_id)
            if cursor.supersedes_approval_id
            else None
        )
    raise ProfileClaimError()


def require_activation_binding_in_transaction(
    db: Session,
    *,
    person_id: UUID,
    email_digest: str,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    now: datetime | None = None,
) -> ProfileClaimBinding:
    """Activation additionally requires today's full assisted eligibility provenance."""
    binding = require_binding_ownership_in_transaction(
        db, person_id=person_id, email_digest=email_digest, cipher=cipher, lookup=lookup, now=now
    )
    person = db.get(Person, person_id)
    if person is not None and person.intake_mode == "pending":
        ready = (
            AssistedReadinessPolicy(db, cipher=cipher, lookup=lookup)
            .evaluate_many(
                [person_id],
                reference_date=business_date_at(now or datetime.now(UTC)),
                original_approvals={person_id: binding.approval_id},
            )[person_id]
            .activation_profile_ready
        )
    else:
        ready = (
            AssistedEligibilityPolicy(db, cipher=cipher, lookup=lookup)
            .evaluate_many_with_provenance(
                [person_id],
                reference_date=business_date_at(now or datetime.now(UTC)),
                original_approvals={person_id: binding.approval_id},
            )[person_id]
            .eligible
        )
    if not ready:
        raise ProfileClaimError()
    return binding
