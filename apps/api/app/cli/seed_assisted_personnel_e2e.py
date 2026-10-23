"""Supplement the canonical disposable fixtures; never reset a database here.

Only the private runner calls this CLI. No credentials, identities or codes are
printed, and the fixed financial verifier has no caller-selected query surface.
"""
from __future__ import annotations

import base64
import json
import os
import stat
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID

from app.cli.e2e_database_safety import require_e2e_database_url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.personnel_intake.schemas import IntakeInput, PersonKind
    from app.security.encryption import EnvelopeCipher, LookupDigest
    from app.security.passwords import PasswordService

TEAM_ID = UUID("e9100000-0000-4000-8000-000000000001")
OTHER_TEAM_ID = UUID("e9100000-0000-4000-8000-000000000002")
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000002")


@dataclass(frozen=True)
class FixtureAccount:
    key: str
    name: str
    sequence: int
    role: str

    @property
    def person_id(self) -> UUID:
        return UUID(f"e9000000-0000-4000-8000-{self.sequence:012d}")

    @property
    def account_id(self) -> UUID:
        return UUID(f"e9010000-0000-4000-8000-{self.sequence:012d}")

    @property
    def phone(self) -> str:
        return f"19900009{self.sequence:03d}"


ACCOUNTS = {row.key: row for row in (
    FixtureAccount("coach", "E2E协助独立教练", 1, "coach"),
    FixtureAccount("manager", "E2E协助独立领队", 2, "team_manager"),
    FixtureAccount("other_coach", "E2E协助其他教练", 3, "coach"),
    FixtureAccount("other_manager", "E2E协助其他领队", 4, "team_manager"),
    FixtureAccount("administration", "E2E协助有范围行政", 5, "administration"),
    FixtureAccount("legacy_disabled", "E2E协助旧停用人员", 6, "student"),
)}


@dataclass(frozen=True)
class FixturePerson:
    kind: PersonKind
    minor: bool
    sequence: int
    unrelated: bool = False


def person_fixtures() -> tuple[FixturePerson, ...]:
    kinds: tuple[tuple[PersonKind, bool], ...] = (
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True),
    )
    return (*tuple(FixturePerson(kind, minor, sequence)
                   for sequence, (kind, minor) in enumerate(kinds, start=1)),
            FixturePerson("player", False, 10, unrelated=True),
            FixturePerson("student", False, 11, unrelated=True))


def fixture_input(fixture: FixturePerson) -> IntakeInput:
    from app.personnel_intake.schemas import IntakeInput

    def identity(birth: str, sequence: int) -> str:
        body = f"110105{birth}{sequence:03d}"
        total = sum(int(char) * weight for char, weight in zip(
            body, (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2), strict=True,
        ))
        value = body + "10X98765432"[total % 11]
        return value

    birth = "20150102" if fixture.minor else "20000102"
    guardians = [{
        "full_name": f"E2E协助监护{fixture.sequence}",
        "identity_number": identity("19800102", 651 + fixture.sequence * 2),
        "phone": "19900009999", "email": None, "relationship": "father",
        "sex": "male", "date_of_birth": "1980-01-02", "is_primary": True,
    }] if fixture.minor else []
    return IntakeInput.model_validate({
        "kind": fixture.kind, "full_name": f"E2E协助种子{fixture.sequence}",
        "identity_number": identity(birth, 601 + fixture.sequence * 2),
        "phone": "19900009998", "email": f"assisted-seed-{fixture.sequence}@example.com",
        "sex": "male", "date_of_birth": "2015-01-02" if fixture.minor else "2000-01-02",
        "guardians": guardians,
        "coach_person_ids": [ACCOUNTS["other_coach" if fixture.unrelated else "coach"].person_id]
            if fixture.kind != "player" else [],
        "team_id": (OTHER_TEAM_ID if fixture.unrelated else TEAM_ID)
            if fixture.kind != "student" else None,
    })


def require_assisted_e2e_environment(app_env: str, database_url: str, owned: str) -> None:
    try:
        require_e2e_database_url(database_url)
        url = urlsplit(database_url)
        if (app_env != "test" or url.hostname != "127.0.0.1"
                or not url.port or url.port <= 1024 or url.port == 5432
                or not owned or url.path != f"/{owned}"):
            raise ValueError
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("Assisted seed requires an owned disposable database") from exc


def business_counts(db: Session) -> dict[str, int]:
    from sqlalchemy import func, select

    from app.admissions.models import AdmissionCase, AdmissionCasePayment, Enrollment, Payment
    from app.revenue_share.models import RevenueShareLedgerEntry, StudentReferralBinding

    return {model.__tablename__: db.scalar(select(func.count()).select_from(model)) or 0
            for model in (AdmissionCase, AdmissionCasePayment, Enrollment, Payment,
                          RevenueShareLedgerEntry, StudentReferralBinding)}


_BASELINE_KEYS = frozenset({"admission_cases", "admission_case_payments", "enrollments",
                          "payments", "revenue_share_ledger_entries", "student_referral_bindings"})


def _baseline_directory(path: Path) -> int:
    if not path.is_absolute() or path.name != "assisted-baseline.json":
        raise RuntimeError("Expected private baseline")
    # macOS /tmp itself is a system symlink; reject the caller-selected immediate
    # runtime parent, then operate through its O_NOFOLLOW descriptor.
    try:
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent = os.fstat(descriptor)
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            os.close(descriptor)
            raise RuntimeError("Expected private baseline")
        return descriptor
    except OSError as exc:
        raise RuntimeError("Expected private baseline") from exc


def _baseline_file(parent: int, *, write: bool) -> int:
    name = "assisted-baseline.json"
    try:
        flags = os.O_NOFOLLOW | (os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY)
        descriptor = os.open(name, flags, 0o600, dir_fd=parent)
        current = os.fstat(descriptor)
        if (not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o600
                or current.st_uid != os.getuid() or current.st_nlink != 1
                or current.st_size > 4096):
            os.close(descriptor)
            raise RuntimeError("Expected private baseline")
        return descriptor
    except OSError as exc:
        raise RuntimeError("Expected private baseline") from exc


def _valid_counts(counts: object) -> bool:
    return (isinstance(counts, dict) and set(counts) == _BASELINE_KEYS
            and all(type(value) is int and value >= 0 for value in counts.values()))


def write_financial_baseline(path: Path, counts: dict[str, int]) -> None:
    parent = _baseline_directory(path)
    try:
        descriptor = _baseline_file(parent, write=True)
        try:
            if not _valid_counts(counts):
                raise RuntimeError("Expected private baseline")
            payload = json.dumps(counts, sort_keys=True).encode()
            os.ftruncate(descriptor, 0)
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def verify_financial_baseline(path: Path, counts: dict[str, int]) -> None:
    parent = _baseline_directory(path)
    try:
        descriptor = _baseline_file(parent, write=False)
        try:
            baseline = json.loads(os.read(descriptor, 4097))
        finally:
            os.close(descriptor)
        if not _valid_counts(baseline) or not _valid_counts(counts) or baseline != counts:
            raise RuntimeError("Financial baseline changed")
    finally:
        os.close(parent)


def seed_assisted_personnel_e2e(
    db: Session, *, settings: Settings, cipher: EnvelopeCipher, lookup: LookupDigest,
    passwords: PasswordService, owned_database: str,
) -> dict[FixturePerson, UUID]:
    from sqlalchemy import select

    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.cli.e2e_credentials import E2E_CREDENTIALS
    from app.cli.seed_e2e import (
        E2E_ADMIN_ACCOUNT_ID,
        _seed_account,
        _seed_person,
        _upsert_seed_phone_identifier,
    )
    from app.core.transactions import write_transaction
    from app.housing.models import HousingBuilding
    from app.identity.models import Person, StaffProfile, StudentProfile
    from app.personnel_intake.approval_service import PersonnelApprovalService
    from app.personnel_intake.service import PersonnelIntakeService
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES
    from app.teaching.models import GameProject, ProfessionalTeam
    from app.team_management.models import TeamManagerAssignment

    require_assisted_e2e_environment(settings.app_env, settings.database_url, owned_database)
    admin = db.get(UserAccount, E2E_ADMIN_ACCOUNT_ID)
    if admin is None:
        raise RuntimeError("Assisted seed requires the canonical fixture")
    if db.get(Person, ACCOUNTS["coach"].person_id) is not None:
        raise RuntimeError("Assisted seed requires a fresh canonical fixture")
    with write_transaction(db):
        project = db.get(GameProject, PROJECT_ID)
        if project is None or project.name != "和平精英":
            raise RuntimeError("Assisted seed requires canonical division projects")
        project.is_team_division = True
        for fixture in ACCOUNTS.values():
            legacy = fixture.key == "legacy_disabled"
            db.add(_seed_person(
                person_id=fixture.person_id, full_name=fixture.name,
                identity_birth="20150102" if legacy else "19810102",
                identity_sequence=619 if legacy else 701 + fixture.sequence * 2,
                sex="male", date_of_birth=date(2015 if legacy else 1981, 1, 2), phone=fixture.phone,
                email=f"assisted-{fixture.key}@example.com", cipher=cipher, lookup=lookup,
            ))
        db.flush()
        for fixture in ACCOUNTS.values():
            account = _seed_account(
                account_id=fixture.account_id, person_id=fixture.person_id,
                phone=fixture.phone, email=f"assisted-{fixture.key}@example.com",
                password=E2E_CREDENTIALS["admin"].password, lookup=lookup, passwords=passwords,
            )
            if fixture.key == "legacy_disabled":
                account.status = "disabled"
            db.add(account)
        db.flush()
        for fixture in ACCOUNTS.values():
            _upsert_seed_phone_identifier(db, account_id=fixture.account_id,
                                         phone=fixture.phone, lookup=lookup)
            db.add(UserRole(user_id=fixture.account_id, role_id=ROLES[fixture.role].id))
            if fixture.role in {"coach", "administration"}:
                db.add(StaffProfile(person_id=fixture.person_id, staff_type=fixture.role))
            if fixture.key == "legacy_disabled":
                db.add(StudentProfile(person_id=fixture.person_id,
                                      student_number="E2E-ASSISTED-LEGACY", status="active"))
        for team_id, name in ((TEAM_ID, "E2E协助目标队"), (OTHER_TEAM_ID, "E2E协助无关队")):
            db.add(ProfessionalTeam(id=team_id, project_id=PROJECT_ID, name=name,
                                    is_active=True, created_by_user_id=admin.id))
        db.flush()
        for key, team_id in (("manager", TEAM_ID), ("other_manager", OTHER_TEAM_ID)):
            db.add(TeamManagerAssignment(account_id=ACCOUNTS[key].account_id,
                                        team_id=team_id, created_by_user_id=admin.id))
        # Real administration role, constrained to existing A building by denies.
        for building in db.scalars(select(HousingBuilding).where(HousingBuilding.code != "A")):
            for permission in ("housing.read", "housing.rooms.manage", "housing.stays.manage",
                               "housing.transfers.manage", "housing.checkouts.manage",
                               "housing.reports.export_sensitive"):
                db.add(UserPermissionOverride(
                    user_id=ACCOUNTS["administration"].account_id,
                    permission_id=PERMISSIONS[permission].id, effect="deny",
                    scope_kind="building", scope_id=building.id,
                ))
        # Intentional legacy override: must never bypass actual owner-only policy.
        for key in ("coach", "manager"):
            for permission in ("housing.read", "housing.stays.manage"):
                db.add(UserPermissionOverride(user_id=ACCOUNTS[key].account_id,
                    permission_id=PERMISSIONS[permission].id, effect="allow", scope_kind="global"))
        db.flush()

    def actor(account_id: UUID) -> AuthContext:
        account = db.get(UserAccount, account_id)
        person = db.get(Person, account.person_id) if account else None
        if account is None or person is None:
            raise RuntimeError("Assisted fixture actor unavailable")
        return AuthContext(account=account, person=person, session=None)  # type: ignore[arg-type]

    approval = PersonnelApprovalService(db, cipher=cipher, lookup=lookup)
    intake = PersonnelIntakeService(db, cipher=cipher, lookup=lookup)
    result = {}
    for person_fixture in person_fixtures():
        approved = approval.direct_create(actor(admin.id), fixture_input(person_fixture),
            UUID(f"e9200000-0000-4000-8000-{person_fixture.sequence:012d}"))
        result[person_fixture] = approved.person_id
    # Account and StudentProfile deliberately predate this later approval.
    # Complete assisted facts must not rewrite legacy provenance or un-disable it.
    from pydantic import SecretStr

    legacy_fixture = ACCOUNTS["legacy_disabled"]
    legacy_input = fixture_input(FixturePerson("student", True, 9)).model_copy(update={
        "full_name": legacy_fixture.name, "phone": SecretStr(legacy_fixture.phone),
        "email": SecretStr("assisted-legacy_disabled@example.com"),
    })
    approval.direct_create(actor(admin.id), legacy_input,
                           UUID("e9200000-0000-4000-8000-000000000009"))
    for sequence, returned in ((7, False), (8, True)):
        pending = intake.submit(actor(ACCOUNTS["manager"].account_id),
                                fixture_input(FixturePerson("player", False, sequence)))
        if returned:
            approval.return_request(actor(admin.id), pending.id, pending.version, "请核实测试资料")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments not in ([], ["--verify-financial-baseline"]):
            raise RuntimeError("Unsupported operation")
        require_assisted_e2e_environment(os.environ.get("APP_ENV", ""),
            os.environ.get("DATABASE_URL", ""), os.environ.get("ASSISTED_E2E_DISPOSABLE_DATABASE", ""))
        path = Path(os.environ["ASSISTED_E2E_BASELINE_FILE"])
        parent = _baseline_directory(path)
        try:
            descriptor = _baseline_file(parent, write=not arguments)
            os.close(descriptor)
        finally:
            os.close(parent)
        # Imports which create Settings/engine happen only after the guard.
        from app.core.config import get_settings
        from app.core.database import SessionLocal
        from app.registration.delivery import create_test_mailbox_sender
        from app.security.encryption import EnvelopeCipher, LookupDigest
        from app.security.passwords import PasswordService

        settings = get_settings()
        settings.validate_runtime_secrets()
        if create_test_mailbox_sender(app_env=settings.app_env,
            server_host=settings.registration_test_server_host,
            mailbox_dir=settings.registration_test_mailbox_dir) is None:
            raise RuntimeError("Private test sender required")
        with SessionLocal() as db:
            if arguments:
                verify_financial_baseline(path, business_counts(db))
            else:
                counts = business_counts(db)
                seed_assisted_personnel_e2e(db, settings=settings,
                    cipher=EnvelopeCipher(base64.urlsafe_b64decode(settings.field_encryption_master_key.get_secret_value())),
                    lookup=LookupDigest(base64.urlsafe_b64decode(settings.field_lookup_key.get_secret_value())),
                    passwords=PasswordService(),
                    owned_database=os.environ["ASSISTED_E2E_DISPOSABLE_DATABASE"])
                if business_counts(db) != counts:
                    raise RuntimeError("Financial baseline changed")
                write_financial_baseline(path, counts)
    except Exception:  # noqa: BLE001 -- CLI intentionally never prints private driver/settings errors.
        print("Assisted E2E seed refused", file=sys.stderr)
        return 2
    print("Assisted E2E financial baseline verified" if arguments else "Assisted E2E fixtures ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
