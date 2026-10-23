from __future__ import annotations

import base64
from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.registration.access import registration_permission_allowed
from app.registration.channel_settings import (
    RegistrationChannelSettingsError,
    RegistrationChannelSettingsService,
    RegistrationPhoneDeploymentDisabled,
)
from app.registration.finalization_service import (
    RegistrationConflictPendingReview,
    RegistrationFinalizationService,
    RegistrationFinalizationUnavailable,
)
from app.registration.review_service import (
    RegistrationReviewDataUnavailable,
    RegistrationReviewNotFound,
    RegistrationReviewReasonInvalid,
    RegistrationReviewService,
)
from app.registration.schemas import (
    RegistrationAdminDetail,
    RegistrationAdminListItem,
    RegistrationChannelSettingsResponse,
    RegistrationChannelSettingsUpdate,
    RegistrationReviewActionResponse,
    RegistrationReviewRejectInput,
    RegistrationReviewResolveInput,
    StaffApplicationAdminDetail,
    StaffApplicationAdminListItem,
    StaffApplicationApproveInput,
    StaffApplicationRejectInput,
    StaffApplicationReviewResponse,
)
from app.registration.staff_service import (
    StaffApplicationDataUnavailable,
    StaffApplicationNotFound,
    StaffIdentityApplicationService,
    StaffReviewReasonInvalid,
)
from app.security.encryption import EnvelopeCipher, LookupDigest

router = APIRouter(
    prefix="/registration-admin",
    tags=["registration administration"],
)


def _secret_bytes(secret: SecretStr) -> bytes:
    return base64.b64decode(
        secret.get_secret_value(),
        altchars=b"-_",
        validate=True,
    )


def get_registration_review_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationReviewService:
    settings: Settings = request.app.state.settings
    cipher = EnvelopeCipher(_secret_bytes(settings.field_encryption_master_key))
    lookup = LookupDigest(_secret_bytes(settings.field_lookup_key))
    return RegistrationReviewService(
        db,
        cipher=cipher,
        lookup=lookup,
        finalization=RegistrationFinalizationService(
            db,
            cipher=cipher,
            lookup=lookup,
        ),
    )


def get_staff_identity_application_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> StaffIdentityApplicationService:
    settings: Settings = request.app.state.settings
    return StaffIdentityApplicationService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings.field_encryption_master_key)),
        lookup=LookupDigest(_secret_bytes(settings.field_lookup_key)),
    )


def _prevent_storage(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


def _request_id(request: Request) -> str:
    return str(request.state.request_id)


def _json_request_body_openapi(model: type[BaseModel]) -> dict[str, object]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": model.model_json_schema(),
                }
            },
        }
    }


async def _parse_staff_approve_input(request: Request) -> StaffApplicationApproveInput:
    try:
        return StaffApplicationApproveInput.model_validate_json(await request.body())
    except (ValidationError, ValueError, TypeError):
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
        ) from None


async def _parse_staff_reject_input(request: Request) -> StaffApplicationRejectInput:
    try:
        return StaffApplicationRejectInput.model_validate_json(await request.body())
    except (ValidationError, ValueError, TypeError):
        raise ProblemError(
            status=422,
            code="validation_error",
            detail="Request validation failed",
        ) from None


def _allowed(
    db: Session,
    *,
    account_id: UUID,
    permission: str,
    person_id: UUID | None = None,
) -> bool:
    return registration_permission_allowed(
        db,
        account_id=account_id,
        permission=permission,
        person_id=person_id,
    )


def _review_denied() -> NoReturn:
    raise ProblemError(
        status=403,
        code="registration_review_denied",
        detail="没有注册审核权限",
    )


def _private_not_found() -> NoReturn:
    raise ProblemError(
        status=404,
        code="registration_application_not_found",
        detail="注册申请不存在",
    )


def _review_data_unavailable() -> NoReturn:
    raise ProblemError(
        status=409,
        code="registration_review_data_unavailable",
        detail="注册审核数据不可用",
    )


def _review_reason_invalid() -> NoReturn:
    raise ProblemError(
        status=422,
        code="registration_review_reason_invalid",
        detail="审核原因包含不可保存的信息",
    )


def _staff_review_denied() -> NoReturn:
    raise ProblemError(
        status=403,
        code="staff_application_review_denied",
        detail="没有工作人员身份审核权限",
    )


def _staff_application_not_found() -> NoReturn:
    raise ProblemError(
        status=404,
        code="staff_application_not_found",
        detail="工作人员身份申请不存在",
    )


def _staff_application_data_unavailable() -> NoReturn:
    raise ProblemError(
        status=409,
        code="staff_application_data_unavailable",
        detail="工作人员身份申请数据不可用",
    )


def _staff_review_reason_invalid() -> NoReturn:
    raise ProblemError(
        status=422,
        code="staff_application_review_reason_invalid",
        detail="审核原因包含不可保存的信息",
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


def _require_review(
    db: Session,
    context: AuthContext,
    *,
    private_object: bool,
) -> None:
    if _allowed(
        db,
        account_id=context.account.id,
        permission="registration.review",
    ):
        return
    if private_object:
        _private_not_found()
    _review_denied()


def require_private_registration_reviewer(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    _require_review(db, context, private_object=True)
    return context


def require_registration_channel_manager(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    _require_review(db, context, private_object=False)
    return context


def require_private_staff_application_reviewer(
    application_id: UUID,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        StaffIdentityApplicationService,
        Depends(get_staff_identity_application_service),
    ],
) -> AuthContext:
    try:
        person_id = service.person_id(application_id)
    except StaffApplicationNotFound:
        _staff_application_not_found()
    if not _allowed(
        db,
        account_id=context.account.id,
        permission="staff_identity_applications.review",
        person_id=person_id,
    ):
        _staff_application_not_found()
    return context


@router.get(
    "/channel-settings",
    response_model=RegistrationChannelSettingsResponse,
)
def get_registration_channel_settings_admin(
    response: Response,
    request: Request,
    context: Annotated[
        AuthContext,
        Depends(require_registration_channel_manager),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationChannelSettingsResponse:
    del context
    state = _channel_settings_service(request, db).state()
    _prevent_storage(response)
    return RegistrationChannelSettingsResponse(
        phone_enabled=state.phone_enabled,
        email_enabled=state.email_enabled,
        phone_signup_allowed_by_deployment=state.phone_signup_allowed_by_deployment,
        updated_at=state.updated_at,
        request_id=_request_id(request),
    )


@router.put(
    "/channel-settings",
    response_model=RegistrationChannelSettingsResponse,
)
def update_registration_channel_settings(
    input: RegistrationChannelSettingsUpdate,
    response: Response,
    request: Request,
    context: Annotated[
        AuthContext,
        Depends(require_registration_channel_manager),
    ],
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationChannelSettingsResponse:
    try:
        state = _channel_settings_service(request, db).save(
            input,
            actor_user_id=context.account.id,
            request_id=_request_id(request),
        )
    except RegistrationPhoneDeploymentDisabled as exc:
        raise ProblemError(status=409, code=exc.code, detail=str(exc)) from exc
    except RegistrationChannelSettingsError as exc:
        raise ProblemError(status=422, code=exc.code, detail=str(exc)) from exc
    _prevent_storage(response)
    return RegistrationChannelSettingsResponse(
        phone_enabled=state.phone_enabled,
        email_enabled=state.email_enabled,
        phone_signup_allowed_by_deployment=state.phone_signup_allowed_by_deployment,
        updated_at=state.updated_at,
        request_id=_request_id(request),
    )


@router.get(
    "/applications",
    response_model=list[RegistrationAdminListItem],
)
def list_registration_conflicts(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationReviewService,
        Depends(get_registration_review_service),
    ],
) -> list[RegistrationAdminListItem]:
    _require_review(db, context, private_object=False)
    try:
        result = service.list_conflicts(
            candidate_allowed=lambda candidate_person_ids: all(
                _allowed(
                    db,
                    account_id=context.account.id,
                    permission="registration.review",
                    person_id=person_id,
                )
                for person_id in candidate_person_ids
            )
        )
    except RegistrationReviewDataUnavailable:
        _review_data_unavailable()
    _prevent_storage(response)
    return result


@router.get(
    "/applications/{application_id}",
    response_model=RegistrationAdminDetail,
    response_model_exclude_none=True,
)
def registration_conflict_detail(
    application_id: UUID,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationReviewService,
        Depends(get_registration_review_service),
    ],
) -> RegistrationAdminDetail:
    _require_review(db, context, private_object=True)
    try:
        candidate_person_ids = service.candidate_person_ids(application_id)
    except RegistrationReviewNotFound:
        _private_not_found()
    except RegistrationReviewDataUnavailable:
        _review_data_unavailable()
    if any(
        not _allowed(
            db,
            account_id=context.account.id,
            permission="registration.review",
            person_id=person_id,
        )
        for person_id in candidate_person_ids
    ):
        _private_not_found()
    globally_sensitive = _allowed(
        db,
        account_id=context.account.id,
        permission="registration.sensitive.read",
    )
    candidate_sensitive_decisions = [
        _allowed(
            db,
            account_id=context.account.id,
            permission="registration.sensitive.read",
            person_id=person_id,
        )
        for person_id in candidate_person_ids
    ]
    if globally_sensitive and not all(candidate_sensitive_decisions):
        _private_not_found()
    sensitive = globally_sensitive or (
        bool(candidate_sensitive_decisions)
        and all(candidate_sensitive_decisions)
    )
    try:
        result = service.detail(
            application_id,
            sensitive=sensitive,
            candidate_person_ids=candidate_person_ids,
        )
    except RegistrationReviewNotFound:
        _private_not_found()
    except RegistrationReviewDataUnavailable:
        _review_data_unavailable()
    _prevent_storage(response)
    return result


@router.post(
    "/applications/{application_id}/resolve",
    response_model=RegistrationReviewActionResponse,
)
def resolve_registration_conflict(
    application_id: UUID,
    input: RegistrationReviewResolveInput,
    request: Request,
    response: Response,
    context: Annotated[
        AuthContext,
        Depends(require_private_registration_reviewer),
    ],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationReviewService,
        Depends(get_registration_review_service),
    ],
) -> RegistrationReviewActionResponse:
    if any(
        not _allowed(
            db,
            account_id=context.account.id,
            permission=permission,
            person_id=input.candidate_person_id,
        )
        for permission in ("registration.review", "registration.sensitive.read")
    ):
        _private_not_found()
    try:
        result = service.resolve(
            application_id,
            candidate_person_id=input.candidate_person_id,
            actor_user_id=context.account.id,
            reason=input.reason,
            request_id=_request_id(request),
        )
    except RegistrationReviewNotFound:
        _private_not_found()
    except RegistrationReviewReasonInvalid:
        _review_reason_invalid()
    except (
        RegistrationConflictPendingReview,
        RegistrationFinalizationUnavailable,
        RegistrationReviewDataUnavailable,
    ):
        _review_data_unavailable()
    _prevent_storage(response)
    if result is None:
        return RegistrationReviewActionResponse(
            state="pending_guardian",
            next_action="complete_guardian",
            request_id=_request_id(request),
        )
    return RegistrationReviewActionResponse(
        state="finalized",
        next_action=(
            "login_or_reset" if result.match_kind == "existing_account" else "login"
        ),
        request_id=_request_id(request),
    )


@router.post(
    "/applications/{application_id}/reject",
    response_model=RegistrationReviewActionResponse,
)
def reject_registration_conflict(
    application_id: UUID,
    input: RegistrationReviewRejectInput,
    request: Request,
    response: Response,
    context: Annotated[
        AuthContext,
        Depends(require_private_registration_reviewer),
    ],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        RegistrationReviewService,
        Depends(get_registration_review_service),
    ],
) -> RegistrationReviewActionResponse:
    try:
        service.reject(
            application_id,
            actor_user_id=context.account.id,
            reason=input.reason,
            request_id=_request_id(request),
            candidate_allowed=lambda candidate_person_ids: all(
                _allowed(
                    db,
                    account_id=context.account.id,
                    permission="registration.review",
                    person_id=person_id,
                )
                for person_id in candidate_person_ids
            ),
        )
    except RegistrationReviewNotFound:
        _private_not_found()
    except RegistrationReviewDataUnavailable:
        _review_data_unavailable()
    except RegistrationReviewReasonInvalid:
        _review_reason_invalid()
    _prevent_storage(response)
    return RegistrationReviewActionResponse(
        state="rejected",
        next_action="closed",
        request_id=_request_id(request),
    )


@router.get(
    "/staff-applications",
    response_model=list[StaffApplicationAdminListItem],
)
def list_staff_identity_applications(
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        StaffIdentityApplicationService,
        Depends(get_staff_identity_application_service),
    ],
) -> list[StaffApplicationAdminListItem]:
    if not _allowed(
        db,
        account_id=context.account.id,
        permission="staff_identity_applications.review",
    ):
        _staff_review_denied()
    result = service.list_pending(
        person_allowed=lambda person_id: _allowed(
            db,
            account_id=context.account.id,
            permission="staff_identity_applications.review",
            person_id=person_id,
        )
    )
    _prevent_storage(response)
    return result


@router.get(
    "/staff-applications/{application_id}",
    response_model=StaffApplicationAdminDetail,
    response_model_exclude_none=True,
)
def staff_identity_application_detail(
    application_id: UUID,
    response: Response,
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[
        StaffIdentityApplicationService,
        Depends(get_staff_identity_application_service),
    ],
) -> StaffApplicationAdminDetail:
    try:
        person_id = service.person_id(application_id)
    except StaffApplicationNotFound:
        _staff_application_not_found()
    if not _allowed(
        db,
        account_id=context.account.id,
        permission="staff_identity_applications.review",
        person_id=person_id,
    ):
        _staff_application_not_found()
    sensitive = _allowed(
        db,
        account_id=context.account.id,
        permission="registration.sensitive.read",
        person_id=person_id,
    )
    try:
        result = service.detail(application_id, sensitive=sensitive)
    except StaffApplicationNotFound:
        _staff_application_not_found()
    except StaffApplicationDataUnavailable:
        _staff_application_data_unavailable()
    _prevent_storage(response)
    return result


@router.post(
    "/staff-applications/{application_id}/approve",
    response_model=StaffApplicationReviewResponse,
    openapi_extra=_json_request_body_openapi(StaffApplicationApproveInput),
)
async def approve_staff_identity_application(
    application_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[
        AuthContext,
        Depends(require_private_staff_application_reviewer),
    ],
    service: Annotated[
        StaffIdentityApplicationService,
        Depends(get_staff_identity_application_service),
    ],
) -> StaffApplicationReviewResponse:
    input = await _parse_staff_approve_input(request)
    try:
        result = service.approve(
            application_id,
            actor_user_id=context.account.id,
            reason=input.reason,
            request_id=_request_id(request),
        )
    except StaffApplicationNotFound:
        _staff_application_not_found()
    except StaffApplicationDataUnavailable:
        _staff_application_data_unavailable()
    except StaffReviewReasonInvalid:
        _staff_review_reason_invalid()
    _prevent_storage(response)
    return StaffApplicationReviewResponse(
        application_id=result.application_id,
        status=result.status,
        requested_staff_type=result.requested_staff_type,
        assigned_role=result.assigned_role,
        request_id=_request_id(request),
    )


@router.post(
    "/staff-applications/{application_id}/reject",
    response_model=StaffApplicationReviewResponse,
    openapi_extra=_json_request_body_openapi(StaffApplicationRejectInput),
)
async def reject_staff_identity_application(
    application_id: UUID,
    request: Request,
    response: Response,
    context: Annotated[
        AuthContext,
        Depends(require_private_staff_application_reviewer),
    ],
    service: Annotated[
        StaffIdentityApplicationService,
        Depends(get_staff_identity_application_service),
    ],
) -> StaffApplicationReviewResponse:
    input = await _parse_staff_reject_input(request)
    try:
        result = service.reject(
            application_id,
            actor_user_id=context.account.id,
            reason=input.reason,
            request_id=_request_id(request),
        )
    except StaffApplicationNotFound:
        _staff_application_not_found()
    except StaffApplicationDataUnavailable:
        _staff_application_data_unavailable()
    except StaffReviewReasonInvalid:
        _staff_review_reason_invalid()
    _prevent_storage(response)
    return StaffApplicationReviewResponse(
        application_id=result.application_id,
        status=result.status,
        requested_staff_type=result.requested_staff_type,
        assigned_role=result.assigned_role,
        request_id=_request_id(request),
    )
