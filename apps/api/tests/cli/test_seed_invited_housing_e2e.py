import subprocess
import sys
from unittest.mock import Mock

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.cli.seed_e2e import E2E_VISITOR_ACCOUNT_ID, E2ESeedRefused, seed_e2e
from app.cli.seed_housing_portal_e2e import seed_housing_portal_e2e
from app.cli.seed_invited_housing_e2e import seed_invited_housing_e2e
from app.core.config import Settings
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.revenue_share.models import (
    RevenueShareOrganizationManagerAssignment,
    StudentReferralBinding,
)
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def settings(app_env: str = "test") -> Settings:
    key = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
    return Settings(
        app_env=app_env,
        database_url="postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e",
        field_encryption_master_key=key,
        field_lookup_key=key,
        session_token_pepper=key,
    )


def test_seed_refuses_non_test_before_any_database_access() -> None:
    db = Mock()
    with pytest.raises(E2ESeedRefused):
        seed_invited_housing_e2e(db, settings=settings("development"))
    assert not db.mock_calls


def test_standalone_seed_registers_binding_foreign_key_dependency() -> None:
    # A fresh CLI process must not depend on pytest's app.main model imports.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.cli.seed_invited_housing_e2e; "
                "from app.revenue_share.models import StudentReferralBinding; "
                "fk = next(iter(StudentReferralBinding.__table__.c."
                "registration_application_id.foreign_keys)); "
                "assert fk.column.table.name == 'registration_applications'"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_seed_is_repeatable_and_does_not_pregrant_managers_or_financial_access(db: Session) -> None:
    config = settings()
    seed_e2e(
        db, settings=config, cipher=EnvelopeCipher(bytes(range(32))),
        lookup=LookupDigest(bytes(range(32))), passwords=PasswordService(),
    )
    seed_housing_portal_e2e(db, settings=config)
    # Match SessionLocal: queries must not silently flush new parent rows.
    db.autoflush = False
    first = seed_invited_housing_e2e(db, settings=config)
    second = seed_invited_housing_e2e(db, settings=config)
    assert first == second == 4
    assert db.scalar(select(func.count(StudentReferralBinding.id))) == 4
    assert db.scalar(select(func.count(RevenueShareOrganizationManagerAssignment.id))) == 0
    assert db.scalar(select(func.count(UserRole.role_id)).where(
        UserRole.user_id == E2E_VISITOR_ACCOUNT_ID
    )) == 0
    overrides = list(db.scalars(select(UserPermissionOverride).where(
        UserPermissionOverride.user_id == E2E_VISITOR_ACCOUNT_ID
    )))
    assert len(overrides) == 1
    assert overrides[0].scope_kind == "self"
    assert overrides[0].effect == "allow"
    assert overrides[0].permission_id == PERMISSIONS["referrals.read_invited_housing"].id
