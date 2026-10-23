from __future__ import annotations

import base64
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth.mailer import (
    ConfiguredSmtpEmailSender,
    DisabledEmailSender,
    LoggingEmailSender,
    SmtpEmailSender,
)
from app.auth.registration import RegistrationService
from app.auth.service import (
    AuthContext,
    AuthService,
    InvalidCsrfToken,
    InvalidSession,
)
from app.core.config import Settings
from app.core.database import get_db
from app.core.errors import ProblemError
from app.mail.settings_service import MailSettingsError, MailSettingsService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

SESSION_COOKIE_NAME = "erp_session"
SESSION_COOKIE_PATH = "/api"
LEGACY_SESSION_COOKIE_PATH = "/"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@lru_cache
def get_password_service() -> PasswordService:
    return PasswordService()


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    secret = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(secret, altchars=b"-_", validate=True)


def get_auth_service(request: Request, db: Annotated[Session, Depends(get_db)]) -> AuthService:
    settings: Settings = request.app.state.settings
    return AuthService(
        db,
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
        passwords=get_password_service(),
        session_token_pepper=_secret_bytes(settings, "session_token_pepper"),
        session_ttl_minutes=settings.session_ttl_minutes,
    )


def get_registration_service(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> RegistrationService:
    settings: Settings = request.app.state.settings
    smtp_password = (
        settings.registration_smtp_password.get_secret_value()
        if settings.registration_smtp_password is not None
        else None
    )
    fallback_sender = (
        SmtpEmailSender(
            host=settings.registration_smtp_host,
            port=settings.registration_smtp_port,
            sender=settings.registration_smtp_sender,
            username=settings.registration_smtp_username,
            password=smtp_password,
            use_tls=settings.registration_smtp_use_tls,
        )
        if settings.registration_smtp_host
        else DisabledEmailSender()
        if settings.app_env == "production"
        else LoggingEmailSender()
    )
    sender = fallback_sender
    try:
        configured = MailSettingsService(
            db,
            cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        ).credentials()
    except MailSettingsError:
        # A corrupted administrator configuration must fail closed rather than
        # silently falling back to an unrelated SMTP account.
        sender = DisabledEmailSender()
    except SQLAlchemyError:
        # Keep old development databases usable until migration 0013 is applied.
        db.rollback()
    else:
        if configured is not None:
            sender = (
                ConfiguredSmtpEmailSender(
                    host=configured.host,
                    port=configured.port,
                    sender=configured.sender,
                    username=configured.username,
                    password=configured.password,
                    tls_mode=configured.tls_mode,
                    from_name=configured.from_name,
                    reply_to=configured.reply_to,
                )
                if configured.enabled
                else DisabledEmailSender()
            )
    return RegistrationService(
        db,
        cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
        lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
        passwords=get_password_service(),
        session_token_pepper=_secret_bytes(settings, "session_token_pepper"),
        email_sender=sender,
        code_ttl=timedelta(minutes=settings.registration_code_ttl_minutes),
        resend_interval=timedelta(seconds=settings.registration_code_resend_interval_seconds),
    )


def _session_cookie_values(request: Request) -> list[str]:
    values: list[str] = []
    for header_name, header_value in request.headers.raw:
        if header_name.lower() != b"cookie":
            continue
        for item in header_value.decode("latin-1").split(";"):
            name, separator, value = item.strip().partition("=")
            if separator and name == SESSION_COOKIE_NAME:
                values.append(value)
    return values


def expire_legacy_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path=LEGACY_SESSION_COOKIE_PATH,
        secure=request.app.state.settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def set_api_session_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path=SESSION_COOKIE_PATH,
    )


def require_session(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthContext:
    tokens = _session_cookie_values(request)
    token = next((value for value in tokens if value), None)
    try:
        context = service.authenticate(
            token,
            request_id=str(request.state.request_id),
        )
    except InvalidSession as exc:
        raise ProblemError(
            status=401,
            code="invalid_session",
            detail="登录已失效，请重新登录",
        ) from exc
    assert token is not None
    if len(tokens) == 1:
        set_api_session_cookie(response, request, token)
    expire_legacy_session_cookie(response, request)
    if request.method not in SAFE_METHODS:
        try:
            service.verify_csrf(context, request.headers.get("X-CSRF-Token"))
        except InvalidCsrfToken as exc:
            raise ProblemError(
                status=403,
                code="csrf_invalid",
                detail="安全校验失败，请刷新后重试",
            ) from exc
    return context


def require_standard_account(
    context: Annotated[AuthContext, Depends(require_session)],
) -> AuthContext:
    if context.account.force_password_change:
        raise ProblemError(
            status=403,
            code="force_password_change_required",
            detail="请先修改临时密码",
        )
    return context
