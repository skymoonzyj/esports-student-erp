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
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import UUID

from app.cli.e2e_database_safety import require_e2e_database_url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.core.config import Settings
    from app.personnel_intake.schemas import IntakeInput, PendingIntakeInput, PersonKind
    from app.security.encryption import EnvelopeCipher, LookupDigest
    from app.security.passwords import PasswordService

TEAM_ID = UUID("e9100000-0000-4000-8000-000000000001")
OTHER_TEAM_ID = UUID("e9100000-0000-4000-8000-000000000002")
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000002")
_PROTECTED_LOCKED_UNTIL = datetime(2100, 1, 1, tzinfo=UTC)
_PROTECTED_DELETED_AT = datetime(2026, 10, 1, tzinfo=UTC)


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
    FixtureAccount("review_accounts_only", "E2E认领仅账号权限", 41, "admin"),
    FixtureAccount("review_intake_only", "E2E认领仅建档权限", 42, "admin"),
    FixtureAccount("review_both_nonadmin", "E2E认领双权限非管理员", 43, "staff"),
    FixtureAccount("review_both_admin", "E2E认领双权限管理员", 44, "admin"),
)}


@dataclass(frozen=True)
class FixturePerson:
    kind: PersonKind
    minor: bool
    sequence: int
    unrelated: bool = False
    missing_email: bool = False


def person_fixtures() -> tuple[FixturePerson, ...]:
    kinds: tuple[tuple[PersonKind, bool], ...] = (
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True),
    )
    return (*tuple(FixturePerson(kind, minor, sequence)
                   for sequence, (kind, minor) in enumerate(kinds, start=1)),
            FixturePerson("player", False, 10, unrelated=True),
            FixturePerson("student", False, 11, unrelated=True))


def claim_person_fixtures() -> tuple[FixturePerson, ...]:
    """Independent approved, accountless, no-email claim targets."""
    kinds: tuple[tuple[PersonKind, bool], ...] = (
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True),
    )
    return tuple(FixturePerson(kind, minor, sequence, missing_email=True)
                 for sequence, (kind, minor) in enumerate(kinds, start=21))


def protected_claim_fixtures() -> dict[str, FixturePerson]:
    return {
        "locked": FixturePerson("player", False, 31, missing_email=True),
        "disabled": FixturePerson("player", False, 32, missing_email=True),
        "deleted": FixturePerson("player", False, 33, missing_email=True),
    }


def pending_person_fixtures() -> tuple[FixturePerson, ...]:
    """A separately reseeded browser spec keeps the original claim quota intact."""
    kinds: tuple[tuple[PersonKind, bool], ...] = (
        ("student", False), ("student", True), ("player", False),
        ("player", True), ("both", False), ("both", True), ("staff", False),
    )
    return (*tuple(FixturePerson(kind, minor, sequence, missing_email=sequence % 2 == 0)
                   for sequence, (kind, minor) in enumerate(kinds, start=51)),
            FixturePerson("staff", False, 58, missing_email=True))


def pending_fixture_input(fixture: FixturePerson) -> PendingIntakeInput:
    from app.personnel_intake.schemas import PendingIntakeInput

    # CN identity facts remain factual; only passport can have unknown sex/DOB.
    strict = fixture_input(FixturePerson(
        "student" if fixture.kind == "staff" else fixture.kind, fixture.minor,
        fixture.sequence, missing_email=fixture.missing_email,
    )).model_dump()
    return PendingIntakeInput.model_validate({
        **strict, "kind": fixture.kind, "full_name": f"E2E待补种子{fixture.sequence}",
        "phone": None, "guardians": [], "coach_person_ids": [], "team_id": None,
        **({"identity_number": "E2e-Pending-P8", "document_type": "passport",
            "document_issuing_country": None, "sex": None, "date_of_birth": None}
           if fixture.sequence == 58 else {}),
    })


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
        "phone": f"19900008{fixture.sequence:03d}" if fixture.missing_email else "19900009998",
        "email": None if fixture.missing_email else f"assisted-seed-{fixture.sequence}@example.com",
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


def verify_protected_claim_accounts(db: Session, *, lookup: LookupDigest) -> None:
    """Fixed private post-spec check; browser and Web never receive the DB URL."""
    from sqlalchemy import select

    from app.auth.models import AccountLoginIdentifier, UserAccount
    from app.identity.models import Person

    for state, fixture in protected_claim_fixtures().items():
        identity = fixture_input(fixture).identity_number.get_secret_value()
        person = db.scalar(select(Person).where(Person.identity_digest == lookup.digest(identity)))
        accounts = (list(db.scalars(select(UserAccount).where(UserAccount.person_id == person.id)))
                    if person is not None else [])
        expected_id = UUID(f"e9310000-0000-4000-8000-{fixture.sequence:012d}")
        expected_status = "disabled" if state == "disabled" else "active"
        expected_lock = _PROTECTED_LOCKED_UNTIL if state == "locked" else None
        expected_deletion = _PROTECTED_DELETED_AT if state == "deleted" else None
        if (person is None or len(accounts) != 1
                or accounts[0].id != expected_id
                or accounts[0].status != expected_status
                or accounts[0].locked_until != expected_lock
                or accounts[0].account_access_origin != "legacy"
                or person.deleted_at != expected_deletion
                or person.email is not None or person.email_ciphertext is not None
                or person.email_digest is not None):
            raise RuntimeError("Protected claim fixture changed")
        identifiers = list(db.scalars(select(AccountLoginIdentifier).where(
            AccountLoginIdentifier.account_id == expected_id)))
        if len(identifiers) != 1 or identifiers[0].kind != "phone":
            raise RuntimeError("Protected claim fixture changed")


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


def _pending_baseline(path: Path, rows: dict[str, object], *, write: bool) -> None:
    parent = _baseline_directory(path)
    descriptor = -1
    try:
        descriptor = os.open("pending-lifecycle-baseline.json", os.O_NOFOLLOW | (
            os.O_WRONLY | os.O_CREAT if write else os.O_RDONLY), 0o600, dir_fd=parent)
        current = os.fstat(descriptor)
        if (not stat.S_ISREG(current.st_mode) or stat.S_IMODE(current.st_mode) != 0o600
                or current.st_uid != os.getuid() or current.st_nlink != 1
                or current.st_size > 65536):
            raise RuntimeError("Expected private baseline")
        if write:
            payload = json.dumps(rows, sort_keys=True).encode()
            if len(payload) > 65536:
                raise RuntimeError("Expected private baseline")
            os.ftruncate(descriptor, 0)
            os.write(descriptor, payload)
        elif json.loads(os.read(descriptor, 65537)) != rows:
            raise RuntimeError("Pending lifecycle baseline changed")
    except OSError as exc:
        raise RuntimeError("Expected private baseline") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def write_pending_baseline(path: Path, rows: dict[str, object]) -> None:
    _pending_baseline(path, rows, write=True)


def verify_pending_baseline(path: Path, rows: dict[str, object]) -> None:
    _pending_baseline(path, rows, write=False)


def pending_lifecycle_rows(db: Session) -> dict[str, object]:
    """Read actual PKs and immutable housing facts, never an HTTP projection.

    There is no PlayerProfile table: team_memberships are the concrete player
    rows. StudentProfile/StaffProfile have person_id as their actual primary key.
    Unknown-sex passport is checked separately in the browser after correction.
    """
    from sqlalchemy import select

    from app.housing.models import HousingStay, HousingTransfer
    from app.identity.models import Person, StaffProfile, StudentProfile
    from app.personnel_intake.models import AssistedApproval
    from app.team_management.models import TeamMembership

    output: dict[str, object] = {}
    for fixture in pending_person_fixtures():
        approval = db.scalar(select(AssistedApproval).where(
            AssistedApproval.operation_key == UUID(f"e9400000-0000-4000-8000-{fixture.sequence:012d}")))
        person = db.get(Person, approval.person_id) if approval else None
        if person is None:
            raise RuntimeError("Pending lifecycle fixture missing")
        student, staff = db.get(StudentProfile, person.id), db.get(StaffProfile, person.id)
        stays = list(db.scalars(select(HousingStay).where(HousingStay.person_id == person.id)
                               .order_by(HousingStay.id))) if fixture.sequence != 58 else []
        memberships = list(db.scalars(select(TeamMembership).where(
            TeamMembership.person_id == person.id).order_by(TeamMembership.id)))
        transfers = list(db.scalars(select(HousingTransfer.id).where(
            HousingTransfer.person_id == person.id).order_by(HousingTransfer.id)))
        output[str(fixture.sequence)] = {
            "person_id": str(person.id), "nickname": person.nickname,
            "profile_notes": person.profile_notes,
            "student": [str(student.person_id), student.student_number] if student else None,
            "staff": [str(staff.person_id), staff.staff_type] if staff else None,
            "memberships": [str(row.id) for row in memberships],
            "stays": [[str(row.id), str(row.person_id), str(row.room_id), row.status,
                       row.planned_check_in_at.isoformat(),
                       row.actual_check_in_at.isoformat() if row.actual_check_in_at else None,
                       row.actual_check_out_at.isoformat() if row.actual_check_out_at else None,
                       row.notes] for row in stays],
            "transfers": [str(identifier) for identifier in transfers],
        }
    return output


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
            if fixture.role in {"coach", "administration", "staff"}:
                db.add(StaffProfile(person_id=fixture.person_id,
                                    staff_type="other" if fixture.role == "staff" else fixture.role))
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
        for key, permission in (("review_accounts_only", "personnel_intake.review"),
                                ("review_intake_only", "accounts.manage")):
            db.add(UserPermissionOverride(
                user_id=ACCOUNTS[key].account_id,
                permission_id=PERMISSIONS[permission].id,
                effect="deny", scope_kind="global",
            ))
        for permission in ("accounts.manage", "personnel_intake.review"):
            db.add(UserPermissionOverride(
                user_id=ACCOUNTS["review_both_nonadmin"].account_id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow", scope_kind="global",
            ))
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
    protected = protected_claim_fixtures()
    for person_fixture in (*person_fixtures(), *claim_person_fixtures(), *protected.values()):
        approved = approval.direct_create(actor(admin.id), fixture_input(person_fixture),
            UUID(f"e9200000-0000-4000-8000-{person_fixture.sequence:012d}"))
        result[person_fixture] = approved.person_id
    with write_transaction(db):
        for claim_fixture in claim_person_fixtures():
            person = db.get(Person, result[claim_fixture])
            if person is None:
                raise RuntimeError("Assisted claim fixture unavailable")
            person.nickname = f"E2E认领昵称{claim_fixture.sequence}"
            person.profile_notes = f"E2E认领保留备注{claim_fixture.sequence}"
        for state, protected_fixture in protected.items():
            person_id = result[protected_fixture]
            person = db.get(Person, person_id)
            if person is None:
                raise RuntimeError("Assisted protected claim fixture unavailable")
            phone = f"19900008{protected_fixture.sequence:03d}"
            account_id = UUID(f"e9310000-0000-4000-8000-{protected_fixture.sequence:012d}")
            account = _seed_account(
                account_id=account_id, person_id=person_id, phone=phone,
                email="", password=E2E_CREDENTIALS["admin"].password,
                lookup=lookup, passwords=passwords,
            )
            if state == "disabled":
                account.status = "disabled"
            if state == "locked":
                account.locked_until = _PROTECTED_LOCKED_UNTIL
            if state == "deleted":
                person.deleted_at = _PROTECTED_DELETED_AT
            db.add(account)
            db.flush()
            _upsert_seed_phone_identifier(db, account_id=account_id, phone=phone, lookup=lookup)
            db.add(UserRole(user_id=account_id, role_id=ROLES["team_member"].id))
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
    # Pending lifecycle fixtures are synthetic, deliberately incomplete, and
    # housed through the same readiness/capacity service used by the API.
    from app.housing.access import HousingAccessService
    from app.housing.cleaning_service import HousingCleaningService
    from app.housing.models import HousingRoom
    from app.housing.occupancy_service import HousingOccupancyService
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.housing.schemas import CheckoutConfirmation, CheckoutRequest, StayCreate

    occupancy = HousingOccupancyService(db, resident_readiness=HousingResidentReadinessPolicy(
        db, cipher=cipher, lookup=lookup))

    def authorize_cleaning(candidate: HousingRoom) -> None:
        current_actor = db.execute(select(
            UserAccount.status, UserAccount.force_password_change, Person.deleted_at,
        ).join(Person, Person.id == UserAccount.person_id).where(UserAccount.id == admin.id)
        ).one_or_none()
        access = HousingAccessService(db, admin.id, datetime.now(UTC))
        if (current_actor is None or current_actor.status != "active"
                or current_actor.force_password_change or current_actor.deleted_at is not None
                or not access.can_read_room(candidate) or not access.can_manage_room_care(candidate)):
            raise RuntimeError("Pending lifecycle cleaning permission denied")

    now = datetime.now(UTC)
    for pending_fixture in pending_person_fixtures():
        created = approval.direct_create_pending(actor(admin.id), pending_fixture_input(pending_fixture),
            UUID(f"e9400000-0000-4000-8000-{pending_fixture.sequence:012d}"))
        with write_transaction(db):
            person = db.get(Person, created.person_id)
            if person is None:
                raise RuntimeError("Pending lifecycle fixture missing")
            person.nickname = f"E2E待补昵称{pending_fixture.sequence}"
            person.profile_notes = f"E2E待补保留备注{pending_fixture.sequence}"
        if pending_fixture.sequence == 58:
            continue  # Unknown-sex passport cannot house before correction.
        room = db.scalar(select(HousingRoom).where(
            HousingRoom.room_number == f"A-{pending_fixture.sequence + 250}"))
        if room is None:
            raise RuntimeError("Pending lifecycle room missing")

        def authorize(candidate: HousingRoom) -> None:
            access = HousingAccessService(db, admin.id, now)
            if not access.can_read_room(candidate) or not access.can_manage_stays(candidate):
                raise RuntimeError("Pending lifecycle housing permission denied")

        if pending_fixture.sequence == 51:
            historical = occupancy.backfill_existing_stay(room.id, StayCreate(
                person_id=person.id, stay_purpose="permanent", duration_mode="long_term",
                planned_check_in_at=now - timedelta(days=3), notes="E2E待补保留历史住宿"),
                actor_user_id=admin.id, occurred_at=now, authorize_room_and_stay=authorize)
            requested = occupancy.request_checkout(historical.id, CheckoutRequest(
                version=historical.version, actual_check_out_at=now - timedelta(days=2)),
                actor_user_id=admin.id, occurred_at=now, authorize_room=authorize)
            occupancy.confirm_checkout(requested.id, CheckoutConfirmation(version=requested.version),
                actor_user_id=admin.id, occurred_at=now, authorize_room=authorize)
            # The synthetic second stay must follow the same care lifecycle as a real arrival.
            HousingCleaningService(db).complete(
                room.id, version=room.version, actor_user_id=admin.id,
                occurred_at=datetime.now(UTC), authorize_room=authorize_cleaning,
            )
        occupancy.backfill_existing_stay(room.id, StayCreate(
            person_id=person.id, stay_purpose="permanent", duration_mode="long_term",
            planned_check_in_at=now, notes="E2E待补住宿保留备注"),
            actor_user_id=admin.id, occurred_at=now, authorize_room_and_stay=authorize)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if arguments not in ([], ["--verify-financial-baseline"], ["--verify-protected-claims"],
                             ["--verify-pending-lifecycle"]):
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
            if arguments == ["--verify-financial-baseline"]:
                verify_financial_baseline(path, business_counts(db))
            elif arguments == ["--verify-protected-claims"]:
                verify_protected_claim_accounts(db, lookup=LookupDigest(base64.urlsafe_b64decode(
                    settings.field_lookup_key.get_secret_value())))
            elif arguments == ["--verify-pending-lifecycle"]:
                verify_pending_baseline(path, pending_lifecycle_rows(db))
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
                write_pending_baseline(path, pending_lifecycle_rows(db))
    except Exception:  # noqa: BLE001 -- CLI intentionally never prints private driver/settings errors.
        print("Assisted E2E seed refused", file=sys.stderr)
        return 2
    message = {
        (): "Assisted E2E fixtures ready",
        ("--verify-financial-baseline",): "Assisted E2E financial baseline verified",
        ("--verify-protected-claims",): "Assisted E2E protected fixtures verified",
        ("--verify-pending-lifecycle",): "Assisted E2E pending lifecycle verified",
    }
    print(message[tuple(arguments)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
