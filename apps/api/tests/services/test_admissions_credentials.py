from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admissions.credential_service import CredentialService
from app.admissions.errors import (
    PaymentRateLimited,
    PaymentReplayRejected,
    PaymentRequestExpired,
    PaymentSignatureInvalid,
    ServiceAccountDisabled,
    ServiceCredentialDisabled,
)
from app.admissions.models import (
    ServiceAccount,
    ServiceAccountCredential,
    ServiceAccountNonce,
    ServiceAccountRequestLimit,
)
from app.admissions.signing import canonical_request, sign_request
from app.auth.models import UserAccount
from app.core.config import Settings
from app.identity.models import Person
from app.security.encryption import EnvelopeCipher


@pytest.fixture
def clock_now() -> datetime:
    return datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def cipher() -> EnvelopeCipher:
    return EnvelopeCipher(bytes(range(32)))


@pytest.fixture
def admin_account(db: Session) -> UserAccount:
    marker = uuid4().hex
    person = Person(
        full_name="机器接口管理员",
        identity_ciphertext=f"identity-{marker}",
        identity_digest=f"identity-digest-{marker}",
        sex="female",
        date_of_birth=date(1990, 1, 1),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=f"phone-digest-{marker}",
        email_normalized=f"machine-admin-{marker}@example.test",
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def service_account(db: Session, admin_account: UserAccount) -> ServiceAccount:
    account = ServiceAccount(
        name="测试付款系统",
        source_system=f"payment-test-{uuid4().hex}",
        is_active=True,
        created_by_user_id=admin_account.id,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def credential_service(
    db: Session,
    cipher: EnvelopeCipher,
    clock_now: datetime,
) -> CredentialService:
    return CredentialService(db, cipher=cipher, clock=lambda: clock_now)


def _signed_request(
    *,
    public_id: str,
    secret: str,
    now: datetime,
    nonce: str = "nonce-1",
    body: bytes = b'{"amount":"100.00"}',
    timestamp: str | None = None,
) -> dict[str, str | bytes]:
    request_timestamp = timestamp or str(int(now.timestamp()))
    canonical = canonical_request(
        method="POST",
        path="/api/v1/payment-imports",
        timestamp=request_timestamp,
        nonce=nonce,
        body=body,
    )
    return {
        "public_id": public_id,
        "method": "POST",
        "path": "/api/v1/payment-imports",
        "timestamp": request_timestamp,
        "nonce": nonce,
        "signature": sign_request(secret=secret.encode(), canonical=canonical),
        "raw_body": body,
    }


def test_payment_security_settings_have_frozen_defaults(test_settings: Settings) -> None:
    assert test_settings.payment_signature_tolerance_seconds == 300
    assert test_settings.payment_nonce_ttl_hours == 24
    assert test_settings.payment_import_requests_per_minute == 120


def test_created_credential_secret_is_returned_once_and_stored_encrypted(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    cipher: EnvelopeCipher,
    db: Session,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
        name="首个凭证",
    )

    assert created.secret
    assert created.public_id
    row = db.get(ServiceAccountCredential, created.id)
    assert row is not None
    assert created.secret not in row.secret_ciphertext
    assert (
        cipher.decrypt(
            row.secret_ciphertext,
            field_name="service_account_credentials.secret",
        )
        == created.secret
    )
    summary = credential_service.get_credential(created.id)
    assert summary.id == created.id
    assert not hasattr(summary, "secret")
    assert not hasattr(summary, "secret_ciphertext")


def test_rotation_disables_old_credentials_and_returns_a_new_secret(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    db: Session,
) -> None:
    original = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
        name="原凭证",
    )

    rotated = credential_service.rotate_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
        name="轮换凭证",
    )

    old_row = db.get(ServiceAccountCredential, original.id)
    assert old_row is not None
    assert old_row.is_active is False
    assert old_row.disabled_at is not None
    assert old_row.disabled_by_user_id == admin_account.id
    assert rotated.id != original.id
    assert rotated.public_id != original.public_id
    assert rotated.secret != original.secret


def test_valid_request_commits_rate_and_nonce_before_returning(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
    db: Session,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    raw_body = b'{ "amount": "100.00" }'

    verified = credential_service.verify_request(
        **_signed_request(
            public_id=created.public_id,
            secret=created.secret,
            now=clock_now,
            body=raw_body,
        )
    )

    assert verified.service_account_id == service_account.id
    assert verified.credential_id == created.id
    assert verified.raw_body == raw_body
    rate = db.get(ServiceAccountRequestLimit, created.id)
    assert rate is not None
    assert rate.request_count == 1
    nonce = db.scalar(
        select(ServiceAccountNonce).where(ServiceAccountNonce.credential_id == created.id)
    )
    assert nonce is not None
    assert nonce.nonce_digest != "nonce-1"
    assert len(nonce.nonce_digest) == 64
    assert nonce.expires_at == clock_now + timedelta(hours=24)


def test_same_nonce_is_rejected_after_later_business_rollback(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
    db: Session,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    request = _signed_request(
        public_id=created.public_id,
        secret=created.secret,
        now=clock_now,
        nonce="persist-after-business-failure",
    )
    credential_service.verify_request(**request)

    db.add(
        ServiceAccount(
            name="会回滚的业务写入",
            source_system=f"rolled-back-{uuid4().hex}",
            is_active=True,
            created_by_user_id=admin_account.id,
        )
    )
    db.flush()
    db.rollback()

    with pytest.raises(PaymentReplayRejected) as error:
        credential_service.verify_request(**request)
    assert error.value.code == "payment_replay_rejected"


def test_wrong_signature_is_counted_and_121st_request_is_rate_limited(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
    db: Session,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    request = _signed_request(
        public_id=created.public_id,
        secret=created.secret,
        now=clock_now,
    )
    request["signature"] = "0" * 64

    for _ in range(120):
        with pytest.raises(PaymentSignatureInvalid):
            credential_service.verify_request(**request)
    with pytest.raises(PaymentRateLimited) as error:
        credential_service.verify_request(**request)

    assert error.value.code == "payment_rate_limited"
    rate = db.get(ServiceAccountRequestLimit, created.id)
    assert rate is not None
    assert rate.request_count == 121


@pytest.mark.parametrize("offset_seconds", [-301, 301])
def test_expired_and_future_requests_are_rejected_after_rate_count(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
    db: Session,
    offset_seconds: int,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    timestamp = str(int((clock_now + timedelta(seconds=offset_seconds)).timestamp()))

    with pytest.raises(PaymentRequestExpired) as error:
        credential_service.verify_request(
            **_signed_request(
                public_id=created.public_id,
                secret=created.secret,
                now=clock_now,
                nonce=f"nonce-{offset_seconds}",
                timestamp=timestamp,
            )
        )

    assert error.value.code == "payment_request_expired"
    rate = db.get(ServiceAccountRequestLimit, created.id)
    assert rate is not None
    assert rate.request_count == 1


def test_invalid_signature_format_has_stable_error(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    request = _signed_request(
        public_id=created.public_id,
        secret=created.secret,
        now=clock_now,
    )
    request["signature"] = "NOT-HEX"

    with pytest.raises(PaymentSignatureInvalid) as error:
        credential_service.verify_request(**request)
    assert error.value.code == "payment_signature_invalid"


def test_disabled_account_and_credential_have_distinct_errors(
    credential_service: CredentialService,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
    db: Session,
) -> None:
    created = credential_service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    request = _signed_request(
        public_id=created.public_id,
        secret=created.secret,
        now=clock_now,
    )
    credential_service.disable_credential(created.id, actor_user_id=admin_account.id)

    with pytest.raises(ServiceCredentialDisabled) as credential_error:
        credential_service.verify_request(**request)
    assert credential_error.value.code == "service_credential_disabled"

    row = db.get(ServiceAccountCredential, created.id)
    assert row is not None
    row.is_active = True
    row.disabled_at = None
    row.disabled_by_user_id = None
    service_account.is_active = False
    db.commit()

    with pytest.raises(ServiceAccountDisabled) as account_error:
        credential_service.verify_request(**request)
    assert account_error.value.code == "service_account_disabled"


def test_nonce_pruning_is_bounded(
    db: Session,
    cipher: EnvelopeCipher,
    service_account: ServiceAccount,
    admin_account: UserAccount,
    clock_now: datetime,
) -> None:
    service = CredentialService(
        db,
        cipher=cipher,
        clock=lambda: clock_now,
        nonce_prune_batch_size=2,
    )
    created = service.create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin_account.id,
    )
    for index in range(3):
        db.add(
            ServiceAccountNonce(
                credential_id=created.id,
                nonce_digest=f"{index:064x}",
                requested_at=clock_now - timedelta(hours=2),
                expires_at=clock_now - timedelta(hours=1, minutes=index),
            )
        )
    db.commit()

    service.verify_request(
        **_signed_request(
            public_id=created.public_id,
            secret=created.secret,
            now=clock_now,
            nonce="fresh-nonce",
        )
    )

    expired_count = db.scalar(
        select(func.count())
        .select_from(ServiceAccountNonce)
        .where(ServiceAccountNonce.expires_at <= clock_now)
    )
    assert expired_count == 1
