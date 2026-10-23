from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person
from app.registration.models import RegistrationVerificationChallenge
from app.security.passwords import PasswordService


def test_password_recovery_api_is_generic_and_revokes_old_cookie(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    phone, old_password, _ = platform_users["credentials"]["admin"]
    account = platform_users["accounts"]["admin"]
    signed_in = db_client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": old_password},
    )
    assert signed_in.status_code == 200

    issued = db_client.post(
        "/api/v1/auth/password-reset/challenges",
        json={"identifier": phone},
    )
    assert issued.status_code == 201
    assert set(issued.json()) == {
        "challenge_id",
        "expires_at",
        "retry_after_seconds",
        "masked_destination",
        "request_id",
    }
    code = captured_sender.code_for(UUID(issued.json()["challenge_id"]))

    completed = db_client.post(
        "/api/v1/auth/password-reset/complete",
        json={
            "challenge_id": issued.json()["challenge_id"],
            "code": code,
            "new_password": "Recover2026Aa1",
        },
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert set(completed.json()) == {"status", "request_id"}
    assert db_client.get("/api/v1/auth/me").status_code == 401
    assert db_client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": old_password},
    ).status_code == 401
    assert db_client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": "Recover2026Aa1"},
    ).status_code == 200
    db.expire_all()
    stored = db.get(UserAccount, account.id)
    assert stored is not None
    assert PasswordService().verify("Recover2026Aa1", stored.password_hash)


def test_missing_password_recovery_identifier_has_same_public_shape(
    db_client,
    captured_sender,
) -> None:
    issued = db_client.post(
        "/api/v1/auth/password-reset/challenges",
        json={"identifier": "missing-account@example.com"},
    )
    assert issued.status_code == 201
    code = captured_sender.code_for(UUID(issued.json()["challenge_id"]))

    completed = db_client.post(
        "/api/v1/auth/password-reset/complete",
        json={
            "challenge_id": issued.json()["challenge_id"],
            "code": code,
            "new_password": "Recover2026Aa1",
        },
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert "missing" not in completed.text


def test_password_reset_rejects_bad_code_without_account_enumeration(
    db_client,
    platform_users,
) -> None:
    phone, _password, _ = platform_users["credentials"]["admin"]
    issued = db_client.post(
        "/api/v1/auth/password-reset/challenges",
        json={"identifier": phone},
    )

    response = db_client.post(
        "/api/v1/auth/password-reset/complete",
        json={
            "challenge_id": issued.json()["challenge_id"],
            "code": "000000",
            "new_password": "Recover2026Aa1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "verification_invalid_or_expired"
    assert phone not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


def test_password_reset_integrity_failure_uses_generic_public_error(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    phone, _password, _ = platform_users["credentials"]["admin"]
    account = platform_users["accounts"]["admin"]
    before_password_hash = account.password_hash
    issued = db_client.post(
        "/api/v1/auth/password-reset/challenges",
        json={"identifier": phone},
    )
    challenge_id = UUID(issued.json()["challenge_id"])
    person = db.get(Person, account.person_id)
    assert person is not None
    person.phone_ciphertext = "invalid-envelope-ciphertext"
    db.commit()

    response = db_client.post(
        "/api/v1/auth/password-reset/complete",
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "new_password": "Recover2026Aa1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "verification_invalid_or_expired"
    assert phone not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    stored_account = db.get(UserAccount, account.id)
    stored_challenge = db.get(
        RegistrationVerificationChallenge,
        challenge_id,
    )
    assert stored_account is not None
    assert stored_account.password_hash == before_password_hash
    assert stored_challenge is not None and stored_challenge.consumed_at is None
