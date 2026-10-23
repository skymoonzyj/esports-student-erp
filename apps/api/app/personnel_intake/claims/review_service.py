"""Atomic claim decisions under one merged credential and lifecycle lock prefix."""

from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.audit.service import AuditService
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.document_identity import DocumentIdentityKeys, lock_document_identity_keys
from app.identity.membership_locks import lock_person_memberships, person_membership_lock_keys
from app.identity.models import Person
from app.identity.readiness import GuardianReadinessPolicy
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.activation_state import invalidate_in_transaction
from app.personnel_intake.approval_service import PersonnelApprovalService
from app.personnel_intake.claims.binding import (
    claim_lifecycle_lock_keys,
    discover_claim_lifecycle_rows,
    invalidate_prelocked_pending_claims_in_transaction,
    require_binding_ownership_in_transaction,
)
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.locking import discover_claim_lock_context
from app.personnel_intake.claims.models import (
    ProfileClaim,
    ProfileClaimBinding,
    ProfileClaimDecision,
    ProfileClaimSession,
)
from app.personnel_intake.claims.proof import validate_claim_proof
from app.personnel_intake.claims.queries import ClaimQueries
from app.personnel_intake.claims.schemas import (
    ClaimCompletionFields,
    ClaimDecisionInput,
    ClaimDetail,
    ClaimReasonInput,
)
from app.personnel_intake.guardian_records import canonical_json
from app.personnel_intake.locks import CredentialReservationPolicy
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelCredentialReservation,
    PersonnelIntakeRequest,
)
from app.personnel_intake.schemas import PendingIntakePayloadView
from app.registration.verification_service import (
    VerificationInvalidOrExpired,
    lock_registration_credential_keys,
    registration_advisory_lock_key,
    registration_credential_lock_keys,
)
from app.team_management.write_support import lock_directory, lock_participants


class ProfileClaimReviewService(ClaimQueries):
    def _completion_candidates(self, row: ProfileClaim) -> PendingIntakePayloadView | None:
        person = self.db.get(Person, row.target_person_id) if row.target_person_id else None
        if person is None or person.intake_mode != "pending" or row.private_match_reason:
            return None
        try:
            return self._completion_payload(row, self._submission(row))
        except ProfileClaimError:
            return None  # Invalid material must still be rejectable under normal scope.

    def _related(self, row: ProfileClaim) -> list[ProfileClaim]:
        rows = discover_claim_lifecycle_rows(
            self.db,
            person_id=row.target_person_id,
            identity_digest=row.identity_digest,
            email_digest=row.email_digest,
        )
        payload = self._completion_candidates(row)
        if payload:
            for guardian in payload.guardians:
                identity = self.lookup.digest(guardian.identity_number)
                person_id = self.db.scalar(
                    select(Person.id).where(Person.identity_digest == identity)
                )
                rows.extend(
                    discover_claim_lifecycle_rows(
                        self.db,
                        person_id=person_id,
                        identity_digest=identity,
                        email_digest=self.lookup.digest_domain(
                            "registration-verification-email", guardian.email
                        )
                        if guardian.email
                        else None,
                    )
                )
        return sorted({value.id: value for value in rows}.values(), key=lambda value: value.id)

    @staticmethod
    def _claim_fingerprint(rows: list[ProfileClaim]) -> dict[UUID, tuple[object, ...]]:
        return {
            r.id: (
                r.version,
                r.status,
                r.target_person_id,
                r.target_approval_id,
                r.email_digest,
                r.identity_digest,
            )
            for r in rows
        }

    def _lock_lifecycle(self, actor: AuthContext, row: ProfileClaim) -> list[ProfileClaim]:
        """Discovery has no writes. Drift aborts; never append earlier locks."""
        target = (
            self.db.get(Person, row.target_person_id, populate_existing=True)
            if row.target_person_id
            else None
        )
        if row.target_person_id and target is None:
            raise ProfileClaimError(code="profile_claim_retry", detail="资料正在更新，请重试")
        identities = {row.identity_digest, self._actor_person(actor).identity_digest}
        if target is not None:
            identities.add(target.identity_digest)
        payload = self._completion_candidates(row)
        if payload:
            identities.update(self.lookup.digest(g.identity_number) for g in payload.guardians)
        phones = (
            {
                self.lookup.digest(p)
                for p in ([payload.phone] + [g.phone for g in payload.guardians])
                if p
            }
            if payload
            else set()
        )
        emails = (
            {email for email in ([payload.email] + [g.email for g in payload.guardians]) if email}
            if payload
            else set()
        )
        contexts = [
            discover_claim_lock_context(
                self.db,
                identity_digest=identity,
                cipher=self.cipher,
                lookup=self.lookup,
                stable_person_id=row.target_person_id if identity == row.identity_digest else None,
            )
            for identity in sorted(identities)
        ]
        related = self._related(row)
        before = self._claim_fingerprint(related)
        # Complete old/new passport bare-number set precedes all credentials.
        lock_document_identity_keys(
            self.db,
            [
                DocumentIdentityKeys(
                    person.identity_digest,
                    person.passport_number_digest,
                    person.document_issuing_country,
                )
                for context in contexts
                for identifier in context.people
                if (person := self.db.get(Person, identifier)) is not None
            ],
        )
        lock_registration_credential_keys(
            self.db,
            [
                *(key for context in contexts for key in context.credential_keys),
                *claim_lifecycle_lock_keys(related),
                *registration_credential_lock_keys(
                    {
                        self.lookup.digest_domain("registration-verification-email", email)
                        for email in emails
                    },
                    identity_digests=identities,
                ),
            ],
        )
        lock_person_memberships(
            self.db,
            [key for c in contexts for key in c.membership_keys]
            + person_membership_lock_keys(
                identity_digests=identities,
                phone_digests=phones,
                email_digests={
                    self.lookup.digest_domain("person-email", email) for email in emails
                },
            ),
        )
        requests = {key for c in contexts for key in c.requests}
        if requests:
            list(
                self.db.scalars(
                    select(PersonnelIntakeRequest)
                    .where(PersonnelIntakeRequest.id.in_(requests))
                    .order_by(PersonnelIntakeRequest.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        guardians = GuardianReadinessPolicy(self.db)
        for identifier in sorted({key for c in contexts for key in c.graph_ids}):
            guardians.lock_guardian_relationships(identifier)
        lock_participants(
            self.db,
            sorted({key for c in contexts for key in c.people}),
            sorted({key for c in contexts for key in c.account_ids}),
        )
        lock_directory(self.db, sorted({key for c in contexts for key in c.team_ids}))
        for context in contexts:
            current = discover_claim_lock_context(
                self.db,
                identity_digest=context.identity_digest,
                cipher=self.cipher,
                lookup=self.lookup,
                stable_person_id=context.stable_person_id,
            )
            if current != context:
                raise ProfileClaimError(code="profile_claim_retry", detail="资料正在更新，请重试")
        if self._claim_fingerprint(self._related(row)) != before:
            raise ProfileClaimError(code="profile_claim_retry", detail="申请正在更新，请重试")
        related = list(
            self.db.scalars(
                select(ProfileClaim)
                .where(ProfileClaim.id.in_(before))
                .order_by(ProfileClaim.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if self._claim_fingerprint(related) != before:
            raise ProfileClaimError(code="profile_claim_retry", detail="申请正在更新，请重试")
        list(
            self.db.scalars(
                select(ProfileClaimBinding)
                .where(ProfileClaimBinding.claim_id.in_(before))
                .order_by(ProfileClaimBinding.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        return related

    def _lock_sessions(self, related: list[ProfileClaim]) -> None:
        ids = [r.id for r in related]
        list(
            self.db.scalars(
                select(ProfileClaimSession)
                .where(
                    or_(
                        ProfileClaimSession.claim_id.in_(ids),
                        ProfileClaimSession.id.in_([r.session_id for r in related]),
                    )
                )
                .order_by(ProfileClaimSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        person_ids = {r.target_person_id for r in related if r.target_person_id}
        list(
            self.db.scalars(
                select(AssistedActivationSession)
                .where(AssistedActivationSession.person_id.in_(person_ids))
                .order_by(AssistedActivationSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    def _release_claim(self, claim: ProfileClaim) -> None:
        for reservation in self.db.scalars(
            select(PersonnelCredentialReservation).where(
                PersonnelCredentialReservation.profile_claim_id == claim.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        ):
            reservation.ended_at = self.clock()
        self.db.flush()

    def approve(self, actor: AuthContext, claim_id: UUID, input: ClaimDecisionInput) -> ClaimDetail:
        return self._decide(actor, claim_id, input, "approve")

    def reject(self, actor: AuthContext, claim_id: UUID, input: ClaimReasonInput) -> ClaimDetail:
        return self._decide(actor, claim_id, input, "reject")

    def revoke(self, actor: AuthContext, claim_id: UUID, input: ClaimReasonInput) -> ClaimDetail:
        return self._decide(actor, claim_id, input, "revoke")

    def _decide(
        self,
        actor: AuthContext,
        claim_id: UUID,
        input: ClaimDecisionInput | ClaimReasonInput,
        action: Literal["approve", "reject", "revoke"],
    ) -> ClaimDetail:
        try:
            with atomic_write_transaction(self.db):
                row = self._row(actor, claim_id)
                self.access.require_review(actor, row.target_person_id)
                if (
                    action == "approve"
                    and isinstance(input, ClaimDecisionInput)
                    and not input.identity_confirmed
                ):
                    raise ProfileClaimError(status=422, detail="请明确确认已核对本人身份")
                if self._is_self(actor, row):
                    raise ProfileClaimError(
                        status=403, code="profile_claim_forbidden", detail="不能审核本人申请"
                    )
                # Operation mutex is disjoint from credentials and follows the
                # existing approval service operation-before-credential pattern.
                lock_registration_credential_keys(
                    self.db,
                    [
                        registration_advisory_lock_key(
                            "claim-review-operation", f"{actor.account.id}:{input.idempotency_key}"
                        )
                    ],
                )
                related = self._lock_lifecycle(actor, row)
                self.access.require_review(actor, row.target_person_id)
                fingerprint = self.lookup.digest_domain(
                    "profile-claim-decision",
                    canonical_json(
                        {
                            "claim_id": str(claim_id),
                            "action": action,
                            "input": input.model_dump(mode="json"),
                        }
                    ),
                )
                prior = self.db.scalar(
                    select(ProfileClaimDecision).where(
                        ProfileClaimDecision.actor_user_id == actor.account.id,
                        ProfileClaimDecision.idempotency_key == input.idempotency_key,
                    )
                )
                if prior:
                    if (
                        prior.claim_id != claim_id
                        or prior.action != action
                        or prior.request_fingerprint != fingerprint
                    ):
                        raise ProfileClaimError()
                    return self.detail(actor, claim_id)
                if (
                    row.version != input.expected_version
                    or (
                        action in {"approve", "reject"}
                        and (row.status != "pending" or row.expires_at <= self.clock())
                    )
                    or (action == "revoke" and row.status != "approved")
                ):
                    raise ProfileClaimError()
                value = self._submission(row)
                if action == "approve":
                    if self._approval_reason(row, value) is not None:
                        raise ProfileClaimError()
                    CredentialReservationPolicy(
                        self.db, lookup=self.lookup
                    ).require_email_available(
                        email=value.email, person_id=row.target_person_id, profile_claim_id=row.id
                    )
                    payload = self._completion_candidates(row)
                    if payload:
                        policy = CredentialReservationPolicy(self.db, lookup=self.lookup)
                        for guardian in payload.guardians:
                            existing = self.db.scalar(
                                select(Person).where(
                                    Person.identity_digest
                                    == self.lookup.digest(guardian.identity_number)
                                )
                            )
                            policy.require_identity_available(
                                identity=guardian.identity_number,
                                person_id=existing.id if existing else None,
                            )
                            if guardian.email:
                                policy.require_email_available(
                                    email=guardian.email,
                                    person_id=existing.id if existing else None,
                                )
                self._lock_sessions(related)
                self.access.require_review(actor, row.target_person_id)
                # Waiting for a pre-existing session/redaction lock can cross
                # the seventh-day boundary. Recheck immediately before writes.
                if action in {"approve", "reject"} and row.expires_at <= self.clock():
                    raise ProfileClaimError()
                before = row.version
                approval = binding = None
                if action == "approve":
                    if row.target_person_id is None or row.target_approval_id is None:
                        raise ProfileClaimError()
                    validate_claim_proof(self.db, row, cipher=self.cipher, lookup=self.lookup)
                    if self._approval_reason(row, value) is not None:
                        raise ProfileClaimError()
                    self._release_claim(row)
                    person = self.db.get(Person, row.target_person_id)
                    trusted_before = self.db.get(AssistedApproval, row.target_approval_id)
                    if trusted_before is None:
                        raise ProfileClaimError()
                    from app.personnel_intake.guardian_records import authenticated_approval_facts

                    own, _ = authenticated_approval_facts(
                        trusted_before, cipher=self.cipher, lookup=self.lookup
                    )
                    origin = "preserved" if own.get("email") == value.email else "introduced"
                    approval_service = PersonnelApprovalService(
                        self.db, cipher=self.cipher, lookup=self.lookup
                    )
                    if person is not None and person.intake_mode == "pending":
                        approval = approval_service.apply_claim_completion_in_transaction(
                            actor,
                            person_id=row.target_person_id,
                            completion=value.completion or ClaimCompletionFields(),
                            expected_approval_id=row.target_approval_id,
                            claim_id=row.id,
                            operation_key=input.idempotency_key,
                        )
                    else:
                        approval = approval_service.apply_claim_email_in_transaction(
                            actor,
                            person_id=row.target_person_id,
                            email=value.email,
                            expected_approval_id=row.target_approval_id,
                            claim_id=row.id,
                            operation_key=input.idempotency_key,
                            operation_kind="claim_bind",
                        )
                    binding = ProfileClaimBinding(
                        claim_id=row.id,
                        person_id=row.target_person_id,
                        email_digest=row.email_digest,
                        approval_id=approval.id,
                        email_binding_origin=origin,
                    )
                    self.db.add(binding)
                    row.status = "approved"
                    invalidate_prelocked_pending_claims_in_transaction(
                        self.db,
                        claims=[
                            other
                            for other in related
                            if other.id != row.id
                            and (
                                other.target_person_id == row.target_person_id
                                or other.identity_digest == row.identity_digest
                            )
                        ],
                        occurred_at=self.clock(),
                        actor_user_id=actor.account.id,
                    )
                elif action == "reject":
                    row.status = "rejected"
                    self._release_claim(row)
                else:
                    if row.target_person_id is None:
                        raise ProfileClaimError()
                    binding = require_binding_ownership_in_transaction(
                        self.db,
                        person_id=row.target_person_id,
                        email_digest=row.email_digest,
                        cipher=self.cipher,
                        lookup=self.lookup,
                    )
                    if binding.claim_id != row.id:
                        raise ProfileClaimError()
                    profile = self.db.get(AssistedPersonProfile, row.target_person_id)
                    if profile is None:
                        raise ProfileClaimError()
                    approval = PersonnelApprovalService(
                        self.db, cipher=self.cipher, lookup=self.lookup
                    ).apply_claim_email_in_transaction(
                        actor,
                        person_id=row.target_person_id,
                        email=value.email if binding.email_binding_origin == "preserved" else None,
                        expected_approval_id=profile.current_approval_id,
                        claim_id=row.id,
                        operation_key=input.idempotency_key,
                        operation_kind="claim_revoke",
                    )
                    binding.revoked_at = self.clock()
                    row.status = "revoked"
                row.version += 1
                row.reviewed_by_user_id, row.reviewed_at = actor.account.id, self.clock()
                row.public_reason = (
                    input.reason if isinstance(input, ClaimReasonInput) else input.note
                )
                self.db.flush()
                self.db.add(
                    ProfileClaimDecision(
                        claim_id=row.id,
                        actor_user_id=actor.account.id,
                        action=action,
                        idempotency_key=input.idempotency_key,
                        request_fingerprint=fingerprint,
                        before_version=before,
                        after_version=row.version,
                        result_approval_id=approval.id if approval else None,
                        result_binding_id=binding.id if binding else None,
                    )
                )
                # Keep old challenge consumption. All claim/session rows above
                # are already locked; the invalidation challenge locks are LAST.
                if action != "reject" and row.target_person_id:
                    invalidate_in_transaction(
                        self.db, person_id=row.target_person_id, occurred_at=self.clock()
                    )
                AuditService(self.db).append(
                    actor_user_id=actor.account.id,
                    action=f"profile_claim.{action}",
                    resource_type="profile_claim",
                    resource_id=row.id,
                    purpose=None,
                    outcome="allowed",
                    request_id=str(uuid4()),
                    metadata={"old_version": before, "new_version": row.version},
                )
                self.db.flush()
                return self.detail(actor, row.id)
        except (IntegrityError, VerificationInvalidOrExpired) as error:
            raise ProfileClaimError() from error
