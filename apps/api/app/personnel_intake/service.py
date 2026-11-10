from datetime import UTC, datetime
from hmac import compare_digest
from typing import cast, get_args
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.identity.schemas import Page
from app.personnel_intake.access import IntakeAccessSnapshot, PersonnelIntakeAccess
from app.personnel_intake.codec import decode_payload, normalize_payload
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import payload_identity_keys
from app.personnel_intake.locks import CredentialReservationPolicy, lock_personnel_credentials
from app.personnel_intake.models import (
    AssistedPersonProfile,
    PersonnelCredentialReservation,
    PersonnelIntakeRequest,
    PersonnelIntakeRevision,
)
from app.personnel_intake.schemas import (
    IntakeInput,
    IntakeListItem,
    IntakePayloadView,
    IntakeQueueView,
    IntakeStatus,
    IntakeSubmitter,
    IntakeView,
    PendingIntakePayloadView,
    PersonKind,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.security.encryption import EnvelopeCipher, LookupDigest


class PersonnelIntakeService:
    def __init__(self, db: Session, *, cipher: EnvelopeCipher, lookup: LookupDigest) -> None:
        self.db, self.cipher, self.lookup = db, cipher, lookup
        self.access = PersonnelIntakeAccess(db)
        self.policy = CredentialReservationPolicy(db, lookup=lookup)

    def _error(
        self,
        *,
        status: int = 409,
        code: str = "personnel_intake_conflict",
        detail: str = "资料存在冲突，请联系管理员核实",
    ) -> PersonnelIntakeError:
        return PersonnelIntakeError(code, status=status, detail=detail)

    def _payload(self, row: PersonnelIntakeRequest) -> IntakePayloadView | PendingIntakePayloadView:
        revision = self.db.scalar(
            select(PersonnelIntakeRevision).where(
                PersonnelIntakeRevision.request_id == row.id,
                PersonnelIntakeRevision.version == row.version,
            )
        )
        if revision is None:
            raise self._error()
        raw = self.cipher.decrypt(
            revision.payload_ciphertext, field_name="personnel_intake_payload"
        )
        if not compare_digest(
            self.lookup.digest_domain("personnel-intake-payload", raw), revision.payload_digest
        ):
            from app.personnel_intake.guardian_records import ApprovalEvidenceError

            raise ApprovalEvidenceError()
        payload = decode_payload(raw)
        if (
            payload.kind != row.kind
            or set(payload.coach_person_ids) != set(row.target_coach_person_ids)
            or payload.team_id != row.target_team_id
        ):
            raise self._error()
        return payload

    def _actions(
        self,
        actor: AuthContext,
        row: PersonnelIntakeRequest,
        state: IntakeAccessSnapshot | None = None,
    ) -> list[str]:
        state = state or self.access.snapshot(actor)
        if row.submitted_by_user_id != actor.account.id:
            if (
                row.state == "pending"
                and state.roles & {"admin", "super_admin"}
                and state.allowed(
                    "personnel_intake.review",
                    target_person_id=row.person_id,
                    coaches=row.target_coach_person_ids,
                    team_id=row.target_team_id,
                )
            ):
                return ["approve", "return"]
            return []
        if not self.access.covers(
            actor,
            kind=cast(PersonKind, row.kind),
            coaches=row.target_coach_person_ids,
            team_id=row.target_team_id,
            target_person_id=row.person_id,
            require_eligible=False,
            snapshot=state,
        ):
            return []
        if row.state == "pending":
            return ["withdraw"]
        return ["resubmit", "withdraw"] if row.state == "returned" else []

    def _item(
        self,
        actor: AuthContext,
        row: PersonnelIntakeRequest,
        state: IntakeAccessSnapshot | None = None,
        *,
        submitter_label: str | None = None,
    ) -> IntakeListItem:
        if submitter_label is None:
            submitter_label = self.db.scalar(
                select(Person.full_name)
                .join(UserAccount, UserAccount.person_id == Person.id)
                .where(UserAccount.id == row.submitted_by_user_id)
            )
        if submitter_label is None:
            raise self._error()
        return IntakeListItem(
            id=row.id,
            version=row.version,
            status=row.state,
            kind=row.kind,
            approved_person_id=row.person_id if row.state == "approved" else None,
            allowed_actions=self._actions(actor, row, state),
            submitted_by=IntakeSubmitter(id=row.submitted_by_user_id, label=submitter_label),
        )

    def _view(self, actor: AuthContext, row: PersonnelIntakeRequest) -> IntakeView:
        profile = (
            self.db.get(AssistedPersonProfile, row.person_id)
            if row.state == "approved" and row.person_id
            else None
        )
        return IntakeView(
            **self._item(actor, row).model_dump(),
            scoped_payload=self._payload(row),
            returned_reason=row.review_reason if row.state == "returned" else None,
            profile_version=profile.version if profile else None,
        )

    def _require_read(
        self, actor: AuthContext, request_id: UUID, *, locked: bool = False
    ) -> PersonnelIntakeRequest:
        query = (
            select(PersonnelIntakeRequest)
            .where(PersonnelIntakeRequest.id == request_id)
            .execution_options(populate_existing=True)
        )
        if locked:
            query = query.with_for_update()
        row = self.db.scalar(query)
        if row is None or not self.access.can_read(actor, row):
            raise self._error(
                status=404, code="personnel_intake_not_found", detail="未找到建档申请"
            )
        return row

    def detail(self, actor: AuthContext, request_id: UUID) -> IntakeView:
        return self._view(actor, self._require_read(actor, request_id))

    def list_requests(
        self,
        actor: AuthContext,
        *,
        page: int = 1,
        page_size: int = 20,
        view: IntakeQueueView | None = None,
        status: IntakeStatus | None = None,
        kind: PersonKind | None = None,
        submitter_query: str | None = None,
    ) -> Page[IntakeListItem]:
        submitter_query = submitter_query.strip() if submitter_query else None
        if (
            page < 1
            or not 1 <= page_size <= 100
            or view is not None
            and view not in get_args(IntakeQueueView)
            or status is not None
            and status not in get_args(IntakeStatus)
            or kind is not None
            and kind not in get_args(PersonKind)
            or submitter_query
            and (len(submitter_query) > 80 or view != "review")
        ):
            raise self._error(
                status=422,
                code="personnel_intake_validation",
                detail="建档请求参数无效，请检查后重试",
            )
        state = self.access.snapshot(actor)
        if view == "review":
            if not state.can_enter_review_queue():
                raise self._error(
                    status=403, code="personnel_intake_forbidden", detail="无权办理此人员资料"
                )
            predicate = state.permission_predicate("personnel_intake.review")
        else:
            predicate = state.readable_predicate()
        query = (
            select(PersonnelIntakeRequest, Person.full_name)
            .join(UserAccount, UserAccount.id == PersonnelIntakeRequest.submitted_by_user_id)
            .join(Person, Person.id == UserAccount.person_id)
            .where(predicate)
        )
        if view == "mine":
            query = query.where(PersonnelIntakeRequest.submitted_by_user_id == actor.account.id)
        if status is not None:
            query = query.where(PersonnelIntakeRequest.state == status)
        if kind is not None:
            query = query.where(PersonnelIntakeRequest.kind == kind)
        if submitter_query:
            query = query.where(Person.full_name.icontains(submitter_query, autoescape=True))
        total = (
            self.db.scalar(query.with_only_columns(func.count(), maintain_column_froms=True)) or 0
        )
        visible = (
            self.db.execute(
                query.order_by(PersonnelIntakeRequest.created_at.desc(), PersonnelIntakeRequest.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            if total
            else []
        )
        return Page[IntakeListItem](
            items=[self._item(actor, row, state, submitter_label=label) for row, label in visible],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _digests(
        self, payload: IntakePayloadView | PendingIntakePayloadView
    ) -> tuple[str, str | None]:
        identity_digest = payload_identity_keys(payload, self.lookup).identity_digest
        return identity_digest, (
            registration_identifier_digest(
                self.lookup, normalize_registration_identifier("email", payload.email)
            )
            if payload.email is not None
            else None
        )

    def _lock_credentials(self, *payloads: IntakePayloadView) -> None:
        lock_personnel_credentials(
            self.db,
            emails={p.email for p in payloads if p.email is not None},
            identity_digests={
                payload_identity_keys(p, self.lookup).identity_digest for p in payloads
            },
            lookup=self.lookup,
        )

    def _lock_people(self, actor: AuthContext, payload: IntakePayloadView) -> UUID | None:
        digest = payload_identity_keys(payload, self.lookup).identity_digest
        candidate_id = self.db.scalar(select(Person.id).where(Person.identity_digest == digest))
        if candidate_id is not None and not self.access.allowed(
            actor,
            "personnel_intake.submit",
            coaches=payload.coach_person_ids,
            team_id=payload.team_id,
            target_person_id=candidate_id,
        ):
            raise self._error()
        guardian_digests = {
            payload_identity_keys(g, self.lookup).identity_digest for g in payload.guardians
        }
        ids = set(
            self.db.scalars(select(Person.id).where(Person.identity_digest.in_(guardian_digests)))
        ) | {actor.account.person_id}
        if candidate_id:
            ids.add(candidate_id)
        people = list(
            self.db.scalars(
                select(Person)
                .where(Person.id.in_(ids))
                .order_by(Person.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        person = next((p for p in people if p.id == candidate_id), None)
        if person and (
            person.deleted_at
            or not self.access.can_reuse(actor, person.id, payload)
            or person.full_name != payload.full_name
            or person.sex != payload.sex
            or person.date_of_birth != payload.date_of_birth
            or (
                person.email_digest
                and (
                    payload.email is None
                    or person.email_digest
                    != self.lookup.digest_domain("person-email", payload.email)
                )
            )
            or (
                person.phone_ciphertext
                and self.cipher.decrypt(person.phone_ciphertext, field_name="phone")
                != payload.phone
            )
        ):
            raise self._error()
        return person.id if person else None

    def _release(self, row: PersonnelIntakeRequest) -> None:
        for reservation in self.db.scalars(
            select(PersonnelCredentialReservation).where(
                PersonnelCredentialReservation.request_id == row.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        ):
            reservation.ended_at = datetime.now(UTC)
        self.db.flush()

    def _save_revision(
        self,
        actor: AuthContext,
        row: PersonnelIntakeRequest,
        payload: IntakePayloadView | PendingIntakePayloadView,
    ) -> None:
        if isinstance(payload, PendingIntakePayloadView):
            # v2 has no draft/submit route: create the permanent Person first in
            # the same atomic transaction. Failure cannot strand opaque v2 drafts.
            person = self.db.get(Person, row.person_id) if row.person_id else None
            if person is None or person.intake_mode != "pending":
                raise self._error()
        identity_digest, email_digest = self._digests(payload)
        serialized = payload.model_dump_json()
        row.kind, row.target_coach_person_ids, row.target_team_id = (
            payload.kind,
            payload.coach_person_ids,
            payload.team_id,
        )
        self.db.flush()
        self.db.add(
            PersonnelIntakeRevision(
                request_id=row.id,
                version=row.version,
                payload_ciphertext=self.cipher.encrypt(
                    serialized, field_name="personnel_intake_payload"
                ),
                payload_digest=self.lookup.digest_domain("personnel-intake-payload", serialized),
                identity_digest=identity_digest,
                email_digest=email_digest,
                created_by_user_id=actor.account.id,
            )
        )
        for kind, digest in (("identity", identity_digest), ("email", email_digest)):
            if digest is None:
                continue
            self.db.add(
                PersonnelCredentialReservation(
                    kind=kind, digest=digest, request_id=row.id, person_id=row.person_id
                )
            )
        self.db.flush()

    def _audit(self, actor: AuthContext, row: PersonnelIntakeRequest, action: str) -> None:
        AuditService(self.db).append(
            actor_user_id=actor.account.id,
            action=f"personnel_intake.{action}",
            resource_type="personnel_intake_request",
            resource_id=row.id,
            purpose=None,
            outcome="allowed",
            request_id=str(uuid4()),
            metadata={"new_version": row.version},
        )

    def submit(self, actor: AuthContext, input: IntakeInput) -> IntakeView:
        payload = normalize_payload(input)
        try:
            with atomic_write_transaction(self.db):
                self._lock_credentials(payload)
                self.access.require_submit(actor, payload)
                person_id = self._lock_people(actor, payload)
                self.access.require_submit(actor, payload)
                self.policy.require_available(
                    identity=payload.identity_number,
                    identity_keys=payload_identity_keys(payload, self.lookup),
                    email=payload.email,
                    person_id=person_id,
                    request_id=None,
                )
                row = PersonnelIntakeRequest(
                    submitted_by_user_id=actor.account.id,
                    version=1,
                    state="pending",
                    kind=payload.kind,
                    person_id=person_id,
                )
                self.db.add(row)
                self.db.flush()
                self._save_revision(actor, row, payload)
                self._audit(actor, row, "submit")
                return self._view(actor, row)
        except IntegrityError as exc:
            raise self._error() from exc

    def resubmit(
        self, actor: AuthContext, request_id: UUID, expected_version: int, input: IntakeInput
    ) -> IntakeView:
        payload = normalize_payload(input)
        try:
            with atomic_write_transaction(self.db):
                self.access.require_submit(actor, payload)
                snapshot = self._require_read(actor, request_id)
                old_version = snapshot.version
                previous = self._payload(snapshot)
                if isinstance(previous, PendingIntakePayloadView):
                    raise self._error()
                self._lock_credentials(previous, payload)
                row = self._require_read(actor, request_id, locked=True)
                if (
                    row.submitted_by_user_id != actor.account.id
                    or row.state != "returned"
                    or row.version != expected_version
                    or row.version != old_version
                ):
                    raise self._error()
                self.access.require_submit(actor, payload)
                person_id = self._lock_people(actor, payload)
                self.access.require_submit(actor, payload)
                self.policy.require_available(
                    identity=payload.identity_number,
                    identity_keys=payload_identity_keys(payload, self.lookup),
                    email=payload.email,
                    person_id=person_id,
                    request_id=row.id,
                )
                self._release(row)
                row.version += 1
                row.state, row.review_reason, row.person_id = "pending", None, person_id
                self._save_revision(actor, row, payload)
                self._audit(actor, row, "resubmit")
                return self._view(actor, row)
        except IntegrityError as exc:
            raise self._error() from exc

    def withdraw(self, actor: AuthContext, request_id: UUID, expected_version: int) -> IntakeView:
        with atomic_write_transaction(self.db):
            snapshot = self._require_read(actor, request_id)
            version = snapshot.version
            payload = self._payload(snapshot)
            if isinstance(payload, PendingIntakePayloadView):
                raise self._error()
            self._lock_credentials(payload)
            row = self._require_read(actor, request_id, locked=True)
            if (
                row.submitted_by_user_id != actor.account.id
                or row.state not in {"pending", "returned"}
                or row.version != expected_version
                or row.version != version
            ):
                raise self._error()
            if not self.access.covers(
                actor,
                kind=cast(PersonKind, row.kind),
                coaches=row.target_coach_person_ids,
                team_id=row.target_team_id,
                target_person_id=row.person_id,
                require_eligible=False,
            ):
                raise self._error(
                    status=403, code="personnel_intake_forbidden", detail="无权撤回此申请"
                )
            row.state = "withdrawn"
            self._release(row)
            self._audit(actor, row, "withdraw")
            return self._view(actor, row)
