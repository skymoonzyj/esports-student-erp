"""Atomic assisted approval. Public methods own the transaction; helpers never commit."""

import hashlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.core.transactions import atomic_write_transaction
from app.identity.document_identity import (
    DocumentIdentityConflict,
    DocumentIdentityKeys,
    document_identity_keys,
    lock_document_identity_keys,
    normalize_document_identity,
    require_document_available,
)
from app.identity.membership_locks import lock_person_memberships, person_membership_lock_keys
from app.identity.models import Person, StaffProfile, StudentProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.student_numbers import generate_student_number
from app.personnel_intake.claims.binding import (
    PreparedClaimLifecycle,
    invalidate_prelocked_pending_claims_in_transaction,
    prepare_claim_lifecycle,
)
from app.personnel_intake.claims.schemas import ClaimCompletionFields
from app.personnel_intake.codec import normalize_payload, normalize_pending_payload
from app.personnel_intake.guardian_records import (
    GUARDIAN_SNAPSHOT_AAD,
    GUARDIAN_SNAPSHOT_DOMAIN,
    PERSON_SNAPSHOT_AAD,
    PERSON_SNAPSHOT_DOMAIN,
    AssistedGuardianWriter,
    canonical_json,
    capture_current_snapshots,
    conflict,
    facts_match,
    guardian_graph_component,
    insert_person,
)
from app.personnel_intake.locks import person_credential_fingerprint
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
)
from app.personnel_intake.schemas import (
    ApprovalResult,
    AssistedGuardianView,
    AssistedProfileCorrection,
    IntakeInput,
    IntakePayloadView,
    IntakeView,
    PendingIntakeInput,
    PendingIntakePayloadView,
    PendingProfileCorrection,
)
from app.personnel_intake.service import PersonnelIntakeService
from app.registration.verification_service import (
    lock_registration_credential_keys,
    registration_credential_lock_keys,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.team_management.errors import TeamServiceError
from app.team_management.write_support import lock_directory, lock_participants


class PersonnelApprovalService(PersonnelIntakeService):
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        super().__init__(db, cipher=cipher, lookup=lookup)

    def apply_claim_completion_in_transaction(
        self,
        actor: AuthContext,
        *,
        person_id: UUID,
        completion: "ClaimCompletionFields",
        expected_approval_id: UUID,
        claim_id: UUID,
        operation_key: UUID,
    ) -> AssistedApproval:
        """Reviewer owns the complete document/credential/business/claim lock prefix.

        Never commits, reacquires that prefix or performs lazy reservation expiry.
        The verified email and every authority fact come from authenticated server
        evidence, not an extra client field or an alleged consent/relationship.
        """
        from app.personnel_intake.claims.access import ClaimReviewAccess
        from app.personnel_intake.claims.completion import (
            completion_is_complete,
            merge_completion_payload,
        )
        from app.personnel_intake.claims.models import ProfileClaim
        from app.personnel_intake.claims.proof import completion_material, validate_claim_proof
        from app.personnel_intake.guardian_records import authenticated_approval_facts
        from app.personnel_intake.readiness import AssistedReadinessPolicy

        ClaimReviewAccess(self.db).require_review(actor, person_id)
        person = self.db.get(Person, person_id, populate_existing=True)
        profile = self.db.get(AssistedPersonProfile, person_id, populate_existing=True)
        old = self.db.get(AssistedApproval, expected_approval_id)
        claim = self.db.get(ProfileClaim, claim_id)
        if (
            person is None
            or person.deleted_at
            or person.intake_mode != "pending"
            or profile is None
            or profile.current_approval_id != expected_approval_id
            or profile.activation_requirement != "reviewed_claim"
            or old is None
            or old.person_id != person_id
            or claim is None
            or claim.status != "pending"
            or claim.target_person_id != person_id
            or claim.target_approval_id != old.id
            or self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is not None
        ):
            raise conflict()
        submitted = validate_claim_proof(self.db, claim, cipher=self.cipher, lookup=self.lookup)
        if (
            submitted.full_name != person.full_name
            or submitted.identity_keys(self.lookup).identity_digest != person.identity_digest
            or completion_material(completion)
            != completion_material(submitted.completion or ClaimCompletionFields())
        ):
            raise conflict()
        now = datetime.now(UTC)
        state = AssistedReadinessPolicy(
            self.db, cipher=self.cipher, lookup=self.lookup
        ).evaluate_many([person_id], reference_date=business_date_at(now))[person_id]
        if not state.completion_allowed:
            raise conflict()
        authenticated_approval_facts(old, cipher=self.cipher, lookup=self.lookup)
        row = self.db.get(PersonnelIntakeRequest, old.request_id)
        if row is None or row.person_id != person_id or row.state != "approved":
            raise conflict()
        current = self._payload(row)
        if not isinstance(current, PendingIntakePayloadView):
            raise conflict()
        payload = merge_completion_payload(
            current, completion, verified_email=submitted.email, today=business_date_at(now)
        )
        if not completion_is_complete(payload, today=business_date_at(now)):
            raise conflict()
        digest = self.lookup.digest_domain(
            "assisted-claim-operation",
            canonical_json(
                {
                    "claim_id": str(claim_id),
                    "kind": "claim_complete",
                    "email": submitted.email,
                    "completion": completion_material(completion),
                    "expected_approval_id": str(old.id),
                }
            ),
        )
        self._release(row)
        if person.phone_ciphertext is None and payload.phone:
            person.phone_ciphertext = self.cipher.encrypt(payload.phone, field_name="phone")
            person.phone_digest = self.lookup.digest(payload.phone)
        if person.sex is None:
            person.sex = payload.sex
        if person.date_of_birth is None:
            person.date_of_birth = payload.date_of_birth
        if person.email_ciphertext is None:
            person.email_ciphertext = self.cipher.encrypt(submitted.email, field_name="email")
            person.email_digest = self.lookup.digest_domain("person-email", submitted.email)
        relationships = tuple(old.guardian_relationship_ids)
        if not current.guardians and payload.guardians:
            relationships = AssistedGuardianWriter(
                self.db, cipher=self.cipher, lookup=self.lookup
            ).apply_in_transaction(person_id, payload.guardians, business_date_at(now))
        row.version += 1
        self._save_revision(actor, row, payload)
        approval = self._new_approval(
            actor,
            row,
            person,
            relationships,
            previous=old,
            key=operation_key,
            digest=digest,
            kind="claim_complete",
        )
        self._release(row)
        ready = AssistedReadinessPolicy(
            self.db, cipher=self.cipher, lookup=self.lookup
        ).evaluate_many([person_id], reference_date=business_date_at(now))[person_id]
        if not ready.activation_profile_ready:
            raise conflict()
        return approval

    def apply_claim_email_in_transaction(
        self,
        actor: AuthContext,
        *,
        person_id: UUID,
        email: str | None,
        expected_approval_id: UUID,
        claim_id: UUID,
        operation_key: UUID,
        operation_kind: Literal["claim_bind", "claim_revoke"],
    ) -> AssistedApproval:
        """Internal only: reviewer already owns the COMPLETE lifecycle lock set.

        Claim reservations must be released/transferred by that reviewer first.
        Do not call _lock_context or policy expiry here, after claim/session locks.
        """
        from app.personnel_intake.claims.access import ClaimReviewAccess
        from app.personnel_intake.guardian_records import authenticated_approval_facts

        ClaimReviewAccess(self.db).require_review(actor, person_id)
        profile = self.db.get(AssistedPersonProfile, person_id, populate_existing=True)
        person = self.db.get(Person, person_id, populate_existing=True)
        old = self.db.get(AssistedApproval, expected_approval_id)
        if (
            profile is None
            or person is None
            or old is None
            or old.person_id != person_id
            or person.deleted_at
            or profile.current_approval_id != old.id
            or profile.activation_requirement != "reviewed_claim"
            or self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id))
            is not None
        ):
            raise conflict()
        own, _ = authenticated_approval_facts(old, cipher=self.cipher, lookup=self.lookup)
        row = self.db.get(PersonnelIntakeRequest, old.request_id)
        if row is None or row.person_id != person_id or row.state != "approved":
            raise conflict()
        payload = self._payload(row).model_copy(update={"email": email})
        digest = self.lookup.digest_domain(
            "assisted-claim-operation",
            canonical_json(
                {
                    "claim_id": str(claim_id),
                    "kind": operation_kind,
                    "email": email,
                    "expected_approval_id": str(expected_approval_id),
                }
            ),
        )
        self._release(row)
        email_digest = self.lookup.digest_domain("person-email", email) if email else None
        preserve_existing = bool(
            email
            and own.get("email") == email
            and person.email_ciphertext
            and person.email_digest == email_digest
            and self.cipher.decrypt(person.email_ciphertext, field_name="email") == email
        )
        if not preserve_existing:
            person.email_ciphertext = (
                self.cipher.encrypt(email, field_name="email") if email else None
            )
            person.email_digest = email_digest
        row.version += 1
        self._save_revision(actor, row, payload)
        approval = self._new_approval(
            actor,
            row,
            person,
            tuple(old.guardian_relationship_ids),
            previous=old,
            key=operation_key,
            digest=digest,
            kind=operation_kind,
        )
        # Match normal approval/correction materialization: Person owns the
        # actual credential once approved. Keep ended request reservation
        # history, not active draft rows that would block the next revision.
        self._release(row)
        return approval

    def _require_admin(
        self,
        actor: AuthContext,
        *,
        permission: str = "personnel_intake.review",
        person_id: UUID | None = None,
        payload: IntakePayloadView | PendingIntakePayloadView | None = None,
        row: PersonnelIntakeRequest | None = None,
    ) -> None:
        roles, _ = self.access.actor_state(actor)
        if not roles & {"admin", "super_admin"} or not self.access.allowed(
            actor,
            permission,
            target_person_id=person_id or (row.person_id if row else None),
            coaches=payload.coach_person_ids
            if payload
            else (row.target_coach_person_ids if row else ()),
            team_id=payload.team_id if payload else (row.target_team_id if row else None),
        ):
            raise self._error(
                status=403, code="personnel_intake_forbidden", detail="无权办理此人员资料"
            )

    def _request(self, actor: AuthContext, request_id: UUID) -> PersonnelIntakeRequest:
        state = self.access.snapshot(actor)
        if not state.roles & {"admin", "super_admin"}:
            raise self._error(
                status=403, code="personnel_intake_forbidden", detail="无权办理此人员资料"
            )
        row = self.db.scalar(
            select(PersonnelIntakeRequest).where(
                PersonnelIntakeRequest.id == request_id,
                state.permission_predicate("personnel_intake.review"),
            )
        )
        if row is None:
            raise self._error(
                status=404, code="personnel_intake_not_found", detail="未找到建档申请"
            )
        self._require_admin(actor, row=row)
        return row

    def _operation(self, actor: AuthContext, key: UUID) -> None:
        digest = hashlib.sha256(f"assisted-operation:{actor.account.id}:{key}".encode()).digest()
        self.db.execute(
            select(func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True)))
        )

    def _fingerprint(
        self,
        kind: str,
        payload: IntakePayloadView | PendingIntakePayloadView,
        *,
        person_id: UUID | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
        preserve_email: bool = False,
        identity_confirmed: bool | None = None,
    ) -> str:
        command = payload.model_dump(mode="json")
        if preserve_email:
            # New omitted-email commands authenticate client intent, not the
            # mutable value resolved for this write. Affiliations are also
            # server-derived for corrections and are not part of that command.
            # Explicit-email callers retain their exact historical digest shape.
            for field in ("kind", "coach_person_ids", "team_id"):
                command.pop(field)
            command["email"] = {"operation": "preserve_current"}
        return self.lookup.digest_domain(
            "assisted-operation",
            canonical_json(
                {
                    "action": kind,
                    "person_id": str(person_id) if person_id else None,
                    "expected_version": expected_version,
                    "payload": command,
                    "reason": reason,
                    **(
                        {"identity_confirmed": identity_confirmed}
                        if identity_confirmed is not None
                        else {}
                    ),
                }
            ),
        )

    def _replay(
        self, actor: AuthContext, key: UUID, digest: str, kind: str
    ) -> ApprovalResult | None:
        old = self.db.scalar(
            select(AssistedApproval).where(
                AssistedApproval.approved_by_user_id == actor.account.id,
                AssistedApproval.operation_key == key,
            )
        )
        if old is None:
            return None
        if old.operation_digest != digest or old.operation_kind != kind:
            raise conflict()
        self._require_admin(
            actor,
            permission="people.update" if kind == "correction" else "personnel_intake.review",
            person_id=old.person_id,
            row=self.db.get(PersonnelIntakeRequest, old.request_id),
        )
        return self._result(old)

    def _result(self, approval: AssistedApproval) -> ApprovalResult:
        return ApprovalResult(
            request_id=None if approval.operation_kind == "direct_create" else approval.request_id,
            person_id=approval.person_id,
            approval_id=approval.id,
        )

    def _lock_context(
        self,
        actor: AuthContext,
        payload: IntakePayloadView | PendingIntakePayloadView,
        *,
        submitter_id: UUID,
        row: PersonnelIntakeRequest | None = None,
        person_id: UUID | None = None,
        permission: str = "personnel_intake.review",
        locked_graph_person_ids: set[UUID] | None = None,
        claim_lifecycle: PreparedClaimLifecycle | None = None,
    ) -> Person | None:
        values: list[IntakePayloadView | PendingIntakePayloadView | AssistedGuardianView] = [
            payload,
            *payload.guardians,
        ]
        own_digest = self._digests(payload)[0]
        digests = {
            own_digest,
            *(self.lookup.digest(value.identity_number) for value in payload.guardians),
        }
        people = list(self.db.scalars(select(Person).where(Person.identity_digest.in_(digests))))
        if person_id and all(person.id != person_id for person in people):
            existing = self.db.get(Person, person_id)
            if existing:
                people.append(existing)
        fingerprints = {
            person.id: person_credential_fingerprint(person) + (person.full_name,)
            for person in people
        }
        own = next(
            (person for person in people if person.identity_digest == own_digest),
            None,
        )
        target_id = person_id or (own.id if own else None)
        self._require_admin(actor, permission=permission, person_id=target_id, payload=payload)
        seeds = {person.id for person in people}
        guardian_ids, edges = guardian_graph_component(self.db, seeds)
        expected_guardian_ids = set(guardian_ids)
        ids = guardian_ids | set(payload.coach_person_ids) | {actor.person.id}
        ids.update(
            self.db.scalars(select(UserAccount.person_id).where(UserAccount.id == submitter_id))
        )
        account_ids = set(
            self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(ids)))
        ) | {actor.account.id, submitter_id}
        emails = {value.email for value in values if value.email}
        phones = {self.lookup.digest(value.phone) for value in values if value.phone}
        email_digests = {self.lookup.digest_domain("person-email", value) for value in emails}
        for person in people:
            digests.add(person.identity_digest)
            if person.phone_digest:
                phones.add(person.phone_digest)
            if person.email_digest:
                email_digests.add(person.email_digest)
            if person.email_ciphertext:
                emails.add(self.cipher.decrypt(person.email_ciphertext, field_name="email"))
        if claim_lifecycle is not None:
            ids.update(claim_lifecycle.context.people)
            account_ids.update(claim_lifecycle.context.account_ids)
            guardian_ids.update(claim_lifecycle.context.graph_ids)
        document_keys = [
            DocumentIdentityKeys(
                p.identity_digest, p.passport_number_digest, p.document_issuing_country
            )
            for p in people
        ]
        if isinstance(payload, PendingIntakePayloadView):
            document_keys.append(self._pending_document_keys(payload))
        lock_document_identity_keys(self.db, document_keys)
        lock_registration_credential_keys(
            self.db,
            [
                *(claim_lifecycle.credential_keys if claim_lifecycle else []),
                *registration_credential_lock_keys(
                    {
                        self.lookup.digest_domain("registration-verification-email", email)
                        for email in emails
                    },
                    identity_digests=digests,
                ),
            ],
        )
        lock_person_memberships(
            self.db,
            (claim_lifecycle.context.membership_keys if claim_lifecycle else [])
            + person_membership_lock_keys(
                identity_digests=digests, phone_digests=phones, email_digests=email_digests
            ),
        )
        request_ids = set(claim_lifecycle.context.requests) if claim_lifecycle else set()
        if row:
            request_ids.add(row.id)
        if request_ids:
            list(
                self.db.scalars(
                    select(PersonnelIntakeRequest)
                    .where(PersonnelIntakeRequest.id.in_(request_ids))
                    .order_by(PersonnelIntakeRequest.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
        guardians = GuardianReadinessPolicy(self.db)
        for identifier in sorted(guardian_ids):
            guardians.lock_guardian_relationships(identifier)
        lock_participants(self.db, sorted(ids), sorted(account_ids))
        current_guardians, current_edges = guardian_graph_component(self.db, seeds)
        if current_guardians != expected_guardian_ids or current_edges != edges:
            raise conflict()
        if any(
            person_credential_fingerprint(person) + (person.full_name,) != fingerprints[person.id]
            for person in people
        ):
            raise conflict()
        current_matches = set(
            self.db.scalars(select(Person.id).where(Person.identity_digest.in_(digests)))
        )
        if not current_matches.issubset(fingerprints):
            raise conflict()
        current_accounts = set(
            self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(ids)))
        )
        if not current_accounts.issubset(account_ids):
            raise conflict()
        directory_ids = set(claim_lifecycle.context.team_ids) if claim_lifecycle else set()
        if payload.team_id:
            directory_ids.add(payload.team_id)
        if directory_ids:
            lock_directory(self.db, sorted(directory_ids))
        if locked_graph_person_ids is not None:
            locked_graph_person_ids.update(guardian_ids)
        return self.db.get(Person, target_id) if target_id else None

    def _submitter(self, account_id: UUID) -> AuthContext:
        account = self.db.get(UserAccount, account_id)
        person = self.db.get(Person, account.person_id) if account else None
        if account is None or person is None:
            raise conflict()
        # Internal authorization context is never used for session authentication.
        return AuthContext(account=account, person=person, session=None)  # type: ignore[arg-type]

    def _check_credentials(
        self,
        payload: IntakePayloadView | PendingIntakePayloadView,
        person: Person | None,
        request_id: UUID | None,
    ) -> None:
        if isinstance(payload, PendingIntakePayloadView):
            keys = self._pending_document_keys(payload)
            require_document_available(
                self.db, keys, exclude_person_id=person.id if person else None
            )
            self.policy.require_reservation_available(
                kind="identity",
                digest=keys.identity_digest,
                person_id=person.id if person else None,
                request_id=request_id,
            )
            if payload.email:
                self.policy.require_email_available(
                    email=payload.email,
                    person_id=person.id if person else None,
                    request_id=request_id,
                )
        else:
            self.policy.require_available(
                identity=payload.identity_number,
                email=payload.email,
                person_id=person.id if person else None,
                request_id=request_id,
            )
        for value in payload.guardians:
            guardian = self.db.scalar(
                select(Person).where(
                    Person.identity_digest == self.lookup.digest(value.identity_number)
                )
            )
            self.policy.require_identity_available(
                identity=value.identity_number, person_id=guardian.id if guardian else None
            )
            if value.email:
                self.policy.require_email_available(
                    email=value.email, person_id=guardian.id if guardian else None
                )

    def _new_approval(
        self,
        actor: AuthContext,
        row: PersonnelIntakeRequest,
        person: Person,
        relationship_ids: tuple[UUID, ...],
        *,
        previous: AssistedApproval | None = None,
        key: UUID | None = None,
        digest: str | None = None,
        kind: str | None = None,
    ) -> AssistedApproval:
        own, guardians = capture_current_snapshots(
            self.db, person_id=person.id, relationship_ids=relationship_ids, cipher=self.cipher
        )
        approval = AssistedApproval(
            request_id=row.id,
            revision_version=row.version,
            person_id=person.id,
            approved_by_user_id=actor.account.id,
            approved_at=datetime.now(UTC),
            snapshot_ciphertext=self.cipher.encrypt(own, field_name=PERSON_SNAPSHOT_AAD),
            snapshot_digest=self.lookup.digest_domain(PERSON_SNAPSHOT_DOMAIN, own),
            guardian_relationship_version=row.version,
            guardian_relationship_ids=list(relationship_ids),
            guardian_snapshot_ciphertext=self.cipher.encrypt(
                guardians, field_name=GUARDIAN_SNAPSHOT_AAD
            ),
            guardian_snapshot_digest=self.lookup.digest_domain(GUARDIAN_SNAPSHOT_DOMAIN, guardians),
            supersedes_approval_id=previous.id if previous else None,
            operation_key=key,
            operation_digest=digest,
            operation_kind=kind,
        )
        self.db.add(approval)
        self.db.flush()
        profile = self.db.get(AssistedPersonProfile, person.id)
        if profile:
            if previous is None or profile.current_approval_id != previous.id:
                raise conflict()
            profile.current_approval_id = approval.id
            profile.version += 1
        else:
            self.db.add(
                AssistedPersonProfile(
                    person_id=person.id,
                    current_approval_id=approval.id,
                    activation_requirement="reserved_email"
                    if person.email_ciphertext and person.intake_mode != "pending"
                    else "reviewed_claim",
                )
            )
        self.db.flush()
        return approval

    def _materialize(
        self,
        actor: AuthContext,
        row: PersonnelIntakeRequest,
        payload: IntakePayloadView,
        person: Person | None,
        *,
        key: UUID | None = None,
        digest: str | None = None,
    ) -> ApprovalResult:
        from app.personnel_intake.responsibilities import replace_in_transaction
        from app.team_management.membership_service import TeamMembershipService

        self._check_credentials(payload, person, row.id)
        if person:
            if (
                not facts_match(person, payload, self.cipher)
                or self.db.get(AssistedPersonProfile, person.id) is not None
            ):
                raise conflict()
        else:
            person = insert_person(self.db, payload, cipher=self.cipher, lookup=self.lookup)
        now = datetime.now(UTC)
        if payload.kind in {"student", "both"} and self.db.get(StudentProfile, person.id) is None:
            self.db.add(
                StudentProfile(
                    person_id=person.id,
                    student_number=generate_student_number(self.db, now),
                    status="draft",
                )
            )
            self.db.flush()
        relation_ids = AssistedGuardianWriter(
            self.db, cipher=self.cipher, lookup=self.lookup
        ).apply_in_transaction(person.id, payload.guardians, business_date_at(now))
        approval = self._new_approval(
            actor,
            row,
            person,
            relation_ids,
            key=key,
            digest=digest,
            kind="direct_create" if key else None,
        )
        replace_in_transaction(self.db, actor, person.id, payload.coach_person_ids, now)
        if payload.team_id:
            TeamMembershipService(self.db).join_assisted_in_transaction(
                actor, payload.team_id, person.id, now
            )
        row.state, row.person_id, row.review_reason = "approved", person.id, None
        self._release(row)
        self._audit(actor, row, "direct_create" if key else "approve")
        return self._result(approval)

    def approve(
        self, actor: AuthContext, request_id: UUID, expected_version: int
    ) -> ApprovalResult:
        try:
            with atomic_write_transaction(self.db):
                # Serialize duplicate decisions before discovering their complete lock set.
                digest = hashlib.sha256(f"assisted-request:{request_id}".encode()).digest()
                self.db.execute(
                    select(
                        func.pg_advisory_xact_lock(int.from_bytes(digest[:8], "big", signed=True))
                    )
                )
                row = self._request(actor, request_id)
                payload = self._payload(row)
                before = row.version
                if isinstance(payload, PendingIntakePayloadView):
                    raise conflict()
                person = self._lock_context(
                    actor,
                    payload,
                    submitter_id=row.submitted_by_user_id,
                    row=row,
                    person_id=row.person_id,
                )
                self._require_admin(actor, row=row)
                if (
                    row.submitted_by_user_id == actor.account.id
                    or row.version != expected_version
                    or row.version != before
                ):
                    raise conflict()
                if row.state == "approved":
                    approval = self.db.scalar(
                        select(AssistedApproval).where(
                            AssistedApproval.request_id == row.id,
                            AssistedApproval.revision_version == expected_version,
                        )
                    )
                    if approval:
                        return self._result(approval)
                if row.state != "pending":
                    raise conflict()
                submitter = self._submitter(row.submitted_by_user_id)
                self.access.require_submit(submitter, payload)
                if person and not self.access.can_reuse(submitter, person.id, payload):
                    raise conflict()
                normalize_payload(IntakeInput.model_validate(payload.model_dump()))
                return self._materialize(actor, row, payload, person)
        except (IntegrityError, TeamServiceError) as exc:
            raise conflict() from exc

    def return_request(
        self, actor: AuthContext, request_id: UUID, expected_version: int, reason: str
    ) -> IntakeView:
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise self._error(status=422, detail="退回原因必填")
        with atomic_write_transaction(self.db):
            row = self._request(actor, request_id)
            payload = self._payload(row)
            self._lock_context(
                actor,
                payload,
                submitter_id=row.submitted_by_user_id,
                row=row,
                person_id=row.person_id,
            )
            self._require_admin(actor, row=row)
            if (
                row.submitted_by_user_id == actor.account.id
                or row.version != expected_version
                or row.state != "pending"
            ):
                raise conflict()
            row.state, row.review_reason = "returned", reason
            self._audit(actor, row, "return")
            return self._view(actor, row)

    def direct_create(
        self, actor: AuthContext, input: IntakeInput, idempotency_key: UUID
    ) -> ApprovalResult:
        payload = normalize_payload(input)
        digest = self._fingerprint("direct_create", payload)
        try:
            with atomic_write_transaction(self.db):
                self._require_admin(actor, payload=payload)
                self._operation(actor, idempotency_key)
                person = self._lock_context(actor, payload, submitter_id=actor.account.id)
                self._require_admin(actor, payload=payload, person_id=person.id if person else None)
                previous = self._replay(actor, idempotency_key, digest, "direct_create")
                if previous:
                    return previous
                if not self.access.covers(
                    actor,
                    kind=payload.kind,
                    coaches=payload.coach_person_ids,
                    team_id=payload.team_id,
                    target_person_id=person.id if person else None,
                    permission="personnel_intake.review",
                ):
                    raise conflict()
                row = PersonnelIntakeRequest(
                    submitted_by_user_id=actor.account.id,
                    version=1,
                    state="pending",
                    kind=payload.kind,
                    person_id=person.id if person else None,
                )
                self._check_credentials(payload, person, None)
                self.db.add(row)
                self.db.flush()
                self._save_revision(actor, row, payload)
                return self._materialize(
                    actor, row, payload, person, key=idempotency_key, digest=digest
                )
        except (IntegrityError, TeamServiceError) as exc:
            raise conflict() from exc

    def _pending_document_keys(self, payload: PendingIntakePayloadView) -> DocumentIdentityKeys:
        return document_identity_keys(
            normalize_document_identity(
                payload.document_type,
                payload.identity_number,
                issuing_country=payload.document_issuing_country,
                sex=payload.sex,
                date_of_birth=payload.date_of_birth,
                allow_pending=True,
            ),
            self.lookup,
        )

    def _require_pending_admin(self, actor: AuthContext) -> None:
        # No affiliation exists yet: a scoped review grant cannot authorize an
        # unowned record. Role labels alone never substitute for a global grant.
        self._require_admin(actor)

    def _validate_pending_affiliations(
        self, actor: AuthContext, payload: PendingIntakePayloadView
    ) -> None:
        if payload.coach_person_ids and (
            payload.kind not in {"student", "both"}
            or not set(payload.coach_person_ids).issubset(
                self.access.coach_ids(actor, permission="personnel_intake.review")
            )
        ):
            raise conflict()
        if payload.team_id and (
            payload.kind not in {"player", "both"}
            or payload.team_id
            not in self.access.team_ids(actor, permission="personnel_intake.review")
        ):
            raise conflict()
        if payload.team_allocation_status == "not_required" and payload.team_id:
            raise conflict()

    def _write_pending_facts(self, person: Person, payload: PendingIntakePayloadView) -> None:
        keys = self._pending_document_keys(payload)
        person.full_name, person.sex, person.date_of_birth = (
            payload.full_name,
            payload.sex,
            payload.date_of_birth,
        )
        person.identity_ciphertext = self.cipher.encrypt(
            payload.identity_number, field_name="identity_number"
        )
        person.identity_digest = keys.identity_digest
        person.document_type, person.document_issuing_country = (
            payload.document_type,
            payload.document_issuing_country,
        )
        person.passport_number_digest = keys.passport_number_digest
        person.phone_ciphertext = (
            self.cipher.encrypt(payload.phone, field_name="phone") if payload.phone else None
        )
        person.phone_digest = self.lookup.digest(payload.phone) if payload.phone else None
        person.email_ciphertext = (
            self.cipher.encrypt(payload.email, field_name="email") if payload.email else None
        )
        person.email_digest = (
            self.lookup.digest_domain("person-email", payload.email) if payload.email else None
        )

    def direct_create_pending(
        self, actor: AuthContext, input: PendingIntakeInput, idempotency_key: UUID
    ) -> ApprovalResult:
        payload = normalize_pending_payload(input)
        digest = self._fingerprint("direct_create", payload)
        try:
            with atomic_write_transaction(self.db):
                self._require_pending_admin(actor)
                self._operation(actor, idempotency_key)
                existing = self._lock_context(actor, payload, submitter_id=actor.account.id)
                self._require_pending_admin(actor)
                previous = self._replay(actor, idempotency_key, digest, "direct_create")
                if previous:
                    return previous
                # This dedicated route never changes/reclassifies an existing person.
                if existing is not None:
                    raise conflict()
                self._validate_pending_affiliations(actor, payload)
                self._check_credentials(payload, None, None)
                person = Person(
                    intake_mode="pending", team_allocation_status=input.team_allocation_status
                )
                self._write_pending_facts(person, payload)
                self.db.add(person)
                self.db.flush()
                row = PersonnelIntakeRequest(
                    submitted_by_user_id=actor.account.id,
                    version=1,
                    state="pending",
                    kind=payload.kind,
                    person_id=person.id,
                )
                self.db.add(row)
                self.db.flush()
                self._save_revision(actor, row, payload)
                now = datetime.now(UTC)
                if payload.kind in {"student", "both"}:
                    self.db.add(
                        StudentProfile(
                            person_id=person.id,
                            student_number=generate_student_number(self.db, now),
                            status="draft",
                        )
                    )
                elif payload.kind == "staff":
                    self.db.add(StaffProfile(person_id=person.id, staff_type="other"))
                self.db.flush()
                relationships = AssistedGuardianWriter(
                    self.db, cipher=self.cipher, lookup=self.lookup
                ).apply_in_transaction(person.id, payload.guardians, business_date_at(now))
                approval = self._new_approval(
                    actor,
                    row,
                    person,
                    relationships,
                    key=idempotency_key,
                    digest=digest,
                    kind="direct_create",
                )
                if payload.coach_person_ids:
                    from app.personnel_intake.responsibilities import replace_in_transaction

                    replace_in_transaction(self.db, actor, person.id, payload.coach_person_ids, now)
                if payload.team_id:
                    from app.team_management.membership_service import TeamMembershipService

                    TeamMembershipService(self.db).join_assisted_in_transaction(
                        actor, payload.team_id, person.id, now
                    )
                row.state = "approved"
                self._release(row)
                self._audit(actor, row, "direct_create")
                return self._result(approval)
        except (IntegrityError, TeamServiceError, DocumentIdentityConflict) as exc:
            raise conflict() from exc

    def correct_pending_profile(
        self, actor: AuthContext, person_id: UUID, input: PendingProfileCorrection
    ) -> ApprovalResult:
        from app.personnel_intake.activation_state import invalidate_in_transaction
        from app.personnel_intake.approved_facts import load_approved_facts
        from app.personnel_intake.claims.binding import require_binding_ownership_in_transaction
        from app.personnel_intake.claims.models import ProfileClaimBinding
        from app.personnel_intake.guardian_records import (
            authenticated_approval_facts,
            end_replaced_assisted_relationships,
        )

        reason = input.reason.strip()
        if not reason:
            raise self._error(status=422, detail="更正原因必填")
        try:
            with atomic_write_transaction(self.db):
                self._require_admin(actor, permission="people.update", person_id=person_id)
                person = self.db.get(Person, person_id)
                profile = self.db.get(AssistedPersonProfile, person_id)
                if person is None or person.intake_mode != "pending" or profile is None:
                    raise conflict()
                old = self.db.get(AssistedApproval, profile.current_approval_id)
                row = self.db.get(PersonnelIntakeRequest, old.request_id) if old else None
                if old is None or row is None or row.state != "approved":
                    raise conflict()
                old_payload = self._payload(row)
                if not isinstance(old_payload, PendingIntakePayloadView):
                    raise conflict()
                values = {
                    field: getattr(input, field)
                    for field in PendingProfileCorrection.model_fields
                    if field
                    not in {"expected_version", "reason", "idempotency_key", "identity_confirmed"}
                }
                preserve_email = "email" not in input.model_fields_set
                if preserve_email:
                    values["email"] = (
                        self.cipher.decrypt(person.email_ciphertext, field_name="email")
                        if person.email_ciphertext
                        else None
                    )
                payload = normalize_pending_payload(
                    PendingIntakeInput(
                        **values,
                        kind=old_payload.kind,
                        coach_person_ids=old_payload.coach_person_ids,
                        team_id=old_payload.team_id,
                        team_allocation_status="not_required"
                        if old_payload.team_allocation_status == "not_required"
                        else "pending",
                    )
                )
                digest = self._fingerprint(
                    "correction",
                    payload,
                    person_id=person_id,
                    expected_version=input.expected_version,
                    reason=reason,
                    preserve_email=preserve_email,
                    identity_confirmed=input.identity_confirmed,
                )
                self._operation(actor, input.idempotency_key)
                # Bare old/new passport keys form the first lock prefix, before
                # lifecycle discovers credentials and before _lock_context locks rows.
                lock_document_identity_keys(
                    self.db,
                    [
                        self._pending_document_keys(payload),
                        DocumentIdentityKeys(
                            person.identity_digest,
                            person.passport_number_digest,
                            person.document_issuing_country,
                        ),
                    ],
                )
                lifecycle = prepare_claim_lifecycle(
                    self.db,
                    person_id=person_id,
                    identity_digest=self._pending_document_keys(payload).identity_digest,
                    cipher=self.cipher,
                    lookup=self.lookup,
                    email_digest=self.lookup.digest_domain(
                        "registration-verification-email", payload.email
                    )
                    if payload.email
                    else None,
                )
                person = self._lock_context(
                    actor,
                    payload,
                    submitter_id=actor.account.id,
                    row=row,
                    person_id=person_id,
                    permission="people.update",
                    claim_lifecycle=lifecycle,
                )
                self._require_admin(actor, permission="people.update", person_id=person_id)
                previous = self._replay(actor, input.idempotency_key, digest, "correction")
                if previous:
                    return previous
                self.db.refresh(profile)
                if (
                    person is None
                    or person.deleted_at
                    or person.intake_mode != "pending"
                    or profile.version != input.expected_version
                    or profile.current_approval_id != old.id
                    or self.db.scalar(
                        select(UserAccount.id).where(UserAccount.person_id == person_id)
                    )
                    is not None
                ):
                    raise conflict()
                own, _ = authenticated_approval_facts(old, cipher=self.cipher, lookup=self.lookup)
                email_changed = own["email"] != payload.email
                binding_proof_changed = email_changed or (
                    person.identity_digest != self._pending_document_keys(payload).identity_digest
                    or person.full_name != payload.full_name
                )
                if email_changed and (preserve_email or not input.identity_confirmed):
                    raise conflict()
                claims = lifecycle.affected_rows(
                    lifecycle.lock_rows(self.db, cipher=self.cipher, lookup=self.lookup)
                )
                # Authenticate the current facts AND every original revision and
                # approval in the lineage before issuing any trusted successor.
                trusted = load_approved_facts(
                    self.db,
                    [person_id],
                    reference_date=business_date_at(datetime.now(UTC)),
                    cipher=self.cipher,
                    lookup=self.lookup,
                )[person_id]
                if not (
                    trusted.approval_valid and trusted.provenance_valid and trusted.profile_matches
                ):
                    raise conflict()
                binding = self.db.scalar(
                    select(ProfileClaimBinding).where(
                        ProfileClaimBinding.person_id == person_id,
                        ProfileClaimBinding.revoked_at.is_(None),
                    )
                )
                if binding is not None and binding_proof_changed:
                    require_binding_ownership_in_transaction(
                        self.db,
                        person_id=person_id,
                        email_digest=binding.email_digest,
                        cipher=self.cipher,
                        lookup=self.lookup,
                    )
                self._check_credentials(payload, person, row.id)
                if person.sex != payload.sex:
                    from app.housing.models import HousingStay

                    # Housing mutations share the locked Person boundary. Never
                    # silently turn an already allocated bed into mixed housing.
                    if (
                        self.db.scalar(
                            select(HousingStay.id)
                            .where(
                                HousingStay.person_id == person_id,
                                HousingStay.status.in_(
                                    ["planned", "checked_in", "checkout_pending"]
                                ),
                            )
                            .limit(1)
                        )
                        is not None
                    ):
                        raise self._error(
                            status=409,
                            code="pending_housing_sex_conflict",
                            detail="该档案已有待入住或在住房记录，请先处理住宿后再更正性别",
                        )
                self._write_pending_facts(person, payload)
                self.db.flush()
                end_replaced_assisted_relationships(
                    self.db,
                    person_id=person_id,
                    previous_ids=old.guardian_relationship_ids,
                    guardians=payload.guardians,
                    actor_id=actor.account.id,
                    now=datetime.now(UTC),
                    reason=reason,
                    lookup=self.lookup,
                )
                relations = AssistedGuardianWriter(
                    self.db, cipher=self.cipher, lookup=self.lookup
                ).apply_in_transaction(
                    person_id, payload.guardians, business_date_at(datetime.now(UTC))
                )
                row.version += 1
                row.review_reason = reason
                self._save_revision(actor, row, payload)
                approval = self._new_approval(
                    actor,
                    row,
                    person,
                    relations,
                    previous=old,
                    key=input.idempotency_key,
                    digest=digest,
                    kind="correction",
                )
                profile.activation_requirement = "reviewed_claim"
                self._release(row)
                if binding is not None and binding_proof_changed:
                    # Explicit, identity-checked correction is not normal claim
                    # revocation: it replaces a bad fact, never transfers OTP
                    # proof. Keep origin and old approval/proof bytes forever.
                    binding.revoked_at = datetime.now(UTC)
                    bound_claim = next((c for c in claims if c.id == binding.claim_id), None)
                    if bound_claim is None or bound_claim.status != "approved":
                        raise conflict()
                    bound_claim.status = "revoked"
                    before_claim_version = bound_claim.version
                    bound_claim.version += 1
                    AuditService(self.db).append(
                        actor_user_id=actor.account.id,
                        action="profile_claim.invalidated",
                        resource_type="profile_claim",
                        resource_id=bound_claim.id,
                        purpose=None,
                        outcome="allowed",
                        request_id=str(input.idempotency_key),
                        metadata={
                            "old_version": before_claim_version,
                            "new_version": bound_claim.version,
                        },
                    )
                invalidate_prelocked_pending_claims_in_transaction(
                    self.db,
                    claims=claims,
                    occurred_at=datetime.now(UTC),
                    actor_user_id=actor.account.id,
                )
                invalidate_in_transaction(
                    self.db, person_id=person_id, occurred_at=datetime.now(UTC)
                )
                self._audit(actor, row, "correct_profile")
                return self._result(approval)
        except (IntegrityError, DocumentIdentityConflict) as exc:
            raise conflict() from exc

    def correct_profile(
        self, actor: AuthContext, person_id: UUID, input: AssistedProfileCorrection
    ) -> ApprovalResult:
        from app.personnel_intake.activation_state import invalidate_in_transaction

        reason = input.reason.strip()
        if not reason:
            raise self._error(status=422, detail="更正原因必填")
        try:
            with atomic_write_transaction(self.db):
                self._require_admin(actor, permission="people.update", person_id=person_id)
                profile = self.db.get(AssistedPersonProfile, person_id)
                if profile is None:
                    raise conflict()
                old = self.db.get(AssistedApproval, profile.current_approval_id)
                if old is None:
                    raise conflict()
                row = self.db.get(PersonnelIntakeRequest, old.request_id)
                if row is None:
                    raise conflict()
                old_payload = self._payload(row)
                if isinstance(old_payload, PendingIntakePayloadView):
                    raise conflict()
                supplied_fields = {
                    key: getattr(input, key)
                    for key in IntakeInput.model_fields
                    if key in type(input).model_fields
                }
                preserve_email = "email" not in input.model_fields_set
                if preserve_email:
                    # Authorized discovery only; locks below recheck this value
                    # before any write. Omission is not a request to clear email.
                    snapshot_person = self.db.get(Person, person_id)
                    if snapshot_person is None:
                        raise conflict()
                    supplied_fields["email"] = (
                        self.cipher.decrypt(snapshot_person.email_ciphertext, field_name="email")
                        if snapshot_person.email_ciphertext
                        else snapshot_person.email
                    )
                payload = normalize_payload(
                    IntakeInput(
                        **supplied_fields,
                        kind=old_payload.kind,
                        coach_person_ids=old_payload.coach_person_ids,
                        team_id=old_payload.team_id,
                    )
                )
                digest = self._fingerprint(
                    "correction",
                    payload,
                    person_id=person_id,
                    expected_version=input.expected_version,
                    reason=reason,
                    preserve_email=preserve_email,
                )
                self._operation(actor, input.idempotency_key)
                lifecycle = prepare_claim_lifecycle(
                    self.db,
                    person_id=person_id,
                    identity_digest=self.lookup.digest(payload.identity_number),
                    cipher=self.cipher,
                    lookup=self.lookup,
                )
                locked_graph_person_ids: set[UUID] = set()
                person = self._lock_context(
                    actor,
                    payload,
                    submitter_id=actor.account.id,
                    row=row,
                    person_id=person_id,
                    permission="people.update",
                    locked_graph_person_ids=locked_graph_person_ids,
                    claim_lifecycle=lifecycle,
                )
                self._require_admin(actor, permission="people.update", person_id=person_id)
                previous = self._replay(actor, input.idempotency_key, digest, "correction")
                if previous:
                    return previous
                self.db.refresh(profile)
                if (
                    person is None
                    or person.deleted_at
                    or profile.version != input.expected_version
                    or profile.current_approval_id != old.id
                ):
                    raise conflict()
                lifecycle_claims = lifecycle.affected_rows(
                    lifecycle.lock_rows(self.db, cipher=self.cipher, lookup=self.lookup)
                )
                self._check_credentials(payload, person, row.id)
                account = self.db.scalar(
                    select(UserAccount).where(UserAccount.person_id == person_id)
                )
                from app.personnel_intake.guardian_records import person_facts

                facts = person_facts(person, self.cipher)
                if preserve_email and facts["email"] != payload.email:
                    raise conflict()
                if (
                    profile.activation_requirement == "reviewed_claim"
                    and facts["email"] != payload.email
                ):
                    # Only explicit claim review/revocation may change a reviewed email.
                    raise conflict()
                if account is not None:
                    # Account email changes belong to the verified-contact flow.
                    # Identity and unverified profile phone are mutable facts, not logins.
                    if facts["email"] != payload.email:
                        raise conflict()
                    old_phone = facts["phone"]
                    if isinstance(old_phone, str) and old_phone != payload.phone:
                        verified_phone = self.db.scalar(
                            select(AccountLoginIdentifier.id).where(
                                AccountLoginIdentifier.account_id == account.id,
                                AccountLoginIdentifier.kind == "phone",
                                AccountLoginIdentifier.identifier_digest
                                == login_identifier_digest(
                                    self.lookup, NormalizedLoginIdentifier("phone", old_phone)
                                ),
                                AccountLoginIdentifier.verified_at.is_not(None),
                            )
                        )
                        if verified_phone is not None:
                            raise conflict()
                person.full_name, person.sex, person.date_of_birth = (
                    payload.full_name,
                    payload.sex,
                    payload.date_of_birth,
                )
                person.identity_ciphertext = self.cipher.encrypt(
                    payload.identity_number, field_name="identity_number"
                )
                person.identity_digest = self.lookup.digest(payload.identity_number)
                person.phone_ciphertext, person.phone_digest = (
                    self.cipher.encrypt(payload.phone, field_name="phone"),
                    self.lookup.digest(payload.phone),
                )
                person.email_ciphertext, person.email_digest = (
                    self.cipher.encrypt(payload.email, field_name="email")
                    if payload.email
                    else None,
                    self.lookup.digest_domain("person-email", payload.email)
                    if payload.email
                    else None,
                )
                if payload.email is None and account is None:
                    profile.activation_requirement = "reviewed_claim"
                self.db.flush()
                from app.personnel_intake.guardian_records import (
                    end_replaced_assisted_relationships,
                )

                end_replaced_assisted_relationships(
                    self.db,
                    person_id=person_id,
                    previous_ids=old.guardian_relationship_ids,
                    guardians=payload.guardians,
                    actor_id=actor.account.id,
                    now=datetime.now(UTC),
                    reason=reason,
                    lookup=self.lookup,
                )
                relation_ids = AssistedGuardianWriter(
                    self.db, cipher=self.cipher, lookup=self.lookup
                ).apply_in_transaction(
                    person_id, payload.guardians, business_date_at(datetime.now(UTC))
                )
                row.version += 1
                row.review_reason = reason
                self._save_revision(actor, row, payload)
                approval = self._new_approval(
                    actor,
                    row,
                    person,
                    relation_ids,
                    previous=old,
                    key=input.idempotency_key,
                    digest=digest,
                    kind="correction",
                )
                from app.auth.account_readiness import AccountAccessReadinessPolicy

                # _lock_context already locked the full reachable graph and accounts.
                # Recheck after the successor pointer flush; original account provenance stays put.
                affected_accounts = list(
                    self.db.scalars(
                        select(UserAccount).where(
                            UserAccount.person_id.in_(locked_graph_person_ids)
                        )
                    )
                )
                checked_at = datetime.now(UTC)
                GuardianReadinessPolicy(self.db).disable_unready_locked_accounts(
                    affected_accounts,
                    reference_date=business_date_at(checked_at),
                    revoked_at=checked_at,
                    audit_actor_user_id=actor.account.id,
                    audit_request_id=str(approval.id),
                    account_readiness=AccountAccessReadinessPolicy(
                        self.db, cipher=self.cipher, lookup=self.lookup
                    ),
                )
                self._release(row)
                invalidate_prelocked_pending_claims_in_transaction(
                    self.db,
                    claims=lifecycle_claims,
                    occurred_at=datetime.now(UTC),
                    actor_user_id=actor.account.id,
                )
                invalidate_in_transaction(
                    self.db, person_id=person_id, occurred_at=datetime.now(UTC)
                )
                self._audit(actor, row, "correct_profile")
                return self._result(approval)
        except IntegrityError as exc:
            raise conflict() from exc
