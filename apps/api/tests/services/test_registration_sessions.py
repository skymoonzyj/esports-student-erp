from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.registration.models import RegistrationApplication, RegistrationSession
from app.registration.session_service import (
    InvalidRegistrationCsrf,
    InvalidRegistrationSession,
    RegistrationSessionService,
    RegistrationSessionUnavailable,
)

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)
SESSION_PEPPER = bytes(range(2, 34))


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


def _application(db: Session) -> RegistrationApplication:
    application = RegistrationApplication(
        primary_purpose="student",
        state="contact_verified",
        verified_identifier_kind="phone",
        verified_identifier_digest="a" * 64,
        verified_identifier_ciphertext="encrypted-contact",
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    return application


def _service(db: Session, *, now: datetime = NOW) -> RegistrationSessionService:
    return RegistrationSessionService(
        db,
        token_pepper=SESSION_PEPPER,
        session_ttl_minutes=480,
        clock=lambda: now,
    )


def test_registration_session_stores_only_token_and_csrf_digests(db: Session) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)
    stored = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.id == issued.context.session.id
        )
    )

    assert stored is not None
    assert issued.session_token not in stored.token_digest
    assert issued.csrf_token not in stored.csrf_digest
    assert stored.token_digest != stored.csrf_digest
    authenticated = _service(db).authenticate(issued.session_token)
    assert authenticated.application.id == application.id
    assert issued.session_token not in repr(issued)
    assert issued.csrf_token not in repr(issued)


def test_registration_session_issue_renews_application_retention_deadline(
    db: Session,
) -> None:
    application = _application(db)
    application.expires_at = NOW + timedelta(days=1)
    db.flush()

    _service(db).issue(application.id)

    db.refresh(application)
    assert application.expires_at == NOW + timedelta(days=30)


def test_registration_session_authentication_does_not_renew_application_retention_deadline(
    db: Session,
) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)
    application.expires_at = NOW + timedelta(days=1)
    db.flush()

    _service(db).authenticate(issued.session_token)

    db.refresh(application)
    assert application.expires_at == NOW + timedelta(days=1)


def test_expired_registration_session_does_not_renew_application_retention_deadline(
    db: Session,
) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)
    application.expires_at = NOW + timedelta(days=1)
    issued.context.session.expires_at = NOW
    db.flush()

    with pytest.raises(InvalidRegistrationSession):
        _service(db).authenticate(issued.session_token)

    db.refresh(application)
    assert application.expires_at == NOW + timedelta(days=1)


def test_new_registration_session_revokes_previous_session(db: Session) -> None:
    application = _application(db)
    first = _service(db).issue(application.id)
    second = _service(db).issue(application.id)

    with pytest.raises(InvalidRegistrationSession):
        _service(db).authenticate(first.session_token)
    assert _service(db).authenticate(second.session_token).application.id == application.id


def test_registration_csrf_requires_exact_current_token(db: Session) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)
    service = _service(db)

    service.verify_csrf(issued.context, issued.csrf_token)
    with pytest.raises(InvalidRegistrationCsrf):
        service.verify_csrf(issued.context, "wrong-registration-csrf")
    with pytest.raises(InvalidRegistrationCsrf):
        service.verify_csrf(issued.context, None)


def test_registration_csrf_rotation_invalidates_previous_token(db: Session) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)
    service = _service(db)

    rotated = service.rotate_csrf(issued.context)
    refreshed = service.authenticate(issued.session_token)

    assert rotated != issued.csrf_token
    service.verify_csrf(refreshed, rotated)
    with pytest.raises(InvalidRegistrationCsrf):
        service.verify_csrf(refreshed, issued.csrf_token)


def test_expired_registration_session_is_private_invalid(db: Session) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)

    with pytest.raises(InvalidRegistrationSession):
        _service(db, now=NOW + timedelta(minutes=480)).authenticate(
            issued.session_token
        )
    with pytest.raises(InvalidRegistrationSession):
        _service(db).authenticate("unknown-registration-session")


def test_session_expiry_is_checked_after_row_locks(db: Session) -> None:
    application = _application(db)
    issued = _service(db).issue(application.id)
    clock = MutableClock(NOW + timedelta(minutes=479, seconds=59))
    service = RegistrationSessionService(
        db,
        token_pepper=SESSION_PEPPER,
        session_ttl_minutes=480,
        clock=clock,
    )
    advanced = False

    def advance_after_lock_wait(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal advanced
        if not advanced and "FOR UPDATE" in statement.upper():
            advanced = True
            clock.advance(seconds=2)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", advance_after_lock_wait)
    try:
        with pytest.raises(InvalidRegistrationSession):
            service.authenticate(issued.session_token)
    finally:
        event.remove(bind, "before_cursor_execute", advance_after_lock_wait)


@pytest.mark.parametrize("state", ["finalized", "rejected", "expired"])
def test_terminal_application_cannot_issue_registration_session(
    db: Session,
    state: str,
) -> None:
    application = _application(db)
    application.state = state
    if state == "finalized":
        application.finalization_match_kind = "new_person"
    db.flush()

    with pytest.raises(RegistrationSessionUnavailable):
        _service(db).issue(application.id)


def test_unverified_draft_cannot_issue_registration_session(db: Session) -> None:
    application = _application(db)
    application.state = "draft"
    db.flush()

    with pytest.raises(RegistrationSessionUnavailable):
        _service(db).issue(application.id)
