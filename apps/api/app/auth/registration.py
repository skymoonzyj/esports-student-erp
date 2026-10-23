from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.mailer import RegistrationEmailSender
from app.auth.models import RegistrationRequest, UserAccount
from app.core.time import business_today
from app.core.transactions import write_transaction
from app.identity.models import Person, StudentProfile
from app.identity.normalization import IdentityValidationError, normalize_cn_id, normalize_phone
from app.identity.readiness import is_minor
from app.identity.schemas import StaffCreate, StaffType, StudentCreate
from app.identity.service import IdentityService
from app.rbac.models import Role, UserRole
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

RegistrationPersonType = Literal["student", "guardian", "staff"]


class RegistrationError(ValueError):
    code = "registration_error"


class RegistrationConflict(RegistrationError):
    code = "registration_conflict"


class RegistrationNotFound(RegistrationError):
    code = "registration_not_found"


class RegistrationExpired(RegistrationError):
    code = "registration_expired"


class RegistrationCodeInvalid(RegistrationError):
    code = "registration_code_invalid"


class RegistrationCodeTooSoon(RegistrationError):
    code = "registration_code_too_soon"


class RegistrationDeliveryError(RegistrationError):
    code = "registration_delivery_failed"


@dataclass(frozen=True)
class RegistrationStartResult:
    registration_id: UUID
    expires_at: datetime


@dataclass(frozen=True)
class RegistrationVerifyResult:
    registration_id: UUID
    person_id: UUID
    account_id: UUID
    account_status: Literal["active", "disabled"]
    requires_guardian: bool


class RegistrationService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        lookup: LookupDigest,
        passwords: PasswordService,
        session_token_pepper: bytes,
        email_sender: RegistrationEmailSender,
        code_ttl: timedelta = timedelta(minutes=10),
        resend_interval: timedelta = timedelta(seconds=60),
        clock: Callable[[], datetime] | None = None,
        reference_date_provider: Callable[[], date] = business_today,
    ) -> None:
        if len(session_token_pepper) != 32:
            raise ValueError("session token pepper must be 32 bytes")
        self.db = db
        self.cipher = cipher
        self.lookup = lookup
        self.passwords = passwords
        self.pepper = session_token_pepper
        self.email_sender = email_sender
        self.code_ttl = code_ttl
        self.resend_interval = resend_interval
        self.clock = clock or (lambda: datetime.now(UTC))
        self.reference_date_provider = reference_date_provider

    def _code_digest(self, code: str) -> str:
        return hmac.new(self.pepper, code.encode("ascii"), hashlib.sha256).hexdigest()

    @staticmethod
    def _new_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def _identity_service(self) -> IdentityService:
        return IdentityService(self.db, cipher=self.cipher, lookup=self.lookup)

    def _ensure_unique_contacts(self, *, identity_digest: str, phone_digest: str, email: str) -> None:
        if self.db.scalar(select(Person.id).where(Person.identity_digest == identity_digest)):
            raise RegistrationConflict("identity is already registered")
        if self.db.scalar(select(Person.id).where(Person.phone_digest == phone_digest)):
            raise RegistrationConflict("phone is already registered")
        if self.db.scalar(select(UserAccount.id).where(UserAccount.email_normalized == email)):
            raise RegistrationConflict("email is already registered")
        if self.db.scalar(
            select(RegistrationRequest.id).where(
                RegistrationRequest.status == "pending_verification",
                (
                    (RegistrationRequest.identity_digest == identity_digest)
                    | (RegistrationRequest.phone_digest == phone_digest)
                    | (RegistrationRequest.email_normalized == email)
                ),
            )
        ):
            raise RegistrationConflict("registration is already pending verification")

    def _validate_inputs(
        self,
        *,
        identity_number: str,
        birth_date: date,
        sex: str,
        phone: str,
        email: str,
        password: str,
    ) -> tuple[str, str, str]:
        identity = normalize_cn_id(identity_number, expected_birth_date=birth_date)
        normalized_phone = normalize_phone(phone)
        if identity.sex != sex:
            raise IdentityValidationError(
                field="sex",
                code="identity_sex_mismatch",
                message="性别与身份证不一致",
            )
        normalized_email = email.strip().lower()
        self.passwords.validate(password)
        return identity.number, normalized_phone, normalized_email

    def start(
        self,
        *,
        full_name: str,
        identity_number: str,
        sex: str,
        date_of_birth: date,
        phone: str,
        email: str,
        password: str,
        person_type: RegistrationPersonType,
        staff_type: StaffType | None = None,
    ) -> RegistrationStartResult:
        normalized_identity, normalized_phone, normalized_email = self._validate_inputs(
            identity_number=identity_number,
            birth_date=date_of_birth,
            sex=sex,
            phone=phone,
            email=email,
            password=password,
        )
        now = self.clock()
        code = self._new_code()
        expires_at = now + self.code_ttl
        with write_transaction(self.db):
            identity_digest = self.lookup.digest(normalized_identity)
            phone_digest = self.lookup.digest(normalized_phone)
            self._ensure_unique_contacts(
                identity_digest=identity_digest,
                phone_digest=phone_digest,
                email=normalized_email,
            )
            if person_type == "staff" and staff_type is None:
                raise RegistrationError("staff type is required")
            request = RegistrationRequest(
                full_name=full_name.strip(),
                identity_ciphertext=self.cipher.encrypt(
                    normalized_identity, field_name="identity_number"
                ),
                identity_digest=identity_digest,
                phone_ciphertext=self.cipher.encrypt(normalized_phone, field_name="phone"),
                phone_digest=phone_digest,
                email_normalized=normalized_email,
                sex=sex,
                date_of_birth=date_of_birth,
                requested_person_type=person_type,
                requested_staff_type=staff_type,
                password_hash=self.passwords.hash(password),
                verification_code_digest=self._code_digest(code),
                verification_expires_at=expires_at,
                verification_attempts=0,
                last_code_sent_at=now,
                status="pending_verification",
            )
            self.db.add(request)
            self.db.flush()
            result = RegistrationStartResult(request.id, expires_at)
        try:
            self.email_sender.send_verification(
                recipient=normalized_email,
                code=code,
                expires_at=expires_at,
            )
        except Exception as exc:
            with write_transaction(self.db):
                self.db.execute(
                    delete(RegistrationRequest).where(RegistrationRequest.id == result.registration_id)
                )
            raise RegistrationDeliveryError("verification email could not be sent") from exc
        return result

    def resend(self, registration_id: UUID) -> RegistrationStartResult:
        now = self.clock()
        code = self._new_code()
        expires_at = now + self.code_ttl
        with write_transaction(self.db):
            request = self.db.scalar(
                select(RegistrationRequest)
                .where(RegistrationRequest.id == registration_id)
                .with_for_update()
            )
            if request is None:
                raise RegistrationNotFound("registration not found")
            if request.status != "pending_verification":
                raise RegistrationExpired("registration is no longer pending")
            if request.last_code_sent_at + self.resend_interval > now:
                raise RegistrationCodeTooSoon("verification code was sent too recently")
            request.verification_code_digest = self._code_digest(code)
            request.verification_expires_at = expires_at
            request.verification_attempts = 0
            request.last_code_sent_at = now
        try:
            self.email_sender.send_verification(
                recipient=request.email_normalized,
                code=code,
                expires_at=expires_at,
            )
        except Exception as exc:
            raise RegistrationDeliveryError("verification email could not be sent") from exc
        return RegistrationStartResult(registration_id, expires_at)

    def verify(self, *, registration_id: UUID, code: str) -> RegistrationVerifyResult:
        now = self.clock()
        with write_transaction(self.db):
            request = self.db.scalar(
                select(RegistrationRequest)
                .where(RegistrationRequest.id == registration_id)
                .with_for_update()
            )
            if request is None:
                raise RegistrationNotFound("registration not found")
            if request.status != "pending_verification":
                raise RegistrationExpired("registration is no longer pending")
            if request.verification_expires_at <= now:
                request.status = "expired"
                raise RegistrationExpired("verification code expired")
            if request.verification_attempts >= 5:
                request.status = "expired"
                raise RegistrationCodeInvalid("too many verification attempts")
            if not hmac.compare_digest(request.verification_code_digest, self._code_digest(code.strip())):
                request.verification_attempts += 1
                if request.verification_attempts >= 5:
                    request.status = "expired"
                raise RegistrationCodeInvalid("invalid verification code")

            identity = self._identity_service()
            if request.requested_person_type == "student":
                person_summary = identity.create_student(
                    StudentCreate(
                        full_name=request.full_name,
                        identity_number=self.cipher.decrypt(
                            request.identity_ciphertext, field_name="identity_number"
                        ),
                        sex=request.sex,
                        date_of_birth=request.date_of_birth,
                        phone=self.cipher.decrypt(request.phone_ciphertext, field_name="phone"),
                        email=request.email_normalized,
                        student_number=f"REG-{request.id.hex[:16]}",
                    )
                )
                profile = self.db.get(StudentProfile, person_summary.id)
                assert profile is not None
                requires_guardian = is_minor(
                    request.date_of_birth, self.reference_date_provider()
                )
                profile.status = "draft" if requires_guardian else "active"
                role_key = "student"
            elif request.requested_person_type == "guardian":
                from app.identity.schemas import GuardianCreate

                person_summary = identity.create_guardian(
                    GuardianCreate(
                        full_name=request.full_name,
                        identity_number=self.cipher.decrypt(
                            request.identity_ciphertext, field_name="identity_number"
                        ),
                        sex=request.sex,
                        date_of_birth=request.date_of_birth,
                        phone=self.cipher.decrypt(request.phone_ciphertext, field_name="phone"),
                        email=request.email_normalized,
                    )
                )
                requires_guardian = False
                role_key = "guardian"
            else:
                staff_type = request.requested_staff_type
                if staff_type is None:
                    raise RegistrationError("staff type is required")
                person_summary = identity.create_staff(
                    StaffCreate(
                        full_name=request.full_name,
                        identity_number=self.cipher.decrypt(
                            request.identity_ciphertext, field_name="identity_number"
                        ),
                        sex=request.sex,
                        date_of_birth=request.date_of_birth,
                        phone=self.cipher.decrypt(request.phone_ciphertext, field_name="phone"),
                        email=request.email_normalized,
                        staff_type=staff_type,
                    )
                )
                requires_guardian = False
                role_key = {
                    "coach": "coach",
                    "life_teacher": "life_teacher",
                    "customer_service": "customer_service",
                    "administration": "staff",
                    "other": "staff",
                }[staff_type]
            phone_digest = request.phone_digest
            account = UserAccount(
                person_id=person_summary.id,
                login_phone_digest=phone_digest,
                email_normalized=request.email_normalized,
                password_hash=request.password_hash,
                status=(
                    "active"
                    if request.requested_person_type in {"student", "guardian"}
                    and not requires_guardian
                    else "disabled"
                ),
                force_password_change=False,
                failed_login_count=0,
            )
            self.db.add(account)
            self.db.flush()
            role = self.db.scalar(select(Role).where(Role.key == role_key))
            if role is None:
                raise RegistrationError("default role is not configured")
            self.db.add(UserRole(user_id=account.id, role_id=role.id))
            request.status = "completed"
            request.verified_at = now
            request.person_id = person_summary.id
            request.account_id = account.id
            return RegistrationVerifyResult(
                registration_id=request.id,
                person_id=person_summary.id,
                account_id=account.id,
                account_status=account.status,  # type: ignore[arg-type]
                requires_guardian=requires_guardian,
            )
