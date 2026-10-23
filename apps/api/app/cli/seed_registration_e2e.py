from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.auth.identifiers import login_identifier_digest, normalize_login_identifier
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2ESeedRefused,
    _make_cn_id,
    _require_e2e_environment,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.identity.models import Person, StaffProfile, StudentProfile
from app.identity.normalization import normalize_email
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.registration.delivery import registration_test_destination_alias
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

REGISTRATION_EXACT_CLAIM_PERSON_ID = UUID(
    "f6100000-0000-4000-8000-000000000001"
)
REGISTRATION_EXISTING_PERSON_ID = UUID("f6100000-0000-4000-8000-000000000002")
REGISTRATION_SPLIT_IDENTITY_PERSON_ID = UUID(
    "f6100000-0000-4000-8000-000000000003"
)
REGISTRATION_SPLIT_CONTACT_PERSON_ID = UUID(
    "f6100000-0000-4000-8000-000000000004"
)
REGISTRATION_EMAIL_ONLY_PERSON_ID = UUID(
    "f6100000-0000-4000-8000-000000000005"
)
REGISTRATION_REVIEWER_PERSON_ID = UUID("f6100000-0000-4000-8000-000000000006")
REGISTRATION_EXISTING_ACCOUNT_ID = UUID("f6110000-0000-4000-8000-000000000002")
REGISTRATION_EMAIL_ONLY_ACCOUNT_ID = UUID("f6110000-0000-4000-8000-000000000005")
REGISTRATION_REVIEWER_ACCOUNT_ID = UUID("f6110000-0000-4000-8000-000000000006")
REGISTRATION_EXISTING_IDENTIFIER_ID = UUID(
    "f6120000-0000-4000-8000-000000000002"
)
REGISTRATION_EXISTING_EMAIL_IDENTIFIER_ID = UUID(
    "f6120000-0000-4000-8000-000000000102"
)
REGISTRATION_EMAIL_ONLY_IDENTIFIER_ID = UUID(
    "f6120000-0000-4000-8000-000000000005"
)
REGISTRATION_REVIEWER_IDENTIFIER_ID = UUID(
    "f6120000-0000-4000-8000-000000000006"
)
REGISTRATION_REVIEW_OVERRIDE_ID = UUID("f6130000-0000-4000-8000-000000000006")

EXACT_CLAIM_PHONE = "13600009001"
EXISTING_ACCOUNT_PHONE = E2E_CREDENTIALS["registration_existing"].identifier
EXISTING_ACCOUNT_EMAIL = "registration-existing-account@example.com"
SPLIT_CONFLICT_PHONE = "13600009003"
REVIEWER_PHONE = E2E_CREDENTIALS["registration_reviewer"].identifier
EMAIL_ONLY_EMAIL = E2E_CREDENTIALS["registration_email_only"].identifier
EXISTING_ACCOUNT_PASSWORD = E2E_CREDENTIALS["registration_existing"].password
EMAIL_ONLY_PASSWORD = E2E_CREDENTIALS["registration_email_only"].password
REVIEWER_PASSWORD = E2E_CREDENTIALS["registration_reviewer"].password

REGISTRATION_E2E_DESTINATIONS = {
    "adult_phone": "13600009101",
    "adult_email": "registration-adult-e2e@example.com",
    "exact_claim": EXACT_CLAIM_PHONE,
    "existing_account": EXISTING_ACCOUNT_PHONE,
    "existing_account_recovery": EXISTING_ACCOUNT_EMAIL,
    "split_conflict": SPLIT_CONFLICT_PHONE,
    "email_only": EMAIL_ONLY_EMAIL,
    "minor_student": "13600009102",
    "minor_guardian": "13600009103",
    "adult_optional_guardian": "13600009104",
    "staff_applicant": "13600009105",
    "privacy_applicant": "13600009106",
}


@dataclass(frozen=True, slots=True)
class RegistrationE2ESeedResult:
    exact_claim_person_id: UUID
    existing_account_id: UUID
    split_identity_person_id: UUID
    split_contact_person_id: UUID
    email_only_account_id: UUID
    reviewer_account_id: UUID
    destination_aliases: tuple[tuple[str, str], ...]

    def public_payload(self) -> dict[str, dict[str, str]]:
        return {
            "fixture_ids": {
                "exact_claim_person": str(self.exact_claim_person_id),
                "existing_account": str(self.existing_account_id),
                "split_identity_person": str(self.split_identity_person_id),
                "split_contact_person": str(self.split_contact_person_id),
                "email_only_account": str(self.email_only_account_id),
                "reviewer_account": str(self.reviewer_account_id),
            },
            "destination_aliases": dict(self.destination_aliases),
        }


@dataclass(frozen=True, slots=True)
class _PersonFixture:
    person_id: UUID
    full_name: str
    birth: str
    sequence: int
    sex: str
    phone: str | None = None
    email: str | None = None

    @property
    def date_of_birth(self) -> date:
        return date.fromisoformat(
            f"{self.birth[:4]}-{self.birth[4:6]}-{self.birth[6:]}"
        )


_PEOPLE = (
    _PersonFixture(
        REGISTRATION_EXACT_CLAIM_PERSON_ID,
        "E2E注册精确认领学员",
        "20000102",
        211,
        "male",
        phone=EXACT_CLAIM_PHONE,
        email="registration-exact-claim@example.com",
    ),
    _PersonFixture(
        REGISTRATION_EXISTING_PERSON_ID,
        "E2E注册既有账号学员",
        "19990102",
        212,
        "female",
        phone=EXISTING_ACCOUNT_PHONE,
        email=EXISTING_ACCOUNT_EMAIL,
    ),
    _PersonFixture(
        REGISTRATION_SPLIT_IDENTITY_PERSON_ID,
        "E2E注册身份冲突档案",
        "19980102",
        213,
        "male",
        email="registration-split-identity@example.com",
    ),
    _PersonFixture(
        REGISTRATION_SPLIT_CONTACT_PERSON_ID,
        "E2E注册联系方式冲突档案",
        "19970102",
        214,
        "female",
        phone=SPLIT_CONFLICT_PHONE,
        email="registration-split-contact@example.com",
    ),
    _PersonFixture(
        REGISTRATION_EMAIL_ONLY_PERSON_ID,
        "E2E注册仅邮箱学员",
        "19960102",
        215,
        "female",
        email=EMAIL_ONLY_EMAIL,
    ),
    _PersonFixture(
        REGISTRATION_REVIEWER_PERSON_ID,
        "E2E注册脱敏审核员",
        "19950102",
        216,
        "male",
        phone=REVIEWER_PHONE,
    ),
)


def _upsert_person(
    db: Session,
    fixture: _PersonFixture,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> Person:
    identity = _make_cn_id(fixture.birth, fixture.sequence)
    normalized_email = normalize_email(fixture.email) if fixture.email else None
    values = {
        "full_name": fixture.full_name,
        "identity_ciphertext": cipher.encrypt(identity, field_name="identity_number"),
        "identity_digest": lookup.digest(identity),
        "phone_ciphertext": (
            cipher.encrypt(fixture.phone, field_name="phone")
            if fixture.phone is not None
            else None
        ),
        "phone_digest": lookup.digest(fixture.phone) if fixture.phone else None,
        "email": None,
        "email_ciphertext": (
            cipher.encrypt(normalized_email, field_name="email")
            if normalized_email is not None
            else None
        ),
        "email_digest": (
            lookup.digest_domain("person-email", normalized_email)
            if normalized_email is not None
            else None
        ),
        "sex": fixture.sex,
        "date_of_birth": fixture.date_of_birth,
        "deleted_at": None,
    }
    person = db.get(Person, fixture.person_id)
    if person is None:
        person = Person(id=fixture.person_id, **values)
        db.add(person)
    else:
        for field_name, value in values.items():
            setattr(person, field_name, value)
    db.flush()
    return person


def _upsert_account(
    db: Session,
    *,
    account_id: UUID,
    person_id: UUID,
    password: str,
    purpose: str,
    passwords: PasswordService,
) -> UserAccount:
    account = db.get(UserAccount, account_id)
    if account is None:
        account = UserAccount(
            id=account_id,
            person_id=person_id,
            password_hash=passwords.hash(password),
            status="active",
            force_password_change=False,
            failed_login_count=0,
            locked_until=None,
            self_service_primary_purpose=purpose,
        )
        db.add(account)
    else:
        account.person_id = person_id
        if not passwords.verify(password, account.password_hash):
            account.password_hash = passwords.hash(password)
        account.status = "active"
        account.force_password_change = False
        account.failed_login_count = 0
        account.locked_until = None
        account.self_service_primary_purpose = purpose
    account.login_phone_digest = None
    account.email_normalized = None
    db.flush()
    return account


def _upsert_identifier(
    db: Session,
    *,
    identifier_id: UUID,
    account_id: UUID,
    identifier: str,
    lookup: LookupDigest,
    is_primary: bool,
) -> None:
    normalized = normalize_login_identifier(identifier)
    row = db.get(AccountLoginIdentifier, identifier_id)
    if row is None:
        row = AccountLoginIdentifier(id=identifier_id, account_id=account_id)
        db.add(row)
    row.account_id = account_id
    row.kind = normalized.kind
    row.identifier_digest = login_identifier_digest(lookup, normalized)
    row.verified_at = datetime(2026, 1, 1, tzinfo=UTC)
    row.verification_source = "registration_e2e_seed"
    row.is_primary = is_primary
    db.flush()


def _upsert_student_profile(db: Session, person_id: UUID, student_number: str) -> None:
    profile = db.get(StudentProfile, person_id)
    if profile is None:
        profile = StudentProfile(person_id=person_id)
        db.add(profile)
    profile.student_number = student_number
    profile.status = "active"
    profile.game_account_limit = 3


def _upsert_role(db: Session, account_id: UUID, role_key: str) -> None:
    role_id = ROLES[role_key].id
    if db.get(UserRole, (account_id, role_id)) is None:
        db.add(UserRole(user_id=account_id, role_id=role_id))


def _upsert_reviewer_override(db: Session) -> None:
    row = db.get(UserPermissionOverride, REGISTRATION_REVIEW_OVERRIDE_ID)
    if row is None:
        row = UserPermissionOverride(id=REGISTRATION_REVIEW_OVERRIDE_ID)
        db.add(row)
    row.user_id = REGISTRATION_REVIEWER_ACCOUNT_ID
    row.permission_id = PERMISSIONS["registration.review"].id
    row.effect = "allow"
    row.scope_kind = "global"
    row.scope_id = None
    row.valid_from = datetime(2026, 1, 1, tzinfo=UTC)
    row.valid_until = datetime(2031, 1, 1, tzinfo=UTC)
    row.created_by_user_id = E2E_ADMIN_ACCOUNT_ID
    row.grant_reason = "E2E注册脱敏审核权限"


def seed_registration_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> RegistrationE2ESeedResult:
    _require_e2e_environment(settings)
    if db.get(UserAccount, E2E_ADMIN_ACCOUNT_ID) is None:
        raise E2ESeedRefused("Registration E2E seed requires canonical E2E fixtures")
    with write_transaction(db):
        for fixture in _PEOPLE:
            _upsert_person(db, fixture, cipher=cipher, lookup=lookup)

        _upsert_student_profile(
            db,
            REGISTRATION_EXACT_CLAIM_PERSON_ID,
            "E2E-REG-EXACT-001",
        )
        _upsert_student_profile(
            db,
            REGISTRATION_EXISTING_PERSON_ID,
            "E2E-REG-EXISTING-002",
        )
        _upsert_student_profile(
            db,
            REGISTRATION_EMAIL_ONLY_PERSON_ID,
            "E2E-REG-EMAIL-005",
        )

        _upsert_account(
            db,
            account_id=REGISTRATION_EXISTING_ACCOUNT_ID,
            person_id=REGISTRATION_EXISTING_PERSON_ID,
            password=EXISTING_ACCOUNT_PASSWORD,
            purpose="student",
            passwords=passwords,
        )
        _upsert_identifier(
            db,
            identifier_id=REGISTRATION_EXISTING_IDENTIFIER_ID,
            account_id=REGISTRATION_EXISTING_ACCOUNT_ID,
            identifier=EXISTING_ACCOUNT_PHONE,
            lookup=lookup,
            is_primary=True,
        )
        _upsert_identifier(
            db,
            identifier_id=REGISTRATION_EXISTING_EMAIL_IDENTIFIER_ID,
            account_id=REGISTRATION_EXISTING_ACCOUNT_ID,
            identifier=EXISTING_ACCOUNT_EMAIL,
            lookup=lookup,
            is_primary=False,
        )
        _upsert_role(db, REGISTRATION_EXISTING_ACCOUNT_ID, "student")

        _upsert_account(
            db,
            account_id=REGISTRATION_EMAIL_ONLY_ACCOUNT_ID,
            person_id=REGISTRATION_EMAIL_ONLY_PERSON_ID,
            password=EMAIL_ONLY_PASSWORD,
            purpose="student",
            passwords=passwords,
        )
        _upsert_identifier(
            db,
            identifier_id=REGISTRATION_EMAIL_ONLY_IDENTIFIER_ID,
            account_id=REGISTRATION_EMAIL_ONLY_ACCOUNT_ID,
            identifier=EMAIL_ONLY_EMAIL,
            lookup=lookup,
            is_primary=True,
        )
        _upsert_role(db, REGISTRATION_EMAIL_ONLY_ACCOUNT_ID, "student")

        _upsert_account(
            db,
            account_id=REGISTRATION_REVIEWER_ACCOUNT_ID,
            person_id=REGISTRATION_REVIEWER_PERSON_ID,
            password=REVIEWER_PASSWORD,
            purpose="staff_applicant",
            passwords=passwords,
        )
        _upsert_identifier(
            db,
            identifier_id=REGISTRATION_REVIEWER_IDENTIFIER_ID,
            account_id=REGISTRATION_REVIEWER_ACCOUNT_ID,
            identifier=REVIEWER_PHONE,
            lookup=lookup,
            is_primary=True,
        )
        staff_profile = db.get(StaffProfile, REGISTRATION_REVIEWER_PERSON_ID)
        if staff_profile is None:
            staff_profile = StaffProfile(person_id=REGISTRATION_REVIEWER_PERSON_ID)
            db.add(staff_profile)
        staff_profile.staff_type = "administration"
        _upsert_role(db, REGISTRATION_REVIEWER_ACCOUNT_ID, "staff")
        _upsert_reviewer_override(db)
        db.flush()

        return RegistrationE2ESeedResult(
            exact_claim_person_id=REGISTRATION_EXACT_CLAIM_PERSON_ID,
            existing_account_id=REGISTRATION_EXISTING_ACCOUNT_ID,
            split_identity_person_id=REGISTRATION_SPLIT_IDENTITY_PERSON_ID,
            split_contact_person_id=REGISTRATION_SPLIT_CONTACT_PERSON_ID,
            email_only_account_id=REGISTRATION_EMAIL_ONLY_ACCOUNT_ID,
            reviewer_account_id=REGISTRATION_REVIEWER_ACCOUNT_ID,
            destination_aliases=tuple(
                (key, registration_test_destination_alias(destination))
                for key, destination in REGISTRATION_E2E_DESTINATIONS.items()
            ),
        )


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name)
    if not isinstance(value, SecretStr):
        raise E2ESeedRefused("Registration E2E seed secret is unavailable")
    return base64.b64decode(value.get_secret_value(), altchars=b"-_", validate=True)


def main() -> int:
    settings = get_settings()
    settings.validate_runtime_secrets()
    _require_e2e_environment(settings)
    with SessionLocal() as db:
        result = seed_registration_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(
                _secret_bytes(settings, "field_encryption_master_key")
            ),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
    print(json.dumps(result.public_payload(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
