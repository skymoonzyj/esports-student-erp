from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admissions.errors import (
    DuplicateServiceAccount,
    PaymentRateLimited,
    PaymentReplayRejected,
    PaymentRequestExpired,
    PaymentSignatureInvalid,
    ServiceAccountDisabled,
    ServiceAccountNotFound,
    ServiceCredentialDisabled,
    ServiceCredentialNotFound,
)
from app.admissions.models import (
    ServiceAccount,
    ServiceAccountCredential,
    ServiceAccountNonce,
    ServiceAccountRequestLimit,
)
from app.admissions.signing import (
    canonical_request,
    request_timestamp_is_current,
    verify_signature,
)
from app.core.transactions import write_transaction
from app.security.encryption import EnvelopeCipher

_SECRET_FIELD_NAME = "service_account_credentials.secret"


@dataclass(frozen=True)
class CreatedCredential:
    id: UUID
    service_account_id: UUID
    public_id: str
    name: str
    secret: str


@dataclass(frozen=True)
class CredentialSummary:
    id: UUID
    service_account_id: UUID
    public_id: str
    name: str
    is_active: bool
    disabled_at: datetime | None


@dataclass(frozen=True)
class VerifiedMachineRequest:
    service_account_id: UUID
    credential_id: UUID
    raw_body: bytes


class CredentialService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        signature_tolerance_seconds: int = 300,
        nonce_ttl_hours: int = 24,
        requests_per_minute: int = 120,
        nonce_prune_batch_size: int = 500,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if min(
            signature_tolerance_seconds,
            nonce_ttl_hours,
            requests_per_minute,
            nonce_prune_batch_size,
        ) <= 0:
            raise ValueError("credential service limits must be positive")
        self.db = db
        self.cipher = cipher
        self.signature_tolerance_seconds = signature_tolerance_seconds
        self.nonce_ttl = timedelta(hours=nonce_ttl_hours)
        self.requests_per_minute = requests_per_minute
        self.nonce_prune_batch_size = nonce_prune_batch_size
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        if now.utcoffset() is None:
            raise ValueError("credential service clock must be timezone-aware")
        return now

    @staticmethod
    def _summary(row: ServiceAccountCredential) -> CredentialSummary:
        return CredentialSummary(
            id=row.id,
            service_account_id=row.service_account_id,
            public_id=row.public_id,
            name=row.name,
            is_active=row.is_active,
            disabled_at=row.disabled_at,
        )

    def _require_account(self, service_account_id: UUID) -> ServiceAccount:
        account = self.db.get(ServiceAccount, service_account_id)
        if account is None:
            raise ServiceAccountNotFound("service account was not found")
        if not account.is_active:
            raise ServiceAccountDisabled("service account is disabled")
        return account

    def create_service_account(
        self,
        *,
        name: str,
        source_system: str,
        actor_user_id: UUID,
    ) -> ServiceAccount:
        normalized_name = name.strip()
        normalized_source = source_system.strip()
        if not normalized_name or not normalized_source:
            raise ValueError("service account name and source are required")
        try:
            with write_transaction(self.db):
                account = ServiceAccount(
                    name=normalized_name,
                    source_system=normalized_source,
                    is_active=True,
                    created_by_user_id=actor_user_id,
                )
                self.db.add(account)
                self.db.flush()
            return account
        except IntegrityError as exc:
            raise DuplicateServiceAccount("service account source already exists") from exc

    def list_service_accounts(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[list[ServiceAccount], int]:
        if page < 1 or page_size < 1:
            raise ValueError("page and page size must be positive")
        total = self.db.scalar(select(func.count()).select_from(ServiceAccount)) or 0
        accounts = list(
            self.db.scalars(
                select(ServiceAccount)
                .order_by(ServiceAccount.created_at.desc(), ServiceAccount.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return accounts, total

    def list_credentials(self, service_account_id: UUID) -> list[CredentialSummary]:
        return [
            self._summary(row)
            for row in self.db.scalars(
                select(ServiceAccountCredential)
                .where(
                    ServiceAccountCredential.service_account_id == service_account_id
                )
                .order_by(
                    ServiceAccountCredential.created_at.desc(),
                    ServiceAccountCredential.id.desc(),
                )
            )
        ]

    def disable_service_account(
        self,
        service_account_id: UUID,
        *,
        actor_user_id: UUID,
    ) -> None:
        with write_transaction(self.db):
            account = self.db.scalar(
                select(ServiceAccount)
                .where(ServiceAccount.id == service_account_id)
                .with_for_update()
            )
            if account is None:
                raise ServiceAccountNotFound("service account was not found")
            if account.is_active:
                account.is_active = False
                account.updated_by_user_id = actor_user_id

    def _new_credential(
        self,
        *,
        service_account_id: UUID,
        actor_user_id: UUID,
        name: str,
    ) -> tuple[ServiceAccountCredential, str]:
        secret = secrets.token_urlsafe(32)
        row = ServiceAccountCredential(
            service_account_id=service_account_id,
            public_id=f"pay_{secrets.token_urlsafe(18)}",
            name=name,
            secret_ciphertext=self.cipher.encrypt(secret, field_name=_SECRET_FIELD_NAME),
            is_active=True,
            created_by_user_id=actor_user_id,
        )
        self.db.add(row)
        self.db.flush()
        return row, secret

    def create_credential(
        self,
        *,
        service_account_id: UUID,
        actor_user_id: UUID,
        name: str = "默认凭证",
    ) -> CreatedCredential:
        if not name.strip():
            raise ValueError("credential name is required")
        with write_transaction(self.db):
            self._require_account(service_account_id)
            row, secret = self._new_credential(
                service_account_id=service_account_id,
                actor_user_id=actor_user_id,
                name=name.strip(),
            )
            created = CreatedCredential(
                id=row.id,
                service_account_id=row.service_account_id,
                public_id=row.public_id,
                name=row.name,
                secret=secret,
            )
        return created

    def rotate_credential(
        self,
        *,
        service_account_id: UUID,
        actor_user_id: UUID,
        name: str = "轮换凭证",
    ) -> CreatedCredential:
        if not name.strip():
            raise ValueError("credential name is required")
        now = self._now()
        with write_transaction(self.db):
            self._require_account(service_account_id)
            existing = self.db.scalars(
                select(ServiceAccountCredential)
                .where(
                    ServiceAccountCredential.service_account_id == service_account_id,
                    ServiceAccountCredential.is_active.is_(True),
                )
                .order_by(ServiceAccountCredential.id)
                .with_for_update()
            )
            for credential in existing:
                credential.is_active = False
                credential.disabled_at = now
                credential.disabled_by_user_id = actor_user_id
            row, secret = self._new_credential(
                service_account_id=service_account_id,
                actor_user_id=actor_user_id,
                name=name.strip(),
            )
            created = CreatedCredential(
                id=row.id,
                service_account_id=row.service_account_id,
                public_id=row.public_id,
                name=row.name,
                secret=secret,
            )
        return created

    def get_credential(self, credential_id: UUID) -> CredentialSummary:
        row = self.db.get(ServiceAccountCredential, credential_id)
        if row is None:
            raise ServiceCredentialNotFound("service credential was not found")
        return self._summary(row)

    def disable_credential(self, credential_id: UUID, *, actor_user_id: UUID) -> None:
        now = self._now()
        with write_transaction(self.db):
            row = self.db.scalar(
                select(ServiceAccountCredential)
                .where(ServiceAccountCredential.id == credential_id)
                .with_for_update()
            )
            if row is None:
                raise ServiceCredentialNotFound("service credential was not found")
            if row.is_active:
                row.is_active = False
                row.disabled_at = now
                row.disabled_by_user_id = actor_user_id

    def _resolve_request_credential(
        self,
        public_id: str,
    ) -> tuple[UUID, UUID, str]:
        credential = self.db.scalar(
            select(ServiceAccountCredential).where(
                ServiceAccountCredential.public_id == public_id
            )
        )
        if credential is None:
            raise PaymentSignatureInvalid("payment signature is invalid")
        if not credential.is_active:
            raise ServiceCredentialDisabled("service credential is disabled")
        account = self.db.get(ServiceAccount, credential.service_account_id)
        if account is None:
            raise PaymentSignatureInvalid("payment signature is invalid")
        if not account.is_active:
            raise ServiceAccountDisabled("service account is disabled")
        return credential.id, account.id, credential.secret_ciphertext

    def _record_request(self, credential_id: UUID) -> None:
        now = self._now()
        with write_transaction(self.db):
            self.db.execute(
                postgresql_insert(ServiceAccountRequestLimit)
                .values(
                    credential_id=credential_id,
                    window_started_at=now,
                    request_count=0,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[ServiceAccountRequestLimit.credential_id]
                )
            )
            row = self.db.scalar(
                select(ServiceAccountRequestLimit)
                .where(ServiceAccountRequestLimit.credential_id == credential_id)
                .with_for_update()
            )
            if row is None:  # pragma: no cover - protected by the insert above
                raise RuntimeError("service account request limit row was not created")
            if row.window_started_at <= now - timedelta(minutes=1):
                row.window_started_at = now
                row.request_count = 0
            row.request_count += 1
            row.updated_at = now
            blocked = row.request_count > self.requests_per_minute
        if blocked:
            raise PaymentRateLimited("payment import rate limit exceeded")

    @staticmethod
    def _nonce_digest(nonce: str) -> str:
        return hashlib.sha256(nonce.encode("utf-8")).hexdigest()

    def _consume_nonce(self, credential_id: UUID, nonce: str) -> None:
        now = self._now()
        expired_ids = (
            select(ServiceAccountNonce.id)
            .where(ServiceAccountNonce.expires_at <= now)
            .order_by(ServiceAccountNonce.expires_at, ServiceAccountNonce.id)
            .limit(self.nonce_prune_batch_size)
        )
        try:
            with write_transaction(self.db):
                self.db.execute(
                    delete(ServiceAccountNonce).where(
                        ServiceAccountNonce.id.in_(expired_ids)
                    )
                )
                self.db.add(
                    ServiceAccountNonce(
                        credential_id=credential_id,
                        nonce_digest=self._nonce_digest(nonce),
                        requested_at=now,
                        expires_at=now + self.nonce_ttl,
                    )
                )
                self.db.flush()
        except IntegrityError as exc:
            raise PaymentReplayRejected("payment request nonce was already used") from exc

    def verify_request(
        self,
        *,
        public_id: str,
        method: str,
        path: str,
        timestamp: str,
        nonce: str,
        signature: str,
        raw_body: bytes,
    ) -> VerifiedMachineRequest:
        credential_id, service_account_id, secret_ciphertext = (
            self._resolve_request_credential(public_id)
        )
        self._record_request(credential_id)
        now = self._now()
        if not request_timestamp_is_current(
            timestamp,
            now=now,
            tolerance_seconds=self.signature_tolerance_seconds,
        ):
            raise PaymentRequestExpired("payment request timestamp is outside the allowed window")
        if not nonce or len(nonce) > 256 or "\n" in nonce or "\r" in nonce:
            raise PaymentSignatureInvalid("payment signature is invalid")
        canonical = canonical_request(
            method=method,
            path=path,
            timestamp=timestamp,
            nonce=nonce,
            body=raw_body,
        )
        secret = self.cipher.decrypt(
            secret_ciphertext,
            field_name=_SECRET_FIELD_NAME,
        ).encode("utf-8")
        if not verify_signature(secret=secret, provided=signature, canonical=canonical):
            raise PaymentSignatureInvalid("payment signature is invalid")
        self._consume_nonce(credential_id, nonce)
        return VerifiedMachineRequest(
            service_account_id=service_account_id,
            credential_id=credential_id,
            raw_body=raw_body,
        )
