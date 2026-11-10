from __future__ import annotations

# Imported pytest fixtures are intentionally named again as test parameters.
# ruff: noqa: F811
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec

import pytest
from sqlalchemy import delete, event, select

from app.admissions.models import Enrollment
from app.auth.service import AuthContext
from app.housing.access import HousingAccessService
from app.housing.models import HousingRoom, HousingStay
from app.identity.models import Person
from app.personnel_intake.access import PersonnelIntakeAccess
from app.personnel_intake.queries import ManagedPeopleQuery
from app.personnel_intake.schemas import ManagedPeopleFilters
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.rbac.service import RbacService
from app.referrals.access import InvitedHousingAccess
from app.teaching.models import CoachAssignment, TeachingClass
from tests.invited_housing_helpers import account, binding, manager, organization, person, stay
from tests.services.test_housing_portal import (
    NOW as HOUSING_NOW,
)
from tests.services.test_housing_portal import housing_portal_scenario  # noqa: F401
from tests.services.test_managed_people_housing import coach, responsibility
from tests.services.test_portal_coach import portal_coach_scenario  # noqa: F401
from tests.services.test_portal_learning import portal_learning_scenario  # noqa: F401


def actor(db, user):
    user.force_password_change = False
    db.flush()
    return AuthContext(account=user, person=db.get(Person, user.person_id), session=None)


def departure(db, who, now):
    assert find_spec("app.portal.departure_access") is not None, "departure authorization missing"
    from app.portal.departure_access import DepartureAccess

    return DepartureAccess(db, who, now)


def override(db, who, permission, effect="deny", scope="global", identifier=None):
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS[permission].id,
            effect=effect,
            scope_kind=scope,
            scope_id=identifier,
        )
    )
    db.flush()


def housing_ids(db, access):
    return set(
        db.scalars(
            select(HousingStay.id)
            .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
            .where(access.housing_criteria())
        )
    )


def enrollment_ids(db, access):
    return set(
        db.scalars(
            select(Enrollment.id)
            .join(TeachingClass, TeachingClass.id == Enrollment.class_id)
            .join(Person, Person.id == Enrollment.student_person_id)
            .where(access.enrollment_criteria())
        )
    )


@pytest.mark.parametrize("scope", ["global", "building", "floor", "owner_only"])
def test_room_scope_matches_housing_access(db, scope):
    who = coach(db) if scope == "owner_only" else actor(db, account(db))
    now = datetime.now(UTC)
    rows = [stay(db, who.account, person(db, student=True)) for _ in range(3)]
    first_room = db.get(HousingRoom, rows[0].room_id)
    if scope != "owner_only":
        identifier = (
            first_room.building_id
            if scope == "building"
            else first_room.floor_id
            if scope == "floor"
            else None
        )
        override(db, who, "housing.read", "allow", scope, identifier)
    else:
        override(db, who, "housing.read", "allow")
    override(
        db,
        who,
        "housing.read",
        scope="building",
        identifier=db.get(HousingRoom, rows[1].room_id).building_id,
    )
    original = HousingAccessService(db, who.account.id, now)
    expected = {row.id for row in rows if original.can_read_room(db.get(HousingRoom, row.room_id))}
    access = departure(db, who, now)
    assert housing_ids(db, access) == expected
    assert access.has_scope() is bool(expected)


def test_managed_people_matches_existing_query(db):
    who = coach(db)
    visible, denied, outsider = [person(db, student=True) for _ in range(3)]
    for target in (visible, denied):
        responsibility(db, who, target)
    rows = {target.id: stay(db, who.account, target) for target in (visible, denied, outsider)}
    override(db, who, "personnel_intake.housing.read", scope="person", identifier=denied.id)
    original = ManagedPeopleQuery(db).list(who, ManagedPeopleFilters())
    state = PersonnelIntakeAccess(db).snapshot(who)
    query = ManagedPeopleQuery(db)
    statement = getattr(query, "authorized_person_ids_statement", None)
    assert callable(statement), "shared managed-people SQL scope is missing"
    expected = {item.person_id for item in original.items}
    assert set(db.scalars(statement(state, datetime.now(UTC)))) == expected == {visible.id}
    assert housing_ids(db, departure(db, who, datetime.now(UTC))) == {
        rows[pid].id for pid in expected
    }


def test_managed_team_deny_applies_across_coach_path_and_ends_immediately(db):
    from app.team_management.models import TeamManagerAssignment, TeamMembership
    from tests.services.test_team_access import teams

    who = coach(db)
    db.add(UserRole(user_id=who.account.id, role_id=ROLES["team_manager"].id))
    first, _ = teams(db, who.account.id)
    target = person(db, student=True)
    responsibility(db, who, target)
    db.add(
        TeamManagerAssignment(
            account_id=who.account.id, team_id=first.id, created_by_user_id=who.account.id
        )
    )
    membership = TeamMembership(
        person_id=target.id,
        team_id=first.id,
        joined_on=datetime.now(UTC).date(),
        created_by_user_id=who.account.id,
    )
    db.add(membership)
    row = stay(db, who.account, target)
    override(db, who, "personnel_intake.housing.read", scope="assigned_team", identifier=first.id)
    assert ManagedPeopleQuery(db).list(who, ManagedPeopleFilters()).total == 0
    assert housing_ids(db, departure(db, who, datetime.now(UTC))) == set()
    membership.ended_at = datetime.now(UTC)
    membership.ended_by_user_id = who.account.id
    membership.left_on = membership.joined_on
    membership.end_kind = "left"
    db.flush()
    assert ManagedPeopleQuery(db).list(who, ManagedPeopleFilters()).total == 1
    assert housing_ids(db, departure(db, who, datetime.now(UTC))) == {row.id}


def test_invited_scope_all_sources_and_revocation(db):
    now = datetime.now(UTC)
    who, creator = actor(db, account(db)), account(db, "super_admin")
    own, _, _ = binding(db, who.account)
    org = organization(db, creator)
    edge = manager(db, org, who.account)
    managed, _, _ = binding(db, creator, org)
    outsider, _, _ = binding(db, creator)
    rows = {target.id: stay(db, creator, target) for target in (own, managed, outsider)}
    original = InvitedHousingAccess(db)

    def expected():
        statement = original.authorized_people_statement(
            user_id=who.account.id,
            actor_person_id=who.person.id,
            source_kind="all",
            organization_id=None,
            as_of=now,
        )
        return {row.student_person_id for row in db.execute(statement)}

    assert expected() == {own.id, managed.id}
    assert housing_ids(db, departure(db, who, now)) == {rows[pid].id for pid in expected()}
    edge.revoked_at = now
    edge.revoked_by_user_id = creator.id
    override(db, who, "referrals.read_invited_housing", scope="person", identifier=own.id)
    assert expected() == set()
    assert housing_ids(db, departure(db, who, now)) == set()


@pytest.mark.parametrize(
    "change",
    ["none", "ended", "unverified", "expired", "future", "person_deny", "global_deny", "deleted"],
)
def test_self_family_keeps_current_guards(housing_portal_scenario, change):
    scenario = housing_portal_scenario
    db = scenario.db
    who = actor(db, scenario.accounts["guardian"])
    target = scenario.people["minor"]
    relation = scenario.relationships["guardian_to_minor"]
    if change == "ended":
        relation.ended_at = HOUSING_NOW
        relation.ended_by_user_id = who.account.id
        relation.end_reason = "test"
    elif change == "unverified":
        relation.requires_verification = True
    elif change == "expired":
        relation.valid_until = HOUSING_NOW.date() - timedelta(days=1)
    elif change == "future":
        relation.valid_from = HOUSING_NOW.date() + timedelta(days=1)
    elif change == "deleted":
        target.deleted_at = HOUSING_NOW
    elif change.endswith("deny"):
        override(
            db,
            who,
            "housing.read",
            scope="person" if change == "person_deny" else "global",
            identifier=target.id if change == "person_deny" else None,
        )
    row = stay(db, who.account, target)
    expected = {subject.person_id for subject in scenario.list_subjects("guardian")}
    assert (row.id in housing_ids(db, departure(db, who, HOUSING_NOW))) is (target.id in expected)


def test_self_housing_matches_portal_without_roommate_projection(
    housing_portal_scenario, monkeypatch
):
    scenario = housing_portal_scenario
    from app.housing.portal_service import HousingPortalService

    monkeypatch.setattr(
        HousingPortalService,
        "get_housing",
        lambda *args, **kwargs: pytest.fail("summary must not fetch roommates"),
    )
    who = actor(scenario.db, scenario.accounts["adult"])
    row = stay(scenario.db, who.account, scenario.people["adult"])
    assert row.id in housing_ids(scenario.db, departure(scenario.db, who, HOUSING_NOW))


def test_learning_scope_matches_portal_and_does_not_require_active_enrollment(
    portal_learning_scenario,
):
    s = portal_learning_scenario
    who = actor(s.db, s.guardian_account)
    original = s.service.list_students(
        user_id=who.account.id, actor_person_id=who.person.id, reference_date=s.today
    )
    ids = {item.student_person_id for item in original.items}
    expected = set(s.db.scalars(select(Enrollment.id).where(Enrollment.student_person_id.in_(ids))))
    assert enrollment_ids(s.db, departure(s.db, who, s.now)) == expected
    s.end_minor_relationship()
    remaining = s.service.list_students(
        user_id=who.account.id, actor_person_id=who.person.id, reference_date=s.today
    )
    expected = set(
        s.db.scalars(
            select(Enrollment.id).where(
                Enrollment.student_person_id.in_(
                    [item.student_person_id for item in remaining.items]
                )
            )
        )
    )
    assert enrollment_ids(s.db, departure(s.db, who, s.now)) == expected


@pytest.mark.parametrize("permission", ["people.read", "teaching.read", "enrollments.read"])
def test_learning_combination_denial_not_rescued_by_other_permissions(
    portal_learning_scenario, permission
):
    s = portal_learning_scenario
    who = actor(s.db, s.guardian_account)
    override(s.db, who, permission)
    assert enrollment_ids(s.db, departure(s.db, who, s.now)) == set()


def test_teaching_paths_do_not_grant_housing(portal_coach_scenario):
    s = portal_coach_scenario
    who = actor(s.db, s.coach_account)
    target_id = s.db.scalar(
        select(Enrollment.student_person_id).where(Enrollment.class_id == s.primary_class.id)
    )
    stay(s.db, who.account, s.db.get(Person, target_id))
    assert enrollment_ids(s.db, departure(s.db, who, s.now))
    assert housing_ids(s.db, departure(s.db, who, s.now)) == set()


def test_housing_paths_do_not_grant_teaching(portal_learning_scenario):
    s = portal_learning_scenario
    who = actor(s.db, account(s.db, "administration"))
    stay(s.db, who.account, s.minor_student)
    access = departure(s.db, who, s.now)
    assert housing_ids(s.db, access)
    assert enrollment_ids(s.db, access) == set()


def test_generic_current_class_matches_existing_visibility_without_coach_portal_combination(
    portal_coach_scenario,
):
    s = portal_coach_scenario
    who = actor(s.db, s.coach_account)
    for permission in ("teaching.read", "coach_roster.read"):
        override(s.db, who, permission)
    rbac = RbacService(s.db)
    visibility = rbac.teaching_visibility(
        user_id=who.account.id,
        actor_person_id=who.person.id,
        permission="enrollments.read",
        at=s.now,
    )
    expected = set(
        s.db.scalars(select(Enrollment.id).where(Enrollment.class_id.in_(visibility.class_ids)))
    )
    assert expected
    assert enrollment_ids(s.db, departure(s.db, who, s.now)) == expected
    s.db.execute(delete(CoachAssignment).where(CoachAssignment.coach_person_id == who.person.id))
    s.db.flush()
    assert enrollment_ids(s.db, departure(s.db, who, s.now)) == set()


def test_global_enrollment_scope_keeps_class_deny_and_detail_person_deny(portal_learning_scenario):
    s = portal_learning_scenario
    who = actor(s.db, account(s.db, "customer_service"))
    rows = list(s.db.scalars(select(Enrollment)))
    denied_class = rows[0].class_id
    override(s.db, who, "enrollments.read", scope="assigned_class", identifier=denied_class)
    override(s.db, who, "enrollments.read", scope="person", identifier=s.minor_student.id)
    rbac = RbacService(s.db)
    visibility = rbac.teaching_visibility(
        user_id=who.account.id,
        actor_person_id=who.person.id,
        permission="enrollments.read",
        at=s.now,
    )
    assert visibility.global_access
    expected = {row.id for row in rows if row.class_id not in visibility.excluded_class_ids}
    access = departure(s.db, who, s.now)
    assert enrollment_ids(s.db, access) == expected
    from app.portal.departure_access import DepartureEnrollmentRow

    paths = access.detail_paths(
        [], [DepartureEnrollmentRow(row.id, row.student_person_id, row.class_id) for row in rows]
    )
    for row in rows:
        tokens = rbac.enrollment_resource_tokens(
            actor_person_id=who.person.id,
            student_person_id=row.student_person_id,
            class_id=row.class_id,
            at=s.now,
            reference_date=s.today,
        )
        allowed = (
            rbac.decision_context(who.account.id, occurred_at=s.now)
            .decide("enrollments.read", tokens)
            .allowed
        )
        assert (("enrollment_end", row.id) in paths) is allowed


def test_summary_only_paths_do_not_grant_room_detail(db):
    who = coach(db)
    target = person(db, student=True)
    responsibility(db, who, target)
    row = stay(db, who.account, target)
    access = departure(db, who, datetime.now(UTC))
    assert housing_ids(db, access) == {row.id}
    from app.portal.departure_access import DepartureHousingRow

    facts = [DepartureHousingRow(row.id, row.person_id, row.room_id)]
    assert access.detail_paths(facts, []) == {}
    admin = actor(db, account(db, "administration"))
    assert departure(db, admin, datetime.now(UTC)).detail_paths(facts, []) == {
        ("planned_checkout", row.id): f"/housing/rooms/{row.room_id}"
    }


def test_scope_does_not_depend_on_reminder_dates_or_any_stays(db):
    who = coach(db)
    responsibility(db, who, person(db, student=True))
    assert departure(db, who, datetime.now(UTC)).has_scope()
    unknown = actor(db, account(db))
    assert not departure(db, unknown, datetime.now(UTC)).has_scope()


def test_one_and_one_hundred_candidates_have_same_authorization_query_count(db):
    who = coach(db)
    rows = []
    for _ in range(100):
        target = person(db, student=True)
        responsibility(db, who, target)
        rows.append(stay(db, who.account, target))
    from app.portal.departure_access import DepartureHousingRow

    now = datetime.now(UTC)
    counts = []
    for size in (1, 100):
        statements = []

        def capture(_connection, _cursor, statement, *_args, statements=statements):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", capture)
        try:
            access = departure(db, who, now)
            assert access.has_scope()
            db.scalars(
                select(HousingStay.id)
                .join(HousingRoom, HousingRoom.id == HousingStay.room_id)
                .where(
                    HousingStay.id.in_([row.id for row in rows[:size]]), access.housing_criteria()
                )
            ).all()
            access.detail_paths(
                [DepartureHousingRow(row.id, row.person_id, row.room_id) for row in rows[:size]], []
            )
        finally:
            event.remove(bind, "before_cursor_execute", capture)
        counts.append(len(statements))
    assert counts[1] <= counts[0] + 1
    assert counts[1] < 40
