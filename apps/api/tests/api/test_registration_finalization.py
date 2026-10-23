from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.core.config import Settings
from app.identity.models import StudentProfile
from app.registration.consents import registration_terms_digest
from app.registration.dependencies import get_registration_session_service
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationSession,
)
from app.registration.session_service import RegistrationSessionService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime.now(UTC)
REQUEST_ID = "00000000-0000-4000-8000-000000000701"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _secret_bytes(value) -> bytes:
    return base64.b64decode(
        value.get_secret_value(),
        altchars=b"-_",
        validate=True,
    )


def _application(
    db: Session,
    settings: Settings,
    *,
    identity_number: str,
    phone: str,
    state: str,
) -> RegistrationApplication:
    cipher = EnvelopeCipher(_secret_bytes(settings.field_encryption_master_key))
    lookup = LookupDigest(_secret_bytes(settings.field_lookup_key))
    identifier = normalize_registration_identifier("phone", phone)
    application = RegistrationApplication(
        primary_purpose="student",
        state=state,
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(lookup, identifier),
        verified_identifier_ciphertext=cipher.encrypt(
            identifier.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Strong701PassAa"),
        full_name="API 注册学员",
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="registration_identity",
        ),
        identity_digest=lookup.digest(identity_number),
        sex="male",
        date_of_birth=date.fromisoformat(
            f"{identity_number[6:10]}-{identity_number[10:12]}-{identity_number[12:14]}"
        ),
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    terms_digest = registration_terms_digest("2026-08")
    assert terms_digest is not None
    db.add(
        RegistrationConsentAcceptance(
            registration_application_id=application.id,
            account_id=None,
            consent_type="registration_terms",
            policy_version="2026-08",
            content_digest=terms_digest,
            accepted_at=NOW,
            request_id=REQUEST_ID,
            source="self_registration",
        )
    )
    db.commit()
    return application


def _session(
    db: Session,
    settings: Settings,
    application_id,
) -> tuple[str, str]:
    issued = RegistrationSessionService(
        db,
        token_pepper=_secret_bytes(settings.registration_session_token_pepper),
        session_ttl_minutes=settings.session_ttl_minutes,
        clock=lambda: NOW,
    ).issue(application_id)
    return issued.session_token, issued.csrf_token


def _freeze_registration_session_clock(
    client: TestClient,
    db: Session,
    settings: Settings,
) -> None:
    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_registration_session_service] = lambda: (
        RegistrationSessionService(
            db,
            token_pepper=_secret_bytes(settings.registration_session_token_pepper),
            session_ttl_minutes=settings.session_ttl_minutes,
            clock=lambda: NOW,
        )
    )


def test_ready_application_finalizes_without_opening_formal_session(
    db_client: TestClient,
    db: Session,
    test_settings: Settings,
) -> None:
    application = _application(
        db,
        test_settings,
        identity_number=make_cn_id("19900102", 701),
        phone="13800007001",
        state="ready_to_finalize",
    )
    token, csrf = _session(db, test_settings, application.id)
    _freeze_registration_session_clock(db_client, db, test_settings)

    response = db_client.post(
        "/api/v1/registration/me/finalize",
        headers={
            "Cookie": f"erp_registration_session={token}",
            "X-Registration-CSRF-Token": csrf,
            "X-Request-ID": REQUEST_ID,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "state": "finalized",
        "next_action": "login",
        "request_id": REQUEST_ID,
    }
    assert "person_id" not in response.text
    assert "account_id" not in response.text
    assert "erp_session=" not in response.headers.get("set-cookie", "")
    assert "erp_registration_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    db.expire_all()
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None and stored.state == "finalized"
    assert db.scalar(select(func.count(UserAccount.id))) == 1
    assert db.scalar(select(func.count(StudentProfile.person_id))) == 1
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application.id
        )
    )
    assert session is not None and session.revoked_at is not None


def test_minor_application_returns_guardian_confirmation_without_account(
    db_client: TestClient,
    db: Session,
    test_settings: Settings,
) -> None:
    application = _application(
        db,
        test_settings,
        identity_number=make_cn_id("20120102", 703),
        phone="13800007003",
        state="identity_completed",
    )
    token, csrf = _session(db, test_settings, application.id)
    _freeze_registration_session_clock(db_client, db, test_settings)

    response = db_client.post(
        "/api/v1/registration/me/finalize",
        headers={
            "Cookie": f"erp_registration_session={token}",
            "X-Registration-CSRF-Token": csrf,
            "X-Request-ID": REQUEST_ID,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "state": "pending_guardian",
        "next_action": "complete_guardian",
        "request_id": REQUEST_ID,
    }
    assert db.scalar(select(func.count(UserAccount.id))) == 0
    stored = db.get(RegistrationApplication, application.id)
    assert stored is not None and stored.state == "pending_guardian"
