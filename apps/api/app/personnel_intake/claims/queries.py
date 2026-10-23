"""Scope-filtered, non-writing claim queries and intentionally masked review DTOs."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, cast, overload
from uuid import UUID

from sqlalchemy import ColumnElement, Select, case, func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.time import business_date_at
from app.identity.document_identity import DocumentType, mask_document_identity
from app.identity.models import Person
from app.personnel_intake.claims.access import ClaimReviewAccess
from app.personnel_intake.claims.completion import (
    approved_completion_view,
    completion_is_complete,
    completion_view,
    merge_completion_payload,
)
from app.personnel_intake.claims.errors import ProfileClaimError
from app.personnel_intake.claims.models import ProfileClaim
from app.personnel_intake.claims.proof import ClaimSubmission, decode_claim_submission
from app.personnel_intake.claims.schemas import (
    ClaimAdminContext,
    ClaimCompletionFields,
    ClaimDetail,
    ClaimListItem,
    ClaimPage,
    ClaimStatus,
)
from app.personnel_intake.eligibility import AssistedEligibilityPolicy
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.personnel_intake.models import (
    AssistedApproval,
    AssistedPersonProfile,
    PersonnelIntakeRequest,
)
from app.personnel_intake.readiness import AssistedReadinessPolicy
from app.personnel_intake.schemas import PendingIntakePayloadView
from app.personnel_intake.service import PersonnelIntakeService
from app.registration.verification_service import VerificationInvalidOrExpired
from app.security.encryption import EncryptedFieldError, EnvelopeCipher, LookupDigest


def mask_name(value: str) -> str:
    return value[:1] + "*" * max(1, len(value) - 1)


def mask_email(value: str) -> str:
    name, _, domain = value.partition("@")
    return name[:1] + "***@" + domain


def mask_identity(value: str, *, document_type: DocumentType = "cn_id") -> str:
    if document_type == "passport":
        return mask_document_identity("passport", value)
    return value[:3] + "*" * max(1, len(value) - 7) + value[-4:]


class ClaimQueries:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db, self.cipher, self.lookup = db, cipher, lookup
        self.clock = clock or (lambda: datetime.now(UTC))
        self.access = ClaimReviewAccess(db)

    def context(self, actor: AuthContext) -> ClaimAdminContext:
        return ClaimAdminContext(can_enter=self.access.can_enter(actor))

    def _actor_person(self, actor: AuthContext) -> Person:
        # Existing test/portal adapters may supply only a PersonSummary; never
        # derive security facts from that presentation object.
        person = self.db.get(Person, actor.person.id, populate_existing=True)
        if person is None:
            raise ProfileClaimError(
                status=403, code="profile_claim_forbidden", detail="无权审核认领申请"
            )
        return person

    def _is_self(self, actor: AuthContext, row: ProfileClaim) -> bool:
        person = self._actor_person(actor)
        return row.target_person_id == person.id or row.identity_digest == person.identity_digest

    def _submission(self, row: ProfileClaim) -> ClaimSubmission:
        return decode_claim_submission(
            ciphertext=row.payload_ciphertext,
            payload_digest=row.payload_digest,
            identity_digest=row.identity_digest,
            email_digest=row.email_digest,
            submission_version=row.submission_version,
            cipher=self.cipher,
            lookup=self.lookup,
        )

    def _effective_status(self) -> ColumnElement[str]:
        return case(
            (
                (ProfileClaim.status == "pending") & (ProfileClaim.expires_at <= self.clock()),
                "expired",
            ),
            else_=ProfileClaim.status,
        )

    def _scoped(self, actor: AuthContext) -> Select[tuple[ProfileClaim]]:
        state = self.access.snapshot(actor)
        if not self.access.capable(state) or not state.can_enter_review_queue():
            raise ProfileClaimError(
                status=403, code="profile_claim_forbidden", detail="无权审核认领申请"
            )
        return select(ProfileClaim).where(self.access.scope_predicate(state))

    def _row(self, actor: AuthContext, claim_id: UUID) -> ProfileClaim:
        row = self.db.scalar(
            self._scoped(actor)
            .where(ProfileClaim.id == claim_id)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise ProfileClaimError(
                status=404, code="profile_claim_not_found", detail="未找到认领申请"
            )
        return row

    def _approval_reason(self, row: ProfileClaim, value: ClaimSubmission) -> str | None:
        if row.private_match_reason:
            return row.private_match_reason
        person = (
            self.db.get(Person, row.target_person_id, populate_existing=True)
            if row.target_person_id
            else None
        )
        profile = (
            self.db.get(AssistedPersonProfile, row.target_person_id, populate_existing=True)
            if row.target_person_id
            else None
        )
        if (
            not person
            or person.deleted_at
            or person.full_name != value.full_name
            or person.identity_digest != row.identity_digest
        ):
            return "profile_changed"
        if (
            not profile
            or profile.current_approval_id != row.target_approval_id
            or profile.activation_requirement != "reviewed_claim"
        ):
            return "approval_changed"
        try:
            if (
                person.email is not None
                or person.email_ciphertext is not None
                or person.email_digest is not None
            ) and not (
                person.intake_mode == "pending"
                and person.email_ciphertext
                and self.cipher.decrypt(person.email_ciphertext, field_name="email") == value.email
                and person.email in (None, value.email)
            ):
                return "email_already_reserved"
        except EncryptedFieldError:
            return "approval_evidence_invalid"
        if self.db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id)):
            return "account_already_exists"
        try:
            if person.intake_mode == "pending":
                readiness = AssistedReadinessPolicy(
                    self.db, cipher=self.cipher, lookup=self.lookup
                ).evaluate_many([person.id], reference_date=business_date_at(self.clock()))[
                    person.id
                ]
                if not readiness.completion_allowed:
                    return "profile_not_eligible"
                payload = self._completion_payload(row, value)
                if not completion_is_complete(payload, today=business_date_at(self.clock())):
                    return "completion_missing"
                return None
            result = AssistedEligibilityPolicy(
                self.db, cipher=self.cipher, lookup=self.lookup
            ).evaluate_many([person.id], reference_date=business_date_at(self.clock()))[person.id]
            if not result.eligible or result.kind not in {"student", "player", "both"}:
                return "profile_not_eligible"
        except (ApprovalEvidenceError, EncryptedFieldError, VerificationInvalidOrExpired):
            return "approval_evidence_invalid"
        except ProfileClaimError as error:
            return error.code
        return None

    def _pending_payload(self, row: ProfileClaim) -> PendingIntakePayloadView:
        approval = (
            self.db.get(AssistedApproval, row.target_approval_id)
            if row.target_approval_id
            else None
        )
        request = self.db.get(PersonnelIntakeRequest, approval.request_id) if approval else None
        if (
            request is None
            or request.state != "approved"
            or request.person_id != row.target_person_id
        ):
            raise ProfileClaimError(code="approval_changed")
        payload = PersonnelIntakeService(self.db, cipher=self.cipher, lookup=self.lookup)._payload(
            request
        )
        if not isinstance(payload, PendingIntakePayloadView):
            raise ProfileClaimError(code="approval_changed")
        return payload

    def _completion_payload(
        self, row: ProfileClaim, value: ClaimSubmission
    ) -> PendingIntakePayloadView:
        return merge_completion_payload(
            self._pending_payload(row),
            value.completion or ClaimCompletionFields(),
            verified_email=value.email,
            today=business_date_at(self.clock()),
        )

    def _can_revoke(self, row: ProfileClaim) -> bool:
        if row.status != "approved" or row.target_person_id is None:
            return False
        from app.personnel_intake.claims.binding import require_binding_ownership_in_transaction

        try:
            binding = require_binding_ownership_in_transaction(
                self.db,
                person_id=row.target_person_id,
                email_digest=row.email_digest,
                cipher=self.cipher,
                lookup=self.lookup,
            )
            return (
                binding.claim_id == row.id
                and self.db.scalar(
                    select(UserAccount.id).where(UserAccount.person_id == row.target_person_id)
                )
                is None
            )
        except (
            ProfileClaimError,
            ApprovalEvidenceError,
            EncryptedFieldError,
            VerificationInvalidOrExpired,
        ):
            return False

    @overload
    def _item(
        self, actor: AuthContext, row: ProfileClaim, *, detail: Literal[False] = False
    ) -> ClaimListItem: ...

    @overload
    def _item(
        self, actor: AuthContext, row: ProfileClaim, *, detail: Literal[True]
    ) -> ClaimDetail: ...

    def _item(
        self, actor: AuthContext, row: ProfileClaim, *, detail: bool = False
    ) -> ClaimListItem | ClaimDetail:
        value = self._submission(row)
        status = (
            "expired" if row.status == "pending" and row.expires_at <= self.clock() else row.status
        )
        reason = (
            self._approval_reason(row, value) if status == "pending" else row.private_match_reason
        )
        own = self._is_self(actor, row)
        # Mirror the mutation service's all-decision self-review veto.
        actions = (
            []
            if own
            else (
                (["approve"] if not reason else []) + ["reject"]
                if status == "pending"
                else (["revoke"] if self._can_revoke(row) else [])
            )
        )
        item = ClaimListItem(
            id=row.id,
            version=row.version,
            status=cast(ClaimStatus, status),
            masked_name=mask_name(value.full_name),
            masked_email=mask_email(value.email),
            verified_at=row.verified_at,
            expires_at=row.expires_at,
            allowed_actions=actions,
        )
        if not detail:
            return item
        person = self.db.get(Person, row.target_person_id) if row.target_person_id else None
        identity = email = None
        if person:
            try:
                identity = self.cipher.decrypt(
                    person.identity_ciphertext, field_name="identity_number"
                )
                email = (
                    self.cipher.decrypt(person.email_ciphertext, field_name="email")
                    if person.email_ciphertext
                    else None
                )
            except EncryptedFieldError:
                pass
        count = (
            self.db.scalar(
                select(func.count())
                .select_from(ProfileClaim)
                .where(
                    ProfileClaim.target_person_id == row.target_person_id,
                    ProfileClaim.target_person_id.is_not(None),
                    self._effective_status() == "pending",
                    self.access.scope_predicate(self.access.snapshot(actor)),
                )
            )
            or 0
        )
        old_completion = None
        if person and person.intake_mode == "pending":
            profile = self.db.get(AssistedPersonProfile, person.id)
            approval = (
                self.db.get(AssistedApproval, profile.current_approval_id) if profile else None
            )
            request = self.db.get(PersonnelIntakeRequest, approval.request_id) if approval else None
            if request and approval and self.access.intake.can_read(actor, request):
                # The existing approved-intake detail boundary, not just queue visibility.
                from app.personnel_intake.guardian_records import authenticated_approval_facts

                authenticated_approval_facts(approval, cipher=self.cipher, lookup=self.lookup)
                payload = PersonnelIntakeService(
                    self.db, cipher=self.cipher, lookup=self.lookup
                )._payload(request)
                if isinstance(payload, PendingIntakePayloadView):
                    old_completion = approved_completion_view(payload)
        return ClaimDetail(
            **item.model_dump(),
            person_id=row.target_person_id,
            applicant_masked_name=mask_name(value.full_name),
            applicant_masked_identity=mask_identity(
                value.identity_number, document_type=value.document_type
            ),
            applicant_email=value.email,
            profile_masked_name=mask_name(person.full_name) if person else None,
            profile_masked_identity=mask_identity(
                identity, document_type=cast(DocumentType, person.document_type)
            )
            if identity and person
            else None,
            profile_masked_email=mask_email(email) if email else None,
            private_match_reason=reason,
            same_target_pending_count=count,
            can_approve="approve" in actions,
            applicant_document_type=value.document_type,
            applicant_issuing_country=value.issuing_country,
            applicant_completion=completion_view(value.completion) if value.completion else None,
            profile_completion=old_completion,
        )

    def list_claims(
        self,
        actor: AuthContext,
        *,
        status: ClaimStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> ClaimPage:
        if page < 1 or not 1 <= page_size <= 100:
            raise ProfileClaimError(status=422, detail="分页参数无效")
        query = self._scoped(actor)
        pending = (
            self.db.scalar(
                select(func.count()).select_from(
                    query.where(self._effective_status() == "pending").subquery()
                )
            )
            or 0
        )
        if status:
            query = query.where(self._effective_status() == status)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.scalars(
            query.order_by(ProfileClaim.created_at.desc(), ProfileClaim.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return ClaimPage(
            items=[self._item(actor, row) for row in rows],
            total=total,
            pending_count=pending,
            page=page,
            page_size=page_size,
        )

    def detail(self, actor: AuthContext, claim_id: UUID) -> ClaimDetail:
        return self._item(actor, self._row(actor, claim_id), detail=True)
