from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount
from app.cli.seed_e2e import seed_e2e
from app.core.config import Settings
from app.identity.models import Person
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
MODULE_PATH = REPOSITORY_ROOT / "apps/api/app/cli/seed_registration_e2e.py"
WRAPPER_PATH = REPOSITORY_ROOT / "scripts/seed-registration-e2e.py"
TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")


def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url="postgresql+psycopg://erp:erp@db:5432/erp_test",
        session_cookie_secure=False,
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
    )


def test_registration_seed_is_idempotent_and_public_output_is_alias_only(
    db: Session,
) -> None:
    assert MODULE_PATH.is_file()
    assert WRAPPER_PATH.is_file()
    module = importlib.import_module("app.cli.seed_registration_e2e")
    seed_registration_e2e = module.seed_registration_e2e
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    passwords = PasswordService()
    runtime_settings = settings()
    seed_e2e(
        db,
        settings=runtime_settings,
        cipher=cipher,
        lookup=lookup,
        passwords=passwords,
    )

    first = seed_registration_e2e(
        db,
        settings=runtime_settings,
        cipher=cipher,
        lookup=lookup,
        passwords=passwords,
    )
    second = seed_registration_e2e(
        db,
        settings=runtime_settings,
        cipher=cipher,
        lookup=lookup,
        passwords=passwords,
    )

    assert first == second
    assert db.get(Person, second.exact_claim_person_id) is not None
    assert db.scalar(
        select(UserAccount.id).where(
            UserAccount.person_id == second.exact_claim_person_id
        )
    ) is None
    assert db.get(UserAccount, second.existing_account_id) is not None
    assert set(
        db.scalars(
            select(AccountLoginIdentifier.kind).where(
                AccountLoginIdentifier.account_id == second.existing_account_id
            )
        )
    ) == {"phone", "email"}
    assert second.split_identity_person_id != second.split_contact_person_id
    assert db.get(Person, second.split_identity_person_id) is not None
    assert db.get(Person, second.split_contact_person_id) is not None

    email_only_identifiers = set(
        db.scalars(
            select(AccountLoginIdentifier.kind).where(
                AccountLoginIdentifier.account_id == second.email_only_account_id
            )
        )
    )
    assert email_only_identifiers == {"email"}
    reviewer = RbacService(db).decision_context(second.reviewer_account_id)
    assert reviewer.decide("registration.review", {"global"}).allowed
    assert not reviewer.decide("registration.sensitive.read", {"global"}).allowed
    reviewer_person = db.scalar(
        select(Person)
        .join(UserAccount, UserAccount.person_id == Person.id)
        .where(UserAccount.id == second.reviewer_account_id)
    )
    assert reviewer_person is not None
    assert reviewer_person.email_ciphertext is None
    assert reviewer_person.email_digest is None

    public_payload = second.public_payload()
    assert set(public_payload) == {"fixture_ids", "destination_aliases"}
    assert set(public_payload["fixture_ids"]) == {
        "exact_claim_person",
        "existing_account",
        "split_identity_person",
        "split_contact_person",
        "email_only_account",
        "reviewer_account",
    }
    assert set(public_payload["destination_aliases"]) == {
        "adult_phone",
        "adult_email",
        "exact_claim",
        "existing_account",
        "existing_account_recovery",
        "split_conflict",
        "email_only",
        "minor_student",
        "minor_guardian",
        "adult_optional_guardian",
        "staff_applicant",
        "privacy_applicant",
    }
    assert all(
        alias.startswith("dst_")
        for alias in public_payload["destination_aliases"].values()
    )
    rendered = json.dumps(public_payload, ensure_ascii=False, sort_keys=True)
    assert not re.search(r"\b1[3-9]\d{9}\b", rendered)
    assert not re.search(r"[\w.+-]+@[\w.-]+", rendered)
    assert "password" not in rendered.casefold()
    assert "identity" not in json.dumps(
        public_payload["destination_aliases"],
        ensure_ascii=False,
    ).casefold()
