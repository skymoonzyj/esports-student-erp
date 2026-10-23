from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.identity.membership_locks import (
    lock_person_memberships,
    person_membership_lock_keys,
)
from app.identity.models import (
    GuardianProfile,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.readiness import is_minor
from app.personnel_intake.locks import require_registration_reservations
from app.rbac.models import Role, UserRole
from app.registration.consents import (
    CURRENT_REGISTRATION_TERMS_VERSION,
    registration_terms_digest,
)
from app.registration.matching_service import (
    RegistrationApplicationInvalid,
    RegistrationCandidateMembership,
    RegistrationIdentitySnapshot,
    RegistrationMatchingService,
)
from app.registration.models import (
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationFinalizationMatchKind,
    RegistrationSession,
    StaffIdentityApplication,
)
from app.registration.verification_service import (
    lock_registration_credential_keys,
    registration_advisory_lock_key,
    registration_credential_lock_keys,
)
from app.revenue_share.service import RevenueShareService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

PURPOSE_ROLES = {
    "student": ("registered_user", "student"),
    "guardian": ("registered_user", "guardian"),
    "staff_applicant": ("registered_user",),
    "visitor": ("registered_user",),
}

_CONCURRENT_CONSTRAINTS = {
    "people_identity_digest_key",
    "user_accounts_person_id_key",
    "uq_account_login_identifiers_identifier_digest",
    "student_profiles_student_number_key",
    "uq_staff_identity_applications_open_person_type",
}

_FINALIZATION_CLAIM_STATES = {
    "ready_to_finalize",
    "identity_completed",
    "pending_guardian",
}


class RegistrationFinalizationError(ValueError):
    code = "registration_finalization_error"


class RegistrationFinalizationUnavailable(RegistrationFinalizationError):
    code = "registration_action_unavailable"


class RegistrationConflictPendingReview(RegistrationFinalizationError):
    code = "registration_conflict_pending_review"


class GuardianConfirmationRequired(RegistrationFinalizationError):
    code = "guardian_confirmation_required"


class _RetryFinalizedSnapshot(Exception):
    pass


def clear_registration_application_secrets(
    application: RegistrationApplication,
) -> None:
    application.verified_identifier_kind = None
    application.verified_identifier_digest = None
    application.verified_identifier_ciphertext = None
    application.password_hash = None
    application.full_name = None
    application.identity_ciphertext = None
    application.identity_digest = None
    application.sex = None
    application.date_of_birth = None
    application.requested_staff_type = None
    application.staff_application_reason = None
    application.visit_reason = None


@dataclass(frozen=True, slots=True)
class RegistrationFinalizationResult:
    application_id: UUID
    person_id: UUID
    account_id: UUID
    match_kind: RegistrationFinalizationMatchKind


@dataclass(frozen=True, slots=True)
class RegistrationClaimLockSnapshot:
    application_id: UUID
    state: str
    expires_at: datetime
    identifier_digest: str | None
    identity_digest: str | None
    reviewed_candidate_person_id: UUID | None
    identifier_kind: str | None = None


class RegistrationFinalizationService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        passwords: PasswordService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.passwords = passwords or PasswordService()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.matching = RegistrationMatchingService(
            db,
            cipher=cipher,
            lookup=lookup,
        )

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration finalization clock must be timezone-aware")
        return value

    def _claim_lock_snapshot(
        self,
        application_id: UUID,
    ) -> RegistrationClaimLockSnapshot:
        row = self.db.execute(
            select(
                RegistrationApplication.id,
                RegistrationApplication.state,
                RegistrationApplication.expires_at,
                RegistrationApplication.verified_identifier_digest,
                RegistrationApplication.identity_digest,
                RegistrationApplication.reviewed_candidate_person_id,
                RegistrationApplication.verified_identifier_kind,
            ).where(RegistrationApplication.id == application_id)
        ).one_or_none()
        if row is None:
            raise RegistrationFinalizationUnavailable("registration application is unavailable")
        return RegistrationClaimLockSnapshot(
            application_id=row.id,
            state=row.state,
            expires_at=row.expires_at,
            identifier_digest=row.verified_identifier_digest,
            identity_digest=row.identity_digest,
            reviewed_candidate_person_id=row.reviewed_candidate_person_id,
            identifier_kind=row.verified_identifier_kind,
        )

    @staticmethod
    def _snapshot_matches_application(
        snapshot: RegistrationClaimLockSnapshot,
        application: RegistrationApplication,
    ) -> bool:
        return (
            application.id == snapshot.application_id
            and application.state == snapshot.state
            and application.expires_at == snapshot.expires_at
            and application.verified_identifier_digest == snapshot.identifier_digest
            and application.identity_digest == snapshot.identity_digest
            and application.verified_identifier_kind == snapshot.identifier_kind
            and application.reviewed_candidate_person_id == snapshot.reviewed_candidate_person_id
        )

    def _lock_ready_claim_snapshot(
        self,
        snapshot: RegistrationClaimLockSnapshot,
    ) -> None:
        if snapshot.identifier_digest is None or snapshot.identity_digest is None:
            raise RegistrationFinalizationUnavailable("registration claim digests are unavailable")
        lock_registration_credential_keys(
            self.db,
            registration_credential_lock_keys(
                {snapshot.identifier_digest},
                identity_digests={snapshot.identity_digest},
            ),
        )
        require_registration_reservations(
            self.db,
            lookup=self.lookup,
            identifier_kind=snapshot.identifier_kind,
            identifier_digest=snapshot.identifier_digest,
            identity_digest=snapshot.identity_digest,
        )

    def lock_candidate_claim_snapshot_in_transaction(
        self,
        application_id: UUID,
    ) -> RegistrationClaimLockSnapshot:
        snapshot = self._claim_lock_snapshot(application_id)
        if snapshot.state not in {
            "identity_completed",
            "pending_guardian",
            "conflict",
        }:
            return snapshot
        if snapshot.identifier_digest is None or snapshot.identity_digest is None:
            raise RegistrationFinalizationUnavailable("registration claim digests are unavailable")
        self._lock_ready_claim_snapshot(snapshot)
        return snapshot

    def candidate_claim_snapshot_matches(
        self,
        snapshot: RegistrationClaimLockSnapshot,
        application: RegistrationApplication,
    ) -> bool:
        return self._snapshot_matches_application(snapshot, application)

    def lock_candidate_person_ids_in_transaction(
        self,
        application: RegistrationApplication,
    ) -> set[UUID]:
        if application.verified_identifier_digest is None or application.identity_digest is None:
            raise RegistrationFinalizationUnavailable("registration claim digests are unavailable")
        lock_registration_credential_keys(
            self.db,
            [
                registration_advisory_lock_key(
                    "identifier",
                    application.verified_identifier_digest,
                ),
                registration_advisory_lock_key(
                    "identity",
                    application.identity_digest,
                ),
            ],
        )
        candidate_ids = self.matching.candidate_person_ids_in_transaction(application)
        if application.reviewed_candidate_person_id is not None:
            candidate_ids.add(application.reviewed_candidate_person_id)
        return candidate_ids

    def _lock_finalization_candidate_memberships(
        self,
        snapshot: RegistrationIdentitySnapshot,
    ) -> None:
        if snapshot.identifier.kind == "phone":
            keys = person_membership_lock_keys(
                identity_digests=[snapshot.identity_digest],
                phone_digests=[self.lookup.digest(snapshot.identifier.value)],
            )
        else:
            keys = person_membership_lock_keys(
                identity_digests=[snapshot.identity_digest],
                email_digests=[
                    self.lookup.digest_domain(
                        "person-email",
                        snapshot.identifier.value,
                    )
                ],
            )
        lock_person_memberships(self.db, keys)

    def _lock_finalization_candidates(
        self,
        application: RegistrationApplication,
        snapshot: RegistrationIdentitySnapshot,
    ) -> tuple[RegistrationCandidateMembership, dict[UUID, Person]]:
        membership = self.matching.candidate_membership_for_snapshot_in_transaction(snapshot)
        person_ids = set(membership.person_ids)
        if application.reviewed_candidate_person_id is not None:
            person_ids.add(application.reviewed_candidate_person_id)
        # Preserve the pre-wait candidate snapshot, but acquire shared membership
        # fences before Person rows, matching assisted and legacy contact writers.
        self._lock_finalization_candidate_memberships(snapshot)
        people = self.matching.lock_candidate_people_in_transaction(person_ids)
        return membership, {person.id: person for person in people}

    def _new_person(self, snapshot: RegistrationIdentitySnapshot) -> Person:
        person = Person(
            full_name=snapshot.full_name,
            identity_ciphertext=self.cipher.encrypt(
                snapshot.identity_number,
                field_name="identity_number",
            ),
            identity_digest=snapshot.identity_digest,
            phone_ciphertext=None,
            phone_digest=None,
            email=None,
            email_ciphertext=None,
            email_digest=None,
            sex=snapshot.sex,
            date_of_birth=snapshot.date_of_birth,
        )
        self._apply_verified_contact(person, snapshot)
        self.db.add(person)
        self.db.flush()
        return person

    def _apply_verified_contact(
        self,
        person: Person,
        snapshot: RegistrationIdentitySnapshot,
    ) -> None:
        if snapshot.identifier.kind == "phone":
            digest = self.lookup.digest(snapshot.identifier.value)
            if person.phone_ciphertext is None and person.phone_digest is None:
                person.phone_ciphertext = self.cipher.encrypt(
                    snapshot.identifier.value,
                    field_name="phone",
                )
                person.phone_digest = digest
            elif person.phone_digest != digest:
                raise RegistrationFinalizationUnavailable(
                    "registration phone does not match person"
                )
            return
        digest = self.lookup.digest_domain("person-email", snapshot.identifier.value)
        if person.email_ciphertext is None and person.email_digest is None:
            person.email = None
            person.email_ciphertext = self.cipher.encrypt(
                snapshot.identifier.value,
                field_name="email",
            )
            person.email_digest = digest
        elif person.email_digest != digest:
            raise RegistrationFinalizationUnavailable("registration email does not match person")

    def _account_for_person(self, person_id: UUID) -> UserAccount | None:
        return self.db.scalar(
            select(UserAccount)
            .where(UserAccount.person_id == person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _new_account(
        self,
        application: RegistrationApplication,
        person: Person,
        snapshot: RegistrationIdentitySnapshot,
        now: datetime,
    ) -> UserAccount:
        if not application.password_hash or not self.passwords.is_valid_encoded_hash(
            application.password_hash
        ):
            raise RegistrationFinalizationUnavailable("registration password is unavailable")
        if self._account_for_person(person.id) is not None:
            raise RegistrationFinalizationUnavailable("person already has an account")
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=None,
            email_normalized=None,
            password_hash=application.password_hash,
            status="active",
            force_password_change=False,
            failed_login_count=0,
            self_service_primary_purpose=application.primary_purpose,
        )
        self.db.add(account)
        self.db.flush()
        self.db.add(
            AccountLoginIdentifier(
                account_id=account.id,
                kind=snapshot.identifier.kind,
                identifier_digest=login_identifier_digest(
                    self.lookup,
                    NormalizedLoginIdentifier(
                        snapshot.identifier.kind,
                        snapshot.identifier.value,
                    ),
                ),
                verified_at=now,
                verification_source="self_registration",
            )
        )
        self.db.flush()
        return account

    def _assign_roles(self, account: UserAccount, purpose: str) -> None:
        role_keys = PURPOSE_ROLES.get(purpose)
        if role_keys is None:
            raise RegistrationFinalizationUnavailable("registration purpose is invalid")
        roles = {
            role.key: role
            for role in self.db.scalars(
                select(Role).where(Role.key.in_(role_keys)).order_by(Role.key)
            )
        }
        if set(roles) != set(role_keys):
            raise RegistrationFinalizationUnavailable("registration roles are unavailable")
        for key in role_keys:
            self.db.add(UserRole(user_id=account.id, role_id=roles[key].id))

    def _student_number(self, now: datetime) -> str:
        from app.identity.student_numbers import generate_student_number

        try:
            return generate_student_number(self.db, now)
        except ValueError as exc:
            raise RegistrationFinalizationUnavailable(
                "registration student number is unavailable"
            ) from exc

    def _ensure_purpose_profile(
        self,
        person: Person,
        snapshot: RegistrationIdentitySnapshot,
        now: datetime,
    ) -> None:
        purpose = snapshot.primary_purpose
        if purpose == "student":
            if self.db.get(StudentProfile, person.id) is None:
                self.db.add(
                    StudentProfile(
                        person_id=person.id,
                        student_number=self._student_number(now),
                        status="draft",
                    )
                )
            return
        if purpose == "guardian":
            if self.db.get(GuardianProfile, person.id) is None:
                self.db.add(GuardianProfile(person_id=person.id))
            return
        if purpose == "visitor":
            if not snapshot.visit_reason:
                raise RegistrationFinalizationUnavailable(
                    "registration visit reason is unavailable"
                )
            if self.db.get(VisitorProfile, person.id) is None:
                self.db.add(
                    VisitorProfile(
                        person_id=person.id,
                        linked_student_id=None,
                        visit_reason=snapshot.visit_reason,
                    )
                )
            return
        if purpose == "staff_applicant":
            if self.db.get(StaffProfile, person.id) is not None:
                raise RegistrationFinalizationUnavailable(
                    "registration staff purpose is incompatible"
                )
            if not snapshot.requested_staff_type or not snapshot.staff_application_reason:
                raise RegistrationFinalizationUnavailable(
                    "registration staff application is incomplete"
                )
            pending = self.db.scalar(
                select(StaffIdentityApplication.id).where(
                    StaffIdentityApplication.person_id == person.id,
                    StaffIdentityApplication.requested_staff_type == snapshot.requested_staff_type,
                    StaffIdentityApplication.status == "pending",
                )
            )
            if pending is None:
                self.db.add(
                    StaffIdentityApplication(
                        person_id=person.id,
                        requested_staff_type=snapshot.requested_staff_type,
                        application_reason=snapshot.staff_application_reason,
                        status="pending",
                    )
                )
            return
        raise RegistrationFinalizationUnavailable("registration purpose is invalid")

    def _application_consents(
        self,
        application_id: UUID,
    ) -> list[RegistrationConsentAcceptance]:
        consents = list(
            self.db.scalars(
                select(RegistrationConsentAcceptance)
                .where(RegistrationConsentAcceptance.registration_application_id == application_id)
                .order_by(RegistrationConsentAcceptance.accepted_at)
                .with_for_update()
            )
        )
        if not consents:
            raise RegistrationFinalizationUnavailable("registration consent is unavailable")
        required_digest = registration_terms_digest(CURRENT_REGISTRATION_TERMS_VERSION)
        if required_digest is None or not any(
            consent.consent_type == "registration_terms"
            and consent.policy_version == CURRENT_REGISTRATION_TERMS_VERSION
            and hmac.compare_digest(consent.content_digest, required_digest)
            for consent in consents
        ):
            raise RegistrationFinalizationUnavailable("current registration consent is unavailable")
        return consents

    def _transfer_consents(
        self,
        application_id: UUID,
        account_id: UUID,
    ) -> None:
        consents = self._application_consents(application_id)
        for consent in consents:
            consent.registration_application_id = None
            consent.account_id = account_id

    def _complete_application(
        self,
        *,
        application: RegistrationApplication,
        person: Person,
        account: UserAccount,
        match_kind: RegistrationFinalizationMatchKind,
        transfer_consents: bool,
        now: datetime,
        request_id: str,
    ) -> None:
        if (
            application.reviewed_candidate_person_id is not None
            and person.id != application.reviewed_candidate_person_id
        ):
            raise RegistrationFinalizationUnavailable(
                "reviewed registration candidate changed before completion"
            )
        if transfer_consents:
            self._transfer_consents(application.id, account.id)
        else:
            self._application_consents(application.id)
        application.state = "finalized"
        application.matched_person_id = person.id
        application.finalized_account_id = account.id
        application.finalization_match_kind = match_kind
        application.conflict_code = None
        application.reviewed_candidate_person_id = None
        application.finalized_at = now
        RevenueShareService(self.db).bind_registration_referral(
            application=application,
            student_person_id=person.id,
            bound_at=now,
        )
        clear_registration_application_secrets(application)
        self.db.execute(
            update(RegistrationSession)
            .where(
                RegistrationSession.application_id == application.id,
                RegistrationSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        AuditService(self.db).append(
            actor_user_id=None,
            action="registration.finalize",
            resource_type="registration_application",
            resource_id=application.id,
            purpose="self_service_registration",
            outcome="allowed",
            request_id=request_id,
            metadata={"person_id": str(person.id)},
        )
        self.db.flush()

    def _finalized_result(
        self,
        application: RegistrationApplication,
    ) -> RegistrationFinalizationResult:
        if application.matched_person_id is None or application.finalized_account_id is None:
            raise RegistrationFinalizationUnavailable(
                "finalized registration references are unavailable"
            )
        account = self.db.get(UserAccount, application.finalized_account_id)
        if account is None or account.person_id != application.matched_person_id:
            raise RegistrationFinalizationUnavailable(
                "finalized registration account is unavailable"
            )
        if application.finalization_match_kind not in {
            "new_person",
            "claim_existing",
            "existing_account",
        }:
            raise RegistrationFinalizationUnavailable(
                "finalized registration match kind is unavailable"
            )
        match_kind = cast(
            RegistrationFinalizationMatchKind,
            application.finalization_match_kind,
        )
        return RegistrationFinalizationResult(
            application_id=application.id,
            person_id=application.matched_person_id,
            account_id=application.finalized_account_id,
            match_kind=match_kind,
        )

    def _mark_concurrent_conflict(self, application_id: UUID) -> bool:
        with write_transaction(self.db):
            try:
                snapshot = self._claim_lock_snapshot(application_id)
            except RegistrationFinalizationUnavailable:
                return False
            if snapshot.state not in _FINALIZATION_CLAIM_STATES | {"conflict"}:
                return False
            if snapshot.identifier_digest is None or snapshot.identity_digest is None:
                return False
            self._lock_ready_claim_snapshot(snapshot)
            application = self.db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == application_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if application is None:
                return False
            now = self._now()
            if application.expires_at <= now:
                return False
            if (
                application.id != snapshot.application_id
                or application.expires_at != snapshot.expires_at
                or application.verified_identifier_digest != snapshot.identifier_digest
                or application.identity_digest != snapshot.identity_digest
                or application.reviewed_candidate_person_id != snapshot.reviewed_candidate_person_id
            ):
                return False
            if application.state == "conflict":
                return True
            if (
                snapshot.state == "conflict"
                or application.state != snapshot.state
                or application.state not in _FINALIZATION_CLAIM_STATES
            ):
                return False
            application.state = "conflict"
            application.conflict_code = "concurrent_claim"
            application.reviewed_candidate_person_id = None
            return True

    @staticmethod
    def _constraint_name(exc: IntegrityError) -> str | None:
        return cast(str | None, getattr(getattr(exc.orig, "diag", None), "constraint_name", None))

    def finalize(
        self,
        application_id: UUID,
        *,
        request_id: str,
    ) -> RegistrationFinalizationResult:
        try:
            return self._finalize_once(
                application_id,
                request_id=request_id,
                allow_finalized_snapshot_retry=True,
            )
        except _RetryFinalizedSnapshot:
            return self._finalize_once(
                application_id,
                request_id=request_id,
                allow_finalized_snapshot_retry=False,
            )

    def _finalize_once(
        self,
        application_id: UUID,
        *,
        request_id: str,
        allow_finalized_snapshot_retry: bool,
    ) -> RegistrationFinalizationResult:
        result: RegistrationFinalizationResult | None = None
        conflict_pending = False
        guardian_required = False
        try:
            with write_transaction(self.db):
                claim_snapshot = self._claim_lock_snapshot(application_id)
                if claim_snapshot.state in _FINALIZATION_CLAIM_STATES:
                    self._lock_ready_claim_snapshot(claim_snapshot)
                application = self.db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None:
                    raise RegistrationFinalizationUnavailable(
                        "registration application is unavailable"
                    )
                if not self._snapshot_matches_application(
                    claim_snapshot,
                    application,
                ) and not (
                    claim_snapshot.state in _FINALIZATION_CLAIM_STATES
                    and application.state == "conflict"
                    and application.id == claim_snapshot.application_id
                    and application.expires_at == claim_snapshot.expires_at
                    and application.verified_identifier_digest == claim_snapshot.identifier_digest
                    and application.identity_digest == claim_snapshot.identity_digest
                    and application.reviewed_candidate_person_id is None
                ):
                    if (
                        allow_finalized_snapshot_retry
                        and claim_snapshot.state in _FINALIZATION_CLAIM_STATES
                        and application.state == "finalized"
                    ):
                        raise _RetryFinalizedSnapshot
                    raise RegistrationFinalizationUnavailable(
                        "registration application changed before finalization"
                    )
                now = self._now()
                reference_date = business_date_at(now)
                if (
                    application.primary_purpose == "student"
                    and application.date_of_birth is not None
                    and application.state in {"identity_completed", "pending_guardian"}
                    and not is_minor(application.date_of_birth, reference_date)
                ):
                    application.state = "ready_to_finalize"
                if application.state == "finalized":
                    result = self._finalized_result(application)
                elif application.expires_at <= now:
                    raise RegistrationFinalizationUnavailable(
                        "registration application is unavailable"
                    )
                elif application.state == "conflict":
                    conflict_pending = True
                elif (
                    application.primary_purpose == "student"
                    and application.date_of_birth is not None
                    and is_minor(
                        application.date_of_birth,
                        reference_date,
                    )
                    and application.state in {"identity_completed", "pending_guardian"}
                ):
                    application.state = "pending_guardian"
                    guardian_required = True
                elif application.state != "ready_to_finalize":
                    raise RegistrationFinalizationUnavailable(
                        "registration application is not ready"
                    )
                else:
                    snapshot = self.matching.application_snapshot(application)
                    candidate_membership, locked_people_by_id = self._lock_finalization_candidates(
                        application, snapshot
                    )
                    refreshed_membership = (
                        self.matching.candidate_membership_for_snapshot_in_transaction(snapshot)
                    )
                    if refreshed_membership != candidate_membership:
                        application.state = "conflict"
                        application.conflict_code = "concurrent_claim"
                        application.reviewed_candidate_person_id = None
                        conflict_pending = True
                    else:
                        match = self.matching.classify_locked_candidates_in_transaction(
                            snapshot,
                            candidate_membership,
                            locked_people_by_id.values(),
                        )
                        if match.kind == "conflict":
                            application.state = "conflict"
                            application.conflict_code = match.conflict_code
                            application.reviewed_candidate_person_id = None
                            conflict_pending = True
                        elif (
                            application.reviewed_candidate_person_id is not None
                            and match.person_id != application.reviewed_candidate_person_id
                        ):
                            application.state = "conflict"
                            application.conflict_code = "reviewed_candidate_changed"
                            application.reviewed_candidate_person_id = None
                            conflict_pending = True
                        elif match.kind == "existing_account":
                            assert match.person_id is not None
                            person = locked_people_by_id.get(match.person_id)
                            if person is None or person.deleted_at is not None:
                                raise RegistrationFinalizationUnavailable(
                                    "existing registration person is unavailable"
                                )
                            account = self._account_for_person(person.id)
                            if account is None:
                                raise RegistrationFinalizationUnavailable(
                                    "existing registration account is unavailable"
                                )
                            if (
                                application.reviewed_candidate_person_id is not None
                                and account.status != "active"
                            ):
                                application.state = "conflict"
                                application.conflict_code = "reviewed_candidate_unavailable"
                                application.reviewed_candidate_person_id = None
                                conflict_pending = True
                            else:
                                self._complete_application(
                                    application=application,
                                    person=person,
                                    account=account,
                                    match_kind="existing_account",
                                    transfer_consents=False,
                                    now=now,
                                    request_id=request_id,
                                )
                                result = RegistrationFinalizationResult(
                                    application_id=application.id,
                                    person_id=person.id,
                                    account_id=account.id,
                                    match_kind="existing_account",
                                )
                        else:
                            if match.kind == "new_person":
                                person = self._new_person(snapshot)
                            else:
                                assert match.person_id is not None
                                person = locked_people_by_id.get(match.person_id)
                                if person is None or person.deleted_at is not None:
                                    raise RegistrationFinalizationUnavailable(
                                        "registration person is unavailable"
                                    )
                                self._apply_verified_contact(person, snapshot)
                            account = self._new_account(
                                application,
                                person,
                                snapshot,
                                now,
                            )
                            self._assign_roles(account, application.primary_purpose)
                            self._ensure_purpose_profile(person, snapshot, now)
                            self._complete_application(
                                application=application,
                                person=person,
                                account=account,
                                match_kind=match.kind,
                                transfer_consents=True,
                                now=now,
                                request_id=request_id,
                            )
                            result = RegistrationFinalizationResult(
                                application_id=application.id,
                                person_id=person.id,
                                account_id=account.id,
                                match_kind=match.kind,
                            )
        except IntegrityError as exc:
            if self._constraint_name(exc) not in _CONCURRENT_CONSTRAINTS:
                raise
            if not self._mark_concurrent_conflict(application_id):
                raise RegistrationFinalizationUnavailable(
                    "registration application changed after concurrent claim"
                ) from exc
            raise RegistrationConflictPendingReview(
                "registration conflict is pending review"
            ) from exc
        except RegistrationApplicationInvalid as exc:
            raise RegistrationFinalizationUnavailable(
                "registration application is invalid"
            ) from exc

        if conflict_pending:
            raise RegistrationConflictPendingReview("registration conflict is pending review")
        if guardian_required:
            raise GuardianConfirmationRequired("guardian confirmation is required")
        if result is None:
            raise RegistrationFinalizationUnavailable(
                "registration finalization did not produce a result"
            )
        return result
