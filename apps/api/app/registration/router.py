from __future__ import annotations

import base64
import hashlib
import hmac
import math
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated, NoReturn, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.client_ip import request_source_ip
from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.core.transactions import write_transaction
from app.identity.normalization import IdentityValidationError, normalize_cn_id
from app.identity.readiness import is_minor
from app.identity.service import IdentityService
from app.registration.channel_settings import RegistrationChannelSettingsService
from app.registration.cleanup_service import (
    RegistrationCleanupService,
    registration_application_deadline,
    touch_registration_application,
)
from app.registration.consents import (
    CURRENT_REGISTRATION_TERMS_VERSION,
    REGISTRATION_TERMS_CONTENT,
    registration_terms_digest,
)
from app.registration.delivery import VerificationSender
from app.registration.dependencies import (
    RegistrationSecretMaterial,
    delete_registration_session_cookie,
    get_registration_finalization_service,
    get_registration_session_service,
    get_registration_verification_sender,
    get_registration_verification_service,
    require_registration_secret_material,
    require_registration_session,
    set_registration_session_cookie,
)
from app.registration.finalization_service import (
    GuardianConfirmationRequired,
    RegistrationConflictPendingReview,
    RegistrationFinalizationService,
    RegistrationFinalizationUnavailable,
    clear_registration_application_secrets,
)
from app.registration.guardian_service import (
    GuardianInvitationFlowContext,
    GuardianInvitationSummary,
    GuardianInvitationUnavailable,
    GuardianRegistrationService,
)
from app.registration.identifiers import (
    NormalizedRegistrationIdentifier,
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.invitation_flow import (
    invitation_id_from_flow_token,
    issue_invitation_flow_token,
)
from app.registration.models import (
    IdentifierKind,
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationPurpose,
    RegistrationSession,
    RegistrationState,
)
from app.registration.schemas import (
    GuardianInvitationAccept,
    GuardianInvitationAcceptanceReceiptResponse,
    GuardianInvitationAcceptResponse,
    GuardianInvitationContextResponse,
    GuardianInvitationCreate,
    GuardianInvitationHandoff,
    GuardianInvitationRegistrationStart,
    GuardianInvitationResponse,
    RegistrationApplicationCreate,
    RegistrationApplicationStarted,
    RegistrationChallengeResponse,
    RegistrationChannelSettingsResponse,
    RegistrationContactChallengeCreate,
    RegistrationContactVerifyRequest,
    RegistrationCsrfResponse,
    RegistrationFinalizeResponse,
    RegistrationProfileInput,
    RegistrationProfileResponse,
    RegistrationStateResponse,
    RegistrationTermsConsentInput,
    RegistrationTermsConsentStatusResponse,
    RegistrationVerifyRequest,
    RegistrationVerifyResponse,
    StaffApplicationSelfResponse,
)
from app.registration.session_service import (
    InvalidRegistrationSession,
    RegistrationContext,
    RegistrationSessionIssued,
    RegistrationSessionService,
    RegistrationSessionUnavailable,
)
from app.registration.staff_service import (
    StaffApplicationNotFound,
    StaffIdentityApplicationService,
)
from app.registration.start_limiter import (
    RegistrationStartLimiter,
    RegistrationStartRateLimited,
)
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationConsumed,
    VerificationInvalidOrExpired,
    VerificationRateLimited,
    VerificationTarget,
    VerificationTargetMismatch,
    lock_registration_credential_keys,
    registration_advisory_lock_key,
)
from app.revenue_share.errors import ReferralCodeInvalid
from app.revenue_share.service import RevenueShareService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

router = APIRouter(prefix="/registration", tags=["registration"])

GUARDIAN_INVITATION_FLOW_COOKIE = "erp_guardian_invitation_flow"
GUARDIAN_INVITATION_FLOW_COOKIE_PATH = "/api/v1/registration/guardian-invitations"
_GUARDIAN_INVITATION_FLOW_TTL = timedelta(days=7)

_ACTIVE_REVERIFICATION_STATES = {
    "draft",
    "contact_verified",
    "identity_completed",
    "pending_guardian",
    "ready_to_finalize",
    "conflict",
}


class _RegistrationActionUnavailable(ValueError):
    pass


class _RegistrationApplicationPending(ValueError):
    def __init__(self, *, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("registration application is pending")


def _secret_bytes(secret: SecretStr) -> bytes:
    return base64.b64decode(
        secret.get_secret_value(),
        altchars=b"-_",
        validate=True,
    )


def _crypto(request: Request) -> tuple[EnvelopeCipher, LookupDigest]:
    settings: Settings = request.app.state.settings
    return (
        EnvelopeCipher(_secret_bytes(settings.field_encryption_master_key)),
        LookupDigest(_secret_bytes(settings.field_lookup_key)),
    )


def _channel_settings_service(
    request: Request,
    db: Session,
) -> RegistrationChannelSettingsService:
    return RegistrationChannelSettingsService(
        db,
        phone_signup_allowed_by_deployment=(
            request.app.state.settings.registration_phone_signup_enabled
        ),
    )


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _mask_identifier(identifier: NormalizedRegistrationIdentifier) -> str:
    if identifier.kind == "phone":
        return f"{identifier.value[:3]}****{identifier.value[-4:]}"
    local, domain = identifier.value.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def _next_action(state: str) -> str:
    return {
        "draft": "verify_contact",
        "contact_verified": "complete_profile",
        "identity_completed": "finalize",
        "pending_guardian": "complete_guardian",
        "ready_to_finalize": "finalize",
        "conflict": "await_review",
        "finalized": "login",
        "rejected": "start_again",
        "expired": "start_again",
    }[state]


def _registration_terms_status(
    db: Session,
    application_id: UUID,
    *,
    request_id: str,
    lock: bool = False,
) -> RegistrationTermsConsentStatusResponse:
    query = (
        select(RegistrationConsentAcceptance)
        .where(
            RegistrationConsentAcceptance.registration_application_id == application_id,
            RegistrationConsentAcceptance.consent_type == "registration_terms",
        )
        .order_by(
            RegistrationConsentAcceptance.accepted_at.desc(),
            RegistrationConsentAcceptance.id.desc(),
        )
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    accepted = db.scalar(query)
    current_digest = registration_terms_digest(CURRENT_REGISTRATION_TERMS_VERSION)
    if current_digest is None:  # pragma: no cover - frozen module invariant.
        raise RuntimeError("current registration terms are unavailable")
    current = bool(
        accepted is not None
        and accepted.policy_version == CURRENT_REGISTRATION_TERMS_VERSION
        and hmac.compare_digest(accepted.content_digest, current_digest)
    )
    return RegistrationTermsConsentStatusResponse(
        current_version=CURRENT_REGISTRATION_TERMS_VERSION,
        accepted_version=accepted.policy_version if accepted is not None else None,
        reconfirmation_required=not current,
        request_id=request_id,
    )


def _raise_action_unavailable(*, status: int = 404) -> NoReturn:
    raise ProblemError(
        status=status,
        code="registration_action_unavailable",
        detail="注册操作不可用，请重新开始或稍后重试",
    )


def _application_identifier(
    application: RegistrationApplication,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> NormalizedRegistrationIdentifier:
    if (
        application.verified_identifier_kind not in {"phone", "email"}
        or application.verified_identifier_digest is None
        or application.verified_identifier_ciphertext is None
    ):
        raise _RegistrationActionUnavailable("registration contact is unavailable")
    try:
        identifier = normalize_registration_identifier(
            cast(IdentifierKind, application.verified_identifier_kind),
            cipher.decrypt(
                application.verified_identifier_ciphertext,
                field_name="registration_identifier",
            ),
        )
    except (ValueError, TypeError) as exc:
        raise _RegistrationActionUnavailable("registration contact is unavailable") from exc
    if not hmac.compare_digest(
        application.verified_identifier_digest,
        registration_identifier_digest(lookup, identifier),
    ):
        raise _RegistrationActionUnavailable("registration contact is unavailable")
    return identifier


def _state_response(
    application: RegistrationApplication,
    *,
    identifier: NormalizedRegistrationIdentifier,
    request: Request,
) -> RegistrationStateResponse:
    return RegistrationStateResponse(
        state=cast(RegistrationState, application.state),
        primary_purpose=cast(RegistrationPurpose, application.primary_purpose),
        masked_identifier=_mask_identifier(identifier),
        next_action=_next_action(application.state),
        expires_at=application.expires_at,
        request_id=_request_id(request),
    )


def _request_source(request: Request) -> str:
    return request_source_ip(
        request,
        trusted_proxy_cidrs=request.app.state.settings.trusted_proxy_cidrs,
    )


def _normalize_registration_contact(
    identifier_kind: IdentifierKind,
    identifier: str,
) -> NormalizedRegistrationIdentifier:
    try:
        return normalize_registration_identifier(identifier_kind, identifier)
    except IdentityValidationError as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
            errors=[{"field": "body.identifier", "message": str(exc)}],
        ) from exc


def _invitation_flow_cookie_values(request: Request) -> list[str]:
    values: list[str] = []
    for header_name, header_value in request.headers.raw:
        if header_name.lower() != b"cookie":
            continue
        for item in header_value.decode("latin-1").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == GUARDIAN_INVITATION_FLOW_COOKIE:
                values.append(value)
    return values


def _invitation_flow_id(
    request: Request,
    material: RegistrationSecretMaterial,
) -> UUID | None:
    values = _invitation_flow_cookie_values(request)
    if len(values) != 1 or not values[0]:
        return None
    return invitation_id_from_flow_token(
        values[0],
        pepper=material.session_token_pepper,
        now=datetime.now(UTC),
    )


def _set_invitation_flow_cookie(
    response: Response,
    request: Request,
    token: str,
) -> None:
    response.set_cookie(
        key=GUARDIAN_INVITATION_FLOW_COOKIE,
        value=token,
        max_age=int(_GUARDIAN_INVITATION_FLOW_TTL.total_seconds()),
        httponly=True,
        secure=request.app.state.settings.session_cookie_secure,
        samesite="lax",
        path=GUARDIAN_INVITATION_FLOW_COOKIE_PATH,
    )


def _delete_invitation_flow_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        GUARDIAN_INVITATION_FLOW_COOKIE,
        path=GUARDIAN_INVITATION_FLOW_COOKIE_PATH,
        httponly=True,
        secure=request.app.state.settings.session_cookie_secure,
        samesite="lax",
    )


def _create_registration_application(
    *,
    db: Session,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    primary_purpose: RegistrationPurpose,
    identifier: NormalizedRegistrationIdentifier,
    referral_code_id: UUID | None = None,
) -> RegistrationApplication:
    identifier_digest = registration_identifier_digest(lookup, identifier)
    cleanup = RegistrationCleanupService(db)
    with write_transaction(db):
        lock_registration_credential_keys(
            db,
            [
                registration_advisory_lock_key(
                    "registration-application-identifier",
                    identifier_digest,
                )
            ],
        )
        cleanup.prune_identifier(identifier_digest=identifier_digest)
        now = datetime.now(UTC)
        drafts = list(
            db.scalars(
                select(RegistrationApplication)
                .where(
                    RegistrationApplication.verified_identifier_digest
                    == identifier_digest,
                    RegistrationApplication.state == "draft",
                )
                .order_by(
                    RegistrationApplication.created_at,
                    RegistrationApplication.id,
                )
                .with_for_update(of=RegistrationApplication)
                .execution_options(populate_existing=True)
            )
        )
        active_deadlines: list[datetime] = []
        for draft in drafts:
            deadline = registration_application_deadline(draft)
            if deadline > now:
                active_deadlines.append(deadline)
                continue
            cleanup.expire_application(draft)
        if active_deadlines:
            remaining = max(active_deadlines) - now
            raise _RegistrationApplicationPending(
                retry_after_seconds=max(1, math.ceil(remaining.total_seconds())),
            )
        application = RegistrationApplication(
            primary_purpose=primary_purpose,
            state="draft",
            verified_identifier_kind=identifier.kind,
            verified_identifier_digest=identifier_digest,
            verified_identifier_ciphertext=cipher.encrypt(
                identifier.value,
                field_name="registration_identifier",
            ),
            referral_code_id=referral_code_id,
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db.add(application)
        db.flush()
    return application


def _minor_contact_change_allowed(
    application: RegistrationApplication | None,
    *,
    now: datetime,
) -> bool:
    return bool(
        application is not None
        and application.primary_purpose == "student"
        and application.state in {"identity_completed", "pending_guardian"}
        and application.expires_at > now
        and application.date_of_birth is not None
        and is_minor(application.date_of_birth, business_date_at(now))
    )


@router.get(
    "/channel-settings",
    response_model=RegistrationChannelSettingsResponse,
)
def get_registration_channel_settings(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationChannelSettingsResponse:
    state = _channel_settings_service(request, db).state()
    _prevent_storage(response)
    return RegistrationChannelSettingsResponse(
        phone_enabled=state.phone_enabled,
        email_enabled=state.email_enabled,
        phone_signup_allowed_by_deployment=state.phone_signup_allowed_by_deployment,
        updated_at=state.updated_at,
        request_id=_request_id(request),
    )


def _get_guardian_registration_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    finalization: Annotated[
        RegistrationFinalizationService,
        Depends(get_registration_finalization_service),
    ],
    sender: Annotated[
        VerificationSender,
        Depends(get_registration_verification_sender),
    ],
) -> GuardianRegistrationService:
    cipher, lookup = _crypto(request)
    return GuardianRegistrationService(
        db,
        cipher=cipher,
        lookup=lookup,
        identity=IdentityService(db, cipher=cipher, lookup=lookup),
        finalization=finalization,
        sender=sender,
    )


def _get_staff_identity_application_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> StaffIdentityApplicationService:
    cipher, lookup = _crypto(request)
    return StaffIdentityApplicationService(
        db,
        cipher=cipher,
        lookup=lookup,
    )


def _guardian_invitation_response(
    invitation: GuardianInvitationSummary,
) -> GuardianInvitationResponse:
    return GuardianInvitationResponse(
        id=invitation.id,
        masked_identifier=invitation.masked_identifier,
        relationship=invitation.relationship,
        is_primary=invitation.is_primary,
        status=invitation.status,
        expires_at=invitation.expires_at,
    )


def _guardian_invitation_context_response(
    invitation: GuardianInvitationFlowContext,
    *,
    request: Request,
) -> GuardianInvitationContextResponse:
    return GuardianInvitationContextResponse(
        masked_identifier=invitation.masked_identifier,
        relationship=invitation.relationship,
        is_primary=invitation.is_primary,
        expires_at=invitation.expires_at,
        consent_version=invitation.consent_version,
        request_id=_request_id(request),
    )


def _raise_profile_validation(field: str, message: str) -> NoReturn:
    raise ProblemError(
        status=422,
        code="validation_error",
        detail="Request validation failed",
        errors=[{"field": f"body.{field}", "message": message}],
    )


def _raise_profile_integrity_error(exc: IntegrityError) -> NoReturn:
    """Translate profile-write constraints without exposing database details.

    A concurrent registration can legitimately hit one of the partial unique
    indexes while the request is being committed. Returning the same generic
    action-unavailable response as an expired session makes the form look
    broken and gives the applicant no safe next step. Keep the constraint name
    server-side, but expose a stable, non-enumerating profile conflict code.
    """
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint in {
        "uq_registration_applications_active_identity",
        "uq_registration_applications_active_identifier",
    }:
        raise ProblemError(
            status=409,
            code="registration_profile_conflict",
            detail="注册资料与已有申请发生冲突，请重新开始注册或联系管理员",
        ) from exc
    if isinstance(constraint, str) and constraint.startswith("ck_registration_applications_"):
        raise ProblemError(
            status=422,
            code="registration_profile_invalid",
            detail="岗位或申请说明格式不符合当前规则，请检查后重试",
            errors=[
                {
                    "field": "body.purpose_details",
                    "message": "purpose details do not match the registration rules",
                }
            ],
        ) from exc
    raise ProblemError(
        status=409,
        code="registration_profile_conflict",
        detail="注册资料保存发生冲突，请刷新页面后重新开始",
    ) from exc


def _profile_purpose_values(
    purpose: str,
    input: RegistrationProfileInput,
) -> tuple[str | None, str | None, str | None]:
    details = input.purpose_details
    if purpose == "staff_applicant":
        if (
            details.requested_staff_type is None
            or details.staff_application_reason is None
            or details.visit_reason is not None
        ):
            _raise_profile_validation(
                "purpose_details",
                "staff applicant fields do not match registration purpose",
            )
        return (
            details.requested_staff_type,
            details.staff_application_reason,
            None,
        )
    if purpose == "visitor":
        if (
            details.requested_staff_type is not None
            or details.staff_application_reason is not None
            or details.visit_reason is None
        ):
            _raise_profile_validation(
                "purpose_details",
                "visitor fields do not match registration purpose",
            )
        return None, None, details.visit_reason
    if purpose in {"student", "guardian"}:
        if any(
            value is not None
            for value in (
                details.requested_staff_type,
                details.staff_application_reason,
                details.visit_reason,
            )
        ):
            _raise_profile_validation(
                "purpose_details",
                "purpose-specific fields are not allowed",
            )
        return None, None, None
    _raise_profile_validation("purpose_details", "registration purpose is invalid")


@router.post(
    "/applications",
    response_model=RegistrationApplicationStarted,
    status_code=201,
)
def start_registration(
    input: RegistrationApplicationCreate,
    request: Request,
    response: Response,
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    _sender: Annotated[
        VerificationSender,
        Depends(get_registration_verification_sender),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationApplicationStarted:
    del _sender
    channel_state = _channel_settings_service(request, db).state()
    if input.identifier_kind == "phone" and not channel_state.phone_enabled:
        raise ProblemError(
            status=403,
            code="registration_phone_disabled",
            detail="当前暂不支持手机号申请账号，请使用邮箱注册",
        )
    if input.identifier_kind == "email" and not channel_state.email_enabled:
        raise ProblemError(
            status=403,
            code="registration_email_disabled",
            detail="当前暂不支持邮箱申请账号，请联系管理员",
        )
    cipher, lookup = _crypto(request)
    try:
        identifier = normalize_registration_identifier(
            input.identifier_kind,
            input.identifier,
        )
    except IdentityValidationError as exc:
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
            errors=[{"field": "body.identifier", "message": str(exc)}],
        ) from exc
    try:
        RegistrationStartLimiter(
            db,
            pepper=material.verification_pepper,
        ).record(
            identifier_digest=registration_identifier_digest(
                lookup,
                identifier,
            ),
            request_source=_request_source(request),
        )
    except RegistrationStartRateLimited as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="注册请求过于频繁，请稍后重试",
        ) from exc
    if input.referral_code is not None and input.primary_purpose != "student":
        raise ProblemError(
            status=422,
            code="referral_code_not_allowed",
            detail="邀请码仅适用于学员账号申请",
        )
    referral_code_id: UUID | None = None
    if input.referral_code is not None:
        try:
            referral_code_id = (
                RevenueShareService(db).resolve_active_referral_code(input.referral_code).id
            )
        except ReferralCodeInvalid as exc:
            raise ProblemError(
                status=422,
                code=exc.code,
                detail="邀请码无效或已停用",
            ) from exc
    RegistrationCleanupService(db).prune(limit=25)
    try:
        application = _create_registration_application(
            db=db,
            cipher=cipher,
            lookup=lookup,
            primary_purpose=input.primary_purpose,
            identifier=identifier,
            referral_code_id=referral_code_id,
        )
    except _RegistrationApplicationPending as exc:
        raise ProblemError(
            status=429,
            code="registration_application_pending",
            detail="已有注册申请正在处理中，请继续原流程或稍后重试",
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc
    except IntegrityError:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return RegistrationApplicationStarted(
        application_id=application.id,
        state="draft",
        masked_identifier=_mask_identifier(identifier),
        expires_at=application.expires_at,
        request_id=_request_id(request),
    )


@router.post(
    "/applications/{application_id}/challenge",
    response_model=RegistrationChallengeResponse,
    status_code=201,
)
def issue_registration_challenge(
    application_id: UUID,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> RegistrationChallengeResponse:
    cipher, lookup = _crypto(request)
    request_source = _request_source(request)
    try:
        with write_transaction(db):
            candidate = db.scalar(
                select(RegistrationApplication).where(RegistrationApplication.id == application_id)
            )
            if candidate is None:
                raise _RegistrationActionUnavailable("registration application unavailable")
            candidate_identifier = _application_identifier(
                candidate,
                cipher=cipher,
                lookup=lookup,
            )
            service.lock_issue_limits_in_transaction(
                identifier_kind=candidate_identifier.kind,
                identifier=candidate_identifier.value,
                request_source=request_source,
            )
            application = db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == application_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                application is None
                or application.state not in _ACTIVE_REVERIFICATION_STATES
                or registration_application_deadline(application) <= datetime.now(UTC)
            ):
                raise _RegistrationActionUnavailable("registration application unavailable")
            identifier = _application_identifier(
                application,
                cipher=cipher,
                lookup=lookup,
            )
            if identifier != candidate_identifier:
                raise _RegistrationActionUnavailable("registration application unavailable")
            issued = service.issue_in_transaction(
                identifier_kind=identifier.kind,
                identifier=identifier.value,
                purpose="registration",
                request_source=request_source,
                application_id=application.id,
                expires_at_cap=registration_application_deadline(application),
            )
            touch_registration_application(application, now=datetime.now(UTC))
    except _RegistrationActionUnavailable:
        _raise_action_unavailable()
    except VerificationRateLimited as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="验证码请求过于频繁，请稍后重试",
        ) from exc
    except VerificationInvalidOrExpired:
        _raise_action_unavailable()
    _prevent_storage(response)
    return RegistrationChallengeResponse(
        challenge_id=issued.challenge_id,
        expires_at=issued.expires_at,
        retry_after_seconds=60,
        masked_destination=_mask_identifier(identifier),
        request_id=_request_id(request),
    )


@router.post(
    "/applications/{application_id}/verify",
    response_model=RegistrationVerifyResponse,
)
def verify_registration_challenge(
    application_id: UUID,
    input: RegistrationVerifyRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    verification_service: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
    session_service: Annotated[
        RegistrationSessionService,
        Depends(get_registration_session_service),
    ],
) -> RegistrationVerifyResponse:
    cipher, lookup = _crypto(request)
    application: RegistrationApplication | None = None
    identifier: NormalizedRegistrationIdentifier | None = None
    expected_identifier_digest: str | None = None
    issued_session: RegistrationSessionIssued | None = None
    initial_identifier_digest: str | None = None

    def prepare_registration_target() -> VerificationTarget:
        nonlocal application, identifier, expected_identifier_digest
        nonlocal initial_identifier_digest
        target = db.execute(
            select(
                RegistrationApplication.verified_identifier_kind,
                RegistrationApplication.verified_identifier_digest,
            ).where(RegistrationApplication.id == application_id)
        ).one_or_none()
        if (
            target is None
            or target.verified_identifier_kind not in {"phone", "email"}
            or target.verified_identifier_digest is None
        ):
            raise _RegistrationActionUnavailable("registration application unavailable")
        initial_identifier_digest = target.verified_identifier_digest
        db.execute(
            select(
                func.pg_advisory_xact_lock(
                    registration_advisory_lock_key(
                        "registration-application-identifier",
                        initial_identifier_digest,
                    )
                )
            )
        )
        locked = db.scalar(
            select(RegistrationApplication)
            .where(RegistrationApplication.id == application_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            locked is None
            or locked.state not in _ACTIVE_REVERIFICATION_STATES
            or registration_application_deadline(locked) <= datetime.now(UTC)
            or locked.verified_identifier_digest != initial_identifier_digest
        ):
            raise _RegistrationActionUnavailable("registration application unavailable")
        application = locked
        identifier = _application_identifier(
            locked,
            cipher=cipher,
            lookup=lookup,
        )
        expected_identifier_digest = locked.verified_identifier_digest
        assert expected_identifier_digest is not None
        return VerificationTarget(
            application_id=application_id,
            account_id=None,
            identifier_kind=identifier.kind,
            identifier_digest=expected_identifier_digest,
            purpose="registration",
        )

    def open_registration_session(consumed: VerificationConsumed) -> None:
        nonlocal application, identifier, issued_session
        assert application is not None
        assert identifier is not None
        assert expected_identifier_digest is not None
        assert initial_identifier_digest is not None
        if (
            consumed.application_id != application_id
            or consumed.account_id is not None
            or consumed.purpose != "registration"
            or consumed.identifier_kind != identifier.kind
            or not hmac.compare_digest(
                consumed.identifier_digest,
                expected_identifier_digest,
            )
        ):
            raise _RegistrationActionUnavailable("verification target mismatch")
        if (
            application.state not in _ACTIVE_REVERIFICATION_STATES
            or registration_application_deadline(application) <= datetime.now(UTC)
        ):
            raise _RegistrationActionUnavailable("registration application unavailable")
        competing = list(
            db.scalars(
                select(RegistrationApplication)
                .where(
                    RegistrationApplication.id != application.id,
                    RegistrationApplication.verified_identifier_digest == initial_identifier_digest,
                    RegistrationApplication.state.in_(_ACTIVE_REVERIFICATION_STATES),
                )
                .order_by(RegistrationApplication.id)
                .with_for_update(of=RegistrationApplication)
                .execution_options(populate_existing=True)
            )
        )
        now = datetime.now(UTC)
        expired_competing = [
            candidate
            for candidate in competing
            if registration_application_deadline(candidate) <= now
        ]
        live_competing = [
            candidate for candidate in competing if candidate not in expired_competing
        ]
        verified = [candidate for candidate in live_competing if candidate.state != "draft"]
        if len(verified) > 1:
            raise _RegistrationActionUnavailable("registration application unavailable")
        resumed = verified[0] if verified else application
        expired_drafts = expired_competing + [
            candidate for candidate in live_competing if candidate.state == "draft"
        ]
        if resumed.id != application.id:
            expired_drafts.append(application)
        for draft in expired_drafts:
            RegistrationCleanupService.expire_application(draft)
        expired_ids = [draft.id for draft in expired_drafts]
        if expired_ids:
            db.execute(
                update(RegistrationSession)
                .where(
                    RegistrationSession.application_id.in_(expired_ids),
                    RegistrationSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
        db.flush()
        if resumed.state == "draft":
            resumed.state = "contact_verified"
        db.flush()
        application = resumed
        identifier = _application_identifier(
            resumed,
            cipher=cipher,
            lookup=lookup,
        )
        issued_session = session_service.issue_in_transaction(resumed.id)

    try:
        verification_service.consume_with(
            input.challenge_id,
            input.code,
            expected_purpose="registration",
            prepare_target=prepare_registration_target,
            on_success=open_registration_session,
        )
    except VerificationInvalidOrExpired as exc:
        raise ProblemError(
            status=422,
            code=exc.code,
            detail="验证码无效或已过期",
        ) from exc
    except (
        _RegistrationActionUnavailable,
        RegistrationSessionUnavailable,
        VerificationTargetMismatch,
    ):
        _raise_action_unavailable()
    assert issued_session is not None
    assert application is not None
    assert identifier is not None
    set_registration_session_cookie(
        response,
        request,
        issued_session.session_token,
    )
    _prevent_storage(response)
    state = _state_response(application, identifier=identifier, request=request)
    return RegistrationVerifyResponse(
        **state.model_dump(),
        csrf_token=issued_session.csrf_token,
    )


@router.get("/csrf", response_model=RegistrationCsrfResponse)
def registration_csrf(
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    service: Annotated[
        RegistrationSessionService,
        Depends(get_registration_session_service),
    ],
) -> RegistrationCsrfResponse:
    try:
        csrf_token = service.rotate_csrf(context)
    except InvalidRegistrationSession as exc:
        raise ProblemError(
            status=401,
            code="registration_session_invalid",
            detail="注册会话已失效，请重新验证联系方式",
        ) from exc
    _prevent_storage(response)
    return RegistrationCsrfResponse(csrf_token=csrf_token)


@router.post(
    "/me/contact-challenges",
    response_model=RegistrationChallengeResponse,
    status_code=201,
)
def issue_registration_contact_challenge(
    input: RegistrationContactChallengeCreate,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> RegistrationChallengeResponse:
    identifier = _normalize_registration_contact(
        input.identifier_kind,
        input.identifier,
    )
    request_source = _request_source(request)
    try:
        with write_transaction(db):
            service.lock_issue_limits_in_transaction(
                identifier_kind=identifier.kind,
                identifier=identifier.value,
                request_source=request_source,
            )
            application = db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == context.application.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            now = datetime.now(UTC)
            if not _minor_contact_change_allowed(application, now=now):
                raise _RegistrationActionUnavailable("registration contact change is unavailable")
            issued = service.issue_in_transaction(
                identifier_kind=identifier.kind,
                identifier=identifier.value,
                purpose="registration",
                request_source=request_source,
                application_id=context.application.id,
            )
            assert application is not None
            touch_registration_application(application, now=now)
    except _RegistrationActionUnavailable:
        _raise_action_unavailable(status=409)
    except VerificationRateLimited as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="验证码请求过于频繁，请稍后重试",
        ) from exc
    _prevent_storage(response)
    return RegistrationChallengeResponse(
        challenge_id=issued.challenge_id,
        expires_at=issued.expires_at,
        retry_after_seconds=60,
        masked_destination=_mask_identifier(identifier),
        request_id=_request_id(request),
    )


@router.post(
    "/me/contact",
    response_model=RegistrationProfileResponse,
)
def verify_registration_contact_change(
    input: RegistrationContactVerifyRequest,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationVerificationService,
        Depends(get_registration_verification_service),
    ],
) -> RegistrationProfileResponse:
    cipher, lookup = _crypto(request)
    identifier = _normalize_registration_contact(
        input.identifier_kind,
        input.identifier,
    )
    identifier_digest = registration_identifier_digest(lookup, identifier)
    application: RegistrationApplication | None = None

    def prepare_contact_target() -> VerificationTarget:
        nonlocal application
        current_identifier_digest = db.scalar(
            select(RegistrationApplication.verified_identifier_digest).where(
                RegistrationApplication.id == context.application.id
            )
        )
        if current_identifier_digest is None:
            raise _RegistrationActionUnavailable("registration contact change is unavailable")
        lock_registration_credential_keys(
            db,
            [
                registration_advisory_lock_key(
                    "registration-application-identifier",
                    digest,
                )
                for digest in {current_identifier_digest, identifier_digest}
            ],
        )
        application = db.scalar(
            select(RegistrationApplication)
            .where(RegistrationApplication.id == context.application.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            application is None
            or not _minor_contact_change_allowed(
                application,
                now=datetime.now(UTC),
            )
            or not hmac.compare_digest(
                application.verified_identifier_digest or "",
                current_identifier_digest,
            )
        ):
            raise _RegistrationActionUnavailable("registration contact change is unavailable")
        return VerificationTarget(
            application_id=context.application.id,
            account_id=None,
            identifier_kind=identifier.kind,
            identifier_digest=identifier_digest,
            purpose="registration",
        )

    def replace_contact(_consumed: VerificationConsumed) -> None:
        assert application is not None
        competing = list(
            db.scalars(
                select(RegistrationApplication)
                .where(
                    RegistrationApplication.id != application.id,
                    RegistrationApplication.verified_identifier_digest == identifier_digest,
                    RegistrationApplication.state.in_(_ACTIVE_REVERIFICATION_STATES),
                )
                .order_by(RegistrationApplication.id)
                .with_for_update(of=RegistrationApplication)
                .execution_options(populate_existing=True)
            )
        )
        if any(candidate.state != "draft" for candidate in competing):
            raise _RegistrationActionUnavailable("registration contact change is unavailable")
        for draft in competing:
            draft.state = "expired"
            clear_registration_application_secrets(draft)
        application.verified_identifier_kind = identifier.kind
        application.verified_identifier_digest = identifier_digest
        application.verified_identifier_ciphertext = cipher.encrypt(
            identifier.value,
            field_name="registration_identifier",
        )
        touch_registration_application(application, now=datetime.now(UTC))
        db.flush()

    try:
        service.consume_with(
            input.challenge_id,
            input.code,
            expected_purpose="registration",
            prepare_target=prepare_contact_target,
            on_success=replace_contact,
        )
    except VerificationInvalidOrExpired as exc:
        raise ProblemError(
            status=422,
            code=exc.code,
            detail="验证码无效或已过期",
        ) from exc
    except (
        _RegistrationActionUnavailable,
        IntegrityError,
        VerificationTargetMismatch,
    ):
        _raise_action_unavailable(status=409)
    assert application is not None
    _prevent_storage(response)
    return RegistrationProfileResponse(
        **_state_response(
            application,
            identifier=identifier,
            request=request,
        ).model_dump(),
    )


@router.get("/me", response_model=RegistrationStateResponse)
def registration_me(
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
) -> RegistrationStateResponse:
    cipher, lookup = _crypto(request)
    try:
        identifier = _application_identifier(
            context.application,
            cipher=cipher,
            lookup=lookup,
        )
    except _RegistrationActionUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return _state_response(context.application, identifier=identifier, request=request)


@router.get(
    "/me/consents/registration-terms",
    response_model=RegistrationTermsConsentStatusResponse,
)
def get_registration_terms_consent(
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationTermsConsentStatusResponse:
    result = _registration_terms_status(
        db,
        context.application.id,
        request_id=_request_id(request),
    )
    _prevent_storage(response)
    return result


@router.post(
    "/me/consents/registration-terms",
    response_model=RegistrationTermsConsentStatusResponse,
)
def confirm_registration_terms_consent(
    input: RegistrationTermsConsentInput,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationTermsConsentStatusResponse:
    content = REGISTRATION_TERMS_CONTENT.get(input.consent_version)
    if content is None or input.consent_version != CURRENT_REGISTRATION_TERMS_VERSION:
        _raise_profile_validation("consent_version", "consent version is unavailable")
    now = datetime.now(UTC)
    try:
        with write_transaction(db):
            application = db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == context.application.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                application is None
                or application.state
                not in {
                    "identity_completed",
                    "pending_guardian",
                    "ready_to_finalize",
                    "conflict",
                }
                or application.expires_at <= now
            ):
                raise _RegistrationActionUnavailable("registration terms cannot be confirmed")
            status = _registration_terms_status(
                db,
                application.id,
                request_id=_request_id(request),
                lock=True,
            )
            if status.reconfirmation_required:
                db.add(
                    RegistrationConsentAcceptance(
                        registration_application_id=application.id,
                        account_id=None,
                        consent_type="registration_terms",
                        policy_version=input.consent_version,
                        content_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        accepted_at=now,
                        request_id=_request_id(request),
                        source="self_registration_reconfirmation",
                    )
                )
                db.flush()
            result = _registration_terms_status(
                db,
                application.id,
                request_id=_request_id(request),
            )
    except _RegistrationActionUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return result


@router.patch("/me", response_model=RegistrationProfileResponse)
@router.post("/me", response_model=RegistrationProfileResponse)
def complete_registration_profile(
    input: RegistrationProfileInput,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationProfileResponse:
    cipher, lookup = _crypto(request)
    terms_content = REGISTRATION_TERMS_CONTENT.get(input.consent_version)
    if terms_content is None:
        _raise_profile_validation("consent_version", "consent version is unavailable")
    try:
        normalized_identity = normalize_cn_id(
            input.identity_number,
            expected_birth_date=input.date_of_birth,
        )
    except IdentityValidationError as exc:
        _raise_profile_validation("identity_number", str(exc))
    if normalized_identity.sex != input.sex:
        _raise_profile_validation("sex", "sex does not match identity number")
    full_name = unicodedata.normalize("NFKC", input.full_name).strip()
    if not full_name:
        _raise_profile_validation("full_name", "full name is required")
    if len(full_name) > 80:
        _raise_profile_validation("full_name", "full name is too long")
    requested_staff_type, staff_reason, visit_reason = _profile_purpose_values(
        context.application.primary_purpose,
        input,
    )
    password_hash = PasswordService().hash(input.password.get_secret_value())
    now = datetime.now(UTC)
    try:
        with write_transaction(db):
            application = db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == context.application.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                application is None
                or application.state != "contact_verified"
                or application.expires_at <= now
            ):
                raise _RegistrationActionUnavailable("registration profile cannot be completed")
            identifier = _application_identifier(
                application,
                cipher=cipher,
                lookup=lookup,
            )
            application.password_hash = password_hash
            application.full_name = full_name
            application.identity_ciphertext = cipher.encrypt(
                normalized_identity.number,
                field_name="registration_identity",
            )
            application.identity_digest = lookup.digest(normalized_identity.number)
            application.sex = input.sex
            application.date_of_birth = input.date_of_birth
            application.requested_staff_type = requested_staff_type
            application.staff_application_reason = staff_reason
            application.visit_reason = visit_reason
            application.state = (
                "identity_completed"
                if application.primary_purpose == "student"
                and is_minor(input.date_of_birth, business_date_at(now))
                else "ready_to_finalize"
            )
            db.add(
                RegistrationConsentAcceptance(
                    registration_application_id=application.id,
                    account_id=None,
                    consent_type="registration_terms",
                    policy_version=input.consent_version,
                    content_digest=hashlib.sha256(terms_content.encode("utf-8")).hexdigest(),
                    accepted_at=now,
                    request_id=_request_id(request),
                    source="self_registration",
                )
            )
            db.flush()
    except _RegistrationActionUnavailable:
        _raise_action_unavailable(status=409)
    except IntegrityError as exc:
        _raise_profile_integrity_error(exc)
    _prevent_storage(response)
    state = _state_response(application, identifier=identifier, request=request)
    return RegistrationProfileResponse(**state.model_dump())


@router.post("/me/finalize", response_model=RegistrationFinalizeResponse)
def finalize_registration(
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    service: Annotated[
        RegistrationFinalizationService,
        Depends(get_registration_finalization_service),
    ],
) -> RegistrationFinalizeResponse:
    try:
        result = service.finalize(
            context.application.id,
            request_id=_request_id(request),
        )
    except GuardianConfirmationRequired:
        return RegistrationFinalizeResponse(
            state="pending_guardian",
            next_action="complete_guardian",
            request_id=_request_id(request),
        )
    except RegistrationConflictPendingReview:
        return RegistrationFinalizeResponse(
            state="conflict",
            next_action="await_review",
            request_id=_request_id(request),
        )
    except RegistrationFinalizationUnavailable:
        _raise_action_unavailable(status=409)
    delete_registration_session_cookie(response, request)
    return RegistrationFinalizeResponse(
        state="finalized",
        next_action=("login_or_reset" if result.match_kind == "existing_account" else "login"),
        request_id=_request_id(request),
    )


@router.post(
    "/guardian-invitations/handoff",
    status_code=204,
    response_model=None,
)
def handoff_guardian_invitation(
    input: GuardianInvitationHandoff,
    request: Request,
    response: Response,
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> None:
    try:
        invitation_id = service.invitation_id_for_handoff_token(input.token)
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=404)
    else:
        token = issue_invitation_flow_token(
            invitation_id,
            pepper=material.session_token_pepper,
            now=datetime.now(UTC),
            ttl=_GUARDIAN_INVITATION_FLOW_TTL,
        )
        _set_invitation_flow_cookie(response, request, token)
    _prevent_storage(response)


@router.get(
    "/guardian-invitations/context",
    response_model=GuardianInvitationContextResponse,
)
def guardian_invitation_context(
    request: Request,
    response: Response,
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationContextResponse:
    invitation_id = _invitation_flow_id(request, material)
    if invitation_id is None:
        _raise_action_unavailable(status=404)
    try:
        invitation = service.invitation_flow_context(invitation_id)
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=404)
    _prevent_storage(response)
    return _guardian_invitation_context_response(invitation, request=request)


@router.post(
    "/guardian-invitations/start-registration",
    response_model=RegistrationApplicationStarted,
    status_code=201,
)
def start_guardian_invitation_registration(
    input: GuardianInvitationRegistrationStart,
    request: Request,
    response: Response,
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> RegistrationApplicationStarted:
    del input
    invitation_id = _invitation_flow_id(request, material)
    if invitation_id is None:
        _raise_action_unavailable(status=409)
    try:
        invitation_snapshot = service.invitation_flow_context(invitation_id)
        RegistrationCleanupService(db).prune(limit=25)
        cipher, lookup = _crypto(request)
        identifier_digest = registration_identifier_digest(
            lookup,
            invitation_snapshot.identifier,
        )
        with write_transaction(db):
            lock_registration_credential_keys(
                db,
                [
                    registration_advisory_lock_key(
                        "registration-application-identifier",
                        identifier_digest,
                    )
                ],
            )
            invitation = service.invitation_flow_context(invitation_id, lock=True)
            if invitation.identifier != invitation_snapshot.identifier:
                raise GuardianInvitationUnavailable(
                    "guardian invitation identifier changed"
                )
            RegistrationStartLimiter(
                db,
                pepper=material.verification_pepper,
            ).record(
                identifier_digest=identifier_digest,
                request_source=_request_source(request),
            )
            application = _create_registration_application(
                db=db,
                cipher=cipher,
                lookup=lookup,
                primary_purpose="guardian",
                identifier=invitation.identifier,
            )
    except RegistrationStartRateLimited as exc:
        raise ProblemError(
            status=429,
            code=exc.code,
            detail="注册请求过于频繁，请稍后重试",
        ) from exc
    except _RegistrationApplicationPending as exc:
        raise ProblemError(
            status=429,
            code="registration_application_pending",
            detail="已有注册申请正在处理中，请继续原流程或稍后重试",
            retry_after_seconds=exc.retry_after_seconds,
        ) from exc
    except (GuardianInvitationUnavailable, IntegrityError):
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return RegistrationApplicationStarted(
        application_id=application.id,
        state="draft",
        masked_identifier=invitation.masked_identifier,
        expires_at=application.expires_at,
        request_id=_request_id(request),
    )


@router.get(
    "/me/guardian-invitations",
    response_model=list[GuardianInvitationResponse],
)
def list_minor_guardian_invitations(
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> list[GuardianInvitationResponse]:
    try:
        invitations = service.list_for_application(context.application.id)
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return [_guardian_invitation_response(invitation) for invitation in invitations]


@router.post(
    "/me/guardian-invitations",
    response_model=GuardianInvitationResponse,
    status_code=201,
)
def create_minor_guardian_invitation(
    input: GuardianInvitationCreate,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationResponse:
    try:
        invitation = service.invite(
            context.application.id,
            identifier_kind=input.identifier_kind,
            identifier=input.identifier,
            relationship=input.relationship,
            is_primary=input.is_primary,
            request_id=_request_id(request),
        )
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return _guardian_invitation_response(invitation)


@router.post(
    "/me/guardian-invitations/{invitation_id}/resend",
    response_model=GuardianInvitationResponse,
    status_code=201,
)
def resend_minor_guardian_invitation(
    invitation_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationResponse:
    try:
        invitation = service.resend(
            context.application.id,
            invitation_id,
            request_id=_request_id(request),
        )
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return _guardian_invitation_response(invitation)


@router.delete(
    "/me/guardian-invitations/{invitation_id}",
    response_model=GuardianInvitationResponse,
)
def revoke_minor_guardian_invitation(
    invitation_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[
        RegistrationContext,
        Depends(require_registration_session),
    ],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationResponse:
    try:
        invitation = service.revoke(
            context.application.id,
            invitation_id,
            request_id=_request_id(request),
        )
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return _guardian_invitation_response(invitation)


@router.get(
    "/guardian-invitations",
    response_model=list[GuardianInvitationResponse],
)
def list_adult_guardian_invitations(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> list[GuardianInvitationResponse]:
    try:
        invitations = service.list_for_account(context.account.id)
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return [_guardian_invitation_response(invitation) for invitation in invitations]


@router.post(
    "/guardian-invitations",
    response_model=GuardianInvitationResponse,
    status_code=201,
)
def create_adult_guardian_invitation(
    input: GuardianInvitationCreate,
    request: Request,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationResponse:
    try:
        invitation = service.invite_for_account(
            context.account.id,
            identifier_kind=input.identifier_kind,
            identifier=input.identifier,
            relationship=input.relationship,
            is_primary=input.is_primary,
            request_id=_request_id(request),
        )
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return _guardian_invitation_response(invitation)


@router.post(
    "/guardian-invitations/accept",
    response_model=GuardianInvitationAcceptResponse,
)
def accept_handed_off_guardian_invitation(
    input: GuardianInvitationAccept,
    request: Request,
    response: Response,
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationAcceptResponse:
    invitation_id = _invitation_flow_id(request, material)
    if invitation_id is None:
        _raise_action_unavailable(status=409)
    try:
        accepted = service.accept(
            invitation_id,
            guardian_account_id=context.account.id,
            consent_version=input.consent_version,
            request_id=_request_id(request),
        )
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=409)
    _prevent_storage(response)
    return GuardianInvitationAcceptResponse(
        status="accepted",
        student_finalized=accepted.student_finalized,
        request_id=_request_id(request),
    )


@router.get(
    "/guardian-invitations/acceptance",
    response_model=GuardianInvitationAcceptanceReceiptResponse,
)
def get_guardian_invitation_acceptance_receipt(
    request: Request,
    response: Response,
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        GuardianRegistrationService,
        Depends(_get_guardian_registration_service),
    ],
) -> GuardianInvitationAcceptanceReceiptResponse:
    invitation_id = _invitation_flow_id(request, material)
    if invitation_id is None:
        _raise_action_unavailable(status=404)
    try:
        accepted = service.acceptance_receipt(
            invitation_id,
            guardian_account_id=context.account.id,
        )
    except GuardianInvitationUnavailable:
        _raise_action_unavailable(status=404)
    _prevent_storage(response)
    return GuardianInvitationAcceptanceReceiptResponse(
        status="accepted",
        student_finalized=accepted.student_finalized,
        request_id=_request_id(request),
    )


@router.get(
    "/staff-application",
    response_model=StaffApplicationSelfResponse,
)
def get_own_staff_identity_application(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    service: Annotated[
        StaffIdentityApplicationService,
        Depends(_get_staff_identity_application_service),
    ],
) -> StaffApplicationSelfResponse:
    try:
        result = service.self_status(context.account.id)
    except StaffApplicationNotFound:
        _raise_action_unavailable()
    _prevent_storage(response)
    return result
