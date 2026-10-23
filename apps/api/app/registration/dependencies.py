from __future__ import annotations

import base64
import ipaddress
from dataclasses import dataclass, field
from typing import Annotated, cast

from fastapi import Depends, Request, Response
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.mail.settings_service import MailSettingsError, MailSettingsService
from app.mail.template_service import MailTemplateError, MailTemplateService
from app.registration.delivery import (
    CapturingVerificationSender,
    ConfiguredSmtpVerificationSender,
    TestMailboxVerificationSender,
    VerificationSender,
)
from app.registration.finalization_service import RegistrationFinalizationService
from app.registration.session_service import (
    InvalidRegistrationCsrf,
    InvalidRegistrationSession,
    RegistrationContext,
    RegistrationSessionService,
)
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationDeliveryNotAllowed,
)
from app.security.encryption import EnvelopeCipher, LookupDigest


@dataclass(frozen=True, slots=True)
class RegistrationSecretMaterial:
    verification_pepper: bytes = field(repr=False)
    session_token_pepper: bytes = field(repr=False)


REGISTRATION_COOKIE_NAME = "erp_registration_session"
REGISTRATION_COOKIE_PATH = "/api/v1/registration"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _decode_secret(secret: SecretStr) -> bytes:
    return base64.b64decode(
        secret.get_secret_value(),
        altchars=b"-_",
        validate=True,
    )


def require_registration_secret_material(
    request: Request,
) -> RegistrationSecretMaterial:
    settings: Settings = request.app.state.settings
    verification = settings.registration_verification_pepper
    session = settings.registration_session_token_pepper
    if verification is None or session is None:
        raise ProblemError(
            status=503,
            code="registration_unavailable",
            detail="注册服务暂不可用，请稍后重试",
        )
    return RegistrationSecretMaterial(
        verification_pepper=_decode_secret(verification),
        session_token_pepper=_decode_secret(session),
    )


def get_registration_verification_sender(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> VerificationSender:
    sender = getattr(request.app.state, "registration_verification_sender", None)
    settings: Settings = request.app.state.settings
    if sender is not None:
        if settings.app_env == "production" and isinstance(
            sender,
            (CapturingVerificationSender, TestMailboxVerificationSender),
        ):
            sender = None
        elif isinstance(sender, TestMailboxVerificationSender):
            client_host = request.client.host if request.client is not None else ""
            try:
                loopback_request = ipaddress.ip_address(client_host).is_loopback
            except ValueError:
                loopback_request = False
            if not loopback_request:
                sender = None
        if sender is not None:
            return cast(VerificationSender, sender)

    try:
        credentials = MailSettingsService(
            db,
            cipher=EnvelopeCipher(_decode_secret(settings.field_encryption_master_key)),
        ).credentials()
        template_service = MailTemplateService(db)
        template = template_service.verification_template()
        password_reset_template = template_service.password_reset_template()
    except (MailSettingsError, MailTemplateError) as exc:
        raise ProblemError(
            status=503,
            code="registration_unavailable",
            detail="邮件配置或模板无效，请联系管理员",
        ) from exc
    except SQLAlchemyError:
        db.rollback()
        credentials = None

    if credentials is None or not credentials.enabled:
        raise ProblemError(
            status=503,
            code="registration_unavailable",
            detail="邮件服务尚未配置，请联系管理员",
        )
    return ConfiguredSmtpVerificationSender(
        host=credentials.host,
        port=credentials.port,
        username=credentials.username,
        password=credentials.password,
        sender=credentials.sender,
        from_name=credentials.from_name,
        reply_to=credentials.reply_to,
        tls_mode=credentials.tls_mode,
        template=template,
        password_reset_template=password_reset_template,
    )

def get_registration_verification_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
    sender: Annotated[VerificationSender, Depends(get_registration_verification_sender)],
) -> RegistrationVerificationService:
    settings: Settings = request.app.state.settings
    try:
        return RegistrationVerificationService(
            db,
            lookup=LookupDigest(_decode_secret(settings.field_lookup_key)),
            verification_pepper=material.verification_pepper,
            sender=sender,
            app_env=settings.app_env,
        )
    except VerificationDeliveryNotAllowed as exc:
        raise ProblemError(
            status=503,
            code="registration_unavailable",
            detail="注册服务暂不可用，请稍后重试",
        ) from exc


def get_registration_session_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    material: Annotated[
        RegistrationSecretMaterial,
        Depends(require_registration_secret_material),
    ],
) -> RegistrationSessionService:
    settings: Settings = request.app.state.settings
    return RegistrationSessionService(
        db,
        token_pepper=material.session_token_pepper,
        session_ttl_minutes=settings.session_ttl_minutes,
    )


def get_registration_finalization_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationFinalizationService:
    settings: Settings = request.app.state.settings
    return RegistrationFinalizationService(
        db,
        cipher=EnvelopeCipher(_decode_secret(settings.field_encryption_master_key)),
        lookup=LookupDigest(_decode_secret(settings.field_lookup_key)),
    )


def _registration_cookie_values(request: Request) -> list[str]:
    values: list[str] = []
    for header_name, header_value in request.headers.raw:
        if header_name.lower() != b"cookie":
            continue
        for item in header_value.decode("latin-1").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == REGISTRATION_COOKIE_NAME:
                values.append(value)
    return values


def set_registration_session_cookie(
    response: Response,
    request: Request,
    token: str,
) -> None:
    settings: Settings = request.app.state.settings
    response.set_cookie(
        key=REGISTRATION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path=REGISTRATION_COOKIE_PATH,
    )


def delete_registration_session_cookie(
    response: Response,
    request: Request,
) -> None:
    settings: Settings = request.app.state.settings
    response.delete_cookie(
        key=REGISTRATION_COOKIE_NAME,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path=REGISTRATION_COOKIE_PATH,
    )


def require_registration_session(
    request: Request,
    service: Annotated[
        RegistrationSessionService,
        Depends(get_registration_session_service),
    ],
) -> RegistrationContext:
    tokens = _registration_cookie_values(request)
    token = tokens[0] if len(tokens) == 1 and tokens[0] else None
    try:
        context = service.authenticate(token)
        if request.method not in SAFE_METHODS:
            context = service.verify_csrf_and_renew(
                context,
                request.headers.get("X-Registration-CSRF-Token"),
            )
    except InvalidRegistrationSession as exc:
        raise ProblemError(
            status=401,
            code="registration_session_invalid",
            detail="注册会话已失效，请重新验证联系方式",
        ) from exc
    except InvalidRegistrationCsrf as exc:
        raise ProblemError(
            status=403,
            code="registration_csrf_invalid",
            detail="安全校验失败，请刷新后重试",
        ) from exc
    return context
