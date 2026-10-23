from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import AuthContext
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.access import TeamAccessService
from app.team_management.errors import TeamAccessDenied, TeamNotFound
from app.team_management.models import TeamManagerAssignment
from tests.api.conftest import platform_users as _api_platform_users


@pytest.fixture(name="platform_users")
def team_platform_users(db: Session):
    return _api_platform_users.__wrapped__(db)


def actor(platform_users, key: str) -> AuthContext:
    account = platform_users["accounts"][key]
    person = platform_users["people"][key]
    return AuthContext(session=None, account=account, person=person)  # type: ignore[arg-type]


def teams(db: Session, admin_id):
    project = db.scalar(select(GameProject).where(GameProject.name == "王者荣耀"))
    assert project is not None and project.is_team_division
    first = ProfessionalTeam(
        project_id=project.id, name="访问测试一队", created_by_user_id=admin_id
    )
    second = ProfessionalTeam(
        project_id=project.id, name="访问测试二队", created_by_user_id=admin_id
    )
    db.add_all([first, second])
    db.flush()
    return first, second


def test_scope_requires_live_role_and_assignment(db: Session, platform_users) -> None:
    admin = actor(platform_users, "admin")
    coach = actor(platform_users, "coach")
    first, second = teams(db, admin.account.id)
    service = TeamAccessService(db)
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.flush()
    with pytest.raises(TeamAccessDenied) as unbound:
        service.require_team(coach, first.id, "read")
    assert unbound.value.code == "access_denied"
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id))
    db.flush()
    with pytest.raises(TeamAccessDenied):
        service.require_team(coach, first.id, "read")
    db.add(
        TeamManagerAssignment(
            account_id=coach.account.id,
            team_id=first.id,
            created_by_user_id=admin.account.id,
        )
    )
    db.flush()
    assert service.require_team(coach, first.id, "read").id == first.id
    with pytest.raises(TeamNotFound) as hidden:
        service.require_team(coach, second.id, "read")
    with pytest.raises(TeamNotFound) as missing:
        service.require_team(coach, uuid4(), "read")
    assert (hidden.value.code, str(hidden.value)) == (missing.value.code, str(missing.value))
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        )
    )
    db.flush()
    assert first.id not in set(db.scalars(service.visible_team_ids(coach, "read")))
    with pytest.raises(TeamNotFound):
        service.require_team(coach, first.id, "read")
    db.add(
        UserPermissionOverride(
            user_id=admin.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    with pytest.raises(TeamNotFound):
        service.require_team(admin, first.id, "read")


def test_assigned_team_override_is_object_specific_and_live(db: Session, platform_users) -> None:
    admin = actor(platform_users, "admin")
    coach = actor(platform_users, "coach")
    first, second = teams(db, admin.account.id)
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id))
    assignment = TeamManagerAssignment(
        account_id=coach.account.id,
        team_id=first.id,
        created_by_user_id=admin.account.id,
    )
    db.add(assignment)
    db.flush()
    service = TeamAccessService(db)
    assert service.require_team(coach, first.id, "manage").id == first.id
    project = db.get(GameProject, first.project_id)
    assert project is not None
    first.is_active = False
    project.is_active = False
    project.is_team_division = False
    db.flush()
    assert service.require_team(coach, first.id, "read").id == first.id
    assert service.require_team(coach, first.id, "manage").id == first.id
    with pytest.raises(TeamNotFound):
        service.require_team(coach, second.id, "manage")
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.manage"].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
            valid_from=datetime.now(UTC) - timedelta(days=2),
            valid_until=datetime.now(UTC) - timedelta(days=1),
        )
    )
    db.flush()
    assert service.require_team(coach, first.id, "manage").id == first.id
    assignment.ended_at = datetime.now(UTC)
    assignment.end_kind = "unassigned"
    db.flush()
    with pytest.raises(TeamAccessDenied):
        service.require_team(coach, first.id, "manage")
    assignment.ended_at = None
    assignment.end_kind = None
    db.execute(
        UserRole.__table__.delete().where(
            UserRole.user_id == coach.account.id,
            UserRole.role_id == ROLES["team_manager"].id,
        )
    )
    db.flush()
    with pytest.raises(TeamAccessDenied):
        service.require_team(coach, first.id, "manage")


def test_context_empty_catalog_superadmin_and_unbound_manager(db: Session, platform_users) -> None:
    admin = actor(platform_users, "admin")
    coach = actor(platform_users, "coach")
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id))
    db.flush()
    service = TeamAccessService(db)
    assert service.context(admin).can_open_management is True
    assert service.context(admin).can_manage_catalog is True
    assert service.context(coach).can_open_management is False
    assert service.context(coach).can_transfer is False
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.transfer"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.flush()
    assert service.context(coach).can_transfer is False
    with pytest.raises(TeamAccessDenied):
        service.require_team(coach, uuid4(), "transfer")


def test_self_requires_member_role_and_never_grants_roster(db: Session, platform_users) -> None:
    coach = actor(platform_users, "coach")
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="allow",
            scope_kind="self",
        )
    )
    db.flush()
    service = TeamAccessService(db)
    with pytest.raises(TeamAccessDenied):
        service.require_self(coach)
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_member"].id))
    db.flush()
    assert service.require_self(coach) == coach.person.id
    assert list(db.scalars(service.visible_team_ids(coach, "read"))) == []


def test_unassigned_projection_requires_superadmin_global_or_own_member_self(
    db: Session,
    platform_users,
) -> None:
    admin = actor(platform_users, "admin")
    coach = actor(platform_users, "coach")
    other_id = platform_users["people"]["self_student"].id
    service = TeamAccessService(db)
    assert service.readable_unassigned_person_ids(admin, [admin.person.id, other_id]) == {
        admin.person.id,
        other_id,
    }
    db.add(
        UserPermissionOverride(
            user_id=admin.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="deny",
            scope_kind="self",
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=coach.account.id,
            permission_id=PERMISSIONS["team_memberships.read"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_manager"].id))
    db.flush()
    assert service.readable_unassigned_person_ids(admin, [admin.person.id, other_id]) == {other_id}
    assert service.readable_unassigned_person_ids(coach, [coach.person.id, other_id]) == set()
    db.add(UserRole(user_id=coach.account.id, role_id=ROLES["team_member"].id))
    db.flush()
    assert service.readable_unassigned_person_ids(coach, [coach.person.id, other_id]) == {
        coach.person.id,
    }
