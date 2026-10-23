from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.admissions import payment_service as payment_service_module
from app.admissions.credential_service import CredentialService
from app.admissions.models import (
    Payment,
    PaymentConflict,
    PaymentImportEvent,
    ServiceAccount,
)
from app.admissions.signing import canonical_request, sign_request
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import UserAccount
from app.identity.models import Person, StudentProfile
from app.security.encryption import EnvelopeCipher, LookupDigest

LOOKUP_KEY = bytes(range(32))


@pytest.fixture
def payment_import_api(db: Session) -> dict[str, object]:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(LOOKUP_KEY)

    def add_person(name: str, phone: str) -> tuple[Person, UserAccount]:
        marker = uuid4().hex
        email = f"{marker}@example.test"
        person = Person(
            full_name=name,
            identity_ciphertext=f"identity-{marker}",
            identity_digest=f"identity-digest-{marker}",
            phone_ciphertext=cipher.encrypt(phone, field_name="people.phone"),
            phone_digest=lookup.digest(phone),
            email_ciphertext=cipher.encrypt(email, field_name="email"),
            email_digest=lookup.digest_domain("person-email", email),
            sex="female",
            date_of_birth=date(1990, 1, 1),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=lookup.digest(phone),
            email_normalized=None,
            password_hash="test-password-hash",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        return person, account

    admin, admin_account = add_person("接口管理员", "13820000001")
    del admin
    student, _ = add_person("接口付款学员", "13820000002")
    db.add(
        StudentProfile(
            person_id=student.id,
            student_number=f"API-PAY-{uuid4().hex[:10]}",
            status="active",
        )
    )
    account = ServiceAccount(
        name="接口测试付款平台",
        source_system=f"api-provider-{uuid4().hex}",
        is_active=True,
        created_by_user_id=admin_account.id,
    )
    db.add(account)
    db.commit()
    credential = CredentialService(db, cipher=cipher).create_credential(
        service_account_id=account.id,
        actor_user_id=admin_account.id,
    )
    return {
        "service_account": account,
        "credential": credential,
        "student": student,
        "phone": "13820000002",
    }


def _payload(context: dict[str, object], **changes: object) -> dict[str, object]:
    account = context["service_account"]
    values: dict[str, object] = {
        "source_system": account.source_system,  # type: ignore[union-attr]
        "external_order_id": "API-ORDER-001",
        "idempotency_key": "API-IDEMPOTENCY-001",
        "payer_phone": context["phone"],
        "amount": "100.00",
        "currency": "CNY",
        "paid_at": "2026-08-01T09:30:00+00:00",
        "raw_payload": {"channel": "api-test", "sequence": 1},
    }
    values.update(changes)
    return values


def _signed_headers(
    context: dict[str, object],
    body: bytes,
    *,
    nonce: str,
    timestamp: datetime | None = None,
    signature: str | None = None,
) -> dict[str, str]:
    credential = context["credential"]
    timestamp_text = str(int((timestamp or datetime.now(UTC)).timestamp()))
    canonical = canonical_request(
        method="POST",
        path="/api/v1/payment-imports",
        timestamp=timestamp_text,
        nonce=nonce,
        body=body,
    )
    return {
        "Content-Type": "application/json",
        "X-Service-Account": credential.public_id,  # type: ignore[union-attr]
        "X-Timestamp": timestamp_text,
        "X-Nonce": nonce,
        "X-Signature": signature
        or sign_request(
            secret=credential.secret.encode(),  # type: ignore[union-attr]
            canonical=canonical,
        ),
    }


def _post(
    client: TestClient,
    context: dict[str, object],
    payload: dict[str, object],
    *,
    nonce: str,
    timestamp: datetime | None = None,
    signature: str | None = None,
):
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    return client.post(
        "/api/v1/payment-imports",
        content=body,
        headers=_signed_headers(
            context,
            body,
            nonce=nonce,
            timestamp=timestamp,
            signature=signature,
        ),
    )


def test_machine_import_returns_201_then_business_idempotent_200(
    db_client: TestClient,
    payment_import_api: dict[str, object],
    db: Session,
) -> None:
    payload = _payload(payment_import_api)

    first = _post(db_client, payment_import_api, payload, nonce="api-nonce-1")
    second = _post(db_client, payment_import_api, payload, nonce="api-nonce-2")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["payment_id"] == first.json()["payment_id"]
    assert "payer_phone" not in first.text
    assert "raw_payload" not in first.text
    assert first.headers["cache-control"] == "no-store, private"
    audits = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "admissions.payment_import.process")
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    expected_metadata = {
        "service_account_id": str(payment_import_api["service_account"].id),  # type: ignore[union-attr]
        "credential_id": str(payment_import_api["credential"].id),  # type: ignore[union-attr]
        "import_event_id": first.json()["import_event_id"],
        "payment_id": first.json()["payment_id"],
    }
    assert len(audits) == 2
    assert all(audit.actor_user_id is None for audit in audits)
    assert all(audit.resource_type == "payment_import_event" for audit in audits)
    assert all(audit.resource_id == audits[0].resource_id for audit in audits)
    assert all(audit.outcome == "allowed" for audit in audits)
    assert all(audit.event_metadata == expected_metadata for audit in audits)


def test_changed_payload_returns_persisted_conflict(
    db_client: TestClient,
    payment_import_api: dict[str, object],
    db: Session,
) -> None:
    first = _post(
        db_client,
        payment_import_api,
        _payload(payment_import_api),
        nonce="api-conflict-1",
    )
    conflict = _post(
        db_client,
        payment_import_api,
        _payload(payment_import_api, amount="101.00"),
        nonce="api-conflict-2",
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "payment_import_conflict"
    stored_conflict = db.scalar(select(PaymentConflict))
    assert stored_conflict is not None
    failed_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "admissions.payment_import.process",
            AuditEvent.outcome == "failed",
        )
    )
    assert failed_audit is not None
    assert failed_audit.actor_user_id is None
    assert failed_audit.resource_type == "payment_conflict"
    assert failed_audit.resource_id == stored_conflict.id
    assert failed_audit.event_metadata == {
        "service_account_id": str(payment_import_api["service_account"].id),  # type: ignore[union-attr]
        "credential_id": str(payment_import_api["credential"].id),  # type: ignore[union-attr]
        "payment_conflict_id": str(stored_conflict.id),
        "import_event_id": str(stored_conflict.original_import_event_id),
        "payment_id": first.json()["payment_id"],
    }


def test_machine_import_audit_failure_rolls_back_business_rows(
    db_client: TestClient,
    payment_import_api: dict[str, object],
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_audit(*args, **kwargs) -> None:
        del args, kwargs
        raise AuditMetadataRejected("simulated machine import audit failure")

    monkeypatch.setattr(payment_service_module.AuditService, "append", reject_audit)

    response = _post(
        db_client,
        payment_import_api,
        _payload(payment_import_api),
        nonce="api-audit-failure",
    )

    assert response.status_code == 500
    assert response.json()["code"] == "admissions_audit_failed"
    assert db.scalar(select(PaymentImportEvent.id)) is None
    assert db.scalar(select(Payment.id).where(Payment.source_kind == "api")) is None


def test_bad_signature_expired_request_and_reused_nonce_are_stable_errors(
    db_client: TestClient,
    payment_import_api: dict[str, object],
) -> None:
    payload = _payload(payment_import_api)
    bad_signature = _post(
        db_client,
        payment_import_api,
        payload,
        nonce="api-bad-signature",
        signature="0" * 64,
    )
    expired = _post(
        db_client,
        payment_import_api,
        payload,
        nonce="api-expired",
        timestamp=datetime.now(UTC) - timedelta(seconds=301),
    )
    accepted = _post(
        db_client,
        payment_import_api,
        payload,
        nonce="api-replay",
    )
    replay = _post(
        db_client,
        payment_import_api,
        payload,
        nonce="api-replay",
    )

    assert bad_signature.status_code == 401
    assert bad_signature.json()["code"] == "payment_signature_invalid"
    assert expired.status_code == 401
    assert expired.json()["code"] == "payment_request_expired"
    assert accepted.status_code == 201
    assert replay.status_code == 409
    assert replay.json()["code"] == "payment_replay_rejected"


def test_validly_signed_invalid_business_body_consumes_nonce(
    db_client: TestClient,
    payment_import_api: dict[str, object],
) -> None:
    invalid = _post(
        db_client,
        payment_import_api,
        _payload(payment_import_api, currency="USD"),
        nonce="api-invalid-business",
    )
    replay = _post(
        db_client,
        payment_import_api,
        _payload(payment_import_api),
        nonce="api-invalid-business",
    )

    assert invalid.status_code == 422
    assert replay.status_code == 409
    assert replay.json()["code"] == "payment_replay_rejected"


def test_machine_import_throttle_overflow_returns_429(
    db_client: TestClient,
    payment_import_api: dict[str, object],
) -> None:
    payload = _payload(payment_import_api)
    for index in range(120):
        response = _post(
            db_client,
            payment_import_api,
            payload,
            nonce=f"api-throttle-{index}",
            signature="0" * 64,
        )
        assert response.status_code == 401

    overflow = _post(
        db_client,
        payment_import_api,
        payload,
        nonce="api-throttle-overflow",
        signature="0" * 64,
    )
    assert overflow.status_code == 429
    assert overflow.json()["code"] == "payment_rate_limited"
