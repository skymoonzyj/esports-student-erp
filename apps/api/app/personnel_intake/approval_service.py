"""Atomic assisted approval. Public methods own the transaction; helpers never commit."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.core.transactions import atomic_write_transaction
from app.identity.membership_locks import lock_person_memberships, person_membership_lock_keys
from app.identity.models import Person, StudentProfile
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.student_numbers import generate_student_number
from app.personnel_intake.codec import normalize_payload
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
from app.personnel_intake.locks import lock_personnel_credentials, person_credential_fingerprint
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
)
from app.personnel_intake.service import PersonnelIntakeService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.team_management.errors import TeamServiceError
from app.team_management.write_support import lock_directory, lock_participants


class PersonnelApprovalService(PersonnelIntakeService):
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest):
        super().__init__(db, cipher=cipher, lookup=lookup)

    def _require_admin(
        self,
        actor: AuthContext,
        *,
        permission: str = "personnel_intake.review",
        person_id: UUID | None = None,
        payload: IntakePayloadView | None = None,
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
        payload: IntakePayloadView,
        *,
        person_id: UUID | None = None,
        expected_version: int | None = None,
        reason: str | None = None,
    ) -> str:
        return self.lookup.digest_domain(
            "assisted-operation",
            canonical_json(
                {
                    "action": kind,
                    "person_id": str(person_id) if person_id else None,
                    "expected_version": expected_version,
                    "payload": payload.model_dump(mode="json"),
                    "reason": reason,
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
        payload: IntakePayloadView,
        *,
        submitter_id: UUID,
        row: PersonnelIntakeRequest | None = None,
        person_id: UUID | None = None,
        permission: str = "personnel_intake.review",
        locked_graph_person_ids: set[UUID] | None = None,
    ) -> Person | None:
        values: list[IntakePayloadView | AssistedGuardianView] = [payload, *payload.guardians]
        digests = {self.lookup.digest(value.identity_number) for value in values}
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
            (
                person
                for person in people
                if person.identity_digest == self.lookup.digest(payload.identity_number)
            ),
            None,
        )
        target_id = person_id or (own.id if own else None)
        self._require_admin(actor, permission=permission, person_id=target_id, payload=payload)
        seeds = {person.id for person in people}
        guardian_ids, edges = guardian_graph_component(self.db, seeds)
        ids = guardian_ids | set(payload.coach_person_ids) | {actor.person.id}
        ids.update(
            self.db.scalars(select(UserAccount.person_id).where(UserAccount.id == submitter_id))
        )
        account_ids = set(
            self.db.scalars(select(UserAccount.id).where(UserAccount.person_id.in_(ids)))
        ) | {actor.account.id, submitter_id}
        emails = {value.email for value in values if value.email}
        phones = {self.lookup.digest(value.phone) for value in values}
        email_digests = {self.lookup.digest_domain("person-email", value) for value in emails}
        for person in people:
            digests.add(person.identity_digest)
            if person.phone_digest:
                phones.add(person.phone_digest)
            if person.email_digest:
                email_digests.add(person.email_digest)
            if person.email_ciphertext:
                emails.add(self.cipher.decrypt(person.email_ciphertext, field_name="email"))
        lock_personnel_credentials(
            self.db, emails=emails, identity_digests=digests, lookup=self.lookup
        )
        lock_person_memberships(
            self.db,
            person_membership_lock_keys(
                identity_digests=digests, phone_digests=phones, email_digests=email_digests
            ),
        )
        if row:
            self.db.scalar(
                select(PersonnelIntakeRequest)
                .where(PersonnelIntakeRequest.id == row.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        guardians = GuardianReadinessPolicy(self.db)
        for identifier in sorted(guardian_ids):
            guardians.lock_guardian_relationships(identifier)
        lock_participants(self.db, sorted(ids), sorted(account_ids))
        current_guardians, current_edges = guardian_graph_component(self.db, seeds)
        if current_guardians != guardian_ids or current_edges != edges:
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
        if payload.team_id:
            lock_directory(self.db, [payload.team_id])
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
        self, payload: IntakePayloadView, person: Person | None, request_id: UUID | None
    ) -> None:
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
            self.db.add(AssistedPersonProfile(person_id=person.id, current_approval_id=approval.id))
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
                payload = normalize_payload(
                    IntakeInput(
                        **{
                            key: getattr(input, key)
                            for key in IntakeInput.model_fields
                            if key in type(input).model_fields
                        },
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
                )
                self._operation(actor, input.idempotency_key)
                locked_graph_person_ids: set[UUID] = set()
                person = self._lock_context(
                    actor,
                    payload,
                    submitter_id=actor.account.id,
                    row=row,
                    person_id=person_id,
                    permission="people.update",
                    locked_graph_person_ids=locked_graph_person_ids,
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
                self._check_credentials(payload, person, row.id)
                account = self.db.scalar(
                    select(UserAccount).where(UserAccount.person_id == person_id)
                )
                if account is not None:
                    # Account email changes belong to the verified-contact flow.
                    # Identity and unverified profile phone are mutable facts, not logins.
                    from app.personnel_intake.guardian_records import person_facts

                    facts = person_facts(person, self.cipher)
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
                    self.cipher.encrypt(payload.email, field_name="email"),
                    self.lookup.digest_domain("person-email", payload.email),
                )
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
                invalidate_in_transaction(
                    self.db, person_id=person_id, occurred_at=datetime.now(UTC)
                )
                self._audit(actor, row, "correct_profile")
                return self._result(approval)
        except IntegrityError as exc:
            raise conflict() from exc
