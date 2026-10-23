from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.auth.models import AuthSession
from app.core.config import Settings
from app.core.database import get_db
from app.core.transactions import write_transaction
from app.main import create_app
from app.registration.delivery import CapturingVerificationSender
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    RegistrationApplication,
    RegistrationSession,
    RegistrationVerificationChallenge,
)
from app.registration.session_service import RegistrationSessionService
from app.registration.verification_service import registration_advisory_lock_key
from app.revenue_share.models import ReferralCode
from app.security.encryption import EnvelopeCipher, LookupDigest


def start_registration(
    client: TestClient,
    *,
    purpose: str = "student",
    identifier_kind: str = "email",
    identifier: str = "new@example.com",
) -> UUID:
    response = client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": purpose,
            "identifier_kind": identifier_kind,
            "identifier": identifier,
        },
    )
    assert response.status_code == 201
    assert identifier not in response.text
    return UUID(response.json()["application_id"])


def send_registration_code(client: TestClient, application_id: UUID) -> UUID:
    response = client.post(f"/api/v1/registration/applications/{application_id}/challenge")
    assert response.status_code == 201
    assert "code" not in response.json()
    return UUID(response.json()["challenge_id"])


def verify_registration(
    client: TestClient,
    sender: CapturingVerificationSender,
    *,
    application_id: UUID,
    challenge_id: UUID,
):
    return client.post(
        f"/api/v1/registration/applications/{application_id}/verify",
        json={
            "challenge_id": str(challenge_id),
            "code": sender.code_for(challenge_id),
        },
    )


def _registration_persistence_snapshot(
    db: Session,
    application_id: UUID,
) -> dict[str, dict[str, object]]:
    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == application_id,
            RegistrationSession.revoked_at.is_(None),
        )
    )
    assert application is not None
    assert session is not None
    return {
        "application": {
            column.name: getattr(application, column.name)
            for column in RegistrationApplication.__table__.columns
        },
        "session": {
            column.name: getattr(session, column.name)
            for column in RegistrationSession.__table__.columns
        },
    }


def test_registration_challenge_ignores_forwarded_ip_from_untrusted_client(
    db_client: TestClient,
    db: Session,
) -> None:
    first_application_id = start_registration(
        db_client,
        identifier="untrusted-source-one@example.com",
    )
    second_application_id = start_registration(
        db_client,
        identifier="untrusted-source-two@example.com",
    )

    first = db_client.post(
        f"/api/v1/registration/applications/{first_application_id}/challenge",
        headers={"X-Real-IP": "203.0.113.10"},
    )
    second = db_client.post(
        f"/api/v1/registration/applications/{second_application_id}/challenge",
        headers={"X-Real-IP": "203.0.113.11"},
    )

    assert first.status_code == second.status_code == 201
    first_challenge = db.get(
        RegistrationVerificationChallenge,
        UUID(first.json()["challenge_id"]),
    )
    second_challenge = db.get(
        RegistrationVerificationChallenge,
        UUID(second.json()["challenge_id"]),
    )
    assert first_challenge is not None
    assert second_challenge is not None
    assert first_challenge.request_source_digest == second_challenge.request_source_digest


def test_registration_challenge_uses_forwarded_ip_from_trusted_proxy(
    db_client: TestClient,
    db: Session,
) -> None:
    db_client.app.state.settings.trusted_proxy_cidrs = "127.0.0.1/32"

    with TestClient(db_client.app, client=("127.0.0.1", 43123)) as proxy_client:
        first_application_id = start_registration(
            proxy_client,
            identifier="trusted-source-one@example.com",
        )
        second_application_id = start_registration(
            proxy_client,
            identifier="trusted-source-two@example.com",
        )
        first = proxy_client.post(
            f"/api/v1/registration/applications/{first_application_id}/challenge",
            headers={"X-Real-IP": "203.0.113.10"},
        )
        second = proxy_client.post(
            f"/api/v1/registration/applications/{second_application_id}/challenge",
            headers={"X-Real-IP": "203.0.113.11"},
        )

    assert first.status_code == second.status_code == 201
    first_challenge = db.get(
        RegistrationVerificationChallenge,
        UUID(first.json()["challenge_id"]),
    )
    second_challenge = db.get(
        RegistrationVerificationChallenge,
        UUID(second.json()["challenge_id"]),
    )
    assert first_challenge is not None
    assert second_challenge is not None
    assert first_challenge.request_source_digest != second_challenge.request_source_digest


def test_repeated_anonymous_start_rejects_normalized_active_draft_without_disclosure(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    referral_code = ReferralCode(
        code="ACTIVE-DRAFT-01",
        label="待处理申请来源",
        source_kind="person",
        referrer_person_id=platform_users["people"]["coach"].id,
        referrer_organization_id=None,
        is_active=True,
        created_by_user_id=platform_users["accounts"]["admin"].id,
    )
    db.add(referral_code)
    db.commit()

    first = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": "repeatable-start@example.com",
            "referral_code": referral_code.code,
        },
    )
    assert first.status_code == 201
    application_id = UUID(first.json()["application_id"])
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    original_deadline = application.expires_at

    second = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "staff_applicant",
            "identifier_kind": "email",
            "identifier": "  REPEATABLE-START@EXAMPLE.COM  ",
        },
    )

    assert second.status_code == 429
    assert second.json()["code"] == "registration_application_pending"
    assert second.json()["detail"] == "已有注册申请正在处理中，请继续原流程或稍后重试"
    assert 1 <= int(second.headers["Retry-After"]) <= 600
    assert "application_id" not in second.json()
    assert str(application_id) not in second.text
    assert "repeatable-start@example.com" not in second.text.casefold()
    assert referral_code.code not in second.text
    assert (
        db.scalar(
            select(func.count(RegistrationApplication.id)).where(
                RegistrationApplication.state == "draft"
            )
        )
        == 1
    )
    db.refresh(application)
    assert application.primary_purpose == "student"
    assert application.referral_code_id == referral_code.id
    assert application.expires_at == original_deadline


def test_expired_draft_allows_a_new_registration_start(
    db_client: TestClient,
    db: Session,
) -> None:
    identifier = "restart-after-expiry@example.com"
    first_id = start_registration(db_client, identifier=identifier)
    first = db.get(RegistrationApplication, first_id)
    assert first is not None
    first.expires_at = datetime.now(UTC) - timedelta(microseconds=1)
    db.commit()

    restarted = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "visitor",
            "identifier_kind": "email",
            "identifier": "  RESTART-AFTER-EXPIRY@EXAMPLE.COM ",
        },
    )

    assert restarted.status_code == 201
    assert UUID(restarted.json()["application_id"]) != first_id
    db.refresh(first)
    assert first.state == "expired"
    assert first.verified_identifier_digest is None
    assert first.verified_identifier_ciphertext is None


def test_anonymous_start_rate_limits_one_identifier_without_reserving_it(
    db_client: TestClient,
    db: Session,
) -> None:
    payload = {
        "primary_purpose": "student",
        "identifier_kind": "email",
        "identifier": "start-identifier-limit@example.com",
    }

    accepted = []
    for _ in range(5):
        response = db_client.post("/api/v1/registration/applications", json=payload)
        accepted.append(response)
        application = db.get(
            RegistrationApplication,
            UUID(response.json()["application_id"]),
        )
        assert application is not None
        application.state = "expired"
        db.commit()
    limited = db_client.post("/api/v1/registration/applications", json=payload)

    assert [response.status_code for response in accepted] == [201] * 5
    assert limited.status_code == 429
    assert limited.json()["code"] == "verification_rate_limited"
    assert (
        db.scalar(
            select(func.count(RegistrationApplication.id)).where(
                RegistrationApplication.state == "expired"
            )
        )
        == 5
    )


def test_invalid_referral_code_is_rate_limited_before_lookup(
    db_client: TestClient,
    db: Session,
) -> None:
    payload = {
        "primary_purpose": "student",
        "identifier_kind": "email",
        "identifier": "invalid-referral-limit@example.com",
        "referral_code": "NONE-AVAILABLE",
    }

    rejected = [db_client.post("/api/v1/registration/applications", json=payload) for _ in range(5)]
    limited = db_client.post("/api/v1/registration/applications", json=payload)

    assert [response.status_code for response in rejected] == [422] * 5
    assert {response.json()["code"] for response in rejected} == {"referral_code_invalid"}
    assert limited.status_code == 429
    assert limited.json()["code"] == "verification_rate_limited"
    assert db.scalar(select(func.count(RegistrationApplication.id))) == 0


def test_valid_referral_code_is_saved_on_registration_application(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    referral_code = ReferralCode(
        code="COACH-START-01",
        label="教练邀请注册",
        source_kind="person",
        referrer_person_id=platform_users["people"]["coach"].id,
        referrer_organization_id=None,
        is_active=True,
        created_by_user_id=platform_users["accounts"]["admin"].id,
    )
    db.add(referral_code)
    db.commit()

    response = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": "qr-invitee@example.com",
            "referral_code": "coach-start-01",
        },
    )

    assert response.status_code == 201
    application = db.get(
        RegistrationApplication,
        UUID(response.json()["application_id"]),
    )
    assert application is not None
    assert application.referral_code_id == referral_code.id


def test_referral_code_cannot_be_used_for_a_non_student_registration(
    db_client: TestClient,
    db: Session,
) -> None:
    response = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "staff_applicant",
            "identifier_kind": "email",
            "identifier": "invited-staff@example.com",
            "referral_code": "INV-STUDENT-ONLY",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "referral_code_not_allowed"
    assert db.scalar(select(func.count(RegistrationApplication.id))) == 0


def test_anonymous_start_limits_persistent_request_source_to_ten_per_minute(
    db_client: TestClient,
    db: Session,
) -> None:
    accepted = [
        db_client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": "email",
                "identifier": f"start-source-{index}@example.com",
            },
        )
        for index in range(10)
    ]
    limited = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": "start-source-limited@example.com",
        },
    )

    assert [response.status_code for response in accepted] == [201] * 10
    assert limited.status_code == 429
    assert limited.json()["code"] == "verification_rate_limited"
    assert (
        db.scalar(
            select(func.count(RegistrationApplication.id)).where(
                RegistrationApplication.state == "draft"
            )
        )
        == 10
    )


def test_verified_draft_expires_other_unverified_drafts_for_same_contact(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    identifier = "verified-wins@example.com"
    first_id = start_registration(db_client, identifier=identifier)
    first = db.get(RegistrationApplication, first_id)
    assert first is not None
    historical_duplicate = RegistrationApplication(
        primary_purpose="student",
        state="draft",
        verified_identifier_kind=first.verified_identifier_kind,
        verified_identifier_digest=first.verified_identifier_digest,
        verified_identifier_ciphertext=first.verified_identifier_ciphertext,
        expires_at=first.expires_at,
    )
    db.add(historical_duplicate)
    db.commit()
    second_id = historical_duplicate.id
    challenge_id = send_registration_code(db_client, second_id)

    response = verify_registration(
        db_client,
        captured_sender,
        application_id=second_id,
        challenge_id=challenge_id,
    )

    assert response.status_code == 200
    assert response.json()["primary_purpose"] == "student"
    db.expire_all()
    first = db.get(RegistrationApplication, first_id)
    second = db.get(RegistrationApplication, second_id)
    assert first is not None and first.state == "expired"
    assert first.verified_identifier_digest is None
    assert first.verified_identifier_ciphertext is None
    assert second is not None and second.state == "contact_verified"


def test_contact_owner_resumes_verified_application_despite_new_wrong_purpose(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    identifier = "resume-owner@example.com"
    existing_id = start_registration(
        db_client,
        purpose="student",
        identifier=identifier,
    )
    existing = db.get(RegistrationApplication, existing_id)
    assert existing is not None
    existing.state = "contact_verified"
    db.commit()
    before_retry = {
        column.name: getattr(existing, column.name)
        for column in RegistrationApplication.__table__.columns
    }
    retry_id = start_registration(
        db_client,
        purpose="staff_applicant",
        identifier=identifier,
    )
    db.refresh(existing)
    assert {
        column.name: getattr(existing, column.name)
        for column in RegistrationApplication.__table__.columns
    } == before_retry
    challenge_id = send_registration_code(db_client, retry_id)

    response = verify_registration(
        db_client,
        captured_sender,
        application_id=retry_id,
        challenge_id=challenge_id,
    )

    assert response.status_code == 200
    assert response.json()["state"] == "contact_verified"
    assert response.json()["primary_purpose"] == "student"
    db.expire_all()
    retry = db.get(RegistrationApplication, retry_id)
    assert retry is not None and retry.state == "expired"
    active_session_application_ids = set(
        db.scalars(
            select(RegistrationSession.application_id).where(
                RegistrationSession.revoked_at.is_(None)
            )
        )
    )
    assert active_session_application_ids == {existing_id}


def test_successful_challenge_send_keeps_original_draft_deadline_and_caps_code(
    db_client: TestClient,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="renew-on-send@example.com",
    )
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    original_deadline = application.expires_at

    response = db_client.post(f"/api/v1/registration/applications/{application_id}/challenge")

    assert response.status_code == 201
    db.refresh(application)
    assert application.expires_at == original_deadline
    assert datetime.fromisoformat(response.json()["expires_at"]) <= original_deadline


def test_successful_challenge_send_renews_verified_application_retention(
    db_client: TestClient,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="renew-verified-on-send@example.com",
    )
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    application.state = "contact_verified"
    application.expires_at = datetime.now(UTC) + timedelta(hours=1)
    db.commit()
    earliest_deadline = datetime.now(UTC) + timedelta(days=30)

    send_registration_code(db_client, application_id)

    latest_deadline = datetime.now(UTC) + timedelta(days=30)
    db.refresh(application)
    assert earliest_deadline <= application.expires_at <= latest_deadline


def test_historical_draft_cannot_verify_after_created_at_plus_ten_minutes(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="historical-draft-deadline@example.com",
    )
    challenge_id = send_registration_code(db_client, application_id)
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    application.created_at = datetime.now(UTC) - timedelta(minutes=11)
    db.commit()

    response = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_action_unavailable"
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None


@pytest.mark.parametrize("reviewed", [False, True])
def test_verification_expires_competing_application_before_promoting_lower_uuid(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    platform_users,
    reviewed: bool,
) -> None:
    identifier = f"competing-expiry-{int(reviewed)}@example.com"
    normalized = normalize_registration_identifier("email", identifier)
    existing = RegistrationApplication(
        id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
        primary_purpose="student",
        state="conflict",
        verified_identifier_kind="email",
        verified_identifier_digest=registration_identifier_digest(
            LookupDigest(bytes(range(32))),
            normalized,
        ),
        verified_identifier_ciphertext=EnvelopeCipher(bytes(range(32))).encrypt(
            normalized.value,
            field_name="registration_identifier",
        ),
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    if reviewed:
        existing.review_decision = "approved"
        existing.reviewed_by_user_id = platform_users["accounts"]["admin"].id
        existing.review_reason = "资料已人工复核"
        existing.reviewed_at = datetime.now(UTC)
    db.add(existing)
    existing.expires_at = datetime.now(UTC) + timedelta(minutes=5)
    db.commit()

    current_id = start_registration(db_client, identifier=identifier)
    current = db.get(RegistrationApplication, current_id)
    assert current is not None
    current.id = UUID("00000000-0000-4000-8000-000000000001")
    db.commit()
    current_id = current.id
    challenge_id = send_registration_code(db_client, current_id)
    existing.expires_at = datetime.now(UTC) - timedelta(microseconds=1)
    db.commit()

    previous_autoflush = db.autoflush
    db.autoflush = False
    try:
        response = verify_registration(
            db_client,
            captured_sender,
            application_id=current_id,
            challenge_id=challenge_id,
        )
    finally:
        db.autoflush = previous_autoflush

    assert response.status_code == 200
    db.refresh(existing)
    assert existing.state == "expired"
    assert existing.review_decision is None
    assert existing.reviewed_by_user_id is None
    assert existing.review_reason is None
    assert existing.reviewed_at is None


def test_rate_limited_challenge_send_does_not_renew_application_retention_deadline(
    db_client: TestClient,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="no-renew-on-rate-limit@example.com",
    )
    send_registration_code(db_client, application_id)
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    db.refresh(application)
    renewed_deadline = application.expires_at

    response = db_client.post(f"/api/v1/registration/applications/{application_id}/challenge")

    assert response.status_code == 429
    db.refresh(application)
    assert application.expires_at == renewed_deadline


def test_verified_code_creates_registration_session_only(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)

    response = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )

    assert response.status_code == 200
    assert response.cookies.get("erp_registration_session")
    assert response.cookies.get("erp_session") is None
    assert response.json()["state"] == "contact_verified"
    assert response.json()["next_action"] == "complete_profile"
    assert response.json()["csrf_token"]
    assert db.scalar(select(func.count(RegistrationSession.id))) == 1
    assert db.scalar(select(func.count(AuthSession.id))) == 0
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    assert application.state == "contact_verified"


def test_registration_cookie_is_http_only_and_path_isolated(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)

    response = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/api/v1/registration" in cookie
    assert "erp_session=" not in cookie


def test_registration_cookie_cannot_authenticate_formal_api(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    verified = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )
    assert verified.status_code == 200

    response = db_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_session"


def test_formal_cookie_cannot_authenticate_registration_api(
    db_client: TestClient,
) -> None:
    response = db_client.get(
        "/api/v1/registration/me",
        headers={"Cookie": "erp_session=formal-session-token"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "registration_session_invalid"


def test_duplicate_registration_cookies_are_rejected(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    verified = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )
    token = verified.cookies.get("erp_registration_session")
    assert token
    db_client.cookies.clear()

    response = db_client.get(
        "/api/v1/registration/me",
        headers={"Cookie": (f"erp_registration_session={token}; erp_registration_session={token}")},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "registration_session_invalid"


def test_registration_me_requires_registration_csrf_for_patch(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    verified = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )
    csrf = verified.json()["csrf_token"]

    assert db_client.get("/api/v1/registration/me").status_code == 200
    missing = db_client.patch("/api/v1/registration/me")
    wrong = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": "wrong-token"},
    )
    accepted = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
    )

    assert missing.status_code == wrong.status_code == 403
    assert missing.json()["code"] == wrong.json()["code"] == "registration_csrf_invalid"
    assert accepted.status_code == 422
    assert accepted.json()["code"] == "validation_error"


@pytest.mark.parametrize(
    "csrf_headers",
    ({}, {"X-Registration-CSRF-Token": "wrong-token"}),
    ids=("missing", "wrong"),
)
def test_rejected_registration_csrf_does_not_change_persisted_session_state(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    csrf_headers: dict[str, str],
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    application.expires_at = datetime.now(UTC) + timedelta(days=1)
    db.commit()
    before = _registration_persistence_snapshot(db, application_id)

    response = db_client.patch(
        "/api/v1/registration/me",
        headers=csrf_headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "registration_csrf_invalid"
    assert _registration_persistence_snapshot(db, application_id) == before


def test_valid_registration_csrf_renews_application_before_write_handler(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    verified = verify_registration(
        db_client,
        captured_sender,
        application_id=application_id,
        challenge_id=challenge_id,
    )
    csrf = verified.json()["csrf_token"]
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    application.expires_at = datetime.now(UTC) + timedelta(days=1)
    db.commit()
    earliest_deadline = datetime.now(UTC) + timedelta(days=30)

    response = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    latest_deadline = datetime.now(UTC) + timedelta(days=30)
    db.refresh(application)
    assert earliest_deadline <= application.expires_at <= latest_deadline


def test_challenge_and_state_responses_never_include_raw_identifier_or_digest(
    db_client: TestClient,
) -> None:
    identifier = "private@example.com"
    application_id = start_registration(db_client, identifier=identifier)

    response = db_client.post(f"/api/v1/registration/applications/{application_id}/challenge")

    assert response.status_code == 201
    assert response.json()["masked_destination"] == "p***@example.com"
    assert identifier not in response.text
    assert "identifier_digest" not in response.text
    assert "code_digest" not in response.text


def test_challenge_from_another_application_cannot_open_session(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    first_id = start_registration(db_client, identifier="first@example.com")
    second_id = start_registration(db_client, identifier="second@example.com")
    challenge_id = send_registration_code(db_client, first_id)

    response = verify_registration(
        db_client,
        captured_sender,
        application_id=second_id,
        challenge_id=challenge_id,
    )

    assert response.status_code in {404, 409}
    assert response.json()["code"] == "registration_action_unavailable"
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0
    assert db.get(RegistrationApplication, first_id).state == "draft"  # type: ignore[union-attr]
    assert db.get(RegistrationApplication, second_id).state == "draft"  # type: ignore[union-attr]
    stored_challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert stored_challenge is not None
    assert stored_challenge.consumed_at is None


def test_wrong_purpose_challenge_matches_missing_challenge_public_error(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    identifier = "wrong-purpose-oracle@example.com"
    application_id = start_registration(db_client, identifier=identifier)
    issued = db_client.post(
        "/api/v1/auth/password-reset/challenges",
        json={"identifier": identifier},
    )
    assert issued.status_code == 201
    wrong_purpose_id = UUID(issued.json()["challenge_id"])
    missing_application_id = uuid4()
    statements: dict[str, list[str]] = {
        "wrong_purpose": [],
        "missing": [],
    }
    active_case = "wrong_purpose"

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements[active_case].append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        wrong_purpose = db_client.post(
            f"/api/v1/registration/applications/{application_id}/verify",
            json={
                "challenge_id": str(wrong_purpose_id),
                "code": captured_sender.code_for(wrong_purpose_id),
            },
        )
        active_case = "missing"
        missing = db_client.post(
            f"/api/v1/registration/applications/{application_id}/verify",
            json={"challenge_id": str(uuid4()), "code": "000000"},
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    wrong_purpose_missing_application = db_client.post(
        f"/api/v1/registration/applications/{missing_application_id}/verify",
        json={
            "challenge_id": str(wrong_purpose_id),
            "code": captured_sender.code_for(wrong_purpose_id),
        },
    )
    missing_challenge_missing_application = db_client.post(
        f"/api/v1/registration/applications/{missing_application_id}/verify",
        json={"challenge_id": str(uuid4()), "code": "000000"},
    )

    assert wrong_purpose.status_code == missing.status_code == 422
    assert wrong_purpose.json()["code"] == missing.json()["code"]
    assert wrong_purpose.json()["detail"] == missing.json()["detail"]
    assert (
        wrong_purpose_missing_application.status_code
        == missing_challenge_missing_application.status_code
        == 422
    )
    assert (
        wrong_purpose_missing_application.json()["code"]
        == missing_challenge_missing_application.json()["code"]
        == "verification_invalid_or_expired"
    )
    assert all(
        not (
            "registration_applications" in statement.casefold()
            and "for update" in statement.casefold()
        )
        for case_statements in statements.values()
        for statement in case_statements
    )
    assert wrong_purpose.headers["Cache-Control"] == "no-store, private"
    assert missing.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    stored = db.get(RegistrationVerificationChallenge, wrong_purpose_id)
    application = db.get(RegistrationApplication, application_id)
    assert stored is not None and stored.consumed_at is None
    assert application is not None and application.state == "draft"
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0


@pytest.mark.parametrize(
    "invalid_state",
    ["expired", "consumed", "attempt_limit"],
)
def test_invalid_same_purpose_challenge_does_not_probe_application(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    invalid_state: str,
) -> None:
    application_id = start_registration(
        db_client,
        identifier=f"invalid-{invalid_state}-oracle@example.com",
    )
    challenge_id = send_registration_code(db_client, application_id)
    now = datetime.now(UTC)
    with write_transaction(db):
        challenge = db.get(RegistrationVerificationChallenge, challenge_id)
        assert challenge is not None
        if invalid_state == "expired":
            challenge.expires_at = now - timedelta(seconds=1)
        elif invalid_state == "consumed":
            challenge.consumed_at = now
        else:
            challenge.attempts = 5
    before = (
        challenge.consumed_at,
        challenge.expires_at,
        challenge.attempts,
    )
    statements: dict[str, list[str]] = {"active": [], "missing": []}
    active_case = "active"

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements[active_case].append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        active = verify_registration(
            db_client,
            captured_sender,
            application_id=application_id,
            challenge_id=challenge_id,
        )
        active_case = "missing"
        missing = verify_registration(
            db_client,
            captured_sender,
            application_id=uuid4(),
            challenge_id=challenge_id,
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert active.status_code == missing.status_code == 422
    assert active.json()["code"] == missing.json()["code"] == "verification_invalid_or_expired"
    assert active.json()["detail"] == missing.json()["detail"]
    assert active.headers["Cache-Control"] == "no-store, private"
    assert missing.headers["Cache-Control"] == "no-store, private"
    assert all(
        "registration_applications" not in statement.casefold()
        and "pg_advisory_xact_lock" not in statement.casefold()
        for case_statements in statements.values()
        for statement in case_statements
    )
    db.expire_all()
    stored = db.get(RegistrationVerificationChallenge, challenge_id)
    application = db.get(RegistrationApplication, application_id)
    assert stored is not None
    assert (stored.consumed_at, stored.expires_at, stored.attempts) == before
    assert application is not None and application.state == "draft"
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0


def test_wrong_code_for_another_application_does_not_mutate_challenge(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    first_id = start_registration(db_client, identifier="first-wrong@example.com")
    second_id = start_registration(db_client, identifier="second-wrong@example.com")
    challenge_id = send_registration_code(db_client, first_id)
    correct_code = captured_sender.code_for(challenge_id)
    wrong_code = "000001" if correct_code == "000000" else "000000"

    response = db_client.post(
        f"/api/v1/registration/applications/{second_id}/verify",
        json={"challenge_id": str(challenge_id), "code": wrong_code},
    )

    assert response.status_code in {404, 409}
    assert response.json()["code"] == "registration_action_unavailable"
    db.expire_all()
    stored_challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert stored_challenge is not None
    assert stored_challenge.attempts == 0
    assert stored_challenge.consumed_at is None
    assert db.get(RegistrationApplication, first_id).state == "draft"  # type: ignore[union-attr]
    assert db.get(RegistrationApplication, second_id).state == "draft"  # type: ignore[union-attr]
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0


def test_wrong_code_for_terminal_application_does_not_mutate_challenge(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="terminal-wrong@example.com",
    )
    challenge_id = send_registration_code(db_client, application_id)
    correct_code = captured_sender.code_for(challenge_id)
    wrong_code = "000001" if correct_code == "000000" else "000000"
    with write_transaction(db):
        application = db.get(RegistrationApplication, application_id)
        assert application is not None
        application.state = "rejected"

    response = db_client.post(
        f"/api/v1/registration/applications/{application_id}/verify",
        json={"challenge_id": str(challenge_id), "code": wrong_code},
    )

    assert response.status_code in {404, 409}
    assert response.json()["code"] == "registration_action_unavailable"
    db.expire_all()
    stored_challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert stored_challenge is not None
    assert stored_challenge.attempts == 0
    assert stored_challenge.consumed_at is None
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0


def test_challenge_locks_issue_limits_before_application(
    db_client: TestClient,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="lock-before-send@example.com",
    )
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    identifier_digest = application.verified_identifier_digest
    assert identifier_digest is not None
    identifier_lock_key = registration_advisory_lock_key(
        "identifier",
        identifier_digest,
    )
    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        parameters,
        _context,
        _executemany,
    ) -> None:
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        statements.append((statement, values))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        response = db_client.post(f"/api/v1/registration/applications/{application_id}/challenge")
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert response.status_code == 201
    application_lock = next(
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "REGISTRATION_APPLICATIONS" in statement.upper() and "FOR UPDATE" in statement.upper()
    )
    identifier_issue_lock = next(
        index
        for index, (statement, parameters) in enumerate(statements)
        if "PG_ADVISORY_XACT_LOCK" in statement.upper() and identifier_lock_key in parameters
    )
    assert identifier_issue_lock < application_lock


def test_verification_locks_application_before_challenge(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id = start_registration(
        db_client,
        identifier="verify-lock-order@example.com",
    )
    challenge_id = send_registration_code(db_client, application_id)
    statements: list[str] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        statements.append(statement)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        response = verify_registration(
            db_client,
            captured_sender,
            application_id=application_id,
            challenge_id=challenge_id,
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    application_lock = next(
        index
        for index, statement in enumerate(statements)
        if "REGISTRATION_APPLICATIONS" in statement.upper() and "FOR UPDATE" in statement.upper()
    )
    challenge_lock = next(
        index
        for index, statement in enumerate(statements)
        if "REGISTRATION_VERIFICATION_CHALLENGES" in statement.upper()
        and "FOR UPDATE" in statement.upper()
    )
    assert application_lock < challenge_lock


def test_wrong_verification_attempts_persist_and_close_challenge(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    correct_code = captured_sender.code_for(challenge_id)
    wrong_code = "000001" if correct_code == "000000" else "000000"

    for _ in range(5):
        response = db_client.post(
            f"/api/v1/registration/applications/{application_id}/verify",
            json={"challenge_id": str(challenge_id), "code": wrong_code},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "verification_invalid_or_expired"

    closed = db_client.post(
        f"/api/v1/registration/applications/{application_id}/verify",
        json={"challenge_id": str(challenge_id), "code": correct_code},
    )

    assert closed.status_code == 422
    stored = db.get(RegistrationVerificationChallenge, challenge_id)
    assert stored is not None
    assert stored.attempts == 5
    assert stored.consumed_at is None
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0


def test_session_creation_failure_rolls_back_verification_transaction(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_id = start_registration(db_client)
    challenge_id = send_registration_code(db_client, application_id)
    original = RegistrationSessionService.issue_in_transaction

    def fail_after_session_write(
        service: RegistrationSessionService,
        target_application_id: UUID,
    ):
        original(service, target_application_id)
        raise RuntimeError("session persistence failed")

    monkeypatch.setattr(
        RegistrationSessionService,
        "issue_in_transaction",
        fail_after_session_write,
    )

    with pytest.raises(RuntimeError, match="session persistence failed"):
        verify_registration(
            db_client,
            captured_sender,
            application_id=application_id,
            challenge_id=challenge_id,
        )

    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert application is not None
    assert challenge is not None
    assert application.state == "draft"
    assert challenge.consumed_at is None
    assert db.scalar(select(func.count(RegistrationSession.id))) == 0


def test_registration_routes_are_unavailable_without_registration_secrets(
    test_settings: Settings,
    db: Session,
) -> None:
    settings = test_settings.model_copy(
        update={
            "registration_verification_pepper": None,
            "registration_session_token_pepper": None,
        }
    )
    app = create_app(settings)

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": "email",
                "identifier": "unavailable@example.com",
            },
        )

    assert response.status_code == 503
    assert response.json()["code"] == "registration_unavailable"
    assert db.scalar(select(func.count(RegistrationApplication.id))) == 0


def test_missing_application_routes_use_non_enumerating_errors(
    db_client: TestClient,
) -> None:
    missing_application = uuid4()
    challenge = db_client.post(f"/api/v1/registration/applications/{missing_application}/challenge")
    verify = db_client.post(
        f"/api/v1/registration/applications/{missing_application}/verify",
        json={"challenge_id": str(uuid4()), "code": "000000"},
    )

    assert challenge.status_code == 404
    assert challenge.json()["code"] == "registration_action_unavailable"
    assert verify.status_code == 422
    assert verify.json()["code"] == "verification_invalid_or_expired"


def test_registration_start_rejects_extra_contact_fields(db_client: TestClient) -> None:
    response = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": "new@example.com",
            "phone": "13800138000",
        },
    )

    assert response.status_code == 422


def test_registration_start_can_temporarily_disable_phone_signup(
    db_client: TestClient,
    db: Session,
    captured_sender: CapturingVerificationSender,
) -> None:
    db_client.app.state.settings.registration_phone_signup_enabled = False
    try:
        response = db_client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": "phone",
                "identifier": "13800138000",
            },
        )
        email_response = db_client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": "email",
                "identifier": "email-still-available@example.com",
            },
        )
    finally:
        db_client.app.state.settings.registration_phone_signup_enabled = True

    assert response.status_code == 403
    assert response.json()["code"] == "registration_phone_disabled"
    assert "手机号" in response.json()["detail"]
    assert email_response.status_code == 201
    assert db.scalar(select(func.count(RegistrationApplication.id))) == 1
    assert captured_sender.messages == []


def test_public_registration_channel_settings_are_readable_and_not_cacheable(
    db_client: TestClient,
) -> None:
    response = db_client.get("/api/v1/registration/channel-settings")

    assert response.status_code == 200
    assert response.json()["phone_enabled"] is True
    assert response.json()["email_enabled"] is True
    assert response.json()["phone_signup_allowed_by_deployment"] is True
    assert response.headers["Cache-Control"] == "no-store, private"


def test_registration_admin_channel_settings_disable_both_public_channels(
    db_client: TestClient,
    db: Session,
    login_as_admin,
    captured_sender: CapturingVerificationSender,
) -> None:
    response = db_client.put(
        "/api/v1/registration-admin/channel-settings",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"phone_enabled": False, "email_enabled": False},
    )

    assert response.status_code == 200
    assert response.json()["phone_enabled"] is False
    assert response.json()["email_enabled"] is False

    phone = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "phone",
            "identifier": "13800138000",
        },
    )
    email = db_client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
            "identifier_kind": "email",
            "identifier": "disabled@example.com",
        },
    )

    assert phone.status_code == 403
    assert phone.json()["code"] == "registration_phone_disabled"
    assert email.status_code == 403
    assert email.json()["code"] == "registration_email_disabled"
    assert db.scalar(select(func.count(RegistrationApplication.id))) == 0
    assert captured_sender.messages == []


def test_registration_admin_cannot_enable_phone_when_deployment_capability_is_off(
    db_client: TestClient,
    login_as_admin,
) -> None:
    db_client.app.state.settings.registration_phone_signup_enabled = False
    try:
        response = db_client.put(
            "/api/v1/registration-admin/channel-settings",
            headers={"X-CSRF-Token": login_as_admin.csrf_token},
            json={"phone_enabled": True, "email_enabled": True},
        )
    finally:
        db_client.app.state.settings.registration_phone_signup_enabled = True

    assert response.status_code == 409
    assert response.json()["code"] == "registration_phone_deployment_disabled"


@pytest.mark.parametrize(
    ("identifier_kind", "identifier"),
    [
        ("phone", "123"),
        ("email", "not-an-email"),
    ],
)
def test_registration_start_rejects_invalid_identifier_as_validation_error(
    db_client: TestClient,
    db: Session,
    identifier_kind: str,
    identifier: str,
) -> None:
    with TestClient(db_client.app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/registration/applications",
            json={
                "primary_purpose": "student",
                "identifier_kind": identifier_kind,
                "identifier": identifier,
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert db.scalar(select(func.count(RegistrationApplication.id))) == 0


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/v1/registration/applications", None),
        ("GET", "/api/v1/registration/me", None),
        (
            "POST",
            "/api/v1/registration/applications",
            {
                "primary_purpose": "student",
                "identifier_kind": "email",
                "identifier": "private-cache@example.com",
                "extra": "rejected",
            },
        ),
        (
            "POST",
            f"/api/v1/registration/applications/{UUID(int=0)}/challenge",
            None,
        ),
    ],
)
def test_registration_error_responses_are_never_cacheable(
    db_client: TestClient,
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    response = db_client.request(method, path, json=payload)

    assert response.status_code >= 400
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"
