"""Add self-service registration persistence and protected legacy backfills.

Revision ID: 0017_self_service_registration
Revises: 0016_camp_evaluation_surveys
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import unicodedata
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import op
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.identity.normalization import normalize_phone
from app.security.encryption import EnvelopeCipher, LookupDigest

revision: str = "0017_self_service_registration"
down_revision: str | Sequence[str] | None = "0016_camp_evaluation_surveys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REGISTERED_USER_ROLE_ID = UUID("10000000-0000-4000-8000-000000000008")
REGISTERED_USER_ROLE_KEY = "registered_user"
REGISTERED_USER_ROLE_NAME = "注册用户"

REGISTRATION_PERMISSION_KEYS = (
    "registration.review",
    "registration.sensitive.read",
    "staff_identity_applications.review",
)
REGISTRATION_PERMISSION_IDS = {
    "registration.review": UUID("20000000-0000-4000-8000-000000000051"),
    "registration.sensitive.read": UUID(
        "20000000-0000-4000-8000-000000000052"
    ),
    "staff_identity_applications.review": UUID(
        "20000000-0000-4000-8000-000000000053"
    ),
}
REGISTRATION_PERMISSIONS = {
    "registration.review": "审核注册冲突",
    "registration.sensitive.read": "查看注册敏感信息",
    "staff_identity_applications.review": "审核工作人员身份申请",
}
REGISTRATION_GRANTS = {
    *(("admin", key, "global") for key in REGISTRATION_PERMISSION_KEYS),
    ("registered_user", "food_preferences.read", "self"),
    ("registered_user", "food_preferences.update", "self"),
}

REGISTRATION_BUSINESS_TABLES = (
    "registration_consent_acceptances",
    "staff_identity_applications",
    "guardian_registration_invitations",
    "registration_sessions",
    "registration_verification_challenges",
    "registration_applications",
)

_URLSAFE_BASE64 = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_ACTIVE_APPLICATION_STATES = (
    "'draft', 'contact_verified', 'identity_completed', 'pending_guardian', "
    "'ready_to_finalize', 'conflict'"
)


def _secret_bytes(name: str) -> bytes:
    value = os.environ.get(name)
    if value is None:
        file_path = os.environ.get(f"{name}_FILE")
        if file_path:
            try:
                value = Path(file_path).read_text(encoding="utf-8").rstrip("\r\n")
            except OSError:
                raise ValueError(f"unable to read {name}_FILE") from None
    if value is None or _URLSAFE_BASE64.fullmatch(value) is None:
        raise ValueError(f"{name} must be strict URL-safe Base64")
    try:
        decoded = base64.b64decode(value, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ValueError(f"{name} must be strict URL-safe Base64") from None
    if len(decoded) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes")
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii")
    if value != canonical:
        raise ValueError(f"{name} must use canonical URL-safe Base64")
    return decoded


def _migration_cipher() -> EnvelopeCipher:
    return EnvelopeCipher(_secret_bytes("FIELD_ENCRYPTION_MASTER_KEY"))


def _migration_lookup() -> LookupDigest:
    return LookupDigest(_secret_bytes("FIELD_LOOKUP_KEY"))


def _normalize_email(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    try:
        return str(_EMAIL_ADAPTER.validate_python(normalized))
    except ValidationError:
        raise RuntimeError("cannot migrate invalid legacy email") from None


def _lock_upgrade_tables(bind: sa.Connection) -> None:
    bind.execute(sa.text("LOCK TABLE people IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE user_accounts IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE roles IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE permissions IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE role_permissions IN SHARE ROW EXCLUSIVE MODE"))


def _legacy_email_rows(bind: sa.Connection) -> list[tuple[UUID, str | None]]:
    rows: list[tuple[UUID, str | None]] = []
    for person_id, person_email, account_email in bind.execute(
        sa.text(
            "SELECT people.id, people.email, user_accounts.email_normalized "
            "FROM people LEFT JOIN user_accounts "
            "ON user_accounts.person_id = people.id ORDER BY people.id"
        )
    ).tuples():
        normalized_person = (
            _normalize_email(person_email) if person_email is not None else None
        )
        normalized_account = (
            _normalize_email(account_email) if account_email is not None else None
        )
        if (
            normalized_person is not None
            and normalized_account is not None
            and normalized_person != normalized_account
        ):
            raise RuntimeError("cannot migrate mismatched legacy email values")
        rows.append((person_id, normalized_person or normalized_account))
    return rows


def _legacy_phone_rows(
    bind: sa.Connection,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    failure_message: str,
) -> list[tuple[UUID, str, datetime]]:
    rows: list[tuple[UUID, str, datetime]] = []
    seen_identifier_digests: set[str] = set()
    for (
        account_id,
        login_phone_digest,
        created_at,
        phone_ciphertext,
        person_phone_digest,
    ) in bind.execute(
        sa.text(
            "SELECT user_accounts.id, user_accounts.login_phone_digest, "
            "user_accounts.created_at, people.phone_ciphertext, people.phone_digest "
            "FROM user_accounts JOIN people ON people.id = user_accounts.person_id "
            "ORDER BY user_accounts.id"
        )
    ).tuples():
        if (
            login_phone_digest is None
            or phone_ciphertext is None
            or person_phone_digest is None
        ):
            raise RuntimeError(failure_message)
        try:
            normalized_phone = normalize_phone(
                cipher.decrypt(phone_ciphertext, field_name="phone")
            )
        except ValueError:
            raise RuntimeError(failure_message) from None
        expected_legacy_digest = lookup.digest(normalized_phone)
        if (
            person_phone_digest != expected_legacy_digest
            or login_phone_digest != expected_legacy_digest
        ):
            raise RuntimeError(failure_message)
        identifier_digest = lookup.digest_domain(
            "account-login-phone",
            normalized_phone,
        )
        if identifier_digest in seen_identifier_digests:
            raise RuntimeError(f"{failure_message} domain conflict")
        seen_identifier_digests.add(identifier_digest)
        rows.append((account_id, identifier_digest, created_at))
    return rows


def _preflight_upgrade(
    bind: sa.Connection,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> tuple[list[tuple[UUID, str | None]], list[tuple[UUID, str, datetime]]]:
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_accounts "
            "WHERE login_phone_digest IS NULL) OR "
            "EXISTS (SELECT 1 FROM user_accounts GROUP BY login_phone_digest "
            "HAVING count(*) <> 1)"
        )
    ):
        raise RuntimeError("cannot migrate missing or duplicate legacy phone login")

    permission_ids = list(REGISTRATION_PERMISSION_IDS.values())
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM roles "
            "WHERE id = :role_id OR key = :role_key) OR "
            "EXISTS (SELECT 1 FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[])) "
            "OR key = ANY(CAST(:permission_keys AS text[])))"
        ),
        {
            "role_id": REGISTERED_USER_ROLE_ID,
            "role_key": REGISTERED_USER_ROLE_KEY,
            "permission_ids": permission_ids,
            "permission_keys": list(REGISTRATION_PERMISSION_KEYS),
        },
    ):
        raise RuntimeError("cannot upgrade self-service registration with seed collision")

    if set(
        bind.scalars(
            sa.text(
                "SELECT key FROM roles WHERE key = ANY(CAST(:keys AS text[]))"
            ),
            {"keys": ["admin"]},
        )
    ) != {"admin"}:
        raise RuntimeError("cannot upgrade self-service registration without admin role")
    if set(
        bind.scalars(
            sa.text(
                "SELECT key FROM permissions "
                "WHERE key = ANY(CAST(:keys AS text[]))"
            ),
            {"keys": ["food_preferences.read", "food_preferences.update"]},
        )
    ) != {"food_preferences.read", "food_preferences.update"}:
        raise RuntimeError(
            "cannot upgrade self-service registration without food preference permissions"
        )
    email_rows = _legacy_email_rows(bind)
    phone_rows = _legacy_phone_rows(
        bind,
        cipher=cipher,
        lookup=lookup,
        failure_message="cannot migrate invalid legacy phone",
    )
    return email_rows, phone_rows


def _timestamp_columns() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    timezone = sa.DateTime(timezone=True)
    return (
        sa.Column(
            "created_at",
            timezone,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            timezone,
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def _create_primary_identifier_triggers() -> None:
    op.execute(
        sa.text(
            """
            CREATE FUNCTION enforce_account_login_identifier_primary()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                target_account_id uuid;
                account_ids uuid[];
                identifier_count bigint;
                primary_count bigint;
            BEGIN
                IF TG_TABLE_NAME = 'user_accounts' THEN
                    account_ids := ARRAY[NEW.id];
                ELSIF TG_OP = 'INSERT' THEN
                    account_ids := ARRAY[NEW.account_id];
                ELSIF TG_OP = 'DELETE' THEN
                    account_ids := ARRAY[OLD.account_id];
                ELSE
                    account_ids := ARRAY[NEW.account_id, OLD.account_id];
                END IF;

                FOREACH target_account_id IN ARRAY account_ids LOOP
                    IF target_account_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM user_accounts
                        WHERE id = target_account_id
                          AND (
                              self_service_primary_purpose IS NOT NULL
                              OR login_phone_digest IS NULL
                          )
                    ) THEN
                        CONTINUE;
                    END IF;

                    SELECT count(*), count(*) FILTER (WHERE is_primary)
                    INTO identifier_count, primary_count
                    FROM account_login_identifiers
                    WHERE account_id = target_account_id;

                    IF identifier_count < 1 OR primary_count <> 1 THEN
                        RAISE EXCEPTION USING
                            ERRCODE = '23514',
                            CONSTRAINT = 'ck_account_login_identifiers_primary',
                            MESSAGE = 'account must have exactly one primary login identifier';
                    END IF;
                END LOOP;
                RETURN NULL;
            END;
            $$;

            CREATE CONSTRAINT TRIGGER trg_account_login_identifiers_primary_account
            AFTER INSERT OR UPDATE OR DELETE ON account_login_identifiers
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION enforce_account_login_identifier_primary();

            CREATE CONSTRAINT TRIGGER trg_user_accounts_primary_identifier
            AFTER INSERT OR UPDATE ON user_accounts
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION enforce_account_login_identifier_primary();
            """
        )
    )


def _create_schema() -> None:
    uuid = sa.UUID()
    timezone = sa.DateTime(timezone=True)

    op.execute(sa.text("CREATE SEQUENCE registration_student_number_seq START WITH 1"))

    op.add_column("people", sa.Column("email_ciphertext", sa.Text(), nullable=True))
    op.add_column(
        "people",
        sa.Column("email_digest", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_people_email_digest", "people", ["email_digest"])

    op.alter_column(
        "user_accounts",
        "login_phone_digest",
        existing_type=sa.String(length=64),
        nullable=True,
    )
    op.alter_column(
        "user_accounts",
        "email_normalized",
        existing_type=sa.String(length=320),
        nullable=True,
    )
    op.add_column(
        "user_accounts",
        sa.Column(
            "self_service_primary_purpose",
            sa.String(length=24),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_user_accounts_self_service_primary_purpose",
        "user_accounts",
        "self_service_primary_purpose IS NULL OR "
        "self_service_primary_purpose IN "
        "('student', 'guardian', 'staff_applicant', 'visitor')",
    )

    op.create_table(
        "account_login_identifiers",
        sa.Column("id", uuid, nullable=False),
        sa.Column("account_id", uuid, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("identifier_digest", sa.String(length=64), nullable=False),
        sa.Column("verified_at", timezone, nullable=False),
        sa.Column("verification_source", sa.String(length=40), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "kind IN ('phone', 'email')",
            name="ck_account_login_identifiers_kind",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_account_login_identifiers_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identifier_digest",
            name="uq_account_login_identifiers_identifier_digest",
        ),
    )
    op.create_index(
        "uq_account_login_identifiers_primary_account",
        "account_login_identifiers",
        ["account_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_index(
        "ix_account_login_identifiers_account_kind",
        "account_login_identifiers",
        ["account_id", "kind", "id"],
    )
    _create_primary_identifier_triggers()

    op.create_table(
        "guardian_profiles",
        sa.Column("person_id", uuid, nullable=False),
        sa.Column(
            "legacy_backfilled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_guardian_profiles_person",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("person_id"),
    )

    op.create_table(
        "registration_applications",
        sa.Column("id", uuid, nullable=False),
        sa.Column("primary_purpose", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("verified_identifier_kind", sa.String(length=16), nullable=True),
        sa.Column("verified_identifier_digest", sa.String(length=64), nullable=True),
        sa.Column("verified_identifier_ciphertext", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("full_name", sa.String(length=80), nullable=True),
        sa.Column("identity_ciphertext", sa.Text(), nullable=True),
        sa.Column("identity_digest", sa.String(length=64), nullable=True),
        sa.Column("sex", sa.String(length=16), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("requested_staff_type", sa.String(length=24), nullable=True),
        sa.Column("staff_application_reason", sa.String(length=500), nullable=True),
        sa.Column("visit_reason", sa.String(length=200), nullable=True),
        sa.Column("matched_person_id", uuid, nullable=True),
        sa.Column("finalized_account_id", uuid, nullable=True),
        sa.Column("finalization_match_kind", sa.String(length=24), nullable=True),
        sa.Column("conflict_code", sa.String(length=64), nullable=True),
        sa.Column("expires_at", timezone, nullable=False),
        sa.Column("finalized_at", timezone, nullable=True),
        sa.Column("rejected_at", timezone, nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "primary_purpose IN "
            "('student', 'guardian', 'staff_applicant', 'visitor')",
            name="ck_registration_applications_primary_purpose",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'contact_verified', 'identity_completed', "
            "'pending_guardian', 'ready_to_finalize', 'finalized', 'conflict', "
            "'rejected', 'expired')",
            name="ck_registration_applications_state",
        ),
        sa.CheckConstraint(
            "verified_identifier_kind IS NULL OR "
            "verified_identifier_kind IN ('phone', 'email')",
            name="ck_registration_applications_identifier_kind",
        ),
        sa.CheckConstraint(
            "sex IS NULL OR sex IN ('male', 'female')",
            name="ck_registration_applications_sex",
        ),
        sa.CheckConstraint(
            "requested_staff_type IS NULL OR requested_staff_type IN "
            "('coach', 'life_teacher', 'customer_service', 'administration', 'other')",
            name="ck_registration_applications_requested_staff_type",
        ),
        sa.CheckConstraint(
            "staff_application_reason IS NULL OR "
            "(regexp_replace(staff_application_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = staff_application_reason AND "
            "regexp_replace(staff_application_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(staff_application_reason) BETWEEN 2 AND 500)",
            name="ck_registration_applications_staff_application_reason",
        ),
        sa.CheckConstraint(
            "visit_reason IS NULL OR "
            "(regexp_replace(visit_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = visit_reason AND "
            "regexp_replace(visit_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(visit_reason) BETWEEN 2 AND 200)",
            name="ck_registration_applications_visit_reason",
        ),
        sa.CheckConstraint(
            "(requested_staff_type IS NULL AND staff_application_reason IS NULL "
            "AND visit_reason IS NULL AND "
            "(primary_purpose IN ('student', 'guardian') OR "
            "state IN ('draft', 'contact_verified', 'finalized', 'rejected', "
            "'expired'))) OR "
            "(primary_purpose = 'staff_applicant' AND "
            "requested_staff_type IS NOT NULL AND "
            "staff_application_reason IS NOT NULL AND visit_reason IS NULL) OR "
            "(primary_purpose = 'visitor' AND requested_staff_type IS NULL AND "
            "staff_application_reason IS NULL AND visit_reason IS NOT NULL)",
            name="ck_registration_applications_purpose_fields",
        ),
        sa.CheckConstraint(
            "(verified_identifier_digest IS NULL) = "
            "(verified_identifier_kind IS NULL)",
            name="ck_registration_applications_identifier_pair",
        ),
        sa.CheckConstraint(
            "finalized_at IS NULL OR state = 'finalized'",
            name="ck_registration_applications_finalized_at",
        ),
        sa.CheckConstraint(
            "(state = 'finalized' AND finalization_match_kind IS NOT NULL AND "
            "finalization_match_kind IN "
            "('new_person', 'claim_existing', 'existing_account')) OR "
            "(state <> 'finalized' AND finalization_match_kind IS NULL)",
            name="ck_registration_applications_finalization_match_kind",
        ),
        sa.CheckConstraint(
            "rejected_at IS NULL OR state = 'rejected'",
            name="ck_registration_applications_rejected_at",
        ),
        sa.ForeignKeyConstraint(
            ["matched_person_id"],
            ["people.id"],
            name="fk_registration_applications_matched_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["finalized_account_id"],
            ["user_accounts.id"],
            name="fk_registration_applications_finalized_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_registration_applications_active_identifier",
        "registration_applications",
        ["verified_identifier_digest"],
        unique=True,
        postgresql_where=sa.text(
            "verified_identifier_digest IS NOT NULL AND state IN ("
            f"{_ACTIVE_APPLICATION_STATES})"
        ),
    )
    op.create_index(
        "uq_registration_applications_active_identity",
        "registration_applications",
        ["identity_digest"],
        unique=True,
        postgresql_where=sa.text(
            "identity_digest IS NOT NULL AND state IN ("
            f"{_ACTIVE_APPLICATION_STATES})"
        ),
    )
    op.create_index(
        "ix_registration_applications_state_expiry",
        "registration_applications",
        ["state", "expires_at", "id"],
    )

    op.create_table(
        "registration_verification_challenges",
        sa.Column("id", uuid, nullable=False),
        sa.Column("application_id", uuid, nullable=True),
        sa.Column("account_id", uuid, nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("identifier_kind", sa.String(length=16), nullable=False),
        sa.Column("identifier_digest", sa.String(length=64), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("request_source_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", timezone, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sent_at", timezone, nullable=False),
        sa.Column("consumed_at", timezone, nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "purpose IN ('registration', 'password_reset', "
            "'guardian_invitation', 'add_login_identifier')",
            name="ck_registration_verification_challenges_purpose",
        ),
        sa.CheckConstraint(
            "identifier_kind IN ('phone', 'email')",
            name="ck_registration_verification_challenges_identifier_kind",
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND 5",
            name="ck_registration_verification_challenges_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["registration_applications.id"],
            name="fk_registration_verification_challenges_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_registration_verification_challenges_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_registration_verification_challenges_identifier",
        "registration_verification_challenges",
        ["purpose", "identifier_digest", "created_at", "id"],
    )
    op.create_index(
        "ix_registration_verification_challenges_source",
        "registration_verification_challenges",
        ["request_source_digest", "created_at", "id"],
    )

    op.create_table(
        "registration_sessions",
        sa.Column("id", uuid, nullable=False),
        sa.Column("application_id", uuid, nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", timezone, nullable=False),
        sa.Column("revoked_at", timezone, nullable=True),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["registration_applications.id"],
            name="fk_registration_sessions_application",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest", name="uq_registration_sessions_token_digest"),
    )
    op.create_index(
        "ix_registration_sessions_application_active",
        "registration_sessions",
        ["application_id", "revoked_at", "expires_at", "id"],
    )

    op.create_table(
        "guardian_registration_invitations",
        sa.Column("id", uuid, nullable=False),
        sa.Column("application_id", uuid, nullable=True),
        sa.Column("student_person_id", uuid, nullable=True),
        sa.Column("invited_identifier_kind", sa.String(length=16), nullable=False),
        sa.Column("invited_identifier_digest", sa.String(length=64), nullable=False),
        sa.Column("invited_identifier_ciphertext", sa.Text(), nullable=False),
        sa.Column("relationship", sa.String(length=40), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("expires_at", timezone, nullable=False),
        sa.Column("accepted_by_person_id", uuid, nullable=True),
        sa.Column("accepted_at", timezone, nullable=True),
        sa.Column("revoked_at", timezone, nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "(application_id IS NULL) <> (student_person_id IS NULL)",
            name="ck_guardian_registration_invitations_target",
        ),
        sa.CheckConstraint(
            "invited_identifier_kind IN ('phone', 'email')",
            name="ck_guardian_registration_invitations_identifier_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_guardian_registration_invitations_status",
        ),
        sa.CheckConstraint(
            "status <> 'accepted' OR "
            "(accepted_by_person_id IS NOT NULL AND accepted_at IS NOT NULL)",
            name="ck_guardian_registration_invitations_acceptance",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["registration_applications.id"],
            name="fk_guardian_registration_invitations_application",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["student_person_id"],
            ["people.id"],
            name="fk_guardian_registration_invitations_student",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_person_id"],
            ["people.id"],
            name="fk_guardian_registration_invitations_guardian",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for suffix, columns, predicate in (
        (
            "pending_application",
            ["application_id", "invited_identifier_digest"],
            "status = 'pending' AND application_id IS NOT NULL",
        ),
        (
            "pending_student",
            ["student_person_id", "invited_identifier_digest"],
            "status = 'pending' AND student_person_id IS NOT NULL",
        ),
        (
            "accepted_application",
            ["application_id", "accepted_by_person_id"],
            "status = 'accepted' AND application_id IS NOT NULL",
        ),
        (
            "accepted_student",
            ["student_person_id", "accepted_by_person_id"],
            "status = 'accepted' AND student_person_id IS NOT NULL",
        ),
    ):
        op.create_index(
            f"uq_guardian_registration_invitations_{suffix}",
            "guardian_registration_invitations",
            columns,
            unique=True,
            postgresql_where=sa.text(predicate),
        )
    op.create_index(
        "ix_guardian_registration_invitations_expiry",
        "guardian_registration_invitations",
        ["status", "expires_at", "id"],
    )

    op.create_table(
        "staff_identity_applications",
        sa.Column("id", uuid, nullable=False),
        sa.Column("person_id", uuid, nullable=False),
        sa.Column("requested_staff_type", sa.String(length=32), nullable=False),
        sa.Column("application_reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reviewed_by_user_id", uuid, nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", timezone, nullable=True),
        sa.Column("withdrawn_at", timezone, nullable=True),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "requested_staff_type IN ('coach', 'life_teacher', "
            "'customer_service', 'administration', 'other')",
            name="ck_staff_identity_applications_requested_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_staff_identity_applications_status",
        ),
        sa.CheckConstraint(
            "regexp_replace(application_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = application_reason AND "
            "regexp_replace(application_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(application_reason) BETWEEN 2 AND 500",
            name="ck_staff_identity_applications_reason",
        ),
        sa.CheckConstraint(
            "review_reason IS NULL OR ("
            "regexp_replace(review_reason, "
            "'^[[:space:]]+|[[:space:]]+$', '', 'g') = review_reason AND "
            "regexp_replace(review_reason, '[[:space:]]', '', 'g') <> '' AND "
            "char_length(review_reason) BETWEEN 2 AND 500)",
            name="ck_staff_identity_applications_review_reason",
        ),
        sa.CheckConstraint(
            "(status IN ('approved', 'rejected') AND "
            "reviewed_by_user_id IS NOT NULL AND review_reason IS NOT NULL "
            "AND reviewed_at IS NOT NULL) OR "
            "(status IN ('pending', 'withdrawn') AND reviewed_by_user_id IS NULL "
            "AND review_reason IS NULL AND reviewed_at IS NULL)",
            name="ck_staff_identity_applications_review_state",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_staff_identity_applications_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["user_accounts.id"],
            name="fk_staff_identity_applications_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_staff_identity_applications_open_person_type",
        "staff_identity_applications",
        ["person_id", "requested_staff_type"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_staff_identity_applications_review_queue",
        "staff_identity_applications",
        ["status", "created_at", "id"],
    )

    op.create_table(
        "registration_consent_acceptances",
        sa.Column("id", uuid, nullable=False),
        sa.Column("registration_application_id", uuid, nullable=True),
        sa.Column("account_id", uuid, nullable=True),
        sa.Column("consent_type", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("accepted_at", timezone, nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            timezone,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(registration_application_id IS NULL) <> (account_id IS NULL)",
            name="ck_registration_consent_acceptances_subject",
        ),
        sa.CheckConstraint(
            "btrim(consent_type) <> '' AND btrim(policy_version) <> ''",
            name="ck_registration_consent_acceptances_labels",
        ),
        sa.ForeignKeyConstraint(
            ["registration_application_id"],
            ["registration_applications.id"],
            name="fk_registration_consent_acceptances_application",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["user_accounts.id"],
            name="fk_registration_consent_acceptances_account",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_registration_consent_acceptances_application",
        "registration_consent_acceptances",
        ["registration_application_id", "accepted_at", "id"],
    )
    op.create_index(
        "ix_registration_consent_acceptances_account",
        "registration_consent_acceptances",
        ["account_id", "accepted_at", "id"],
    )


def _seed_registration_rbac(bind: sa.Connection) -> None:
    bind.execute(
        sa.text("INSERT INTO roles (id, key, name) VALUES (:id, :key, :name)"),
        {
            "id": REGISTERED_USER_ROLE_ID,
            "key": REGISTERED_USER_ROLE_KEY,
            "name": REGISTERED_USER_ROLE_NAME,
        },
    )
    for key in REGISTRATION_PERMISSION_KEYS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (id, key, name, sensitive) "
                "VALUES (:id, :key, :name, true)"
            ),
            {
                "id": REGISTRATION_PERMISSION_IDS[key],
                "key": key,
                "name": REGISTRATION_PERMISSIONS[key],
            },
        )
    for role_key, permission_key, scope_kind in sorted(REGISTRATION_GRANTS):
        result = bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT roles.id, permissions.id, :scope_kind "
                "FROM roles CROSS JOIN permissions "
                "WHERE roles.key = :role_key AND permissions.key = :permission_key"
            ),
            {
                "role_key": role_key,
                "permission_key": permission_key,
                "scope_kind": scope_kind,
            },
        )
        if result.rowcount != 1:
            raise RuntimeError("cannot seed self-service registration grant")


def _backfill_legacy_data(
    bind: sa.Connection,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    email_rows: list[tuple[UUID, str | None]],
    phone_rows: list[tuple[UUID, str, datetime]],
) -> None:
    for person_id, normalized_email in email_rows:
        if normalized_email is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE people SET email_ciphertext = :ciphertext, "
                "email_digest = :digest WHERE id = :person_id"
            ),
            {
                "person_id": person_id,
                "ciphertext": cipher.encrypt(normalized_email, field_name="email"),
                "digest": lookup.digest_domain("person-email", normalized_email),
            },
        )

    for account_id, identifier_digest, created_at in phone_rows:
        bind.execute(
            sa.text(
                "INSERT INTO account_login_identifiers "
                "(id, account_id, kind, identifier_digest, verified_at, "
                "verification_source, is_primary) VALUES "
                "(:id, :account_id, 'phone', :identifier_digest, :verified_at, "
                "'legacy_phone_login', true)"
            ),
            {
                "id": uuid4(),
                "account_id": account_id,
                "identifier_digest": identifier_digest,
                "verified_at": created_at,
            },
        )

    expected_identifiers = {
        (account_id, identifier_digest, "phone", "legacy_phone_login", True)
        for account_id, identifier_digest, _created_at in phone_rows
    }
    actual_identifiers = set(
        bind.execute(
            sa.text(
                "SELECT account_id, identifier_digest, kind, verification_source, "
                "is_primary FROM account_login_identifiers"
            )
        ).tuples()
    )
    if actual_identifiers != expected_identifiers:
        raise RuntimeError("cannot establish one legacy phone identifier per account")

    bind.execute(sa.text("UPDATE people SET email = NULL WHERE email IS NOT NULL"))
    bind.execute(
        sa.text(
            "UPDATE user_accounts SET email_normalized = NULL "
            "WHERE email_normalized IS NOT NULL"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO guardian_profiles (person_id, legacy_backfilled) "
            "SELECT people.id, true FROM people "
            "WHERE (NOT EXISTS (SELECT 1 FROM student_profiles "
            "WHERE student_profiles.person_id = people.id) "
            "AND NOT EXISTS (SELECT 1 FROM staff_profiles "
            "WHERE staff_profiles.person_id = people.id) "
            "AND NOT EXISTS (SELECT 1 FROM visitor_profiles "
            "WHERE visitor_profiles.person_id = people.id)) "
            "OR EXISTS (SELECT 1 FROM guardian_relationships "
            "WHERE guardian_relationships.guardian_person_id = people.id)"
        )
    )

    _seed_registration_rbac(bind)
    bind.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT id, :role_id FROM user_accounts"
        ),
        {"role_id": REGISTERED_USER_ROLE_ID},
    )


def upgrade() -> None:
    # Key material is validated and both helpers are constructed before the first
    # lock or schema statement, so an invalid deployment secret leaves 0016 intact.
    cipher = _migration_cipher()
    lookup = _migration_lookup()
    bind = op.get_bind()
    _lock_upgrade_tables(bind)
    email_rows, phone_rows = _preflight_upgrade(
        bind,
        cipher=cipher,
        lookup=lookup,
    )
    _create_schema()
    _backfill_legacy_data(
        bind,
        cipher=cipher,
        lookup=lookup,
        email_rows=email_rows,
        phone_rows=phone_rows,
    )


def _lock_downgrade_tables(bind: sa.Connection) -> None:
    bind.execute(sa.text("LOCK TABLE people IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE user_accounts IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE roles IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE permissions IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE role_permissions IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(sa.text("LOCK TABLE user_roles IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(
        sa.text("LOCK TABLE user_permission_overrides IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(
        sa.text(
            "LOCK TABLE cohort_staff_assignment_permissions "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    bind.execute(
        sa.text("LOCK TABLE account_login_identifiers IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(sa.text("LOCK TABLE guardian_profiles IN SHARE ROW EXCLUSIVE MODE"))
    bind.execute(
        sa.text("LOCK TABLE registration_applications IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(
        sa.text(
            "LOCK TABLE registration_verification_challenges "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    bind.execute(
        sa.text("LOCK TABLE registration_sessions IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(
        sa.text(
            "LOCK TABLE guardian_registration_invitations "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )
    bind.execute(
        sa.text("LOCK TABLE staff_identity_applications IN SHARE ROW EXCLUSIVE MODE")
    )
    bind.execute(
        sa.text(
            "LOCK TABLE registration_consent_acceptances "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    )


def _expected_registration_grants() -> set[tuple[str, str, str]]:
    return set(REGISTRATION_GRANTS)


def _validate_downgrade_rbac(bind: sa.Connection) -> None:
    role_row = bind.execute(
        sa.text("SELECT id, key, name FROM roles WHERE id = :id"),
        {"id": REGISTERED_USER_ROLE_ID},
    ).one_or_none()
    if role_row != (
        REGISTERED_USER_ROLE_ID,
        REGISTERED_USER_ROLE_KEY,
        REGISTERED_USER_ROLE_NAME,
    ):
        raise RuntimeError("cannot downgrade self-service registration with modified role")

    permission_ids = list(REGISTRATION_PERMISSION_IDS.values())
    permission_rows = set(
        bind.execute(
            sa.text(
                "SELECT id, key, name, sensitive FROM permissions "
                "WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"ids": permission_ids},
        ).tuples()
    )
    expected_permissions = {
        (REGISTRATION_PERMISSION_IDS[key], key, REGISTRATION_PERMISSIONS[key], True)
        for key in REGISTRATION_PERMISSION_KEYS
    }
    if permission_rows != expected_permissions:
        raise RuntimeError(
            "cannot downgrade self-service registration with modified permissions"
        )

    grants = set(
        bind.execute(
            sa.text(
                "SELECT roles.key, permissions.key, role_permissions.scope_kind "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id "
                "WHERE roles.id = :role_id "
                "OR permissions.id = ANY(CAST(:permission_ids AS uuid[]))"
            ),
            {"role_id": REGISTERED_USER_ROLE_ID, "permission_ids": permission_ids},
        ).tuples()
    )
    if grants != _expected_registration_grants():
        raise RuntimeError(
            "cannot downgrade self-service registration with customized grant"
        )

    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_permission_overrides "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[]))) OR "
            "EXISTS (SELECT 1 FROM cohort_staff_assignment_permissions "
            "WHERE permission_id = ANY(CAST(:permission_ids AS uuid[])))"
        ),
        {"permission_ids": permission_ids},
    ):
        raise RuntimeError(
            "cannot downgrade self-service registration with permission override"
        )


def _validate_downgrade_accounts(
    bind: sa.Connection,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> None:
    account_ids = set(bind.scalars(sa.text("SELECT id FROM user_accounts")))
    assigned_ids = set(
        bind.scalars(
            sa.text("SELECT user_id FROM user_roles WHERE role_id = :role_id"),
            {"role_id": REGISTERED_USER_ROLE_ID},
        )
    )
    if assigned_ids != account_ids:
        raise RuntimeError(
            "cannot downgrade self-service registration with nonbaseline role assignment"
        )

    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM user_accounts "
            "WHERE email_normalized IS NOT NULL "
            "OR self_service_primary_purpose IS NOT NULL)"
        )
    ):
        raise RuntimeError(
            "cannot downgrade self-service registration with new account or identifier"
        )
    phone_rows = _legacy_phone_rows(
        bind,
        cipher=cipher,
        lookup=lookup,
        failure_message=(
            "cannot downgrade self-service registration with changed legacy phone"
        ),
    )
    expected_identifiers = {
        (account_id, identifier_digest, "phone", "legacy_phone_login", True)
        for account_id, identifier_digest, _created_at in phone_rows
    }
    actual_identifiers = set(
        bind.execute(
            sa.text(
                "SELECT account_id, identifier_digest, kind, verification_source, "
                "is_primary FROM account_login_identifiers"
            )
        ).tuples()
    )
    if actual_identifiers != expected_identifiers:
        raise RuntimeError(
            "cannot downgrade self-service registration with new account or identifier"
        )


def _validate_downgrade_guardians(bind: sa.Connection) -> None:
    guardian_rows = set(
        bind.execute(
            sa.text("SELECT person_id, legacy_backfilled FROM guardian_profiles")
        ).tuples()
    )
    legacy_implicit_guardian_ids = set(
        bind.scalars(
            sa.text(
                "SELECT people.id FROM people "
                "WHERE (NOT EXISTS (SELECT 1 FROM student_profiles "
                "WHERE student_profiles.person_id = people.id) "
                "AND NOT EXISTS (SELECT 1 FROM staff_profiles "
                "WHERE staff_profiles.person_id = people.id) "
                "AND NOT EXISTS (SELECT 1 FROM visitor_profiles "
                "WHERE visitor_profiles.person_id = people.id)) "
                "OR EXISTS (SELECT 1 FROM guardian_relationships "
                "WHERE guardian_relationships.guardian_person_id = people.id)"
            )
        )
    )
    expected_guardian_rows = {
        (person_id, True) for person_id in legacy_implicit_guardian_ids
    }
    if guardian_rows != expected_guardian_rows:
        raise RuntimeError(
            "cannot downgrade self-service registration with changed guardian profile"
        )


def _decrypt_legacy_emails(
    bind: sa.Connection,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> list[tuple[UUID, str | None]]:
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM people WHERE email IS NOT NULL)")):
        raise RuntimeError("cannot downgrade self-service registration with plaintext email")

    restored: list[tuple[UUID, str | None]] = []
    for person_id, ciphertext, digest in bind.execute(
        sa.text(
            "SELECT id, email_ciphertext, email_digest FROM people ORDER BY id"
        )
    ).tuples():
        if (ciphertext is None) != (digest is None):
            raise RuntimeError(
                "cannot downgrade self-service registration with incomplete email pair"
            )
        if ciphertext is None:
            restored.append((person_id, None))
            continue
        try:
            plaintext = cipher.decrypt(ciphertext, field_name="email")
        except ValueError:
            raise RuntimeError(
                "cannot downgrade self-service registration with invalid encrypted email"
            ) from None
        normalized = _normalize_email(plaintext)
        if plaintext != normalized or digest != lookup.digest_domain(
            "person-email",
            normalized,
        ):
            raise RuntimeError(
                "cannot downgrade self-service registration with changed email data"
            )
        restored.append((person_id, normalized))

    emails_by_person = dict(restored)
    if any(
        emails_by_person.get(person_id) is None
        for person_id in bind.scalars(sa.text("SELECT person_id FROM user_accounts"))
    ):
        raise RuntimeError(
            "cannot downgrade self-service registration with email-only or new account"
        )
    return restored


def _preflight_downgrade(
    bind: sa.Connection,
    *,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
) -> list[tuple[UUID, str | None]]:
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM registration_applications) OR "
            "EXISTS (SELECT 1 FROM registration_verification_challenges) OR "
            "EXISTS (SELECT 1 FROM registration_sessions) OR "
            "EXISTS (SELECT 1 FROM guardian_registration_invitations) OR "
            "EXISTS (SELECT 1 FROM staff_identity_applications) OR "
            "EXISTS (SELECT 1 FROM registration_consent_acceptances)"
        )
    ):
        raise RuntimeError("cannot downgrade self-service registration with business data")
    _validate_downgrade_rbac(bind)
    _validate_downgrade_accounts(bind, cipher=cipher, lookup=lookup)
    _validate_downgrade_guardians(bind)
    return _decrypt_legacy_emails(bind, cipher=cipher, lookup=lookup)


def _restore_legacy_data(
    bind: sa.Connection,
    restored_emails: list[tuple[UUID, str | None]],
) -> None:
    for person_id, email in restored_emails:
        bind.execute(
            sa.text("UPDATE people SET email = :email WHERE id = :person_id"),
            {"email": email, "person_id": person_id},
        )
    bind.execute(
        sa.text(
            "UPDATE user_accounts SET email_normalized = people.email "
            "FROM people WHERE people.id = user_accounts.person_id"
        )
    )
    if bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM user_accounts WHERE email_normalized IS NULL)")
    ):
        raise RuntimeError("cannot restore required legacy account email")

    bind.execute(
        sa.text("DELETE FROM user_roles WHERE role_id = :role_id"),
        {"role_id": REGISTERED_USER_ROLE_ID},
    )
    bind.execute(sa.text("DELETE FROM account_login_identifiers"))
    bind.execute(sa.text("DELETE FROM guardian_profiles"))

    permission_ids = list(REGISTRATION_PERMISSION_IDS.values())
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_id = :role_id "
            "OR permission_id = ANY(CAST(:permission_ids AS uuid[]))"
        ),
        {"role_id": REGISTERED_USER_ROLE_ID, "permission_ids": permission_ids},
    )
    bind.execute(
        sa.text(
            "DELETE FROM permissions "
            "WHERE id = ANY(CAST(:permission_ids AS uuid[]))"
        ),
        {"permission_ids": permission_ids},
    )
    bind.execute(
        sa.text("DELETE FROM roles WHERE id = :role_id"),
        {"role_id": REGISTERED_USER_ROLE_ID},
    )


def _drop_schema() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_user_accounts_primary_identifier "
            "ON user_accounts"
        )
    )
    for table_name in REGISTRATION_BUSINESS_TABLES:
        op.drop_table(table_name)
    op.drop_table("account_login_identifiers")
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS enforce_account_login_identifier_primary()"
        )
    )
    op.drop_table("guardian_profiles")
    op.execute(sa.text("DROP SEQUENCE registration_student_number_seq"))

    op.drop_constraint(
        "ck_user_accounts_self_service_primary_purpose",
        "user_accounts",
        type_="check",
    )
    op.drop_column("user_accounts", "self_service_primary_purpose")
    op.alter_column(
        "user_accounts",
        "email_normalized",
        existing_type=sa.String(length=320),
        nullable=False,
    )
    op.alter_column(
        "user_accounts",
        "login_phone_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )

    op.drop_index("ix_people_email_digest", table_name="people")
    op.drop_column("people", "email_digest")
    op.drop_column("people", "email_ciphertext")


def downgrade() -> None:
    # Downgrade needs the same keys to prove that legacy plaintext can be restored.
    # Validation and every refusal check complete before _restore_legacy_data or DDL.
    cipher = _migration_cipher()
    lookup = _migration_lookup()
    bind = op.get_bind()
    _lock_downgrade_tables(bind)
    restored_emails = _preflight_downgrade(bind, cipher=cipher, lookup=lookup)
    _restore_legacy_data(bind, restored_emails)
    # Deferred primary-identifier checks must be settled while the compatibility
    # columns still identify legacy accounts; otherwise PostgreSQL refuses to drop
    # the child table with pending constraint-trigger events.
    bind.execute(
        sa.text(
            "SET CONSTRAINTS trg_account_login_identifiers_primary_account, "
            "trg_user_accounts_primary_identifier IMMEDIATE"
        )
    )
    _drop_schema()
