from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.auth.schemas import AccountIdentifierChallengeRequest
from app.identity.models import Person
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.registration.models import RegistrationVerificationChallenge
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

PASSWORD = "IdentPass2026Aa!"


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _email_only_account(db: Session) -> tuple[UserAccount, Person, str]:
    email = "email-only@example.com"
    lookup = LookupDigest(bytes(range(32)))
    identity = IdentityService(
        db,
        cipher=EnvelopeCipher(bytes(range(32))),
        lookup=lookup,
    )
    summary = identity.create_base_person(
        PersonBaseInput(
            full_name="仅邮箱登录人员",
            identity_number=make_cn_id("19900102", 962),
            sex="female",
            date_of_birth=date(1990, 1, 2),
            phone=None,
            email=email,
        )
    )
    person = db.get(Person, summary.id)
    assert person is not None
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=None,
        email_normalized=None,
        password_hash=PasswordService().hash(PASSWORD),
        status="active",
        force_password_change=False,
        failed_login_count=0,
        self_service_primary_purpose="guardian",
    )
    db.add(account)
    db.flush()
    normalized = normalize_login_identifier(email)
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind="email",
            identifier_digest=login_identifier_digest(lookup, normalized),
            verified_at=datetime(2026, 8, 15, 7, tzinfo=UTC),
            verification_source="test_verified",
            is_primary=True,
        )
    )
    db.commit()
    return account, person, email


def _login(db_client, platform_users, key: str = "customer_service") -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = db_client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _identifier_write_counts(db: Session) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count(RegistrationVerificationChallenge.id))) or 0,
        db.scalar(select(func.count(AccountLoginIdentifier.id))) or 0,
        db.scalar(select(func.count(AuditEvent.id))) or 0,
    )


def test_identifier_challenge_preserves_exact_current_password() -> None:
    request = AccountIdentifierChallengeRequest(
        identifier_kind="email",
        identifier="new@example.com",
        current_password="  Identifier-password-2026  ",
    )

    assert request.current_password.get_secret_value() == "  Identifier-password-2026  "


def test_identifier_challenge_requires_current_password_without_writes(
    db_client,
    db: Session,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "customer_service")
    before = _identifier_write_counts(db)

    denied = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={"identifier_kind": "email", "identifier": "missing-password@example.com"},
    )

    assert denied.status_code == 422
    assert denied.json()["code"] == "validation_error"
    assert denied.json()["detail"] == "Request validation failed"
    assert denied.headers["Cache-Control"] == "no-store, private"
    assert _identifier_write_counts(db) == before


def test_identifier_challenge_rejects_wrong_password_without_writes(
    db_client,
    db: Session,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "customer_service")
    before = _identifier_write_counts(db)

    denied = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "wrong-password@example.com",
            "current_password": "Wrong-password-2026",
        },
    )

    assert denied.status_code == 401
    assert denied.json()["code"] == "invalid_credentials"
    assert denied.json()["detail"] == "当前密码错误"
    assert denied.headers["Cache-Control"] == "no-store, private"
    assert _identifier_write_counts(db) == before


def test_account_can_add_verified_secondary_email_and_login_with_it(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    csrf = _login(db_client, platform_users)
    account = platform_users["accounts"]["customer_service"]

    before = db_client.get("/api/v1/auth/identifiers")
    assert before.status_code == 200
    assert before.json()["items"] == [
        {
            "kind": "phone",
            "masked_identifier": "138****0002",
            "verified_at": before.json()["items"][0]["verified_at"],
        }
    ]

    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "new-cs@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert issued.status_code == 201
    code = captured_sender.code_for(UUID(issued.json()["challenge_id"]))

    verified = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": issued.json()["challenge_id"],
            "code": code,
            "identifier_kind": "email",
            "identifier": "new-cs@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )

    assert verified.status_code == 200
    assert {item["kind"] for item in verified.json()["items"]} == {"phone", "email"}
    assert next(
        item for item in verified.json()["items"] if item["kind"] == "email"
    )["masked_identifier"] == "n***@example.com"
    assert db_client.post(
        "/api/v1/auth/login",
        json={
            "identifier": "new-cs@example.com",
            "password": platform_users["credentials"]["customer_service"][1],
        },
    ).status_code == 200
    added = db.scalar(
        select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "email",
        )
    )
    assert added is not None
    assert added.is_primary is False


def test_identifier_verification_requires_the_current_password_without_consuming_code(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    csrf = _login(db_client, platform_users, "customer_service")
    account = platform_users["accounts"]["customer_service"]
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "attacker@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])
    before = _identifier_write_counts(db)

    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "email",
            "identifier": "attacker@example.com",
            "current_password": "Wrong-password-2026",
        },
    )

    assert denied.status_code == 401
    assert denied.json()["code"] == "invalid_credentials"
    assert denied.json()["detail"] == "当前密码错误"
    assert denied.headers["Cache-Control"] == "no-store, private"
    assert _identifier_write_counts(db) == before
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "email",
        )
    ) is None


def test_identifier_verification_requires_password_field_without_writes(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    csrf = _login(db_client, platform_users, "customer_service")
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "missing-verify@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])
    before = _identifier_write_counts(db)

    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "email",
            "identifier": "missing-verify@example.com",
        },
    )

    assert denied.status_code == 422
    assert denied.json()["code"] == "validation_error"
    assert denied.json()["detail"] == "Request validation failed"
    assert denied.headers["Cache-Control"] == "no-store, private"
    assert _identifier_write_counts(db) == before
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None


def test_identifier_verification_rejects_obsolete_primary_selection(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "new-admin@example.com",
            "current_password": platform_users["credentials"]["admin"][1],
        },
    )

    challenge_id = UUID(issued.json()["challenge_id"])
    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "email",
            "identifier": "new-admin@example.com",
            "current_password": platform_users["credentials"]["admin"][1],
            "make_primary": True,
        },
    )

    assert denied.status_code == 422
    assert denied.json()["code"] == "validation_error"
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id
            == platform_users["accounts"]["admin"].id,
            AccountLoginIdentifier.kind == "email",
        )
    ) is None


def test_identifier_addition_requires_session_and_csrf(
    db_client,
    platform_users,
) -> None:
    anonymous = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        json={
            "identifier_kind": "email",
            "identifier": "new@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert anonymous.status_code == 401
    assert anonymous.headers["Cache-Control"] == "no-store, private"

    _login(db_client, platform_users)
    missing_csrf = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        json={
            "identifier_kind": "email",
            "identifier": "new@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_invalid"


def test_existing_identifier_kind_is_private_unavailable(
    db_client,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users)
    response = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "13800009999",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert response.status_code == 404
    assert response.json()["code"] == "registration_action_unavailable"
    assert response.headers["Cache-Control"] == "no-store, private"


def test_taken_identifier_is_nonenumerating_until_code_verification(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    _owner_account, _owner_person, owned_email = _email_only_account(db)
    csrf = _login(db_client, platform_users, "customer_service")

    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": owned_email,
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )

    assert issued.status_code == 201
    assert set(issued.json()) == {
        "challenge_id",
        "expires_at",
        "retry_after_seconds",
        "masked_destination",
        "request_id",
    }
    challenge_id = UUID(issued.json()["challenge_id"])
    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "email",
            "identifier": owned_email,
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )

    assert denied.status_code == 404
    assert denied.json()["code"] == "registration_action_unavailable"
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id
            == platform_users["accounts"]["customer_service"].id,
            AccountLoginIdentifier.kind == "email",
        )
    ) is None


def test_identifier_challenge_rate_limit_uses_stable_rate_limit_error(
    db_client,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users)
    payload = {
        "identifier_kind": "email",
        "identifier": "rate-limited@example.com",
        "current_password": platform_users["credentials"]["customer_service"][1],
    }
    first = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert first.status_code == 201

    limited = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )

    assert limited.status_code == 429
    assert limited.json()["code"] == "verification_rate_limited"
    assert limited.headers["Cache-Control"] == "no-store, private"


def test_email_only_account_can_add_verified_phone(
    db_client,
    db: Session,
    captured_sender,
) -> None:
    account, person, email = _email_only_account(db)
    signed_in = db_client.post(
        "/api/v1/auth/login",
        json={"identifier": email, "password": PASSWORD},
    )
    assert signed_in.status_code == 200
    csrf = signed_in.json()["csrf_token"]
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "13800007777",
            "current_password": PASSWORD,
        },
    )
    assert issued.status_code == 201

    verified = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": issued.json()["challenge_id"],
            "code": captured_sender.code_for(UUID(issued.json()["challenge_id"])),
            "identifier_kind": "phone",
            "identifier": "13800007777",
            "current_password": PASSWORD,
        },
    )

    assert verified.status_code == 200
    assert {item["kind"] for item in verified.json()["items"]} == {"email", "phone"}
    phone = next(item for item in verified.json()["items"] if item["kind"] == "phone")
    assert phone["masked_identifier"] == "138****7777"
    db.expire_all()
    stored_person = db.get(Person, person.id)
    assert stored_person is not None
    assert stored_person.phone_ciphertext is not None
    assert stored_person.phone_digest == LookupDigest(bytes(range(32))).digest(
        "13800007777"
    )
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "phone",
        )
    ) is not None


def test_identifier_challenge_cannot_be_consumed_by_another_account(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    first_csrf = _login(db_client, platform_users, "customer_service")
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": first_csrf},
        json={
            "identifier_kind": "email",
            "identifier": "bound@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])
    second_csrf = _login(db_client, platform_users, "admin")

    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": second_csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "email",
            "identifier": "bound@example.com",
            "current_password": platform_users["credentials"]["admin"][1],
        },
    )

    assert denied.status_code == 404
    assert denied.json()["code"] == "registration_action_unavailable"
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id
            == platform_users["accounts"]["admin"].id,
            AccountLoginIdentifier.kind == "email",
        )
    ) is None
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None


def test_password_reset_challenge_cannot_be_consumed_as_identifier_addition(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    phone, _password, _person_key = platform_users["credentials"]["customer_service"]
    csrf = _login(db_client, platform_users, "customer_service")
    issued = db_client.post(
        "/api/v1/auth/password-reset/challenges",
        json={"identifier": phone},
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])

    wrong_purpose = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "phone",
            "identifier": phone,
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    missing = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(uuid4()),
            "code": "000000",
            "identifier_kind": "phone",
            "identifier": phone,
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )

    assert wrong_purpose.status_code == missing.status_code == 422
    assert (
        wrong_purpose.json()["code"]
        == missing.json()["code"]
        == "verification_invalid_or_expired"
    )
    assert wrong_purpose.json()["detail"] == missing.json()["detail"]
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None


def test_identifier_verify_rejects_whitespace_code_without_consuming_challenge(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    csrf = _login(db_client, platform_users, "customer_service")
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "spaces@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])

    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": f" {captured_sender.code_for(challenge_id)} ",
            "identifier_kind": "email",
            "identifier": "spaces@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )

    assert denied.status_code == 422
    assert denied.json()["code"] == "validation_error"
    assert denied.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None


def test_identifier_verify_maps_invalid_code_to_verification_error(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
) -> None:
    csrf = _login(db_client, platform_users, "customer_service")
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "wrong-code@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])
    actual_code = captured_sender.code_for(challenge_id)
    wrong_code = "000000" if actual_code != "000000" else "999999"

    denied = db_client.post(
        "/api/v1/auth/identifiers/verify",
        headers={"X-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": wrong_code,
            "identifier_kind": "email",
            "identifier": "wrong-code@example.com",
            "current_password": platform_users["credentials"]["customer_service"][1],
        },
    )

    assert denied.status_code == 422
    assert denied.json()["code"] == "verification_invalid_or_expired"
    assert denied.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.attempts == 1
    assert challenge.consumed_at is None


def test_identifier_audit_failure_rolls_back_contact_identifier_and_challenge(
    db_client,
    db: Session,
    platform_users,
    captured_sender,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = _login(db_client, platform_users, "admin")
    account = platform_users["accounts"]["admin"]
    person = db.get(Person, account.person_id)
    assert person is not None
    before_email = (
        person.email,
        person.email_ciphertext,
        person.email_digest,
    )
    issued = db_client.post(
        "/api/v1/auth/identifiers/challenges",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "rollback@example.com",
            "current_password": platform_users["credentials"]["admin"][1],
        },
    )
    assert issued.status_code == 201
    challenge_id = UUID(issued.json()["challenge_id"])

    def fail_audit(_service: AuditService, **_values: object) -> None:
        raise RuntimeError("audit persistence failed")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit persistence failed"):
        db_client.post(
            "/api/v1/auth/identifiers/verify",
            headers={"X-CSRF-Token": csrf},
            json={
                "challenge_id": str(challenge_id),
                "code": captured_sender.code_for(challenge_id),
                "identifier_kind": "email",
                "identifier": "rollback@example.com",
                "current_password": platform_users["credentials"]["admin"][1],
            },
        )

    db.expire_all()
    stored_person = db.get(Person, account.person_id)
    assert stored_person is not None
    assert (
        stored_person.email,
        stored_person.email_ciphertext,
        stored_person.email_digest,
    ) == before_email
    assert db.scalar(
        select(AccountLoginIdentifier.id).where(
            AccountLoginIdentifier.account_id == account.id,
            AccountLoginIdentifier.kind == "email",
        )
    ) is None
    challenge = db.get(RegistrationVerificationChallenge, challenge_id)
    assert challenge is not None
    assert challenge.consumed_at is None
