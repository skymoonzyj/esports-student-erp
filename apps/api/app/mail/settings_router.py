from __future__ import annotations

import base64
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.auth.dependencies import require_standard_account
from app.auth.service import AuthContext
from app.core.database import get_db
from app.core.errors import ProblemError
from app.mail.settings_schemas import (
    MailDeliverySettingsResponse,
    MailDeliverySettingsTestResponse,
    MailDeliverySettingsUpdate,
    MailTemplateEventKey,
    MailTemplatePreviewResponse,
    MailTemplateResponse,
    MailTemplateUpdate,
)
from app.mail.settings_service import (
    MailSettingsError,
    MailSettingsNotConfigured,
    MailSettingsService,
)
from app.mail.template_service import MailTemplateError, MailTemplateService
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher

router = APIRouter(prefix="/mail/settings", tags=["mail administration"])

_EVENT_KEY_BY_PATH: dict[str, MailTemplateEventKey] = {
    "verification-code": "verification_code",
    "password-reset": "password_reset",
    # Accept the internal key as well so early clients using the generic API
    # are not broken while the public URL remains kebab-case.
    "verification_code": "verification_code",
    "password_reset": "password_reset",
}


def _event_key(path_value: str) -> MailTemplateEventKey:
    try:
        return _EVENT_KEY_BY_PATH[path_value]
    except KeyError as exc:
        raise ProblemError(status=422, code="mail_template_event_invalid", detail="不支持的邮件事件") from exc


def _secret_bytes(request: Request) -> bytes:
    value: SecretStr = request.app.state.settings.field_encryption_master_key
    return base64.b64decode(value.get_secret_value(), altchars=b"-_", validate=True)


def _require_global_mail_manager(
    context: Annotated[AuthContext, Depends(require_standard_account)],
    db: Annotated[Session, Depends(get_db)],
) -> AuthContext:
    if (
        not RbacService(db)
        .decide(
            user_id=context.account.id,
            permission="mail.settings.manage",
            resource_tokens={"global"},
        )
        .allowed
    ):
        raise ProblemError(status=403, code="permission_denied", detail="没有执行此操作的权限")
    return context


def _service(request: Request, db: Session) -> MailSettingsService:
    return MailSettingsService(db, cipher=EnvelopeCipher(_secret_bytes(request)))


def _template_service(db: Session) -> MailTemplateService:
    return MailTemplateService(db)


@router.get("", response_model=MailDeliverySettingsResponse)
def get_mail_settings(
    response: Response,
    _: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MailDeliverySettingsResponse:
    response.headers["Cache-Control"] = "no-store, private"
    return _service(request, db).public()


@router.put("", response_model=MailDeliverySettingsResponse)
def save_mail_settings(
    input: MailDeliverySettingsUpdate,
    response: Response,
    context: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MailDeliverySettingsResponse:
    try:
        result = _service(request, db).save(
            input,
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except MailSettingsError as exc:
        raise ProblemError(status=422, code=exc.code, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store, private"
    return result


@router.post("/test", response_model=MailDeliverySettingsTestResponse)
def test_mail_settings(
    response: Response,
    context: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MailDeliverySettingsTestResponse:
    try:
        status = _service(request, db).test_connection(
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except MailSettingsNotConfigured as exc:
        raise ProblemError(status=409, code=exc.code, detail="请先保存邮件配置") from exc
    except MailSettingsError as exc:
        raise ProblemError(status=422, code=exc.code, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store, private"
    return MailDeliverySettingsTestResponse(
        status=status,
        request_id=str(request.state.request_id),
    )


@router.get("/templates/{event_key}", response_model=MailTemplateResponse)
def get_mail_template(
    event_key: str,
    response: Response,
    _: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> MailTemplateResponse:
    response.headers["Cache-Control"] = "no-store, private"
    try:
        return _template_service(db).public(_event_key(event_key))
    except MailTemplateError as exc:
        raise ProblemError(status=422, code=exc.code, detail=str(exc)) from exc


@router.put("/templates/{event_key}", response_model=MailTemplateResponse)
def save_mail_template(
    event_key: str,
    input: MailTemplateUpdate,
    response: Response,
    context: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MailTemplateResponse:
    try:
        result = _template_service(db).save(
            input,
            event_key=_event_key(event_key),
            actor_user_id=context.account.id,
            request_id=str(request.state.request_id),
        )
    except MailTemplateError as exc:
        raise ProblemError(status=422, code=exc.code, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store, private"
    return result


@router.delete("/templates/{event_key}", response_model=MailTemplateResponse)
def restore_default_mail_template(
    event_key: str,
    response: Response,
    context: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> MailTemplateResponse:
    response.headers["Cache-Control"] = "no-store, private"
    return _template_service(db).restore_default(
        event_key=_event_key(event_key),
        actor_user_id=context.account.id,
        request_id=str(request.state.request_id),
    )


@router.post("/templates/{event_key}/preview", response_model=MailTemplatePreviewResponse)
def preview_mail_template(
    event_key: str,
    input: MailTemplateUpdate,
    response: Response,
    _: Annotated[AuthContext, Depends(_require_global_mail_manager)],
    db: Annotated[Session, Depends(get_db)],
) -> MailTemplatePreviewResponse:
    response.headers["Cache-Control"] = "no-store, private"
    try:
        return _template_service(db).preview(input, _event_key(event_key))
    except MailTemplateError as exc:
        raise ProblemError(status=422, code=exc.code, detail=str(exc)) from exc
