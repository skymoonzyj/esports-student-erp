from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from math import ceil
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.identifiers import (
    IdentifierKind,
    NormalizedLoginIdentifier,
    login_identifier_digest,
    normalize_login_identifier,
)
from app.auth.models import AccountLoginIdentifier, AuthRateLimit, AuthSession, UserAccount
from app.auth.schemas import (
    AccountCreate,
    AccountCreatedWithTemporaryPassword,
    AccountIdentifierSummary,
    AccountStatus,
    AccountSummary,
    AuthUser,
    TemporaryPassword,
)
from app.core.time import business_date_at, business_today
from app.core.transactions import atomic_write_transaction, write_transaction
from app.identity.membership_locks import (
    lock_person_memberships,
    person_membership_lock_keys,
)
from app.identity.models import Person
from app.identity.normalization import (
    IdentityValidationError,
    normalize_email,
    normalize_phone,
)
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.schemas import AccountReadinessCandidate, Page
from app.identity.service import IdentityService, IdentityServiceError, PersonNotFound
from app.personnel_intake.errors import CredentialReservationConflict
from app.personnel_intake.locks import (
    CredentialReservationPolicy,
    lock_personnel_credentials,
    person_credential_fingerprint,
)
from app.personnel_intake.models import AssistedPersonProfile
from app.rbac.service import RbacService
from app.registration.finalization_service import clear_registration_application_secrets
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import RegistrationApplication, RegistrationVerificationChallenge
from app.registration.verification_service import (
    RegistrationVerificationService,
    VerificationConsumed,
    VerificationIssued,
    VerificationTarget,
    VerificationTargetMismatch,
    invalidate_password_reset_challenges_in_transaction,
    lock_password_credentials_in_transaction,
    lock_registration_credential_keys,
    registration_credential_lock_keys,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$JNj0f4PvY4p4nRkmz05qOA$"
    "/maXKxtsguN2hnljmKWpByz5rf/P6D1WBxeWDHVHA0g"
)

_UNFINISHED_REGISTRATION_STATES = frozenset(
    {
        "draft",
        "contact_verified",
        "identity_completed",
        "pending_guardian",
        "ready_to_finalize",
        "conflict",
    }
)


@dataclass(frozen=True, slots=True)
class _PasswordResetRegistrationSnapshot:
    account_id: UUID
    person_id: UUID
    identifier_fingerprints: tuple[
        tuple[UUID, IdentifierKind, str, str],
        ...,
    ]
    registration_identifier_digests: frozenset[str]


class AccountServiceError(ValueError):
    code = "account_service_error"


class PersonNotReady(AccountServiceError):
    code = "person_not_ready"

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__("person is not ready for an account")


class AccountContactMismatch(AccountServiceError):
    code = "account_contact_mismatch"

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


class InvalidAccountContact(AccountServiceError):
    code = "invalid_account_contact"

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(message)


class LoginPhoneAlreadyExists(AccountServiceError):
    code = "login_phone_already_exists"


class AccountAlreadyExists(AccountServiceError):
    code = "account_already_exists"


class AccountNotFound(AccountServiceError):
    code = "account_not_found"


class LastActiveAdminProtected(AccountServiceError):
    code = "last_admin_protected"


class AccountManagementDenied(AccountServiceError):
    code = "permission_denied"


class SuperAdminAccountProtected(AccountServiceError):
    code = "super_admin_account_protected"


class InvalidCredentials(ValueError):
    pass


class AccountTemporarilyLocked(InvalidCredentials):
    code = "account_locked"

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("account temporarily locked")


class LoginRateLimited(InvalidCredentials):
    code = "login_rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__("login rate limited")


class InvalidSession(ValueError):
    pass


class InvalidCsrfToken(ValueError):
    pass


class AccountIdentifierUnavailable(AccountServiceError):
    code = "registration_action_unavailable"


@dataclass(frozen=True)
class AuthContext:
    session: AuthSession
    account: UserAccount
    person: Person


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    csrf_token: str
    context: AuthContext


class AuthRateLimiter:
    max_attempts = 5
    block_duration = timedelta(minutes=15)
    window_duration = timedelta(minutes=15)
    source_max_attempts = 20
    source_block_duration = timedelta(minutes=1)
    source_window_duration = timedelta(minutes=1)

    def __init__(
        self,
        db: Session,
        *,
        pepper: bytes,
        clock: Callable[[], datetime],
    ) -> None:
        self.db = db
        self.pepper = pepper
        self.clock = clock

    @staticmethod
    def _normalize_ip(value: str | None) -> str:
        if not value:
            return "unknown"
        try:
            return ipaddress.ip_address(value).compressed
        except ValueError:
            return "unknown"

    def key_digest(self, identifier_digest: str, ip_address: str | None) -> str:
        material = f"{identifier_digest}|{self._normalize_ip(ip_address)}".encode()
        return hmac.new(self.pepper, material, hashlib.sha256).hexdigest()

    def source_key_digest(self, ip_address: str | None) -> str:
        material = f"account-login-source|{self._normalize_ip(ip_address)}".encode()
        return hmac.new(self.pepper, material, hashlib.sha256).hexdigest()

    def prune_expired(self, *, limit: int = 500) -> None:
        cutoff = self.clock() - timedelta(days=7)
        stale_keys = (
            select(AuthRateLimit.key_digest)
            .where(AuthRateLimit.updated_at < cutoff)
            .order_by(AuthRateLimit.updated_at)
            .limit(limit)
        )
        self.db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(stale_keys)))

    def _lock_rows(
        self,
        key_digests: tuple[str, ...],
        *,
        create: bool,
    ) -> dict[str, AuthRateLimit]:
        ordered_keys = tuple(sorted(set(key_digests)))
        if create:
            now = self.clock()
            for key_digest in ordered_keys:
                self.db.execute(
                    postgresql_insert(AuthRateLimit)
                    .values(
                        key_digest=key_digest,
                        attempts=0,
                        window_started_at=now,
                        blocked_until=None,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[AuthRateLimit.key_digest])
                )
        if not ordered_keys:
            return {}
        rows = self.db.scalars(
            select(AuthRateLimit)
            .where(AuthRateLimit.key_digest.in_(ordered_keys))
            .order_by(AuthRateLimit.key_digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        return {row.key_digest: row for row in rows}

    def blocked_deadlines(self, *key_digests: str) -> dict[str, datetime]:
        now = self.clock()
        return {
            key_digest: row.blocked_until
            for key_digest, row in self._lock_rows(tuple(key_digests), create=False).items()
            if row.blocked_until is not None and row.blocked_until > now
        }

    def record_failures(
        self,
        policies: dict[str, tuple[int, timedelta, timedelta]],
    ) -> dict[str, datetime]:
        now = self.clock()
        rows = self._lock_rows(tuple(policies), create=True)
        for key_digest in sorted(policies):
            row = rows.get(key_digest)
            if row is None:  # pragma: no cover - protected by the insert above
                raise RuntimeError("rate limit row was not created")
            max_attempts, block_duration, window_duration = policies[key_digest]
            if row.blocked_until is not None and row.blocked_until > now:
                continue
            if row.blocked_until is not None or row.window_started_at <= now - window_duration:
                row.attempts = 0
                row.window_started_at = now
                row.blocked_until = None
            row.attempts += 1
            row.updated_at = now
            if row.attempts >= max_attempts:
                row.blocked_until = now + block_duration
        return {
            key_digest: row.blocked_until
            for key_digest, row in rows.items()
            if row.blocked_until is not None and row.blocked_until > now
        }

    def is_blocked(self, key_digest: str) -> bool:
        return key_digest in self.blocked_deadlines(key_digest)

    def record_failure(self, key_digest: str) -> None:
        self.record_failures(
            {
                key_digest: (
                    self.max_attempts,
                    self.block_duration,
                    self.window_duration,
                )
            }
        )

    def clear(self, key_digest: str) -> None:
        self.db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest == key_digest))


class AuthService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        passwords: PasswordService,
        session_token_pepper: bytes,
        session_ttl_minutes: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(session_token_pepper) != 32:
            raise ValueError("session token pepper must be 32 bytes")
        self.db = db
        self.lookup = lookup
        self.identity_service = IdentityService(db, cipher=cipher, lookup=lookup)
        self.passwords = passwords
        self.pepper = session_token_pepper
        self.session_ttl = timedelta(minutes=session_ttl_minutes)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.rate_limiter = AuthRateLimiter(
            db,
            pepper=session_token_pepper,
            clock=self.clock,
        )

    @staticmethod
    def _token() -> str:
        return secrets.token_urlsafe(32)

    def _token_digest(self, token: str) -> str:
        return hmac.new(self.pepper, token.encode(), hashlib.sha256).hexdigest()

    def _normalized_identifier(
        self,
        raw: str,
    ) -> tuple[NormalizedLoginIdentifier | None, str]:
        try:
            normalized = normalize_login_identifier(raw)
        except IdentityValidationError:
            return None, self.lookup.digest_domain(
                "account-login-invalid",
                raw.strip().casefold(),
            )
        return normalized, login_identifier_digest(self.lookup, normalized)

    def _auth_user(self, context: AuthContext) -> AuthUser:
        rbac = RbacService(self.db)
        is_super_admin = rbac.account_has_role(context.account.id, "super_admin")
        global_permissions = rbac.allowed_permissions(
            user_id=context.account.id,
            resource_tokens={"global"},
        )
        try:
            email = self.identity_service.decrypt_email(context.person)
        except (IdentityServiceError, IdentityValidationError):
            email = None
        return AuthUser(
            account_id=context.account.id,
            person_id=context.person.id,
            full_name=context.person.full_name,
            email=email,
            force_password_change=context.account.force_password_change,
            is_super_admin=is_super_admin,
            permissions=global_permissions,
            scoped_permissions=sorted(
                set(rbac.available_permissions(user_id=context.account.id))
                - set(global_permissions)
            ),
        )

    def user(self, context: AuthContext) -> AuthUser:
        return self._auth_user(context)

    def _deny_pending_student_access(
        self,
        account: UserAccount,
        *,
        readiness_policy: GuardianReadinessPolicy,
        now: datetime,
        request_id: str,
        action: str,
    ) -> bool:
        from app.auth.account_readiness import AccountAccessReadinessPolicy

        account_id = readiness_policy.disable_student_account_if_pending(
            account.person_id,
            reference_date=business_date_at(now),
            revoked_at=now,
            audit_actor_user_id=account.id,
            audit_request_id=request_id,
            locked_account=account,
            account_readiness=AccountAccessReadinessPolicy(
                self.db, cipher=self.identity_service.cipher, lookup=self.lookup
            ),
        )
        if account_id is None:
            return False
        AuditService(self.db).append(
            actor_user_id=account.id,
            action=action,
            resource_type="account",
            resource_id=account_id,
            purpose=None,
            outcome="denied",
            request_id=request_id,
            metadata={"error_code": "minor_guardian_required"},
        )
        return True

    def login(
        self,
        *,
        identifier: str,
        password: str,
        ip_address: str | None,
        request_id: str,
    ) -> LoginResult:
        normalized_identifier, identifier_digest = self._normalized_identifier(identifier)
        pair_rate_key = self.rate_limiter.key_digest(identifier_digest, ip_address)
        source_rate_key = self.rate_limiter.source_key_digest(ip_address)
        result: LoginResult | None = None
        account_lock_deadline: datetime | None = None
        rate_deadlines: dict[str, datetime] = {}
        with write_transaction(self.db):
            self.rate_limiter.prune_expired(limit=500)
            matched = (
                self.db.execute(
                    select(AccountLoginIdentifier.account_id, UserAccount.person_id)
                    .join(
                        UserAccount,
                        UserAccount.id == AccountLoginIdentifier.account_id,
                    )
                    .where(
                        AccountLoginIdentifier.kind == normalized_identifier.kind,
                        AccountLoginIdentifier.identifier_digest == identifier_digest,
                    )
                )
                .tuples()
                .one_or_none()
                if normalized_identifier is not None
                else None
            )
            account_id = matched[0] if matched is not None else None
            person_id = matched[1] if matched is not None else None
            readiness_policy = GuardianReadinessPolicy(self.db)
            locked_people = readiness_policy.lock_students(
                [person_id] if person_id is not None else []
            )
            account = (
                self.db.scalar(
                    select(UserAccount)
                    .where(UserAccount.id == account_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if account_id is not None
                else None
            )
            now = self.clock()
            account_counter_locked = False
            rate_deadlines = self.rate_limiter.blocked_deadlines(
                pair_rate_key,
                source_rate_key,
            )
            if account is not None and account.locked_until is not None:
                if account.locked_until <= now:
                    account.failed_login_count = 0
                    account.locked_until = None
                else:
                    account_counter_locked = True
                    if account.status == "active":
                        account_lock_deadline = account.locked_until
            rate_blocked = bool(rate_deadlines)
            account_locked = account_lock_deadline is not None
            encoded = account.password_hash if account is not None else _DUMMY_PASSWORD_HASH
            password_valid = self.passwords.verify(password, encoded)
            accepted = bool(
                account
                and account.status == "active"
                and not rate_blocked
                and not account_locked
                and password_valid
            )
            if not accepted:
                credential_rejected = bool(
                    account is None or account.status != "active" or not password_valid
                )
                if not rate_blocked and not account_locked and credential_rejected:
                    rate_deadlines = self.rate_limiter.record_failures(
                        {
                            pair_rate_key: (
                                self.rate_limiter.max_attempts,
                                self.rate_limiter.block_duration,
                                self.rate_limiter.window_duration,
                            ),
                            source_rate_key: (
                                self.rate_limiter.source_max_attempts,
                                self.rate_limiter.source_block_duration,
                                self.rate_limiter.source_window_duration,
                            ),
                        }
                    )
                if (
                    account is not None
                    and not account_locked
                    and not account_counter_locked
                    and not rate_blocked
                    and not password_valid
                ):
                    account.failed_login_count += 1
                    if account.failed_login_count >= 5:
                        account.locked_until = now + timedelta(minutes=15)
                        account_lock_deadline = account.locked_until
                account_lock_deadline = (
                    account_lock_deadline
                    if (
                        account
                        and account.status == "active"
                        and account_lock_deadline is not None
                        and account_lock_deadline > now
                    )
                    else None
                )
            else:
                assert account is not None
                account.failed_login_count = 0
                account.locked_until = None
                self.rate_limiter.clear(pair_rate_key)
                person = locked_people[0] if locked_people else None
                if (
                    person is None
                    or person.deleted_at is not None
                    or self._deny_pending_student_access(
                        account,
                        readiness_policy=readiness_policy,
                        now=now,
                        request_id=request_id,
                        action="auth.login.guardian_missing",
                    )
                ):
                    accepted = False
                else:
                    session_token = self._token()
                    csrf_token = self._token()
                    session = AuthSession(
                        user_id=account.id,
                        token_digest=self._token_digest(session_token),
                        csrf_digest=self._token_digest(csrf_token),
                        expires_at=now + self.session_ttl,
                    )
                    self.db.add(session)
                    self.db.flush()
                    result = LoginResult(
                        session_token=session_token,
                        csrf_token=csrf_token,
                        context=AuthContext(session=session, account=account, person=person),
                    )
        if result is None:
            active_deadlines = list(rate_deadlines.values())
            if account_lock_deadline is not None:
                active_deadlines.append(account_lock_deadline)
                raise AccountTemporarilyLocked(
                    ceil(max((deadline - now).total_seconds() for deadline in active_deadlines))
                )
            if active_deadlines:
                raise LoginRateLimited(
                    ceil(max((deadline - now).total_seconds() for deadline in active_deadlines))
                )
            raise InvalidCredentials("invalid credentials")
        return result

    def authenticate(
        self, token: str | None, *, request_id: str,
        foreign_key_compatible_locks: bool = False,
    ) -> AuthContext:
        if not token or len(token) > 256:
            raise InvalidSession("invalid session")
        context: AuthContext | None = None
        token_digest = self._token_digest(token)
        with write_transaction(self.db):
            person_id = self.db.scalar(
                select(UserAccount.person_id)
                .join(AuthSession, AuthSession.user_id == UserAccount.id)
                .where(AuthSession.token_digest == token_digest)
            )
            if person_id is None:
                raise InvalidSession("invalid session")
            readiness_policy = GuardianReadinessPolicy(self.db)
            # Rental writes retain identity locks while waiting for assets.
            # NO KEY UPDATE still excludes identity/status/readiness mutations,
            # but permits legacy custody/audit FK KEY SHARE checks on these rows.
            # Other callers retain the original FOR UPDATE locking contract.
            locked_people = readiness_policy.lock_students(
                [person_id], no_key_update=foreign_key_compatible_locks
            )
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.person_id == person_id)
                .with_for_update(key_share=foreign_key_compatible_locks)
                .execution_options(populate_existing=True)
            )
            if not locked_people or account is None:
                raise InvalidSession("invalid session")
            person = locked_people[0]
            session = self.db.scalar(
                select(AuthSession)
                .where(
                    AuthSession.token_digest == token_digest,
                    AuthSession.user_id == account.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if session is None:
                raise InvalidSession("invalid session")
            now = self.clock()
            if (
                session.revoked_at is not None
                or session.expires_at <= now
                or account.status != "active"
                or person.deleted_at is not None
            ):
                raise InvalidSession("invalid session")
            if not self._deny_pending_student_access(
                account,
                readiness_policy=readiness_policy,
                now=now,
                request_id=request_id,
                action="auth.session.guardian_missing",
            ):
                context = AuthContext(session=session, account=account, person=person)
        if context is None:
            raise InvalidSession("invalid session")
        return context

    def verify_csrf(self, context: AuthContext, token: str | None) -> None:
        if not token or len(token) > 256:
            raise InvalidCsrfToken("invalid csrf token")
        if not hmac.compare_digest(context.session.csrf_digest, self._token_digest(token)):
            raise InvalidCsrfToken("invalid csrf token")

    def rotate_csrf(self, context: AuthContext) -> str:
        token = self._token()
        with write_transaction(self.db):
            context.session.csrf_digest = self._token_digest(token)
        return token

    def logout(self, context: AuthContext) -> None:
        with write_transaction(self.db):
            context.session.revoked_at = self.clock()

    def change_password(
        self,
        context: AuthContext,
        *,
        current_password: str,
        new_password: str,
    ) -> str:
        csrf_token = self._token()
        with write_transaction(self.db):
            lock_password_credentials_in_transaction(self.db, context.account.id)
            locked_people = GuardianReadinessPolicy(self.db).lock_students(
                [context.account.person_id]
            )
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.id == context.account.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            current_session = self.db.scalar(
                select(AuthSession)
                .where(
                    AuthSession.id == context.session.id,
                    AuthSession.user_id == context.account.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                not locked_people
                or locked_people[0].deleted_at is not None
                or account is None
                or account.status != "active"
                or current_session is None
                or current_session.revoked_at is not None
                or not self.passwords.verify(current_password, account.password_hash)
            ):
                raise InvalidCredentials("invalid credentials")
            account.password_hash = self.passwords.hash(new_password)
            account.force_password_change = False
            account.failed_login_count = 0
            account.locked_until = None
            invalidate_password_reset_challenges_in_transaction(
                self.db, account_id=account.id, invalidated_at=self.clock()
            )
            self.db.execute(
                update(AuthSession)
                .where(
                    AuthSession.user_id == account.id,
                    AuthSession.id != current_session.id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=self.clock())
            )
            current_session.csrf_digest = self._token_digest(csrf_token)
        return csrf_token

    @staticmethod
    def _mask_identifier(kind: IdentifierKind, value: str) -> str:
        if kind == "phone":
            return f"{value[:3]}****{value[-4:]}"
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"

    def _build_password_reset_registration_snapshot(
        self,
        consumed: VerificationConsumed,
        *,
        account: UserAccount,
        person: Person,
        identifiers: list[AccountLoginIdentifier],
    ) -> _PasswordResetRegistrationSnapshot:
        if (
            consumed.account_id is None
            or consumed.account_id != account.id
            or account.person_id != person.id
            or account.status != "active"
            or person.deleted_at is not None
            or not identifiers
        ):
            raise VerificationTargetMismatch("password reset account is unavailable")
        fingerprints: list[tuple[UUID, IdentifierKind, str, str]] = []
        registration_digests: set[str] = set()
        seen_kinds: set[IdentifierKind] = set()
        consumed_identifier_found = False
        for identifier in sorted(identifiers, key=lambda row: row.id):
            if identifier.kind not in {"phone", "email"}:
                raise VerificationTargetMismatch("password reset identifier is unavailable")
            kind = cast(IdentifierKind, identifier.kind)
            if kind in seen_kinds:
                raise VerificationTargetMismatch("password reset identifier is duplicated")
            seen_kinds.add(kind)
            try:
                contact = (
                    self.identity_service.decrypt_phone(person)
                    if kind == "phone"
                    else self.identity_service.decrypt_email(person)
                )
                if contact is None:
                    raise VerificationTargetMismatch("password reset contact is unavailable")
                normalized = normalize_registration_identifier(kind, contact)
            except VerificationTargetMismatch:
                raise
            except (IdentityServiceError, IdentityValidationError, ValueError) as exc:
                raise VerificationTargetMismatch("password reset contact is unavailable") from exc
            expected_login_digest = login_identifier_digest(
                self.lookup,
                NormalizedLoginIdentifier(kind, normalized.value),
            )
            if not hmac.compare_digest(
                expected_login_digest,
                identifier.identifier_digest,
            ):
                raise VerificationTargetMismatch("password reset identifier is inconsistent")
            registration_digest = registration_identifier_digest(
                self.lookup,
                normalized,
            )
            fingerprints.append(
                (
                    identifier.id,
                    kind,
                    identifier.identifier_digest,
                    registration_digest,
                )
            )
            registration_digests.add(registration_digest)
            if kind == consumed.identifier_kind and hmac.compare_digest(
                registration_digest,
                consumed.identifier_digest,
            ):
                consumed_identifier_found = True
        if not consumed_identifier_found:
            raise VerificationTargetMismatch("password reset identifier does not belong to account")
        return _PasswordResetRegistrationSnapshot(
            account_id=account.id,
            person_id=person.id,
            identifier_fingerprints=tuple(fingerprints),
            registration_identifier_digests=frozenset(registration_digests),
        )

    def _password_reset_registration_snapshot(
        self,
        consumed: VerificationConsumed,
    ) -> _PasswordResetRegistrationSnapshot:
        if consumed.account_id is None:
            raise VerificationTargetMismatch("password reset account is unavailable")
        row = self.db.execute(
            select(UserAccount, Person)
            .join(Person, Person.id == UserAccount.person_id)
            .where(UserAccount.id == consumed.account_id)
        ).one_or_none()
        if row is None:
            raise VerificationTargetMismatch("password reset account is unavailable")
        account, person = row
        identifiers = list(
            self.db.scalars(
                select(AccountLoginIdentifier)
                .where(AccountLoginIdentifier.account_id == account.id)
                .order_by(AccountLoginIdentifier.id)
            )
        )
        return self._build_password_reset_registration_snapshot(
            consumed,
            account=account,
            person=person,
            identifiers=identifiers,
        )

    def _lock_password_reset_registration_snapshot(
        self,
        consumed: VerificationConsumed,
        expected: _PasswordResetRegistrationSnapshot,
    ) -> UserAccount:
        person = self.db.scalar(
            select(Person)
            .where(Person.id == expected.person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        account = self.db.scalar(
            select(UserAccount)
            .where(UserAccount.id == expected.account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        identifiers = list(
            self.db.scalars(
                select(AccountLoginIdentifier)
                .where(AccountLoginIdentifier.account_id == expected.account_id)
                .order_by(AccountLoginIdentifier.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        if person is None or account is None:
            raise VerificationTargetMismatch("password reset account is unavailable")
        locked = self._build_password_reset_registration_snapshot(
            consumed,
            account=account,
            person=person,
            identifiers=identifiers,
        )
        if locked != expected:
            raise VerificationTargetMismatch("password reset account changed during verification")
        return account

    def issue_password_reset(
        self,
        *,
        identifier: str,
        request_source: str,
        verification: RegistrationVerificationService,
    ) -> VerificationIssued:
        normalized = normalize_login_identifier(identifier)
        login_digest = login_identifier_digest(self.lookup, normalized)
        account_query = (
            select(AccountLoginIdentifier.account_id)
            .join(UserAccount, UserAccount.id == AccountLoginIdentifier.account_id)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                AccountLoginIdentifier.kind == normalized.kind,
                AccountLoginIdentifier.identifier_digest == login_digest,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        )
        with write_transaction(self.db):
            account_id = self.db.scalar(account_query)
            if account_id is not None:
                lock_password_credentials_in_transaction(self.db, account_id)
                # Recheck after the wait, but never switch to another account
                # under the original account's credential lock.
                account_id = self.db.scalar(
                    account_query.where(UserAccount.id == account_id)
                )
            return verification.issue_in_transaction(
                identifier_kind=normalized.kind,
                identifier=normalized.value,
                purpose="password_reset",
                request_source=request_source,
                account_id=account_id,
            )

    def complete_password_reset(
        self,
        *,
        challenge_id: UUID,
        code: str,
        new_password: str,
        request_id: str,
        verification: RegistrationVerificationService,
    ) -> None:
        # Keep policy validation cheap; Argon2 must only run after both the OTP
        # and the live account binding have authorized a real password write.
        self.passwords.validate(new_password)
        snapshot: _PasswordResetRegistrationSnapshot | None = None
        applications: list[RegistrationApplication] = []
        account: UserAccount | None = None
        prepared_target: VerificationTarget | None = None
        preparation_error: VerificationTargetMismatch | None = None

        def lock_reset_target() -> VerificationTarget:
            nonlocal snapshot, applications, account, prepared_target
            challenge = self.db.scalar(
                select(RegistrationVerificationChallenge)
                .where(
                    RegistrationVerificationChallenge.id == challenge_id,
                )
                .execution_options(populate_existing=True)
            )
            if challenge is None:
                raise VerificationTargetMismatch("password reset target is unavailable")
            target = VerificationTarget(
                application_id=challenge.application_id,
                account_id=challenge.account_id,
                identifier_kind=cast(IdentifierKind, challenge.identifier_kind),
                identifier_digest=challenge.identifier_digest,
                purpose="password_reset",
            )
            prepared_target = target
            if challenge.account_id is None:
                return target
            lock_password_credentials_in_transaction(self.db, challenge.account_id)
            candidate = VerificationConsumed(
                challenge_id=challenge.id,
                application_id=challenge.application_id,
                account_id=challenge.account_id,
                identifier_kind=cast(IdentifierKind, challenge.identifier_kind),
                identifier_digest=challenge.identifier_digest,
                purpose="password_reset",
                consumed_at=self.clock(),
            )
            snapshot = self._password_reset_registration_snapshot(candidate)
            lock_registration_credential_keys(
                self.db,
                registration_credential_lock_keys(set(snapshot.registration_identifier_digests)),
            )
            applications = list(
                self.db.scalars(
                    select(RegistrationApplication)
                    .where(
                        RegistrationApplication.verified_identifier_digest.in_(
                            snapshot.registration_identifier_digests
                        ),
                        RegistrationApplication.state.in_(_UNFINISHED_REGISTRATION_STATES),
                    )
                    .order_by(RegistrationApplication.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            account = self._lock_password_reset_registration_snapshot(
                candidate,
                snapshot,
            )
            return target

        def prepare_reset_target() -> VerificationTarget:
            nonlocal preparation_error
            try:
                return lock_reset_target()
            except VerificationTargetMismatch as exc:
                # Preserve invalid-code attempts/nonenumeration: an unavailable
                # target is reported only after a correct code, as before.
                if prepared_target is None:
                    raise
                preparation_error = exc
                return prepared_target

        def reset_account(consumed: VerificationConsumed) -> None:
            if preparation_error is not None:
                raise preparation_error
            if consumed.account_id is None:
                return
            assert snapshot is not None and account is not None
            new_hash = self.passwords.hash(new_password)
            now = self.clock()
            application_ids = {application.id for application in applications}
            for application in applications:
                application.review_decision = None
                application.reviewed_by_user_id = None
                application.reviewed_candidate_person_id = None
                application.review_reason = None
                application.reviewed_at = None
                application.state = "expired"
                clear_registration_application_secrets(application)
            verification.invalidate_registration_credentials_in_transaction(
                account_id=account.id,
                application_ids=application_ids,
                identifier_digests=set(snapshot.registration_identifier_digests),
                invalidated_at=now,
            )
            invalidate_password_reset_challenges_in_transaction(
                self.db, account_id=account.id, invalidated_at=now
            )
            account.password_hash = new_hash
            account.force_password_change = False
            account.failed_login_count = 0
            account.locked_until = None
            self.db.execute(
                update(AuthSession)
                .where(
                    AuthSession.user_id == account.id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            AuditService(self.db).append(
                actor_user_id=account.id,
                action="auth.password_reset",
                resource_type="account",
                resource_id=account.id,
                purpose="password_recovery",
                outcome="allowed",
                request_id=request_id,
                metadata={},
            )

        verification.consume_with(
            challenge_id,
            code,
            expected_purpose="password_reset",
            prepare_target=prepare_reset_target,
            on_success=reset_account,
        )

    def _identifier_summaries(self, account_id: UUID) -> list[AccountIdentifierSummary]:
        row = self.db.execute(
            select(UserAccount, Person)
            .join(Person, Person.id == UserAccount.person_id)
            .where(
                UserAccount.id == account_id,
                UserAccount.status == "active",
                Person.deleted_at.is_(None),
            )
        ).one_or_none()
        if row is None:
            raise AccountIdentifierUnavailable("account is unavailable")
        _account, person = row
        identifiers = list(
            self.db.scalars(
                select(AccountLoginIdentifier)
                .where(AccountLoginIdentifier.account_id == account_id)
                .order_by(
                    AccountLoginIdentifier.kind,
                    AccountLoginIdentifier.id,
                )
            )
        )
        if not identifiers:
            raise AccountIdentifierUnavailable("account identifiers are unavailable")
        summaries: list[AccountIdentifierSummary] = []
        seen_kinds: set[IdentifierKind] = set()
        for identifier in identifiers:
            kind = cast(IdentifierKind, identifier.kind)
            if kind in seen_kinds:
                raise AccountIdentifierUnavailable("account identifier kind is duplicated")
            seen_kinds.add(kind)
            try:
                contact = (
                    self.identity_service.decrypt_phone(person)
                    if kind == "phone"
                    else self.identity_service.decrypt_email(person)
                )
            except (IdentityServiceError, IdentityValidationError) as exc:
                raise AccountIdentifierUnavailable("account contact is unavailable") from exc
            if contact is None:
                raise AccountIdentifierUnavailable("account contact is unavailable")
            expected = login_identifier_digest(
                self.lookup,
                NormalizedLoginIdentifier(kind, contact),
            )
            if not hmac.compare_digest(expected, identifier.identifier_digest):
                raise AccountIdentifierUnavailable("account identifier is inconsistent")
            summaries.append(
                AccountIdentifierSummary(
                    kind=kind,
                    masked_identifier=self._mask_identifier(kind, contact),
                    verified_at=identifier.verified_at,
                )
            )
        return summaries

    def list_identifiers(self, context: AuthContext) -> list[AccountIdentifierSummary]:
        return self._identifier_summaries(context.account.id)

    def issue_identifier_addition(
        self,
        context: AuthContext,
        *,
        identifier_kind: IdentifierKind,
        identifier: str,
        current_password: str,
        request_source: str,
        verification: RegistrationVerificationService,
    ) -> VerificationIssued:
        normalized = normalize_registration_identifier(identifier_kind, identifier)
        with write_transaction(self.db):
            verification.lock_issue_limits_in_transaction(
                identifier_kind=normalized.kind,
                identifier=normalized.value,
                request_source=request_source,
            )
            people = GuardianReadinessPolicy(self.db).lock_students([context.person.id])
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.id == context.account.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                not people
                or people[0].deleted_at is not None
                or account is None
                or account.person_id != people[0].id
                or account.status != "active"
            ):
                raise AccountIdentifierUnavailable("identifier addition is unavailable")
            if not self.passwords.verify(current_password, account.password_hash):
                raise InvalidCredentials("invalid credentials")
            if (
                self.db.scalar(
                    select(AccountLoginIdentifier.id).where(
                        AccountLoginIdentifier.account_id == account.id,
                        AccountLoginIdentifier.kind == identifier_kind,
                    )
                )
                is not None
            ):
                raise AccountIdentifierUnavailable("identifier addition is unavailable")
            return verification.issue_in_transaction(
                identifier_kind=normalized.kind,
                identifier=normalized.value,
                purpose="add_login_identifier",
                request_source=request_source,
                account_id=account.id,
            )

    @staticmethod
    def _identifier_lock_key(identifier_digest: str) -> int:
        return int.from_bytes(
            hashlib.sha256(f"account-identifier\x00{identifier_digest}".encode()).digest()[:8],
            byteorder="big",
            signed=True,
        )

    def verify_identifier_addition(
        self,
        context: AuthContext,
        *,
        challenge_id: UUID,
        code: str,
        identifier_kind: IdentifierKind,
        identifier: str,
        current_password: str,
        request_id: str,
        verification: RegistrationVerificationService,
    ) -> list[AccountIdentifierSummary]:
        from app.personnel_intake.activation_state import (
            invalidate_in_transaction,
            lock_sessions_in_transaction,
        )
        from app.personnel_intake.claims.binding import (
            invalidate_prelocked_pending_claims_in_transaction,
            prepare_claim_lifecycle,
        )
        from app.personnel_intake.claims.errors import ProfileClaimError
        from app.personnel_intake.claims.locking import lock_claim_business_context
        from app.personnel_intake.claims.models import ProfileClaim

        normalized = normalize_registration_identifier(identifier_kind, identifier)
        registration_digest = registration_identifier_digest(self.lookup, normalized)
        login_identifier = NormalizedLoginIdentifier(normalized.kind, normalized.value)
        login_digest = login_identifier_digest(self.lookup, login_identifier)
        target = VerificationTarget(
            application_id=None,
            account_id=context.account.id,
            identifier_kind=normalized.kind,
            identifier_digest=registration_digest,
            purpose="add_login_identifier",
        )

        person: Person | None = None
        account: UserAccount | None = None
        identifiers: list[AccountLoginIdentifier] = []
        lifecycle_claims: list[ProfileClaim] = []
        preparation_error: AccountIdentifierUnavailable | CredentialReservationConflict | None = None

        def lock_identifier_target() -> VerificationTarget:
            nonlocal person, account, identifiers, lifecycle_claims
            snapshot = self.db.scalar(
                select(Person)
                .where(Person.id == context.person.id)
                .execution_options(populate_existing=True)
            )
            if snapshot is None or snapshot.deleted_at is not None:
                raise AccountIdentifierUnavailable("identifier addition is unavailable")
            fingerprint = person_credential_fingerprint(snapshot)
            old_email = self.identity_service.decrypt_email(snapshot)
            old_phone = self.identity_service.decrypt_phone(snapshot)
            lifecycle = prepare_claim_lifecycle(
                self.db, person_id=snapshot.id, identity_digest=snapshot.identity_digest,
                email_digest=registration_digest if normalized.kind == "email" else None,
                cipher=self.identity_service.cipher, lookup=self.lookup,
            )
            credential_digests = {registration_digest}
            for kind, value in (("email", old_email), ("phone", old_phone)):
                if value is not None:
                    credential_digests.add(
                        registration_identifier_digest(
                            self.lookup,
                            normalize_registration_identifier(cast(IdentifierKind, kind), value),
                        )
                    )
            lock_registration_credential_keys(
                self.db,
                lifecycle.credential_keys + registration_credential_lock_keys(
                    credential_digests,
                    identity_digests={snapshot.identity_digest},
                ),
            )
            self.db.execute(
                select(func.pg_advisory_xact_lock(self._identifier_lock_key(login_digest)))
            )
            destination_phone_digest = (
                self.lookup.digest(normalized.value) if normalized.kind == "phone" else None
            )
            destination_email_digest = (
                self.lookup.digest_domain("person-email", normalized.value)
                if normalized.kind == "email"
                else None
            )
            # Membership fences precede every Person lock, including shared phones.
            lock_person_memberships(
                self.db,
                lifecycle.context.membership_keys + person_membership_lock_keys(
                    phone_digests={
                        digest
                        for digest in (snapshot.phone_digest, destination_phone_digest)
                        if digest
                    },
                    email_digests={
                        digest
                        for digest in (snapshot.email_digest, destination_email_digest)
                        if digest
                    },
                ),
            )
            lock_claim_business_context(self.db, lifecycle.context)
            people = GuardianReadinessPolicy(self.db).lock_students([context.person.id])
            if not people or people[0].deleted_at is not None:
                raise AccountIdentifierUnavailable("identifier addition is unavailable")
            person = people[0]
            if person_credential_fingerprint(person) != fingerprint:
                raise CredentialReservationConflict()
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.id == context.account.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if account is None or account.person_id != person.id or account.status != "active":
                raise AccountIdentifierUnavailable("identifier addition is unavailable")
            identifiers = list(
                self.db.scalars(
                    select(AccountLoginIdentifier)
                    .where(AccountLoginIdentifier.account_id == account.id)
                    .order_by(AccountLoginIdentifier.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            lifecycle_claims = lifecycle.affected_rows(
                lifecycle.lock_rows(self.db, cipher=self.identity_service.cipher, lookup=self.lookup)
            )
            lock_sessions_in_transaction(self.db, person_id=person.id)
            return target

        def prepare_identifier_target() -> VerificationTarget:
            nonlocal preparation_error
            try:
                return lock_identifier_target()
            except (AccountIdentifierUnavailable, CredentialReservationConflict) as exc:
                preparation_error = exc
                return target
            except ProfileClaimError as exc:
                if (exc.code, exc.status) != ("profile_claim_retry", 409):
                    raise
                # Claim-context drift is the same unavailable credential target
                # as the older fingerprint guard. Still count bad OTP guesses;
                # only a correct OTP reaches the deferred conflict/rollback.
                preparation_error = CredentialReservationConflict()
                return target

        def add_identifier(consumed: VerificationConsumed) -> None:
            del consumed
            if preparation_error is not None:
                raise preparation_error
            assert person is not None and account is not None
            if not self.passwords.verify(current_password, account.password_hash):
                raise InvalidCredentials("invalid credentials")
            policy = CredentialReservationPolicy(self.db, lookup=self.lookup)
            try:
                policy.require_reservation_available(
                    kind="identity", digest=person.identity_digest, person_id=person.id
                )
                if normalized.kind == "email":
                    policy.require_reservation_available(
                        kind="email", digest=registration_digest, person_id=person.id
                    )
                    # Legacy contact emails may intentionally be shared; only
                    # the new persisted reviewed source owns an unactivated
                    # formal email independently of draft reservation rows.
                    protected_owner = self.db.scalar(
                        select(Person.id).join(
                            AssistedPersonProfile, AssistedPersonProfile.person_id == Person.id
                        ).where(
                            AssistedPersonProfile.activation_requirement == "reviewed_claim",
                            Person.id != person.id,
                            ~select(UserAccount.id).where(UserAccount.person_id == Person.id).exists(),
                            (Person.email_digest == self.lookup.digest_domain("person-email", normalized.value))
                            | (Person.email == normalized.value),
                        )
                    )
                    if protected_owner is not None:
                        raise CredentialReservationConflict()
            except CredentialReservationConflict as exc:
                raise AccountIdentifierUnavailable("identifier addition is unavailable") from exc
            if (
                any(row.kind == normalized.kind for row in identifiers)
                or self.db.scalar(
                    select(AccountLoginIdentifier.id).where(
                        AccountLoginIdentifier.identifier_digest == login_digest
                    )
                )
                is not None
            ):
                raise AccountIdentifierUnavailable("identifier addition is unavailable")
            if normalized.kind == "phone":
                person.phone_ciphertext = self.identity_service.cipher.encrypt(
                    normalized.value,
                    field_name="phone",
                )
                person.phone_digest = self.lookup.digest(normalized.value)
            else:
                person.email = None
                person.email_ciphertext = self.identity_service.cipher.encrypt(
                    normalized.value,
                    field_name="email",
                )
                person.email_digest = self.lookup.digest_domain(
                    "person-email",
                    normalized.value,
                )
            self.db.add(
                AccountLoginIdentifier(
                    account_id=account.id,
                    kind=normalized.kind,
                    identifier_digest=login_digest,
                    verified_at=self.clock(),
                    verification_source="self_added",
                )
            )
            occurred_at = self.clock()
            invalidate_prelocked_pending_claims_in_transaction(
                self.db, claims=lifecycle_claims, occurred_at=occurred_at,
                actor_user_id=account.id,
            )
            invalidate_in_transaction(self.db, person_id=person.id, occurred_at=occurred_at)
            AuditService(self.db).append(
                actor_user_id=account.id,
                action="auth.identifier.add",
                resource_type="account",
                resource_id=account.id,
                purpose="account_identifier_verification",
                outcome="allowed",
                request_id=request_id,
                metadata={},
            )
            self.db.flush()

        try:
            verification.consume_with(
                challenge_id,
                code,
                prepare_target=prepare_identifier_target,
                expected_purpose="add_login_identifier",
                on_success=add_identifier,
            )
        except IntegrityError as exc:
            raise AccountIdentifierUnavailable("identifier addition is unavailable") from exc
        return self._identifier_summaries(context.account.id)


class AccountService:
    def __init__(
        self,
        db: Session,
        *,
        identity_service: IdentityService,
        passwords: PasswordService,
        reference_date_provider: Callable[[], date] = business_today,
    ) -> None:
        self.db = db
        self.identity_service = identity_service
        self.passwords = passwords
        self.reference_date_provider = reference_date_provider

    @staticmethod
    def _mask_phone(value: str) -> str:
        return f"{value[:3]}****{value[-4:]}"

    @staticmethod
    def _mask_email(value: str) -> str:
        local, domain = value.rsplit("@", 1)
        return f"{local[:1]}***@{domain}"

    def _person(self, person_id: UUID) -> Person:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise PersonNotFound("person not found")
        return person

    def _account_summaries(
        self,
        rows: list[tuple[UserAccount, Person]],
    ) -> list[AccountSummary]:
        if not rows:
            return []
        account_ids = {account.id for account, _person in rows}
        identifiers_by_account: dict[UUID, list[AccountLoginIdentifier]] = {
            account_id: [] for account_id in account_ids
        }
        for identifier in self.db.scalars(
            select(AccountLoginIdentifier)
            .where(AccountLoginIdentifier.account_id.in_(account_ids))
            .order_by(
                AccountLoginIdentifier.account_id,
                AccountLoginIdentifier.kind,
                AccountLoginIdentifier.id,
            )
        ):
            identifiers_by_account[identifier.account_id].append(identifier)

        summaries: list[AccountSummary] = []
        for account, person in rows:
            if person.deleted_at is not None:
                raise AccountServiceError("account person is deleted")
            identifiers = identifiers_by_account[account.id]
            if not identifiers:
                raise AccountServiceError("account has no verified login identifier")
            kinds = {cast(IdentifierKind, identifier.kind) for identifier in identifiers}
            phone: str | None = None
            if "phone" in kinds:
                try:
                    phone = self.identity_service.decrypt_phone(person)
                except IdentityServiceError as exc:
                    raise AccountServiceError("account phone contact is invalid") from exc
                if phone is None:
                    raise AccountServiceError("account phone identifier has no contact")
                normalized_phone = NormalizedLoginIdentifier("phone", phone)
                expected_phone_digest = login_identifier_digest(
                    self.identity_service.lookup,
                    normalized_phone,
                )
                if any(
                    identifier.kind == "phone"
                    and identifier.identifier_digest != expected_phone_digest
                    for identifier in identifiers
                ):
                    raise AccountServiceError("account phone identifier is invalid")
            email: str | None = None
            if "email" in kinds:
                try:
                    email = self.identity_service.decrypt_email(person)
                except IdentityServiceError as exc:
                    raise AccountServiceError("account email contact is invalid") from exc
                if email is None:
                    raise AccountServiceError("account email identifier has no contact")
                normalized_email = NormalizedLoginIdentifier("email", email)
                expected_email_digest = login_identifier_digest(
                    self.identity_service.lookup,
                    normalized_email,
                )
                if any(
                    identifier.kind == "email"
                    and identifier.identifier_digest != expected_email_digest
                    for identifier in identifiers
                ):
                    raise AccountServiceError("account email identifier is invalid")
            # Legacy response fields now choose a stable display contact only.
            # Every verified identifier remains equally valid for login.
            display_kind: IdentifierKind | None = None
            display_contact: str | None = None
            if phone is not None:
                display_kind, display_contact = "phone", phone
            elif email is not None:
                display_kind, display_contact = "email", email
            if display_kind is None or display_contact is None:
                raise AccountServiceError("account has no verified login contact")
            summaries.append(
                AccountSummary(
                    id=account.id,
                    person_id=person.id,
                    full_name=person.full_name,
                    nickname=person.nickname,
                    masked_primary_identifier=(
                        self._mask_phone(display_contact)
                        if display_kind == "phone"
                        else self._mask_email(display_contact)
                    ),
                    primary_identifier_kind=display_kind,
                    masked_phone=self._mask_phone(phone) if phone is not None else None,
                    masked_email=self._mask_email(email) if email is not None else None,
                    verified_identifier_kinds=[
                        kind for kind in ("phone", "email") if kind in kinds
                    ],
                    status=cast(AccountStatus, account.status),
                    force_password_change=account.force_password_change,
                    created_at=account.created_at,
                )
            )
        return summaries

    def _account_summary(self, account: UserAccount, person: Person) -> AccountSummary:
        return self._account_summaries([(account, person)])[0]

    def _translate_integrity_error(self, exc: IntegrityError) -> AccountServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in {
            "user_accounts_login_phone_digest_key",
            "uq_account_login_identifiers_identifier_digest",
        }:
            return LoginPhoneAlreadyExists("login phone already exists")
        if constraint == "user_accounts_person_id_key":
            return AccountAlreadyExists("person already has an account")
        return AccountServiceError("account transaction failed")

    def create_account(
        self,
        input: AccountCreate,
    ) -> AccountCreatedWithTemporaryPassword:
        try:
            with write_transaction(self.db):
                snapshot = self.db.scalar(
                    select(Person)
                    .where(Person.id == input.person_id)
                    .execution_options(populate_existing=True)
                )
                if snapshot is None or snapshot.deleted_at is not None:
                    raise PersonNotFound("person not found")
                fingerprint = person_credential_fingerprint(snapshot)
                old_email = self.identity_service.decrypt_email(snapshot)
                next_email = (
                    normalize_email(str(input.email)) if input.email is not None else old_email
                )
                lock_personnel_credentials(
                    self.db,
                    emails={value for value in (old_email, next_email) if value},
                    identity_digests={snapshot.identity_digest},
                    lookup=self.identity_service.lookup,
                )
                if input.login_phone is None:
                    if snapshot.phone_ciphertext is None:
                        raise PersonNotReady(["phone"])
                    normalized_phone = self.identity_service.cipher.decrypt(
                        snapshot.phone_ciphertext,
                        field_name="phone",
                    )
                else:
                    try:
                        normalized_phone = normalize_phone(input.login_phone)
                    except IdentityValidationError as exc:
                        raise InvalidAccountContact("login_phone", str(exc)) from exc
                if input.email is None:
                    existing_email = old_email
                    if existing_email is None:
                        raise PersonNotReady(["email"])
                    normalized_email = existing_email
                else:
                    normalized_email = normalize_email(str(input.email))
                phone_contact_digest = self.identity_service.lookup.digest(normalized_phone)
                email_contact_digest = self.identity_service.lookup.digest_domain(
                    "person-email",
                    normalized_email,
                )
                # Derive the complete set from the snapshot; reject locked drift below.
                lock_person_memberships(
                    self.db,
                    person_membership_lock_keys(
                        phone_digests={
                            digest
                            for digest in (
                                snapshot.phone_digest,
                                phone_contact_digest if snapshot.phone_digest is None else None,
                            )
                            if digest
                        },
                        email_digests={
                            digest
                            for digest in (
                                snapshot.email_digest,
                                email_contact_digest if snapshot.email_digest is None else None,
                            )
                            if digest
                        },
                    ),
                )
                locked_people = GuardianReadinessPolicy(self.db).lock_students([input.person_id])
                if not locked_people or locked_people[0].deleted_at is not None:
                    raise PersonNotFound("person not found")
                person = locked_people[0]
                if person_credential_fingerprint(person) != fingerprint:
                    raise CredentialReservationConflict()
                profile = self.db.get(AssistedPersonProfile, person.id, populate_existing=True)
                if person.intake_mode == "pending" or (
                    profile is not None and profile.activation_requirement == "reviewed_claim"
                ):
                    raise PersonNotReady(["reviewed_claim"])
                policy = CredentialReservationPolicy(self.db, lookup=self.identity_service.lookup)
                policy.require_reservation_available(
                    kind="identity", digest=person.identity_digest, person_id=person.id
                )
                if next_email is not None:
                    policy.require_reservation_available(
                        kind="email",
                        digest=self.identity_service.lookup.digest_domain(
                            "registration-verification-email", next_email
                        ),
                        person_id=person.id,
                    )
                normalized_login_phone = NormalizedLoginIdentifier(
                    "phone",
                    normalized_phone,
                )
                phone_identifier_digest = login_identifier_digest(
                    self.identity_service.lookup,
                    normalized_login_phone,
                )
                if (
                    self.db.scalar(
                        select(UserAccount.id).where(UserAccount.person_id == input.person_id)
                    )
                    is not None
                ):
                    raise AccountAlreadyExists("person already has an account")
                if (
                    self.db.scalar(
                        select(AccountLoginIdentifier.id).where(
                            AccountLoginIdentifier.identifier_digest == phone_identifier_digest
                        )
                    )
                    is not None
                ):
                    raise LoginPhoneAlreadyExists("login phone already exists")
                readiness = self.identity_service.readiness(
                    input.person_id,
                    reference_date=self.reference_date_provider(),
                    account_candidate=AccountReadinessCandidate(
                        phone=normalized_phone,
                        email=normalized_email,
                    ),
                )
                if not readiness.ready:
                    raise PersonNotReady(readiness.missing)
                if person.phone_digest is None:
                    person.phone_ciphertext = self.identity_service.cipher.encrypt(
                        normalized_phone,
                        field_name="phone",
                    )
                    person.phone_digest = phone_contact_digest
                elif person.phone_digest != phone_contact_digest:
                    raise AccountContactMismatch(
                        "login_phone",
                        "登录手机号与人员档案不一致",
                    )
                existing_email = self.identity_service.decrypt_email(person)
                if existing_email is None:
                    person.email = None
                    person.email_ciphertext = self.identity_service.cipher.encrypt(
                        normalized_email,
                        field_name="email",
                    )
                    person.email_digest = email_contact_digest
                elif existing_email != normalized_email:
                    raise AccountContactMismatch("email", "邮箱与人员档案不一致")
                else:
                    if person.email_ciphertext is None:
                        person.email_ciphertext = self.identity_service.cipher.encrypt(
                            normalized_email,
                            field_name="email",
                        )
                        person.email_digest = email_contact_digest
                    person.email = None

                temporary_password = self.passwords.temporary_password()
                account = UserAccount(
                    person_id=input.person_id,
                    login_phone_digest=None,
                    email_normalized=None,
                    password_hash=self.passwords.hash(temporary_password),
                    status="active",
                    force_password_change=True,
                    failed_login_count=0,
                )
                self.db.add(account)
                self.db.flush()
                self.db.add(
                    AccountLoginIdentifier(
                        account_id=account.id,
                        kind="phone",
                        identifier_digest=phone_identifier_digest,
                        verified_at=datetime.now(UTC),
                        verification_source="admin_created",
                    )
                )
                self.db.flush()
                result = AccountCreatedWithTemporaryPassword(
                    account=self._account_summary(account, person),
                    temporary_password=temporary_password,
                )
            return result
        except IntegrityError as exc:
            raise self._translate_integrity_error(exc) from exc

    def list_accounts(
        self,
        *,
        page: int,
        page_size: int,
        status: AccountStatus | None,
    ) -> Page[AccountSummary]:
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        query = (
            select(UserAccount, Person)
            .join(Person, Person.id == UserAccount.person_id)
            .where(Person.deleted_at.is_(None))
        )
        if status is not None:
            query = query.where(UserAccount.status == status)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = list(
            self.db.execute(
                query.order_by(UserAccount.created_at.desc(), UserAccount.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).tuples()
        )
        return Page[AccountSummary](
            items=self._account_summaries(rows),
            page=page,
            page_size=page_size,
            total=total,
        )

    def _account(self, account_id: UUID) -> UserAccount:
        account = self.db.get(UserAccount, account_id)
        if account is None:
            raise AccountNotFound("account not found")
        return account

    def _management_person_ids(self, person_id: UUID, actor_user_id: UUID | None) -> set[UUID]:
        person_ids = {person_id}
        if actor_user_id is not None:
            actor_person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == actor_user_id)
            )
            if actor_person_id is not None:
                person_ids.add(actor_person_id)
        return person_ids

    def _require_management_authority(
        self, account: UserAccount, actor_user_id: UUID | None
    ) -> None:
        # Internal maintenance calls have no browser actor. Every HTTP mutation
        # supplies the authenticated account ID; callers cannot choose this value.
        if actor_user_id is None:
            return
        actor = self.db.get(UserAccount, actor_user_id)
        actor_person = self.db.get(Person, actor.person_id) if actor is not None else None
        if (
            actor is None
            or actor.status != "active"
            or actor_person is None
            or actor_person.deleted_at is not None
        ):
            raise AccountManagementDenied("account management is not allowed")
        rbac = RbacService(self.db)
        tokens = rbac.person_resource_tokens(
            actor_person_id=actor.person_id, target_person_id=account.person_id
        )
        if not rbac.decide(
            user_id=actor.id, permission="accounts.manage", resource_tokens=tokens
        ).allowed:
            raise AccountManagementDenied("account management is not allowed")
        if not rbac.account_has_role(actor.id, "super_admin"):
            if rbac.account_has_role(account.id, "super_admin"):
                raise SuperAdminAccountProtected("only a super administrator may manage this account")
            from app.rbac.account_protection import protected_management_target

            if protected_management_target(self.db, account.id):
                raise AccountManagementDenied("only a super administrator may manage this account")

    def _lock_managed_account(self, account_id: UUID, actor_user_id: UUID | None) -> UserAccount:
        person_id = self.db.scalar(
            select(UserAccount.person_id).where(UserAccount.id == account_id)
        )
        if person_id is None:
            raise AccountNotFound("account not found")
        person_ids = self._management_person_ids(person_id, actor_user_id)
        readiness = GuardianReadinessPolicy(self.db)
        readiness.lock_students(person_ids)
        accounts = readiness.lock_accounts(person_ids)
        account = next((row for row in accounts if row.id == account_id), None)
        if account is None:
            raise AccountNotFound("account not found")
        self._require_management_authority(account, actor_user_id)
        return account

    def disable_account(
        self,
        account_id: UUID,
        *,
        actor_user_id: UUID | None,
        request_id: str,
    ) -> None:
        with write_transaction(self.db):
            person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == account_id)
            )
            if person_id is None:
                raise AccountNotFound("account not found")
            readiness_policy = GuardianReadinessPolicy(self.db)
            rbac = RbacService(self.db)
            dependent_person_ids = tuple(
                readiness_policy.guardian_and_dependent_person_ids(person_id)
            )
            person_ids = set(dependent_person_ids) | self._management_person_ids(
                person_id, actor_user_id
            )
            person_ids.update(rbac.active_super_admin_person_ids())
            readiness_policy.lock_students(person_ids)
            locked_accounts = readiness_policy.lock_accounts(person_ids)
            account_by_id = {account.id: account for account in locked_accounts}
            account = account_by_id.get(account_id)
            if account is None:
                raise AccountNotFound("account not found")
            self._require_management_authority(account, actor_user_id)
            active_admin_ids = set(rbac.active_super_admin_account_ids())
            active_admins = [
                locked_account
                for locked_account in locked_accounts
                if locked_account.id in active_admin_ids and locked_account.status == "active"
            ]
            protects_recovery = account.id in {admin.id for admin in active_admins}
            account.status = "disabled"
            self.db.flush()
            if protects_recovery and not rbac.has_effective_recovery_super_admin(active_admins):
                raise LastActiveAdminProtected(
                    "the last effective super administrator is protected"
                )
            now = datetime.now(UTC)
            from app.auth.account_readiness import AccountAccessReadinessPolicy

            readiness_policy.disable_unready_dependents(
                account.person_id,
                reference_date=business_today(),
                revoked_at=now,
                audit_actor_user_id=actor_user_id,
                audit_request_id=request_id,
                dependent_person_ids=dependent_person_ids,
                locked_accounts=locked_accounts,
                additional_session_account_ids=(account.id,),
                account_readiness=AccountAccessReadinessPolicy(
                    self.db, cipher=self.identity_service.cipher, lookup=self.identity_service.lookup
                ),
            )

    def delete_account(
        self,
        account_id: UUID,
        *,
        actor_user_id: UUID | None,
        request_id: str,
    ) -> None:
        """Soft-delete an account while retaining its historical business records.

        Accounts are tied to people and many historical tables deliberately use
        restrictive foreign keys. Marking the person deleted keeps those records
        available for reporting while making the account immediately unusable and
        removing it from the account directory.
        """
        from app.team_management.lifecycle import TeamLifecycleService
        from app.team_management.models import TeamManagerAssignment, TeamMembership
        from app.team_management.staff_service import staff_team_ids_for_account
        from app.team_management.write_support import lock_directory

        with atomic_write_transaction(self.db):
            person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == account_id)
            )
            if person_id is None:
                raise AccountNotFound("account not found")

            readiness_policy = GuardianReadinessPolicy(self.db)
            rbac = RbacService(self.db)
            dependent_person_ids = tuple(
                readiness_policy.guardian_and_dependent_person_ids(person_id)
            )
            person_ids = set(dependent_person_ids) | self._management_person_ids(
                person_id, actor_user_id
            )
            person_ids.update(rbac.active_super_admin_person_ids())
            readiness_policy.lock_students(person_ids)
            locked_accounts = readiness_policy.lock_accounts(person_ids)
            account_by_id = {account.id: account for account in locked_accounts}
            account = account_by_id.get(account_id)
            if account is None:
                raise AccountNotFound("account not found")
            self._require_management_authority(account, actor_user_id)

            person = self.db.scalar(
                select(Person)
                .where(Person.id == account.person_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if person is None or person.deleted_at is not None:
                raise AccountNotFound("account not found")

            active_admin_ids = set(rbac.active_super_admin_account_ids())
            active_admins = [
                locked_account
                for locked_account in locked_accounts
                if locked_account.id in active_admin_ids and locked_account.status == "active"
            ]
            protects_recovery = account.id in {admin.id for admin in active_admins}
            remaining_admins = [admin for admin in active_admins if admin.id != account.id]
            if protects_recovery and not rbac.has_effective_recovery_super_admin(remaining_admins):
                raise LastActiveAdminProtected(
                    "the last effective super administrator is protected"
                )

            # Identity locks freeze roster changes for this target. Discover
            # every directory participant after those locks, before directory locks.
            team_ids = set(
                self.db.scalars(
                    select(TeamMembership.team_id).where(
                        TeamMembership.person_id == account.person_id,
                        TeamMembership.ended_at.is_(None),
                    )
                )
            )
            team_ids.update(
                self.db.scalars(
                    select(TeamManagerAssignment.team_id).where(
                        TeamManagerAssignment.account_id == account.id,
                        TeamManagerAssignment.ended_at.is_(None),
                    )
                )
            )
            team_ids.update(staff_team_ids_for_account(self.db, account.id))
            lock_directory(self.db, list(team_ids))
            now = datetime.now(UTC)
            TeamLifecycleService(self.db).close_for_deleted_account(account.id, actor_user_id, now)
            account.status = "disabled"
            person.deleted_at = now
            self.db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == account.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            self.db.flush()
            from app.auth.account_readiness import AccountAccessReadinessPolicy

            readiness_policy.disable_unready_dependents(
                account.person_id,
                reference_date=business_today(),
                revoked_at=now,
                audit_actor_user_id=actor_user_id,
                audit_request_id=request_id,
                dependent_person_ids=dependent_person_ids,
                locked_accounts=locked_accounts,
                additional_session_account_ids=(account.id,),
                account_readiness=AccountAccessReadinessPolicy(
                    self.db, cipher=self.identity_service.cipher, lookup=self.identity_service.lookup
                ),
            )

            AuditService(self.db).append(
                actor_user_id=actor_user_id,
                action="account.delete",
                resource_type="account",
                resource_id=account.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id,
                metadata={},
            )

    def enable_account(self, account_id: UUID, *, actor_user_id: UUID | None = None) -> None:
        with write_transaction(self.db):
            account = self._lock_managed_account(account_id, actor_user_id)
            reference_date = self.reference_date_provider()
            readiness = self.identity_service.readiness(
                account.person_id,
                reference_date=reference_date,
            )
            if account.account_access_origin != "legacy":
                from app.auth.account_readiness import AccountAccessReadinessPolicy

                projection = AccountAccessReadinessPolicy(
                    self.db, cipher=self.identity_service.cipher, lookup=self.identity_service.lookup
                ).evaluate_one(account, reference_date=reference_date)
                missing = [item for item in readiness.missing if item != "guardian"]
                missing.extend(item for item in projection.missing if item not in missing)
                if missing:
                    raise PersonNotReady(missing)
                account.status = "active"
                return
            if not readiness.ready:
                raise PersonNotReady(readiness.missing)
            account.status = "active"

    def reset_password(
        self, account_id: UUID, *, actor_user_id: UUID | None = None
    ) -> TemporaryPassword:
        with write_transaction(self.db):
            lock_password_credentials_in_transaction(self.db, account_id)
            account = self._lock_managed_account(account_id, actor_user_id)
            temporary_password = self.passwords.temporary_password()
            account.password_hash = self.passwords.hash(temporary_password)
            account.force_password_change = True
            invalidate_password_reset_challenges_in_transaction(
                self.db, account_id=account.id, invalidated_at=datetime.now(UTC)
            )
            self.db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == account.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC))
            )
            result = TemporaryPassword(
                account_id=account.id,
                temporary_password=temporary_password,
            )
        return result
