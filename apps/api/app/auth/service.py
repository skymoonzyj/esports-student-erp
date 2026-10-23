from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import AuthRateLimit, AuthSession, UserAccount
from app.auth.schemas import (
    AccountCreate,
    AccountCreatedWithTemporaryPassword,
    AccountStatus,
    AccountSummary,
    AuthUser,
    TemporaryPassword,
)
from app.core.time import business_date_at, business_today
from app.core.transactions import write_transaction
from app.identity.models import Person
from app.identity.normalization import IdentityValidationError, normalize_phone
from app.identity.readiness import GuardianReadinessPolicy
from app.identity.schemas import AccountReadinessCandidate, Page
from app.identity.service import IdentityService, PersonNotFound
from app.rbac.service import RbacService
from app.security.encryption import LookupDigest
from app.security.passwords import PasswordService

_DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$JNj0f4PvY4p4nRkmz05qOA$"
    "/maXKxtsguN2hnljmKWpByz5rf/P6D1WBxeWDHVHA0g"
)
_EMAIL_ADAPTER = TypeAdapter(EmailStr)


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


class InvalidCredentials(ValueError):
    pass


class InvalidSession(ValueError):
    pass


class InvalidCsrfToken(ValueError):
    pass


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

    def key_digest(self, phone_digest: str, ip_address: str | None) -> str:
        material = f"{phone_digest}|{self._normalize_ip(ip_address)}".encode()
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

    def is_blocked(self, key_digest: str) -> bool:
        row = self.db.scalar(
            select(AuthRateLimit).where(AuthRateLimit.key_digest == key_digest).with_for_update()
        )
        return bool(row and row.blocked_until and row.blocked_until > self.clock())

    def record_failure(self, key_digest: str) -> None:
        now = self.clock()
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
        row = self.db.scalar(
            select(AuthRateLimit).where(AuthRateLimit.key_digest == key_digest).with_for_update()
        )
        if row is None:  # pragma: no cover - protected by the insert above
            raise RuntimeError("rate limit row was not created")
        if row.window_started_at < now - self.window_duration:
            row.attempts = 0
            row.window_started_at = now
            row.blocked_until = None
        row.attempts += 1
        row.updated_at = now
        if row.attempts >= self.max_attempts:
            row.blocked_until = now + self.block_duration

    def clear(self, key_digest: str) -> None:
        self.db.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest == key_digest))


class AuthService:
    def __init__(
        self,
        db: Session,
        *,
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

    def _phone_digest(self, phone: str) -> str:
        try:
            return self.lookup.digest(normalize_phone(phone))
        except IdentityValidationError:
            return self.lookup.digest(f"invalid:{phone.strip()}")

    @staticmethod
    def _normalized_email(value: str) -> str | None:
        try:
            return str(_EMAIL_ADAPTER.validate_python(value.strip())).lower()
        except ValidationError:
            return None

    def _identifier_lookup(self, identifier: str) -> tuple[str, str]:
        """Return the lookup kind and value without revealing which kind matched."""
        raw = identifier.strip()
        try:
            return "phone", self.lookup.digest(normalize_phone(raw))
        except IdentityValidationError:
            pass

        normalized_email = self._normalized_email(raw)
        if normalized_email is not None:
            return "email", normalized_email
        return "invalid", self.lookup.digest(f"invalid:{raw}")

    def _auth_user(self, context: AuthContext) -> AuthUser:
        rbac = RbacService(self.db)
        global_permissions = rbac.allowed_permissions(
            user_id=context.account.id,
            resource_tokens={"global"},
        )
        return AuthUser(
            account_id=context.account.id,
            person_id=context.person.id,
            full_name=context.person.full_name,
            email=context.account.email_normalized,
            force_password_change=context.account.force_password_change,
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
        account_id = readiness_policy.disable_student_account_if_pending(
            account.person_id,
            reference_date=business_date_at(now),
            revoked_at=now,
            audit_actor_user_id=account.id,
            audit_request_id=request_id,
            locked_account=account,
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
        phone: str | None = None,
        password: str,
        ip_address: str | None,
        request_id: str,
        identifier: str | None = None,
    ) -> LoginResult:
        login_identifier = identifier if identifier is not None else phone
        if login_identifier is None:
            login_identifier = ""
        lookup_kind, lookup_value = self._identifier_lookup(login_identifier)
        account_lookup = (
            UserAccount.email_normalized == lookup_value
            if lookup_kind == "email"
            else UserAccount.login_phone_digest == lookup_value
        )
        rate_identifier = (
            self.lookup.digest_domain("auth-login-email", lookup_value)
            if lookup_kind == "email"
            else lookup_value
        )
        rate_key = self.rate_limiter.key_digest(rate_identifier, ip_address)
        result: LoginResult | None = None
        with write_transaction(self.db):
            self.rate_limiter.prune_expired(limit=500)
            person_id = self.db.scalar(
                select(UserAccount.person_id).where(account_lookup)
            )
            readiness_policy = GuardianReadinessPolicy(self.db)
            locked_people = readiness_policy.lock_students(
                [person_id] if person_id is not None else []
            )
            account = (
                self.db.scalar(
                    select(UserAccount)
                    .where(account_lookup)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if person_id is not None
                else None
            )
            now = self.clock()
            rate_blocked = self.rate_limiter.is_blocked(rate_key)
            account_locked = bool(account and account.locked_until and account.locked_until > now)
            encoded = account.password_hash if account is not None else _DUMMY_PASSWORD_HASH
            password_valid = (
                False
                if rate_blocked or account_locked
                else self.passwords.verify(password, encoded)
            )
            accepted = bool(
                account
                and account.status == "active"
                and not rate_blocked
                and not account_locked
                and password_valid
            )
            if not accepted:
                if not rate_blocked:
                    self.rate_limiter.record_failure(rate_key)
                if account is not None and not account_locked:
                    if account.locked_until is not None and account.locked_until <= now:
                        account.failed_login_count = 0
                        account.locked_until = None
                    account.failed_login_count += 1
                    if account.failed_login_count >= 5:
                        account.locked_until = now + timedelta(minutes=15)
            else:
                assert account is not None
                account.failed_login_count = 0
                account.locked_until = None
                self.rate_limiter.clear(rate_key)
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
            raise InvalidCredentials("invalid credentials")
        return result

    def authenticate(self, token: str | None, *, request_id: str) -> AuthContext:
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
            locked_people = readiness_policy.lock_students([person_id])
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.person_id == person_id)
                .with_for_update()
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

    def _person(self, person_id: UUID) -> Person:
        person = self.db.scalar(
            select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
        )
        if person is None:
            raise PersonNotFound("person not found")
        return person

    def _account_summary(self, account: UserAccount, person: Person) -> AccountSummary:
        if person.phone_ciphertext is None:
            raise AccountServiceError("account person has no phone")
        phone = self.identity_service.cipher.decrypt(
            person.phone_ciphertext,
            field_name="phone",
        )
        return AccountSummary(
            id=account.id,
            person_id=person.id,
            full_name=person.full_name,
            masked_login_phone=self._mask_phone(phone),
            email=account.email_normalized,
            status=cast(AccountStatus, account.status),
            force_password_change=account.force_password_change,
            created_at=account.created_at,
        )

    def _translate_integrity_error(self, exc: IntegrityError) -> AccountServiceError:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "user_accounts_login_phone_digest_key":
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
                locked_people = GuardianReadinessPolicy(self.db).lock_students([input.person_id])
                if not locked_people or locked_people[0].deleted_at is not None:
                    raise PersonNotFound("person not found")
                person = locked_people[0]
                if input.login_phone is None:
                    if person.phone_ciphertext is None:
                        raise PersonNotReady(["phone"])
                    normalized_phone = self.identity_service.cipher.decrypt(
                        person.phone_ciphertext,
                        field_name="phone",
                    )
                else:
                    try:
                        normalized_phone = normalize_phone(input.login_phone)
                    except IdentityValidationError as exc:
                        raise InvalidAccountContact("login_phone", str(exc)) from exc
                if input.email is None:
                    if person.email is None:
                        raise PersonNotReady(["email"])
                    normalized_email = person.email.strip().lower()
                else:
                    normalized_email = str(input.email).strip().lower()
                phone_digest = self.identity_service.lookup.digest(normalized_phone)
                if (
                    self.db.scalar(
                        select(UserAccount.id).where(UserAccount.person_id == input.person_id)
                    )
                    is not None
                ):
                    raise AccountAlreadyExists("person already has an account")
                if (
                    self.db.scalar(
                        select(UserAccount.id).where(UserAccount.login_phone_digest == phone_digest)
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
                    person.phone_digest = phone_digest
                elif person.phone_digest != phone_digest:
                    raise AccountContactMismatch(
                        "login_phone",
                        "登录手机号与人员档案不一致",
                    )
                if person.email is None:
                    person.email = normalized_email
                elif person.email.strip().lower() != normalized_email:
                    raise AccountContactMismatch("email", "邮箱与人员档案不一致")

                temporary_password = self.passwords.temporary_password()
                account = UserAccount(
                    person_id=input.person_id,
                    login_phone_digest=phone_digest,
                    email_normalized=normalized_email,
                    password_hash=self.passwords.hash(temporary_password),
                    status="active",
                    force_password_change=True,
                    failed_login_count=0,
                )
                self.db.add(account)
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
        query = select(UserAccount, Person).join(Person, Person.id == UserAccount.person_id)
        if status is not None:
            query = query.where(UserAccount.status == status)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(
            query.order_by(UserAccount.created_at.desc(), UserAccount.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return Page[AccountSummary](
            items=[self._account_summary(account, person) for account, person in rows],
            page=page,
            page_size=page_size,
            total=total,
        )

    def _account(self, account_id: UUID) -> UserAccount:
        account = self.db.get(UserAccount, account_id)
        if account is None:
            raise AccountNotFound("account not found")
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
            person_ids = set(dependent_person_ids)
            person_ids.update(rbac.active_admin_person_ids())
            readiness_policy.lock_students(person_ids)
            locked_accounts = readiness_policy.lock_accounts(person_ids)
            account_by_id = {account.id: account for account in locked_accounts}
            account = account_by_id.get(account_id)
            if account is None:
                raise AccountNotFound("account not found")
            active_admin_ids = set(rbac.active_admin_account_ids())
            active_admins = [
                locked_account
                for locked_account in locked_accounts
                if locked_account.id in active_admin_ids
                and locked_account.status == "active"
            ]
            protects_recovery = account.id in {admin.id for admin in active_admins}
            account.status = "disabled"
            self.db.flush()
            if protects_recovery and not rbac.has_effective_recovery_admin(active_admins):
                raise LastActiveAdminProtected(
                    "the last effective administrator is protected"
                )
            now = datetime.now(UTC)
            readiness_policy.disable_unready_dependents(
                account.person_id,
                reference_date=business_today(),
                revoked_at=now,
                audit_actor_user_id=actor_user_id,
                audit_request_id=request_id,
                dependent_person_ids=dependent_person_ids,
                locked_accounts=locked_accounts,
                additional_session_account_ids=(account.id,),
            )

    def enable_account(self, account_id: UUID) -> None:
        with write_transaction(self.db):
            person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == account_id)
            )
            if person_id is None:
                raise AccountNotFound("account not found")
            GuardianReadinessPolicy(self.db).lock_students([person_id])
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.id == account_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if account is None:
                raise AccountNotFound("account not found")
            readiness = self.identity_service.readiness(
                account.person_id,
                reference_date=self.reference_date_provider(),
            )
            if not readiness.ready:
                raise PersonNotReady(readiness.missing)
            account.status = "active"

    def reset_password(self, account_id: UUID) -> TemporaryPassword:
        with write_transaction(self.db):
            person_id = self.db.scalar(
                select(UserAccount.person_id).where(UserAccount.id == account_id)
            )
            if person_id is None:
                raise AccountNotFound("account not found")
            GuardianReadinessPolicy(self.db).lock_students([person_id])
            account = self.db.scalar(
                select(UserAccount)
                .where(UserAccount.id == account_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if account is None:
                raise AccountNotFound("account not found")
            temporary_password = self.passwords.temporary_password()
            account.password_hash = self.passwords.hash(temporary_password)
            account.force_password_change = True
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
