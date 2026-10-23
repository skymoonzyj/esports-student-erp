from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.cli.e2e_database_safety import E2EDatabaseRefused, require_e2e_database_url
from app.cli.seed_e2e import (
    E2E_ADMIN_ACCOUNT_ID,
    E2E_SELF_STUDENT_ACCOUNT_ID,
    E2E_SELF_STUDENT_PERSON_ID,
    _seed_account,
    _seed_person,
    _upsert_seed_phone_identifier,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.core.transactions import write_transaction
from app.identity.models import Person
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.models import TeamManagerAssignment, TeamMembership

KING_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000005")
PEACE_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000002")
DELTA_PROJECT_ID = UUID("30000000-0000-4000-8000-000000000003")
TEAM_A_ID = UUID("e8100000-0000-4000-8000-000000000001")
TEAM_B_ID = UUID("e8100000-0000-4000-8000-000000000002")
TEAM_C_ID = UUID("e8100000-0000-4000-8000-000000000003")
STUDENT_MEMBERSHIP_ID = UUID("e8120000-0000-4000-8000-000000000001")
HISTORICAL_DISABLED_MEMBERSHIP_ID = UUID("e8120000-0000-4000-8000-000000000002")
HISTORICAL_DELETED_MEMBERSHIP_ID = UUID("e8120000-0000-4000-8000-000000000003")


@dataclass(frozen=True, slots=True)
class FixtureAccount:
    key: str
    name: str
    sequence: int
    role: str
    status: str = "active"
    deleted: bool = False

    @property
    def person_id(self) -> UUID:
        return UUID(f"e8000000-0000-4000-8000-{self.sequence:012d}")

    @property
    def account_id(self) -> UUID:
        return UUID(f"e8010000-0000-4000-8000-{self.sequence:012d}")


FIXTURE_ACCOUNTS = (
    FixtureAccount("team_manager_a", "E2E队伍管理甲", 1, "team_manager"),
    FixtureAccount("team_manager_b", "E2E队伍管理乙", 2, "team_manager"),
    FixtureAccount("team_manager_multi", "E2E多队管理", 3, "team_manager"),
    FixtureAccount("team_manager_shared", "E2E同队管理", 4, "team_manager"),
    FixtureAccount("team_manager_unbound", "E2E未绑定管理", 5, "team_manager"),
    FixtureAccount("team_member_only", "E2E纯队员", 6, "team_member"),
    FixtureAccount("team_ordinary_admin", "E2E普通管理员", 7, "admin"),
    FixtureAccount("team_disabled", "E2E停用历史队员", 8, "team_member", "disabled"),
    FixtureAccount("team_deleted", "E2E删除历史队员", 9, "team_member", "disabled", True),
)
ACCOUNTS_BY_KEY = {fixture.key: fixture for fixture in FIXTURE_ACCOUNTS}


def require_team_e2e_environment(app_env: str, database_url: str) -> None:
    if app_env != "test":
        raise RuntimeError("Team E2E seed requires APP_ENV=test")
    try:
        require_e2e_database_url(database_url)
    except E2EDatabaseRefused as exc:
        raise RuntimeError("Team E2E seed requires an isolated test database") from exc


def _secret_bytes(settings: Settings, field_name: str) -> bytes:
    value = getattr(settings, field_name).get_secret_value()
    return base64.b64decode(value, altchars=b"-_", validate=True)


def seed_team_management_e2e(
    db: Session,
    *,
    settings: Settings,
    cipher: EnvelopeCipher,
    lookup: LookupDigest,
    passwords: PasswordService,
) -> None:
    require_team_e2e_environment(settings.app_env, settings.database_url)
    if db.get(UserAccount, E2E_ADMIN_ACCOUNT_ID) is None:
        raise RuntimeError("Team E2E seed requires the canonical E2E fixture")
    if db.get(Person, FIXTURE_ACCOUNTS[0].person_id) is not None:
        raise RuntimeError("Team E2E seed requires a fresh canonical fixture")

    with write_transaction(db):
        king = db.get(GameProject, KING_PROJECT_ID)
        if king is None:
            db.add(GameProject(
                id=KING_PROJECT_ID,
                name="王者荣耀",
                sort_order=50,
                is_active=True,
                is_team_division=True,
                created_by_user_id=None,
            ))
        elif king.name != "王者荣耀":
            raise RuntimeError("Canonical 王者荣耀 project ID is occupied")
        else:
            king.is_team_division = True
        for project_id, expected_name in (
            (PEACE_PROJECT_ID, "和平精英"),
            (DELTA_PROJECT_ID, "三角洲行动"),
        ):
            project = db.get(GameProject, project_id)
            if project is None or project.name != expected_name:
                raise RuntimeError("Team E2E seed requires canonical division projects")
            project.is_team_division = True
        db.flush()

        for team_id, project_id, name in (
            (TEAM_A_ID, KING_PROJECT_ID, "E2E王者一队"),
            (TEAM_B_ID, PEACE_PROJECT_ID, "E2E和平二队"),
            (TEAM_C_ID, DELTA_PROJECT_ID, "E2E三角洲三队"),
        ):
            db.add(ProfessionalTeam(
                id=team_id,
                project_id=project_id,
                name=name,
                notes="仅隔离 E2E 使用",
                is_active=True,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            ))
        db.flush()

        for fixture in FIXTURE_ACCOUNTS:
            credential = E2E_CREDENTIALS[fixture.key]
            person = _seed_person(
                person_id=fixture.person_id,
                full_name=fixture.name,
                identity_birth="19800101",
                identity_sequence=801 + fixture.sequence,
                sex="female" if fixture.sequence % 2 else "male",
                date_of_birth=date(1980, 1, 1),
                phone=credential.identifier,
                cipher=cipher,
                lookup=lookup,
            )
            if fixture.deleted:
                person.deleted_at = datetime(2026, 6, 2, tzinfo=UTC)
            db.add(person)
        db.flush()

        for fixture in FIXTURE_ACCOUNTS:
            credential = E2E_CREDENTIALS[fixture.key]
            account = _seed_account(
                account_id=fixture.account_id,
                person_id=fixture.person_id,
                phone=credential.identifier,
                email=f"{fixture.key}@example.invalid",
                password=credential.password,
                lookup=lookup,
                passwords=passwords,
            )
            account.status = fixture.status
            db.add(account)
        db.flush()
        for fixture in FIXTURE_ACCOUNTS:
            _upsert_seed_phone_identifier(
                db,
                account_id=fixture.account_id,
                phone=E2E_CREDENTIALS[fixture.key].identifier,
                lookup=lookup,
            )
            db.add(UserRole(user_id=fixture.account_id, role_id=ROLES[fixture.role].id))
        db.add(UserRole(
            user_id=E2E_SELF_STUDENT_ACCOUNT_ID,
            role_id=ROLES["team_member"].id,
        ))
        db.flush()

        started_at = datetime(2026, 1, 1, tzinfo=UTC)
        for sequence, (key, team_id) in enumerate((
            ("team_manager_a", TEAM_A_ID),
            ("team_manager_b", TEAM_B_ID),
            ("team_manager_multi", TEAM_A_ID),
            ("team_manager_multi", TEAM_B_ID),
            ("team_manager_shared", TEAM_A_ID),
        ), start=1):
            db.add(TeamManagerAssignment(
                id=UUID(f"e8110000-0000-4000-8000-{sequence:012d}"),
                account_id=ACCOUNTS_BY_KEY[key].account_id,
                team_id=team_id,
                created_at=started_at,
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
            ))

        db.add(TeamMembership(
            id=STUDENT_MEMBERSHIP_ID,
            person_id=E2E_SELF_STUDENT_PERSON_ID,
            team_id=TEAM_A_ID,
            joined_on=date(2026, 1, 1),
            created_at=started_at,
            created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
        ))
        for membership_id, key, end_kind in (
            (HISTORICAL_DISABLED_MEMBERSHIP_ID, "team_disabled", "left"),
            (HISTORICAL_DELETED_MEMBERSHIP_ID, "team_deleted", "account_deleted"),
        ):
            db.add(TeamMembership(
                id=membership_id,
                person_id=ACCOUNTS_BY_KEY[key].person_id,
                team_id=TEAM_B_ID,
                joined_on=date(2026, 1, 1),
                left_on=date(2026, 6, 2),
                created_at=started_at,
                ended_at=datetime(2026, 6, 2, tzinfo=UTC),
                created_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                ended_by_user_id=E2E_ADMIN_ACCOUNT_ID,
                end_kind=end_kind,
            ))
        db.flush()


def main() -> int:
    settings = get_settings()
    require_team_e2e_environment(settings.app_env, settings.database_url)
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        seed_team_management_e2e(
            db,
            settings=settings,
            cipher=EnvelopeCipher(_secret_bytes(settings, "field_encryption_master_key")),
            lookup=LookupDigest(_secret_bytes(settings, "field_lookup_key")),
            passwords=PasswordService(),
        )
    print("Team management E2E fixtures rebuilt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
