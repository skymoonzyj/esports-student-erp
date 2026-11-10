"""Readonly team staff must never inherit legacy manager write authority."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.time import business_date_at
from app.housing.access import HousingAccessService
from app.identity.models import Person, StaffProfile
from app.personnel_intake.access import IntakeAccessSnapshot, PersonnelIntakeAccess
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.policy import PolicyRule, RbacPolicy
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacDecisionContext
from tests.invited_housing_helpers import account, person, stay
from tests.services.test_managed_people_housing import page, responsibility
from tests.services.test_team_access import teams

PERMISSION = "personnel_intake.housing.read"
STAFF_ROLES = [
    ("team_manager", "manager"),
    ("coach", "coach"),
    ("assistant_coach", "assistant_coach"),
]


def test_assistant_can_enter_readonly_managed_projection():
    state = IntakeAccessSnapshot(
        uuid4(),
        uuid4(),
        frozenset({"assistant_coach"}),
        RbacDecisionContext(RbacPolicy(), [PolicyRule(PERMISSION, "allow", "global")], []),
        False,
        frozenset(),
    )
    assert state.can_enter_managed_people()


def test_assistant_global_override_cannot_open_rooms_or_housing_writes():
    # Exercise the real role/policy boundary without requiring a database.
    access = HousingAccessService.__new__(HousingAccessService)
    access.roles = frozenset({"assistant_coach"})
    access.context = RbacDecisionContext(
        RbacPolicy(),
        [],
        [
            PolicyRule(key, "allow", "global")
            for key in (
                "housing.read",
                "housing.stays.manage",
                "housing.transfers.manage",
                "housing.checkouts.manage",
            )
        ],
    )
    assert not access.can("housing.read", {"global"})
    assert not access.can("housing.stays.manage", {"global"})
    assert not access.can("housing.transfers.manage", {"global"})
    assert not access.can("housing.checkouts.manage", {"global"})


def staff_fixture(db, role_key, staff_role):
    from app.team_management.models import TeamMembership, TeamStaffAssignment

    user = account(db, role_key)
    user.force_password_change = False
    # Team staff scope follows account roles, not the single primary staff type.
    db.add(
        StaffProfile(
            person_id=user.person_id, staff_type="coach" if role_key == "coach" else "other"
        )
    )
    who = AuthContext(account=user, person=db.get(Person, user.person_id), session=None)
    first, other_team = teams(db, user.id)
    assignment = TeamStaffAssignment(
        account_id=user.id, team_id=first.id, staff_role=staff_role, created_by_user_id=user.id
    )
    target, outsider = person(db), person(db)
    members = [
        TeamMembership(
            person_id=subject.id,
            team_id=team.id,
            joined_on=datetime.now(UTC).date() - timedelta(days=1),
            created_by_user_id=user.id,
        )
        for subject, team in ((target, first), (outsider, other_team))
    ]
    db.add_all([assignment, *members])
    db.flush()
    return who, first, other_team, assignment, members[0], target, outsider


@pytest.mark.parametrize("role_key,staff_role", STAFF_ROLES)
def test_staff_reads_only_current_team_summary_without_intake_write(db, role_key, staff_role):
    who, first, other_team, _, _, target, outsider = staff_fixture(db, role_key, staff_role)
    stay(db, who.account, target, status="checked_in")
    result = page(db, who)
    assert result.total == 1
    item = result.items[0]
    assert item.person_id == target.id
    assert [(a.kind, a.id) for a in item.authorized_affiliations] == [("team", first.id)]
    assert item.housing.status == "checked_in"
    assert set(item.model_dump()) == {
        "person_id",
        "full_name",
        "nickname",
        "kinds",
        "authorized_affiliations",
        "housing",
    }
    assert "identity" not in item.model_dump_json()
    assert "phone" not in item.model_dump_json()
    assert "私密住宿备注" not in item.model_dump_json()
    assert page(db, who, team_id=other_team.id).total == 0
    assert page(db, who, query=outsider.full_name).total == 0
    access = PersonnelIntakeAccess(db)
    state = access.snapshot(who)
    assert state.managed_team_ids == frozenset()
    assert state.readable_staff_team_ids == frozenset({first.id})
    # Even a global independent intake allow cannot turn a readonly staff team
    # into the old manager binding needed to submit a player.
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.submit"].id,
            effect="allow",
            scope_kind="global",
        )
    )
    db.flush()
    assert not access.covers(who, kind="player", coaches=[], team_id=first.id)


@pytest.mark.parametrize(
    "change",
    [
        "binding",
        "membership",
        "future_member",
        "role",
        "disabled",
        "deleted",
        "team",
        "division",
        "global_deny",
    ],
)
def test_staff_summary_rechecks_live_qualification(db, change):
    from app.teaching.models import GameProject

    who, team, _, assignment, membership, _, _ = staff_fixture(
        db, "assistant_coach", "assistant_coach"
    )
    assert page(db, who).total == 1
    if change == "binding":
        assignment.ended_at, assignment.end_kind = datetime.now(UTC), "unassigned"
    elif change == "membership":
        membership.ended_at, membership.left_on, membership.end_kind = (
            datetime.now(UTC),
            datetime.now(UTC).date(),
            "left",
        )
    elif change == "future_member":
        membership.joined_on = business_date_at(datetime.now(UTC)) + timedelta(days=1)
    elif change == "role":
        db.delete(db.get(UserRole, (who.account.id, ROLES["assistant_coach"].id)))
    elif change == "disabled":
        who.account.status = "disabled"
    elif change == "deleted":
        who.person.deleted_at = datetime.now(UTC)
    elif change == "team":
        team.is_active = False
    elif change == "division":
        db.get(GameProject, team.project_id).is_active = False
    else:
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS[PERMISSION].id,
                effect="deny",
                scope_kind="global",
            )
        )
    db.flush()
    if change in {"role", "disabled", "deleted", "global_deny"}:
        with pytest.raises(ProblemError) as denied:
            page(db, who)
        assert denied.value.status == 403
    else:
        assert page(db, who).total == 0


def test_multi_staff_paths_deduplicate_pages_and_deny_cannot_be_rescued_by_coach(db):
    from app.team_management.models import (
        TeamManagerAssignment,
        TeamMembership,
        TeamStaffAssignment,
    )

    who, first, second, _, _, target, _ = staff_fixture(db, "coach", "coach")
    db.add_all(
        [
            UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id),
            UserRole(user_id=who.account.id, role_id=ROLES["assistant_coach"].id),
            TeamManagerAssignment(
                account_id=who.account.id, team_id=first.id, created_by_user_id=who.account.id
            ),
            TeamStaffAssignment(
                account_id=who.account.id,
                team_id=first.id,
                staff_role="manager",
                created_by_user_id=who.account.id,
            ),
            TeamStaffAssignment(
                account_id=who.account.id,
                team_id=second.id,
                staff_role="assistant_coach",
                created_by_user_id=who.account.id,
            ),
        ]
    )
    additional = person(db)
    db.add(
        TeamMembership(
            person_id=additional.id,
            team_id=first.id,
            joined_on=datetime.now(UTC).date(),
            created_by_user_id=who.account.id,
        )
    )
    from app.identity.models import StudentProfile

    db.add(StudentProfile(person_id=target.id, student_number=uuid4().hex, status="active"))
    responsibility(db, who, target)
    db.flush()
    first_page = page(db, who, page_size=1)
    collected = list(first_page.items)
    cursor = first_page.next_cursor
    while cursor:
        following = page(db, who, page_size=1, cursor=cursor)
        assert following.total == 3
        collected.extend(following.items)
        cursor = following.next_cursor
    assert first_page.total == 3 and len({p.person_id for p in collected}) == 3
    target_view = next(p for p in collected if p.person_id == target.id)
    assert [(a.kind, a.id) for a in target_view.authorized_affiliations].count(
        ("team", first.id)
    ) == 1
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS[PERMISSION].id,
            effect="deny",
            scope_kind="assigned_team",
            scope_id=first.id,
        )
    )
    db.flush()
    assert page(db, who).total == 1
