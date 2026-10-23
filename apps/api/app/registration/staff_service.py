from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.models import UserAccount
from app.core.privacy import (
    contains_email,
    contains_labeled_sensitive_value,
    contains_phone_or_identity,
    looks_like_raw_secret,
)
from app.core.transactions import write_transaction
from app.identity.models import Person, StaffProfile
from app.identity.normalization import IdentityValidationError, normalize_cn_id
from app.identity.schemas import StaffType
from app.identity.service import IdentityService, IdentityServiceError
from app.rbac.models import Role, UserRole
from app.registration.models import StaffIdentityApplication
from app.registration.schemas import (
    StaffApplicationAdminDetail,
    StaffApplicationAdminListItem,
    StaffApplicationSelfResponse,
    StaffApplicationStatus,
)
from app.security.encryption import EnvelopeCipher, LookupDigest

STAFF_ROLE_BY_TYPE: dict[StaffType, str] = {
    "coach": "coach",
    "life_teacher": "life_teacher",
    "customer_service": "customer_service",
    "administration": "administration",
    "other": "staff",
}
_ELIGIBLE_STAFF_APPLICANT_FILTERS = (
    Person.deleted_at.is_(None),
    UserAccount.status == "active",
    UserAccount.self_service_primary_purpose == "staff_applicant",
)
_MASKED_APPLICATION_REASON = "申请说明需敏感资料权限查看"


class StaffApplicationError(ValueError):
    code = "staff_application_error"


class StaffApplicationNotFound(StaffApplicationError):
    code = "staff_application_not_found"


class StaffApplicationDataUnavailable(StaffApplicationError):
    code = "staff_application_data_unavailable"


class StaffReviewReasonInvalid(StaffApplicationError):
    code = "staff_application_review_reason_invalid"


@dataclass(frozen=True, slots=True)
class StaffApplicationReviewResult:
    application_id: UUID
    status: Literal["approved", "rejected"]
    requested_staff_type: StaffType
    assigned_role: Literal[
        "coach", "life_teacher", "customer_service", "administration", "staff"
    ] | None


class StaffIdentityApplicationService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.identity = IdentityService(db, cipher=cipher, lookup=lookup)
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("staff review clock must be timezone-aware")
        return value

    @staticmethod
    def _validate_reason(reason: str) -> str:
        normalized = reason.strip()
        if (
            not 2 <= len(normalized) <= 500
            or contains_email(normalized)
            or contains_labeled_sensitive_value(normalized)
            or contains_phone_or_identity(normalized)
            or looks_like_raw_secret(normalized)
        ):
            raise StaffReviewReasonInvalid("staff review reason is invalid")
        return normalized

    @classmethod
    def _validate_optional_reason(cls, reason: str | None) -> str | None:
        if reason is None or not reason.strip():
            return None
        return cls._validate_reason(reason)

    @staticmethod
    def _staff_type(value: str) -> StaffType:
        if value not in STAFF_ROLE_BY_TYPE:
            raise StaffApplicationDataUnavailable("staff application type is invalid")
        return value

    @staticmethod
    def _status(value: str) -> StaffApplicationStatus:
        if value not in {"pending", "approved", "rejected", "withdrawn"}:
            raise StaffApplicationDataUnavailable("staff application status is invalid")
        return cast(StaffApplicationStatus, value)

    @classmethod
    def _self_response(
        cls,
        application: StaffIdentityApplication,
    ) -> StaffApplicationSelfResponse:
        return StaffApplicationSelfResponse(
            application_id=application.id,
            requested_staff_type=cls._staff_type(application.requested_staff_type),
            application_reason=application.application_reason,
            status=cls._status(application.status),
            reviewed_at=application.reviewed_at,
            created_at=application.created_at,
        )

    @classmethod
    def _list_item(
        cls,
        application: StaffIdentityApplication,
    ) -> StaffApplicationAdminListItem:
        return StaffApplicationAdminListItem(
            application_id=application.id,
            requested_staff_type=cls._staff_type(application.requested_staff_type),
            status=cls._status(application.status),
            created_at=application.created_at,
        )

    def self_status(self, account_id: UUID) -> StaffApplicationSelfResponse:
        application = self.db.scalar(
            select(StaffIdentityApplication)
            .join(Person, Person.id == StaffIdentityApplication.person_id)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .where(
                UserAccount.id == account_id,
                UserAccount.status == "active",
                UserAccount.self_service_primary_purpose == "staff_applicant",
                Person.deleted_at.is_(None),
            )
            .order_by(
                StaffIdentityApplication.created_at.desc(),
                StaffIdentityApplication.id.desc(),
            )
        )
        if application is None:
            raise StaffApplicationNotFound("staff application not found")
        return self._self_response(application)

    def list_pending(
        self,
        *,
        person_allowed: Callable[[UUID], bool] | None = None,
    ) -> list[StaffApplicationAdminListItem]:
        applications = list(
            self.db.scalars(
                select(StaffIdentityApplication)
                .join(Person, Person.id == StaffIdentityApplication.person_id)
                .join(UserAccount, UserAccount.person_id == Person.id)
                .where(
                    StaffIdentityApplication.status == "pending",
                    *_ELIGIBLE_STAFF_APPLICANT_FILTERS,
                )
                .order_by(
                    StaffIdentityApplication.created_at,
                    StaffIdentityApplication.id,
                )
            )
        )
        return [
            self._list_item(application)
            for application in applications
            if person_allowed is None or person_allowed(application.person_id)
        ]

    def person_id(self, application_id: UUID) -> UUID:
        person_id = self.db.scalar(
            select(StaffIdentityApplication.person_id)
            .join(Person, Person.id == StaffIdentityApplication.person_id)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .where(
                StaffIdentityApplication.id == application_id,
                *_ELIGIBLE_STAFF_APPLICANT_FILTERS,
            )
        )
        if person_id is None:
            raise StaffApplicationNotFound("staff application not found")
        return person_id

    def _identity_number(self, person: Person) -> str:
        try:
            normalized = normalize_cn_id(
                self.cipher.decrypt(
                    person.identity_ciphertext,
                    field_name="identity_number",
                ),
                expected_birth_date=person.date_of_birth,
            )
        except (IdentityValidationError, ValueError, TypeError) as exc:
            raise StaffApplicationDataUnavailable(
                "staff applicant identity is unavailable"
            ) from exc
        if (
            not hmac.compare_digest(
                person.identity_digest,
                self.lookup.digest(normalized.number),
            )
            or normalized.sex != person.sex
        ):
            raise StaffApplicationDataUnavailable(
                "staff applicant identity is unavailable"
            )
        return normalized.number

    def detail(
        self,
        application_id: UUID,
        *,
        sensitive: bool,
    ) -> StaffApplicationAdminDetail:
        row = self.db.execute(
            select(StaffIdentityApplication, Person)
            .join(Person, Person.id == StaffIdentityApplication.person_id)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .where(
                StaffIdentityApplication.id == application_id,
                *_ELIGIBLE_STAFF_APPLICANT_FILTERS,
            )
        ).one_or_none()
        if row is None:
            raise StaffApplicationNotFound("staff application not found")
        application, person = row
        application_reason = (
            application.application_reason
            if sensitive
            else _MASKED_APPLICATION_REASON
        )
        detail = StaffApplicationAdminDetail(
            **self._list_item(application).model_dump(),
            application_reason=application_reason,
        )
        if not sensitive:
            return detail
        try:
            phone = self.identity.decrypt_phone(person)
            email = self.identity.decrypt_email(person)
        except IdentityServiceError as exc:
            raise StaffApplicationDataUnavailable(
                "staff applicant contact is unavailable"
            ) from exc
        return detail.model_copy(
            update={
                "person_id": person.id,
                "full_name": person.full_name,
                "identity_number": self._identity_number(person),
                "sex": person.sex,
                "date_of_birth": person.date_of_birth,
                "phone": phone,
                "email": email,
            }
        )

    def _lock_subject(
        self,
        application: StaffIdentityApplication,
    ) -> tuple[Person, StaffProfile | None, UserAccount]:
        person = self.db.scalar(
            select(Person)
            .where(
                Person.id == application.person_id,
                Person.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if person is None:
            raise StaffApplicationNotFound("staff application not found")
        profile = self.db.scalar(
            select(StaffProfile)
            .where(StaffProfile.person_id == person.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        account = self.db.scalar(
            select(UserAccount)
            .where(
                UserAccount.person_id == person.id,
                UserAccount.status == "active",
                UserAccount.self_service_primary_purpose == "staff_applicant",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise StaffApplicationNotFound("staff application not found")
        return person, profile, account

    @staticmethod
    def _result(
        application: StaffIdentityApplication,
    ) -> StaffApplicationReviewResult:
        requested_type = StaffIdentityApplicationService._staff_type(
            application.requested_staff_type
        )
        status = application.status
        if status == "approved":
            return StaffApplicationReviewResult(
                application_id=application.id,
                status="approved",
                requested_staff_type=requested_type,
                assigned_role=cast(
                    Literal[
                        "coach", "life_teacher", "customer_service", "administration", "staff"
                    ],
                    STAFF_ROLE_BY_TYPE[requested_type],
                ),
            )
        if status == "rejected":
            return StaffApplicationReviewResult(
                application_id=application.id,
                status="rejected",
                requested_staff_type=requested_type,
                assigned_role=None,
            )
        raise StaffApplicationNotFound("staff application not found")

    def approve(
        self,
        application_id: UUID,
        *,
        actor_user_id: UUID,
        reason: str | None,
        request_id: str,
    ) -> StaffApplicationReviewResult:
        normalized_reason = self._validate_optional_reason(reason)
        try:
            with write_transaction(self.db):
                application = self.db.scalar(
                    select(StaffIdentityApplication)
                    .where(StaffIdentityApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None:
                    raise StaffApplicationNotFound("staff application not found")
                person, profile, account = self._lock_subject(application)
                requested_type = self._staff_type(application.requested_staff_type)
                role_key = STAFF_ROLE_BY_TYPE[requested_type]
                role = self.db.scalar(
                    select(Role).where(Role.key == role_key).with_for_update()
                )
                if role is None:
                    raise StaffApplicationDataUnavailable(
                        "staff baseline role is unavailable"
                    )
                if application.status == "approved":
                    if (
                        profile is None
                        or profile.staff_type != requested_type
                        or self.db.get(UserRole, (account.id, role.id)) is None
                    ):
                        raise StaffApplicationDataUnavailable(
                            "approved staff application is inconsistent"
                        )
                    return self._result(application)
                if application.status != "pending":
                    raise StaffApplicationNotFound("staff application not found")
                if profile is None:
                    self.db.add(
                        StaffProfile(
                            person_id=person.id,
                            staff_type=requested_type,
                        )
                    )
                elif profile.staff_type != requested_type:
                    raise StaffApplicationDataUnavailable(
                        "staff profile conflicts with application"
                    )
                if self.db.get(UserRole, (account.id, role.id)) is None:
                    self.db.add(UserRole(user_id=account.id, role_id=role.id))
                application.status = "approved"
                application.reviewed_by_user_id = actor_user_id
                application.review_reason = normalized_reason
                application.reviewed_at = self._now()
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="staff_identity_application.approve",
                    resource_type="staff_identity_application",
                    resource_id=application.id,
                    purpose="staff_identity_application_review",
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "review_decision": "approved",
                        "staff_person_id": str(person.id),
                        "role_key": role_key,
                    },
                )
                self.db.flush()
                return self._result(application)
        except AuditMetadataRejected as exc:
            raise StaffReviewReasonInvalid("staff review reason is invalid") from exc
        except IntegrityError as exc:
            raise StaffApplicationDataUnavailable(
                "staff application approval failed"
            ) from exc

    def reject(
        self,
        application_id: UUID,
        *,
        actor_user_id: UUID,
        reason: str,
        request_id: str,
    ) -> StaffApplicationReviewResult:
        normalized_reason = self._validate_reason(reason)
        try:
            with write_transaction(self.db):
                application = self.db.scalar(
                    select(StaffIdentityApplication)
                    .where(StaffIdentityApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is None:
                    raise StaffApplicationNotFound("staff application not found")
                if application.status == "rejected":
                    return self._result(application)
                if application.status != "pending":
                    raise StaffApplicationNotFound("staff application not found")
                person, profile, _account = self._lock_subject(application)
                if profile is not None:
                    raise StaffApplicationDataUnavailable(
                        "staff profile conflicts with pending application"
                    )
                application.status = "rejected"
                application.reviewed_by_user_id = actor_user_id
                application.review_reason = normalized_reason
                application.reviewed_at = self._now()
                AuditService(self.db).append(
                    actor_user_id=actor_user_id,
                    action="staff_identity_application.reject",
                    resource_type="staff_identity_application",
                    resource_id=application.id,
                    purpose="staff_identity_application_review",
                    outcome="allowed",
                    request_id=request_id,
                    metadata={
                        "review_decision": "rejected",
                        "staff_person_id": str(person.id),
                    },
                )
                self.db.flush()
                return self._result(application)
        except AuditMetadataRejected as exc:
            raise StaffReviewReasonInvalid("staff review reason is invalid") from exc
        except IntegrityError as exc:
            raise StaffApplicationDataUnavailable(
                "staff application rejection failed"
            ) from exc
