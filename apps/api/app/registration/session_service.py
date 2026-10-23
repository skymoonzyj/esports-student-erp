from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.transactions import write_transaction
from app.registration.cleanup_service import touch_registration_application
from app.registration.models import RegistrationApplication, RegistrationSession

_SESSION_ELIGIBLE_STATES = {
    "contact_verified",
    "identity_completed",
    "pending_guardian",
    "ready_to_finalize",
    "conflict",
}


class RegistrationSessionError(ValueError):
    code = "registration_session_error"


class InvalidRegistrationSession(RegistrationSessionError):
    code = "registration_session_invalid"


class InvalidRegistrationCsrf(RegistrationSessionError):
    code = "registration_csrf_invalid"


class RegistrationSessionUnavailable(RegistrationSessionError):
    code = "registration_action_unavailable"


@dataclass(frozen=True, slots=True)
class RegistrationContext:
    session: RegistrationSession
    application: RegistrationApplication


@dataclass(frozen=True, slots=True)
class RegistrationSessionIssued:
    session_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    context: RegistrationContext


class RegistrationSessionService:
    def __init__(
        self,
        db: Session,
        *,
        token_pepper: bytes,
        session_ttl_minutes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(token_pepper) != 32:
            raise ValueError("registration session pepper must be 32 bytes")
        if session_ttl_minutes <= 0:
            raise ValueError("registration session TTL must be positive")
        self.db = db
        self.token_pepper = token_pepper
        self.session_ttl = timedelta(minutes=session_ttl_minutes)
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _token() -> str:
        return secrets.token_urlsafe(32)

    def _digest(self, domain: str, token: str) -> str:
        return hmac.new(
            self.token_pepper,
            f"{domain}\x00{token}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("registration session clock must be timezone-aware")
        return value

    @staticmethod
    def _application_available(
        application: RegistrationApplication,
        now: datetime,
    ) -> bool:
        return (
            application.state in _SESSION_ELIGIBLE_STATES
            and application.expires_at > now
        )

    def issue(self, application_id: UUID) -> RegistrationSessionIssued:
        with write_transaction(self.db):
            return self.issue_in_transaction(application_id)

    def issue_in_transaction(
        self,
        application_id: UUID,
    ) -> RegistrationSessionIssued:
        session_token = self._token()
        csrf_token = self._token()
        application = self.db.scalar(
            select(RegistrationApplication)
            .where(RegistrationApplication.id == application_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self._now()
        if application is None or not self._application_available(application, now):
            raise RegistrationSessionUnavailable("registration session is unavailable")
        touch_registration_application(application, now=now)
        self.db.execute(
            update(RegistrationSession)
            .where(
                RegistrationSession.application_id == application.id,
                RegistrationSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        session = RegistrationSession(
            application_id=application.id,
            token_digest=self._digest("registration-session-token", session_token),
            csrf_digest=self._digest("registration-session-csrf", csrf_token),
            expires_at=min(now + self.session_ttl, application.expires_at),
        )
        self.db.add(session)
        self.db.flush()
        return RegistrationSessionIssued(
            session_token=session_token,
            csrf_token=csrf_token,
            context=RegistrationContext(session=session, application=application),
        )

    def authenticate(self, token: str | None) -> RegistrationContext:
        if not token or len(token) > 256:
            raise InvalidRegistrationSession("invalid registration session")
        token_digest = self._digest("registration-session-token", token)
        context: RegistrationContext | None = None
        with write_transaction(self.db):
            application_id = self.db.scalar(
                select(RegistrationSession.application_id).where(
                    RegistrationSession.token_digest == token_digest
                )
            )
            application = (
                self.db.scalar(
                    select(RegistrationApplication)
                    .where(RegistrationApplication.id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application_id is not None
                else None
            )
            session = (
                self.db.scalar(
                    select(RegistrationSession)
                    .where(
                        RegistrationSession.application_id == application_id,
                        RegistrationSession.token_digest == token_digest,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if application is not None
                else None
            )
            now = self._now()
            if (
                application is not None
                and session is not None
                and session.revoked_at is None
                and session.expires_at > now
                and self._application_available(application, now)
            ):
                context = RegistrationContext(session=session, application=application)
        if context is None:
            raise InvalidRegistrationSession("invalid registration session")
        return context

    def verify_csrf(self, context: RegistrationContext, token: str | None) -> None:
        if not token or len(token) > 256:
            raise InvalidRegistrationCsrf("invalid registration CSRF token")
        if not hmac.compare_digest(
            context.session.csrf_digest,
            self._digest("registration-session-csrf", token),
        ):
            raise InvalidRegistrationCsrf("invalid registration CSRF token")

    def verify_csrf_and_renew(
        self,
        context: RegistrationContext,
        token: str | None,
    ) -> RegistrationContext:
        self.verify_csrf(context, token)
        with write_transaction(self.db):
            application = self.db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == context.application.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            session = self.db.scalar(
                select(RegistrationSession)
                .where(
                    RegistrationSession.id == context.session.id,
                    RegistrationSession.application_id == context.application.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            now = self._now()
            if (
                application is None
                or session is None
                or session.revoked_at is not None
                or session.expires_at <= now
                or not self._application_available(application, now)
            ):
                raise InvalidRegistrationSession("invalid registration session")
            refreshed = RegistrationContext(session=session, application=application)
            self.verify_csrf(refreshed, token)
            touch_registration_application(application, now=now)
        return refreshed

    def rotate_csrf(self, context: RegistrationContext) -> str:
        csrf_token = self._token()
        with write_transaction(self.db):
            application = self.db.scalar(
                select(RegistrationApplication)
                .where(RegistrationApplication.id == context.application.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            session = self.db.scalar(
                select(RegistrationSession)
                .where(
                    RegistrationSession.id == context.session.id,
                    RegistrationSession.application_id == context.application.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            now = self._now()
            if (
                application is None
                or session is None
                or session.revoked_at is not None
                or session.expires_at <= now
                or not self._application_available(application, now)
            ):
                raise InvalidRegistrationSession("invalid registration session")
            touch_registration_application(application, now=now)
            session.csrf_digest = self._digest(
                "registration-session-csrf",
                csrf_token,
            )
        return csrf_token
