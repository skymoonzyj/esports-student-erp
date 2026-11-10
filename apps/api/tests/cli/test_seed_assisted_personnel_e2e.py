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


@pytest.mark.parametrize("arguments", [[], ["--verify-protected-claims"], ["--verify-pending-lifecycle"]])
def test_no_db_cli_refuses_before_settings_or_connection_and_redacts_input(arguments):
    seed_module()
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}}
    env.update(APP_ENV="production", DATABASE_URL="secret-not-a-dsn", PYTHONPATH=str(API_ROOT))
    result = subprocess.run(
        [sys.executable, "-m", MODULE, *arguments], cwd=API_ROOT, env=env,
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


def test_no_db_claim_fixture_payloads_have_six_absent_email_kinds_and_private_protected_cases():
    module = seed_module()
    from app.personnel_intake.codec import normalize_payload

    claim = module.claim_person_fixtures()
    protected = module.protected_claim_fixtures()
    assert len(claim) == 6
    assert {(row.kind, row.minor) for row in claim} == {
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True),
    }
    assert set(protected) == {"locked", "disabled", "deleted"}
    assert len({row.sequence for row in (*claim, *protected.values())}) == 9
    for fixture in (*claim, *protected.values()):
        payload = normalize_payload(module.fixture_input(fixture))
        assert payload.email is None
        assert payload.phone.startswith("19900008")
        assert bool(payload.coach_person_ids) == (fixture.kind != "player")
        assert bool(payload.team_id) == (fixture.kind != "student")


def test_no_db_review_actor_catalog_is_distinct_and_uses_real_roles():
    module = seed_module()
    actors = {key: module.ACCOUNTS[key] for key in (
        "review_accounts_only", "review_intake_only", "review_both_nonadmin",
        "review_both_admin",
    )}
    assert {key: row.role for key, row in actors.items()} == {
        "review_accounts_only": "admin", "review_intake_only": "admin",
        "review_both_nonadmin": "staff", "review_both_admin": "admin",
    }
    assert len({row.phone for row in actors.values()}) == 4


def test_no_db_identity_no_team_actor_is_a_separate_synthetic_root_with_explicit_denial():
    module = seed_module()
    actor = module.ACCOUNTS["identity_no_team"]
    assert actor.role == "super_admin"
    assert actor.phone == "19900009045"
    assert actor.person_id not in {row.person_id for key, row in module.ACCOUNTS.items() if key != actor.key}
    assert module.IDENTITY_NO_TEAM_DENIED_PERMISSION == "team_manager_assignments.manage"


def test_no_db_pending_fixtures_keep_missing_facts_and_reservations_truthful():
    module = seed_module()
    fixtures = module.pending_person_fixtures()
    assert len(fixtures) == 8
    assert {(row.kind, row.minor) for row in fixtures} >= {
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True), ("staff", False),
    }
    payloads = [module.pending_fixture_input(row) for row in fixtures]
    assert sum(row.email is not None for row in payloads) == 4
    assert all(row.phone is None and row.guardians == [] for row in payloads)
    assert all(row.team_id is None and row.team_allocation_status == "pending" for row in payloads)
    passport = payloads[-1]
    assert passport.document_type == "passport"
    assert passport.identity_number.get_secret_value() == "E2e-Pending-P8"
    assert passport.sex is None and passport.date_of_birth is None
    assert passport.document_issuing_country is None


def test_no_db_pending_snapshot_detects_replaced_profile_or_stay_and_is_private(tmp_path):
    module = seed_module()
    tmp_path.chmod(0o700)
    path = tmp_path / "assisted-baseline.json"
    rows = {"synthetic": {"person_id": "person", "student": ["person", "student-number"],
                          "staff": None, "memberships": [], "stays": [["stay", "room", "checked_in"]]}}
    module.write_pending_baseline(path, rows)
    snapshot = tmp_path / "pending-lifecycle-baseline.json"
    assert snapshot.stat().st_mode & 0o777 == 0o600
    module.verify_pending_baseline(path, rows)
    for changed in ({**rows["synthetic"], "student": None},
                    {**rows["synthetic"], "stays": [["replacement", "room", "checked_in"]]}):
        with pytest.raises(RuntimeError, match="Pending lifecycle baseline changed"):
            module.verify_pending_baseline(path, {"synthetic": changed})
    snapshot.unlink()
    outside = tmp_path / "outside"
    outside.write_text("not a baseline")
    snapshot.symlink_to(outside)
    with pytest.raises(RuntimeError, match="private baseline"):
        module.write_pending_baseline(path, rows)


def test_seed_preserves_base_inventory_and_builds_real_approved_accountless_people(db, tmp_path):
    """DB test: run only inside the controller's exclusive disposable DB slot."""
    from datetime import date

    from sqlalchemy import func, select

    from app.auth.models import UserAccount
    from app.cli.seed_e2e import seed_e2e
    from app.core.config import get_settings
    from app.housing.models import HousingRoom
    from app.identity.models import Person, StudentProfile
    from app.personnel_intake.models import AssistedPersonProfile, PersonnelIntakeRequest
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from app.security.encryption import EnvelopeCipher, LookupDigest
    from app.security.passwords import PasswordService

    module = seed_module()
    owned_database = db.get_bind().engine.url.database
    settings = get_settings().model_copy(update={
        "database_url": db.get_bind().engine.url.render_as_string(hide_password=False),
    })
    kwargs = {"settings": settings, "cipher": EnvelopeCipher(bytes(range(32))),
              "lookup": LookupDigest(bytes(range(32))), "passwords": PasswordService()}
    seed_e2e(db, **kwargs)
    before = module.business_counts(db)
    room_ids = set(db.scalars(select(HousingRoom.id)))
    result = module.seed_assisted_personnel_e2e(
        db, owned_database=owned_database, **kwargs,
    )
    assert module.business_counts(db) == before
    assert set(db.scalars(select(HousingRoom.id))) == room_ids
    assert len(room_ids) == 188
    assert len(result) == 17
    pending_rows = module.pending_lifecycle_rows(db)
    assert len(pending_rows) == 8
    tmp_path.chmod(0o700)
    path = tmp_path / "assisted-baseline.json"
    module.write_pending_baseline(path, pending_rows)
    module.verify_pending_baseline(path, module.pending_lifecycle_rows(db))
    from uuid import UUID

    from app.identity.models import StaffProfile
    from app.personnel_intake.readiness import AssistedReadinessPolicy

    policy = AssistedReadinessPolicy(db, cipher=kwargs["cipher"], lookup=kwargs["lookup"])
    for fixture in module.pending_person_fixtures():
        actual = pending_rows[str(fixture.sequence)]
        person_id = UUID(actual["person_id"])
        person = db.get(Person, person_id)
        assert person is not None and person.team_allocation_status == "pending"
        assert db.scalar(select(UserAccount).where(UserAccount.person_id == person_id)) is None
        assert (db.get(StudentProfile, person_id) is not None) == (fixture.kind in {"student", "both"})
        assert (db.get(StaffProfile, person_id) is not None) == (fixture.kind == "staff")
        ready = policy.evaluate_many([person_id], reference_date=date(2026, 10, 21))[person_id]
        assert ready.approval_valid and not ready.activation_profile_ready
        assert ready.housing_ready == (fixture.sequence != 58)
        assert len(actual["stays"]) == (0 if fixture.sequence == 58 else 2 if fixture.sequence == 51 else 1)
        if actual["stays"]:
            assert sorted(row[3] for row in actual["stays"]) == (
                ["checked_in", "checked_out"] if fixture.sequence == 51 else ["checked_in"])
        if fixture.sequence == 51:
            from app.cli.seed_e2e import E2E_ADMIN_ACCOUNT_ID

            cleaned = db.scalar(select(HousingRoom).where(HousingRoom.room_number == "A-301"))
            assert cleaned is not None
            assert cleaned.cleaning_status == "ready"
            assert cleaned.cleaning_pending_at is not None
            assert cleaned.cleaning_completed_at is not None
            assert cleaned.cleaning_completed_at >= cleaned.cleaning_pending_at
            assert cleaned.cleaning_completed_by_user_id == E2E_ADMIN_ACCOUNT_ID
            assert cleaned.version >= 3  # departure and real completion each advance it
    student_id = UUID(pending_rows["51"]["person_id"])
    student = db.get(StudentProfile, student_id)
    old_number = student.student_number
    student.student_number = "E2E-REPLACEMENT-SHOULD-FAIL"
    db.flush()
    with pytest.raises(RuntimeError, match="Pending lifecycle baseline changed"):
        module.verify_pending_baseline(path, module.pending_lifecycle_rows(db))
    student.student_number = old_number
    db.flush()
    module.verify_pending_baseline(path, module.pending_lifecycle_rows(db))
    protected = module.protected_claim_fixtures()
    for fixture, person_id in result.items():
        assert db.get(AssistedPersonProfile, person_id) is not None
        account = db.scalar(select(UserAccount).where(UserAccount.person_id == person_id))
        if fixture in protected.values():
            assert account is not None
        else:
            assert account is None
        assert (db.get(StudentProfile, person_id) is not None) == (fixture.kind != "player")
    for fixture in module.claim_person_fixtures():
        person = db.get(Person, result[fixture])
        assert person is not None
        assert person.email_digest is None and person.email_ciphertext is None
        assert person.nickname == f"E2E认领昵称{fixture.sequence}"
        assert person.profile_notes == f"E2E认领保留备注{fixture.sequence}"
    for state, fixture in protected.items():
        person = db.get(Person, result[fixture])
        account = db.scalar(select(UserAccount).where(UserAccount.person_id == result[fixture]))
        assert person is not None and account is not None
        assert person.email_digest is None and person.email_ciphertext is None
        assert (account.status == "disabled") == (state == "disabled")
        assert (account.locked_until is not None) == (state == "locked")
        assert (person.deleted_at is not None) == (state == "deleted")
    module.verify_protected_claim_accounts(db, lookup=kwargs["lookup"])
    for state, field, wrong in (
        ("locked", "locked_until", None),
        ("disabled", "status", "active"),
        ("deleted", "deleted_at", None),
    ):
        fixture = protected[state]
        person = db.get(Person, result[fixture])
        account = db.scalar(select(UserAccount).where(UserAccount.person_id == result[fixture]))
        assert person is not None and account is not None
        target = person if field == "deleted_at" else account
        original = getattr(target, field)
        setattr(target, field, wrong)
        db.flush()
        with pytest.raises(RuntimeError, match="Protected claim fixture changed"):
            module.verify_protected_claim_accounts(db, lookup=kwargs["lookup"])
        setattr(target, field, original)
        db.flush()
    module.verify_protected_claim_accounts(db, lookup=kwargs["lookup"])
    for key in ("review_accounts_only", "review_intake_only", "review_both_nonadmin",
                "review_both_admin"):
        actor_fixture = module.ACCOUNTS[key]
        assert db.get(UserAccount, actor_fixture.account_id) is not None
        assert db.get(UserRole, (actor_fixture.account_id, ROLES[actor_fixture.role].id)) is not None
        rules = {(rule.permission_id, rule.effect) for rule in db.scalars(
            select(UserPermissionOverride).where(UserPermissionOverride.user_id == actor_fixture.account_id))}
        expected = {
            "review_accounts_only": {(PERMISSIONS["personnel_intake.review"].id, "deny")},
            "review_intake_only": {(PERMISSIONS["accounts.manage"].id, "deny")},
            "review_both_nonadmin": {(PERMISSIONS["accounts.manage"].id, "allow"),
                                      (PERMISSIONS["personnel_intake.review"].id, "allow")},
            "review_both_admin": set(),
        }[key]
        assert rules == expected
    assert db.scalar(select(func.count(PersonnelIntakeRequest.id)).where(
        PersonnelIntakeRequest.state == "pending")) == 1
    assert db.scalar(select(func.count(PersonnelIntakeRequest.id)).where(
        PersonnelIntakeRequest.state == "returned")) == 1
    legacy = db.get(UserAccount, module.ACCOUNTS["legacy_disabled"].account_id)
    assert legacy is not None and legacy.status == "disabled"
    assert legacy.account_access_origin == "legacy"
    assert legacy.assisted_approval_id is None
    assert db.get(AssistedPersonProfile, legacy.person_id) is not None
    from app.auth.account_readiness import AccountAccessReadinessPolicy

    readiness = AccountAccessReadinessPolicy(
        db, cipher=kwargs["cipher"], lookup=kwargs["lookup"],
    ).evaluate_one(legacy, reference_date=date(2026, 10, 18))
    assert readiness.age_status == "minor"
    assert readiness.guardian_status == "pending"
    assert readiness.missing
    with pytest.raises(RuntimeError, match="fresh canonical"):
        module.seed_assisted_personnel_e2e(db, owned_database=owned_database, **kwargs)


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
