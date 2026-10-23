from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[2]
MODULE = "app.cli.seed_assisted_personnel_e2e"
SAFE_DSN = "postgresql+psycopg://synthetic:synthetic@127.0.0.1:56527/erp_e2e"


def seed_module():
    # An ordinary assertion documents the initially missing implementation.
    assert (API_ROOT / "app/cli/seed_assisted_personnel_e2e.py").is_file()
    return importlib.import_module(MODULE)


@pytest.mark.parametrize(
    ("app_env", "dsn", "owned"),
    [
        ("production", SAFE_DSN, "erp_e2e"),
        ("test", SAFE_DSN, ""),
        ("test", SAFE_DSN, "other_test"),
        ("test", SAFE_DSN.replace("127.0.0.1", "192.168.31.151"), "erp_e2e"),
        ("test", SAFE_DSN.replace("56527", "5432"), "erp_e2e"),
        ("test", SAFE_DSN.replace("erp_e2e", "erp"), "erp"),
        ("test", SAFE_DSN + "?host=production", "erp_e2e"),
        ("test", SAFE_DSN.replace(":56527", ""), "erp_e2e"),
    ],
)
def test_no_db_guard_refuses_unowned_or_unsafe_destination(app_env, dsn, owned):
    module = seed_module()
    with pytest.raises(RuntimeError, match="owned disposable"):
        module.require_assisted_e2e_environment(app_env, dsn, owned)


def test_no_db_guard_accepts_exact_attested_loopback_test_destination():
    seed_module().require_assisted_e2e_environment("test", SAFE_DSN, "erp_e2e")


def test_no_db_cli_refuses_before_settings_or_connection_and_redacts_input():
    seed_module()
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}}
    env.update(APP_ENV="production", DATABASE_URL="secret-not-a-dsn", PYTHONPATH=str(API_ROOT))
    result = subprocess.run(
        [sys.executable, "-m", MODULE], cwd=API_ROOT, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "Assisted E2E seed refused\n"


def test_no_db_fixture_payloads_are_complete_synthetic_and_independent():
    module = seed_module()
    from app.personnel_intake.codec import normalize_payload

    fixtures = module.person_fixtures()
    assert {(row.kind, row.minor) for row in fixtures} >= {
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True),
    }
    emails = set()
    for fixture in fixtures:
        payload = normalize_payload(module.fixture_input(fixture))
        assert payload.email.endswith("@example.com")
        assert payload.email not in emails
        emails.add(payload.email)
        if fixture.minor:
            assert len(payload.guardians) == 1
            assert payload.guardians[0].email is None
            assert payload.guardians[0].date_of_birth.year == 1980
        else:
            assert payload.guardians == []
        assert bool(payload.coach_person_ids) == (fixture.kind != "player")
        assert bool(payload.team_id) == (fixture.kind != "student")


def test_seed_preserves_base_inventory_and_builds_real_approved_accountless_people(db):
    """DB test: run only inside the controller's exclusive disposable DB slot."""
    from sqlalchemy import func, select

    from app.auth.models import UserAccount
    from app.cli.seed_e2e import seed_e2e
    from app.core.config import get_settings
    from app.housing.models import HousingRoom
    from app.identity.models import StudentProfile
    from app.personnel_intake.models import AssistedPersonProfile, PersonnelIntakeRequest
    from app.security.encryption import EnvelopeCipher, LookupDigest
    from app.security.passwords import PasswordService

    module = seed_module()
    settings = get_settings()
    kwargs = {"settings": settings, "cipher": EnvelopeCipher(bytes(range(32))),
              "lookup": LookupDigest(bytes(range(32))), "passwords": PasswordService()}
    seed_e2e(db, **kwargs)
    before = module.business_counts(db)
    room_ids = set(db.scalars(select(HousingRoom.id)))
    result = module.seed_assisted_personnel_e2e(
        db, owned_database="erp_e2e", **kwargs,
    )
    assert module.business_counts(db) == before
    assert set(db.scalars(select(HousingRoom.id))) == room_ids
    assert len(room_ids) == 188
    assert len(result) == 8
    for fixture, person_id in result.items():
        assert db.get(AssistedPersonProfile, person_id) is not None
        assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
        assert (db.get(StudentProfile, person_id) is not None) == (fixture.kind != "player")
    assert db.scalar(select(func.count(PersonnelIntakeRequest.id)).where(
        PersonnelIntakeRequest.state == "pending")) == 1
    assert db.scalar(select(func.count(PersonnelIntakeRequest.id)).where(
        PersonnelIntakeRequest.state == "returned")) == 1
    legacy = db.get(UserAccount, module.ACCOUNTS["legacy_disabled"].account_id)
    assert legacy is not None and legacy.status == "disabled"
    assert legacy.account_access_origin == "legacy"
    assert legacy.assisted_approval_id is None
    assert db.get(AssistedPersonProfile, legacy.person_id) is not None
    from datetime import date

    from app.auth.account_readiness import AccountAccessReadinessPolicy

    readiness = AccountAccessReadinessPolicy(
        db, cipher=kwargs["cipher"], lookup=kwargs["lookup"],
    ).evaluate_one(legacy, reference_date=date(2026, 10, 18))
    assert readiness.age_status == "minor"
    assert readiness.guardian_status == "pending"
    assert readiness.missing
    with pytest.raises(RuntimeError, match="fresh canonical"):
        module.seed_assisted_personnel_e2e(db, owned_database="erp_e2e", **kwargs)


def test_no_db_financial_baseline_detects_growth_without_disclosing_data(tmp_path):
    module = seed_module()
    tmp_path.chmod(0o700)
    path = tmp_path / "assisted-baseline.json"
    counts = {"admission_cases": 3, "admission_case_payments": 3, "enrollments": 3,
              "payments": 4, "revenue_share_ledger_entries": 0, "student_referral_bindings": 0}
    module.write_financial_baseline(path, counts)
    assert path.stat().st_mode & 0o777 == 0o600
    module.verify_financial_baseline(path, counts)
    with pytest.raises(RuntimeError, match="Financial baseline changed"):
        module.verify_financial_baseline(path, {**counts, "payments": 5})


@pytest.mark.parametrize("unsafe", ["public_directory", "symlink_file", "public_file", "symlink_parent"])
def test_no_db_financial_baseline_refuses_unsafe_path(tmp_path, unsafe):
    module = seed_module()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "assisted-baseline.json"
    if unsafe == "public_directory":
        private.chmod(0o755)
    elif unsafe == "symlink_file":
        path.symlink_to(tmp_path / "outside")
    elif unsafe == "public_file":
        path.write_text("{}")
        path.chmod(0o644)
    else:
        link = tmp_path / "linked"
        link.symlink_to(private, target_is_directory=True)
        path = link / "assisted-baseline.json"
    with pytest.raises(RuntimeError, match="private baseline"):
        module.write_financial_baseline(path, {})
