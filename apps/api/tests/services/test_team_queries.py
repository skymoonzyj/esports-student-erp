from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import Person, StaffProfile
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.teaching.models import GameProject
from app.team_management.errors import TeamAccessDenied
from app.team_management.models import TeamManagerAssignment, TeamMembership
from app.team_management.projection import project_person_teams
from app.team_management.query_service import TeamQueryService
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_team_access import actor, teams


@pytest.fixture(name="platform_users")
def users(db: Session):
    return _platform_users.__wrapped__(db)


def candidate(db, index, *, name="张三", nickname=None, staff=False):
    person = Person(
        id=UUID(int=10000 + index),
        full_name=name,
        nickname=nickname,
        identity_ciphertext=EnvelopeCipher(bytes(range(32))).encrypt(
            "110105201009300001", field_name="identity_number"
        ),
        identity_digest=f"{index:064x}",
        sex="male",
        date_of_birth=date(2010, 9, 30),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id, password_hash="unused", status="active", force_password_change=False
    )
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES["team_member"].id))
    if staff:
        db.add(StaffProfile(person_id=person.id, staff_type="other"))
    db.flush()
    return person, account


@pytest.mark.parametrize("role", ["self_student", "admin"])
@pytest.mark.parametrize("has_current", [False, True])
def test_self_history_filters_actual_team_denials_before_pagination(
    db, platform_users, role, has_current
):
    admin = actor(platform_users, "admin")
    member = actor(platform_users, role)
    first, second = teams(db, admin.account.id)
    if role != "admin":
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == member.account.id))
        db.add(UserRole(user_id=member.account.id, role_id=ROLES["team_member"].id))
    for index, team in enumerate([first, second]):
        db.add(
            TeamMembership(
                person_id=member.person.id,
                team_id=team.id,
                joined_on=date(2026, 9, 1),
                left_on=date(2026, 9, 2),
                created_at=datetime(2026, 9, 1, index, tzinfo=UTC),
                ended_at=datetime(2026, 9, 2, tzinfo=UTC),
                end_kind="transferred",
                transfer_reason="禁止泄露原因" if index == 0 else "允许原因",
                created_by_user_id=admin.account.id,
            )
        )
    if has_current:
        db.add(
            TeamMembership(
                person_id=member.person.id,
                team_id=second.id,
                joined_on=date(2026, 9, 3),
                created_by_user_id=admin.account.id,
            )
        )
    deny = UserPermissionOverride(
        user_id=member.account.id,
        permission_id=PERMISSIONS["team_memberships.read"].id,
        effect="deny",
        scope_kind="assigned_team",
        scope_id=first.id,
    )
    db.add(deny)
    db.flush()
    service = TeamQueryService(db)
    statements = []

    def capture(*args):
        statements.append(args[2])

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        response = service.my_team(member, page=1, page_size=1)
        count = len(statements)
        assert response.history.total == 1
        assert [row.team_id for row in response.history.items] == [second.id]
        assert "禁止泄露原因" not in response.model_dump_json()
        assert service.my_team(member, page=2, page_size=1).history.items == []
        deny.valid_from = datetime.now(UTC) - timedelta(days=2)
        deny.valid_until = datetime.now(UTC) - timedelta(days=1)
        db.flush()
        statements.clear()
        response = service.my_team(member, page=1, page_size=100)
        assert response.history.total == 2
        assert len(statements) <= count + 1
    finally:
        event.remove(connection, "before_cursor_execute", capture)


def test_same_day_history_uses_creation_time_then_stable_id_across_pages(db, platform_users):
    admin = actor(platform_users, "admin")
    first, _ = teams(db, admin.account.id)
    for identifier, hour in [(3, 1), (1, 2), (2, 2)]:
        db.add(
            TeamMembership(
                id=UUID(int=identifier),
                person_id=admin.person.id,
                team_id=first.id,
                joined_on=date(2026, 9, 1),
                left_on=date(2026, 9, 1),
                created_at=datetime(2026, 9, 1, hour, tzinfo=UTC),
                ended_at=datetime(2026, 9, 1, 3, tzinfo=UTC),
                end_kind="left",
                created_by_user_id=admin.account.id,
            )
        )
    db.flush()
    service = TeamQueryService(db)
    for own in [False, True]:
        rows = []
        for page in range(1, 4):
            result = (
                service.my_team(admin, page=page, page_size=1).history
                if own
                else service.list_memberships(
                    admin, first.id, status="left", page=page, page_size=1
                )
            )
            rows.extend(result.items)
        assert [row.id for row in rows] == [UUID(int=2), UUID(int=1), UUID(int=3)]
        assert all("created_at" not in row.model_dump() for row in rows)


def test_search_and_pagination_do_not_leak_or_skip(db, platform_users):
    admin = actor(platform_users, "admin")
    first, second = teams(db, admin.account.id)
    # An initial-only match beyond a complete scan batch cannot be skipped.
    for index in range(201):
        candidate(db, index, name="李四")
    expected = []
    for index in range(201, 226):
        person, _ = candidate(db, index, nickname="星辰")
        expected.append(person.id)
    _, disabled = candidate(db, 226)
    disabled.status = "disabled"
    _, temporary = candidate(db, 227)
    temporary.force_password_change = True
    occupied, _ = candidate(db, 228)
    db.add(
        TeamMembership(
            person_id=occupied.id,
            team_id=second.id,
            joined_on=date(2026, 9, 29),
            created_by_user_id=admin.account.id,
        )
    )
    db.flush()
    service = TeamQueryService(db)
    for query in ("ZS", "ＺＳ", "张", "星辰"):
        seen = []
        cursor = None
        while True:
            page = service.candidates(
                admin, first.id, kind="member", query=query, cursor=cursor, page_size=7
            )
            assert len(page.items) <= 7
            assert "total" not in page.model_dump()
            seen.extend(item.person_id for item in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert seen == expected
    literal, _ = candidate(db, 229, name="张%_三")
    result = service.candidates(
        admin, first.id, kind="member", query="%_", cursor=None, page_size=20
    )
    assert [item.person_id for item in result.items] == [literal.id]


def test_manager_candidate_can_already_manage_another_team(db, platform_users):
    admin = actor(platform_users, "admin")
    coach = actor(platform_users, "coach")
    first, second = teams(db, admin.account.id)
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id))
    db.add(
        TeamManagerAssignment(
            account_id=coach.account.id, team_id=second.id, created_by_user_id=admin.account.id
        )
    )
    db.flush()
    result = TeamQueryService(db).candidates(
        admin, first.id, kind="manager", query="教练", cursor=None, page_size=20
    )
    assert [item.account_id for item in result.items] == [coach.account.id]


def test_private_history_and_projection(db, platform_users):
    admin = actor(platform_users, "admin")
    coach = actor(platform_users, "coach")
    member = actor(platform_users, "self_student")
    first, second = teams(db, admin.account.id)
    db.add_all(
        [
            UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id),
            UserRole(user_id=member.account.id, role_id=ROLES["team_member"].id),
            TeamManagerAssignment(
                account_id=coach.account.id, team_id=first.id, created_by_user_id=admin.account.id
            ),
        ]
    )
    old = TeamMembership(
        person_id=member.person.id,
        team_id=first.id,
        joined_on=date(2026, 9, 1),
        left_on=date(2026, 9, 2),
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        ended_at=datetime(2026, 9, 2, tzinfo=UTC),
        end_kind="transferred",
        transfer_reason="私密原因",
        created_by_user_id=admin.account.id,
    )
    current = TeamMembership(
        person_id=member.person.id,
        team_id=second.id,
        joined_on=date(2026, 9, 2),
        created_by_user_id=admin.account.id,
    )
    db.add_all([old, current])
    db.flush()
    service = TeamQueryService(db)
    page = service.list_memberships(coach, first.id, status="all", page=1, page_size=20)
    assert page.total == 1 and page.items[0].transfer_reason is None
    own = service.my_team(member, page=1, page_size=20)
    assert own.current.team_id == second.id
    assert own.history.total == 1 and own.history.items[0].transfer_reason == "私密原因"
    projection = project_person_teams(db, coach, [member.person.id])[member.person.id]
    assert projection.model_dump() == {"visibility": "denied", "current": None}
    page = service.list_teams(coach, project_id=None, page=1, page_size=1)
    assert page.total == 1 and page.items[0].id == first.id
    project = db.get(GameProject, first.project_id)
    project.is_active = False
    project.is_team_division = False
    db.flush()
    assert service.my_team(member, page=1, page_size=20).history.total == 1
    listed = service.list_teams(coach, project_id=None, page=1, page_size=20)
    assert listed.total == 1 and listed.items[0].is_active is False


def test_people_projection_has_bounded_queries(db, platform_users):
    admin = actor(platform_users, "admin")
    ids = [candidate(db, index, staff=True)[0].id for index in range(100)]
    statements = []

    def capture(*args):
        statements.append(args[2])

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        project_person_teams(db, admin, ids[:20])
        short = len(statements)
        statements.clear()
        result = project_person_teams(db, admin, ids)
        assert len(statements) <= short + 1
        assert all(
            item.visibility == "allowed" and item.current is None for item in result.values()
        )
        identity = IdentityService(
            db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
        )
        statements.clear()
        identity.search_people(actor=admin, role="team_member", page=1, page_size=20)
        short = len(statements)
        statements.clear()
        page = identity.search_people(actor=admin, role="team_member", page=1, page_size=100)
        assert len(statements) <= short + 1
        assert page.total == 100 and len(page.items) == 100
        assert all(item.staff_type == "other" for item in page.items)
        assert identity.count_people(actor=admin).team_member == 100
    finally:
        event.remove(connection, "before_cursor_execute", capture)


def test_roster_age_deleted_history_and_no_private_columns(db, platform_users, monkeypatch):
    import app.team_management.query_service as query_module

    admin = actor(platform_users, "admin")
    first, _ = teams(db, admin.account.id)
    person, account = candidate(db, 1000)
    db.add(
        TeamMembership(
            person_id=person.id,
            team_id=first.id,
            joined_on=date(2026, 9, 1),
            created_by_user_id=admin.account.id,
        )
    )
    db.flush()
    monkeypatch.setattr(query_module, "business_today", lambda: date(2026, 9, 29))
    service = TeamQueryService(db)
    page = service.list_memberships(admin, first.id, status="current", page=1, page_size=20)
    assert page.items[0].age == 15 and page.items[0].is_minor
    forbidden = {"date_of_birth", "phone", "email", "password_hash", "identity_ciphertext"}
    assert forbidden.isdisjoint(page.items[0].model_dump())
    person.deleted_at = datetime.now(UTC)
    account.status = "disabled"
    db.flush()
    assert (
        service.list_memberships(admin, first.id, status="all", page=1, page_size=20)
        .items[0]
        .account_status
        == "deleted"
    )


@pytest.mark.parametrize("deny_scope", ["self", "assigned_team"])
def test_own_current_projection_and_count_honor_all_actual_denies(db, platform_users, deny_scope):
    admin = actor(platform_users, "admin")
    other = platform_users["people"]["self_student"]
    first, second = teams(db, admin.account.id)
    own = TeamMembership(
        person_id=admin.person.id,
        team_id=first.id,
        joined_on=date(2026, 9, 1),
        created_by_user_id=admin.account.id,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    db.add_all(
        [
            own,
            TeamMembership(
                person_id=other.id,
                team_id=second.id,
                joined_on=date(2026, 9, 1),
                created_by_user_id=admin.account.id,
            ),
        ]
    )
    db.add(
        UserPermissionOverride(
            user_id=admin.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind=deny_scope,
            scope_id=first.id if deny_scope == "assigned_team" else None,
        )
    )
    db.flush()
    result = project_person_teams(db, admin, [admin.person.id, other.id])
    assert result[admin.person.id].model_dump() == {"visibility": "denied", "current": None}
    assert result[other.id].visibility == "allowed"
    assert result[other.id].current.team_id == second.id
    identity = platform_users["identity_service"]
    assert identity.count_people(actor=admin).in_team == 1
    with pytest.raises(TeamAccessDenied):
        TeamQueryService(db).my_team(admin, page=1, page_size=20)
    if deny_scope == "self":
        # Narrow projection denial must not rewrite general roster access.
        assert (
            TeamQueryService(db)
            .list_memberships(admin, first.id, status="current", page=1, page_size=20)
            .total
            == 1
        )
    own.left_on = date(2026, 9, 2)
    own.ended_at = datetime(2026, 9, 2, tzinfo=UTC)
    own.end_kind = "left"
    db.flush()
    result = project_person_teams(db, admin, [admin.person.id])
    assert result[admin.person.id].current is None
    assert result[admin.person.id].visibility == ("denied" if deny_scope == "self" else "allowed")
