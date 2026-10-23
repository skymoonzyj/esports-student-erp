from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.identity.models import Person, StaffProfile
from app.personnel_intake.models import CoachPersonResponsibility
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES, seed_roles_and_permissions
from tests.invited_housing_helpers import account, person, stay


def coach(db):
    user = account(db, "coach")
    user.force_password_change = False
    db.add(StaffProfile(person_id=user.person_id, staff_type="coach"))
    db.flush()
    return AuthContext(account=user, person=db.get(Person, user.person_id), session=None)


def responsibility(db, who, target):
    edge = CoachPersonResponsibility(
        coach_person_id=who.person.id, person_id=target.id, created_by_user_id=who.account.id
    )
    db.add(edge)
    db.flush()
    return edge


def page(db, who, **filters):
    assert find_spec("app.personnel_intake.queries") is not None, "managed-person query is missing"
    from app.personnel_intake.queries import ManagedPeopleQuery
    from app.personnel_intake.schemas import ManagedPeopleFilters

    return ManagedPeopleQuery(db).list(who, ManagedPeopleFilters(**filters))


def test_owner_scope_applies_to_counts_pages_and_filters(db):
    who, other = coach(db), coach(db)
    own = [person(db, student=True) for _ in range(3)]
    outsider = person(db, student=True)
    for target in own:
        responsibility(db, who, target)
    responsibility(db, other, outsider)
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.housing.read"].id,
            effect="allow",
            scope_kind="person",
            scope_id=outsider.id,
        )
    )
    db.flush()
    first = page(db, who, page_size=2)
    second = page(db, who, page_size=2, cursor=first.next_cursor)
    assert first.total == second.total == 3
    assert len(first.items) == 2 and len(second.items) == 1
    assert {item.person_id for item in [*first.items, *second.items]} == {p.id for p in own}
    assert second.next_cursor is None
    assert page(db, who, query=outsider.full_name).total == 0
    assert page(db, who, team_id=uuid4()).total == 0
    for item in first.items:
        assert set(item.model_dump()) == {
            "person_id",
            "full_name",
            "nickname",
            "kinds",
            "authorized_affiliations",
            "housing",
        }
        assert item.kinds == ["student"]
        assert [a.id for a in item.authorized_affiliations] == [who.person.id]
    with pytest.raises(ProblemError) as wrong_account:
        page(db, other, page_size=2, cursor=first.next_cursor)
    assert wrong_account.value.status == 422
    with pytest.raises(ProblemError):
        page(db, who, query="different", page_size=2, cursor=first.next_cursor)


@pytest.mark.parametrize(
    "status,offset,want",
    [
        (None, 0, "unarranged"),
        ("planned", 2, "reserved"),
        ("planned", -1, "check_in_pending"),
        ("checked_in", -2, "checked_in"),
        ("checkout_pending", -2, "checkout_pending"),
        ("checked_out", -2, "checked_out"),
    ],
)
def test_revocation_removes_new_reads_without_changing_stays(db, status, offset, want):
    who = coach(db)
    target = person(db, student=True)
    edge = responsibility(db, who, target)
    now = datetime.now(UTC)
    physical = (
        stay(
            db,
            who.account,
            target,
            status=status,
            start=now + timedelta(days=offset),
            actual_end=now - timedelta(days=1) if status == "checked_out" else None,
        )
        if status
        else None
    )
    result = page(db, who, housing_status=want)
    assert result.total == 1 and result.items[0].housing.status == want
    if physical:
        assert result.items[0].housing.stay.duration_mode == "long_term"
        assert set(result.items[0].housing.stay.model_dump()) == {
            "building_name",
            "floor_name",
            "room_name",
            "duration_mode",
            "planned_check_in_at",
            "planned_check_out_at",
            "actual_check_in_at",
            "actual_check_out_at",
        }
    edge.ended_at = now + timedelta(seconds=1)
    edge.ended_by_user_id = who.account.id
    db.flush()
    assert page(db, who).total == 0
    if physical:
        db.refresh(physical)
        assert physical.status == status


def test_explicit_person_deny_is_applied_before_count_and_summary(db):
    who = coach(db)
    hidden, visible = person(db, student=True), person(db, student=True)
    for target in (hidden, visible):
        responsibility(db, who, target)
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.housing.read"].id,
            effect="deny",
            scope_kind="person",
            scope_id=hidden.id,
        )
    )
    db.flush()
    result = page(db, who)
    assert result.total == 1 and [item.person_id for item in result.items] == [visible.id]


@pytest.mark.parametrize("revoke", ["manager", "membership", "role", "team", "project"])
def test_current_team_owner_and_membership_are_both_required(db, revoke):
    from app.teaching.models import GameProject
    from app.team_management.models import TeamManagerAssignment, TeamMembership
    from tests.services.test_team_access import teams

    who = coach(db)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    first, second = teams(db, who.account.id)
    target, other = person(db), person(db)
    assignment = TeamManagerAssignment(
        account_id=who.account.id, team_id=first.id, created_by_user_id=who.account.id
    )
    membership = TeamMembership(
        person_id=target.id,
        team_id=first.id,
        joined_on=datetime.now(UTC).date(),
        created_by_user_id=who.account.id,
    )
    db.add_all(
        [
            assignment,
            membership,
            TeamMembership(
                person_id=other.id,
                team_id=second.id,
                joined_on=datetime.now(UTC).date(),
                created_by_user_id=who.account.id,
            ),
        ]
    )
    db.flush()
    result = page(db, who, team_id=first.id, kind="player")
    assert result.total == 1 and result.items[0].person_id == target.id
    assert result.items[0].kinds == ["player"]
    assert [(a.kind, a.id) for a in result.items[0].authorized_affiliations] == [("team", first.id)]
    assert page(db, who, team_id=second.id).total == 0
    now = datetime.now(UTC) + timedelta(seconds=1)
    if revoke == "manager":
        assignment.ended_at, assignment.ended_by_user_id, assignment.end_kind = (
            now,
            who.account.id,
            "unassigned",
        )
    elif revoke == "membership":
        (
            membership.ended_at,
            membership.ended_by_user_id,
            membership.end_kind,
            membership.left_on,
        ) = now, who.account.id, "left", now.date()
    elif revoke == "role":
        db.delete(db.get(UserRole, (who.account.id, ROLES["team_manager"].id)))
    elif revoke == "team":
        first.is_active = False
    else:
        db.get(GameProject, first.project_id).is_active = False
    db.flush()
    assert page(db, who).total == 0


def test_projection_queries_are_bounded_and_never_include_unowned_ids(db):
    who = coach(db)
    outsider = person(db, student=True)
    own = [person(db, student=True) for _ in range(8)]
    for target in own:
        responsibility(db, who, target)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "housing_stays" in statement:
            statements.append((statement, parameters))

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        result = page(db, who)
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert result.total == 8
    assert len(statements) <= 4
    assert all(str(outsider.id) not in str(parameters) for _, parameters in statements)


def test_repeated_seed_does_not_restore_coach_global_housing_grant(db):
    from app.rbac.models import RolePermission

    seed_roles_and_permissions(db)
    seed_roles_and_permissions(db)
    assert (
        db.scalar(
            select(RolePermission.role_id).where(
                RolePermission.role_id == ROLES["coach"].id,
                RolePermission.permission_id == PERMISSIONS["housing.read"].id,
                RolePermission.scope_kind == "global",
            )
        )
        is None
    )


def test_privileged_role_never_substitutes_for_current_owner_relation(db):
    who = coach(db)
    target = person(db, student=True)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["admin"].id))
    db.flush()
    assert page(db, who).total == 0
    edge = responsibility(db, who, target)
    assert page(db, who).total == 1
    edge.ended_at, edge.ended_by_user_id = datetime.now(UTC) + timedelta(seconds=1), who.account.id
    db.flush()
    assert page(db, who).total == 0


def test_mixed_administration_retains_scoped_management_despite_old_coach_grant(db):
    from app.housing.access import HousingAccessService
    from app.housing.models import HousingRoom
    from app.rbac.models import RolePermission
    from tests.invited_housing_helpers import room

    who = coach(db)
    owned, hidden = room(db), room(db)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["administration"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["administration"].id,
            RolePermission.permission_id == PERMISSIONS["housing.read"].id,
        )
    )
    # Simulate a not-yet-cleaned older grant alongside a legitimate mixed role.
    db.add(
        RolePermission(
            role_id=ROLES["coach"].id,
            permission_id=PERMISSIONS["housing.read"].id,
            scope_kind="global",
        )
    )
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["housing.read"].id,
            effect="allow",
            scope_kind="floor",
            scope_id=owned.floor_id,
        )
    )
    db.flush()
    access = HousingAccessService(db, who.account.id, datetime.now(UTC))
    assert access.can_read_room(owned)
    assert not access.can_read_room(hidden)
    assert set(
        db.scalars(select(HousingRoom.id).where(access.room_permission_criteria("housing.read")))
    ) == {owned.id}


@pytest.mark.parametrize("change", ["global_deny", "role", "disabled", "deleted", "coach_staff"])
def test_current_actor_qualification_and_capability_are_required(db, change):
    who = coach(db)
    target = person(db, student=True)
    responsibility(db, who, target)
    assert page(db, who).total == 1
    if change == "global_deny":
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["personnel_intake.housing.read"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    elif change == "role":
        db.delete(db.get(UserRole, (who.account.id, ROLES["coach"].id)))
    elif change == "disabled":
        who.account.status = "disabled"
    elif change == "deleted":
        who.person.deleted_at = datetime.now(UTC)
    else:
        db.get(StaffProfile, who.person.id).staff_type = "other"
    db.flush()
    if change == "coach_staff":
        assert page(db, who).total == 0
    else:
        with pytest.raises(ProblemError) as error:
            page(db, who)
        assert error.value.status == 403


def test_explicit_team_deny_wins_over_another_coach_owner_path(db):
    from app.team_management.models import TeamManagerAssignment, TeamMembership
    from tests.services.test_team_access import teams

    who = coach(db)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    target = person(db, student=True)
    responsibility(db, who, target)
    team, _ = teams(db, who.account.id)
    db.add_all(
        [
            TeamManagerAssignment(
                account_id=who.account.id, team_id=team.id, created_by_user_id=who.account.id
            ),
            TeamMembership(
                person_id=target.id,
                team_id=team.id,
                joined_on=datetime.now(UTC).date(),
                created_by_user_id=who.account.id,
            ),
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["personnel_intake.housing.read"].id,
                effect="deny",
                scope_kind="assigned_team",
                scope_id=team.id,
            ),
        ]
    )
    db.flush()
    assert page(db, who).total == 0


def test_team_membership_without_student_kind_does_not_create_coach_authority(db):
    from app.team_management.models import TeamMembership
    from tests.services.test_team_access import teams

    who = coach(db)
    target = person(db)
    team, _ = teams(db, who.account.id)
    db.add(
        TeamMembership(
            person_id=target.id,
            team_id=team.id,
            joined_on=datetime.now(UTC).date(),
            created_by_user_id=who.account.id,
        )
    )
    responsibility(db, who, target)
    assert page(db, who).total == 0
