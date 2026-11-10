"""Real synthetic personnel positions, without premature account grants."""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, insert, select

from app.auth.models import UserAccount
from app.identity.models import Person
from app.personnel_intake.role_grants import latest_plan
from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import LookupDigest
from app.team_management.allocation import sync_team_allocation_in_transaction
from app.team_management.models import TeamManagerAssignment, TeamStaffAssignment
from app.team_management.person_staff_models import (
    PersonTeamStaffAuthorization,
    PersonTeamStaffPosition,
)
from app.team_management.person_staff_query import PersonStaffQuery
from app.team_management.person_staff_schemas import (
    PersonStaffAssignInput,
    PersonStaffEndInput,
    PersonStaffReconfirmInput,
    StaffAuthorizationEvidence,
    staff_authorization_digest,
)
from app.team_management.person_staff_service import PersonStaffService
from tests.api.conftest import make_cn_id
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_team_access import teams

LOOKUP = LookupDigest(bytes(range(32)))


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def managed_person(db, users, *, keys=None, sequence=321, authorizer="admin"):
    return approvals(db).direct_create(
        actor(users, authorizer),
        intake(
            users,
            identity_number=make_cn_id("20000102", sequence),
            email=f"synthetic-{sequence}@example.com",
        ),
        uuid4(),
        activation_role_keys=["coach", "assistant_coach"] if keys is None else keys,
    )


def raw_position(db, users, result, team_id, *, role="coach", authorizer="admin"):
    plan = latest_plan(db, result.person_id)
    row = PersonTeamStaffPosition(
        id=uuid4(),
        person_id=result.person_id,
        team_id=team_id,
        staff_role=role,
        origin_approval_id=result.approval_id,
        created_by_user_id=users["accounts"][authorizer].id,
        started_at=datetime.now(UTC),
        version=1,
    )
    db.add(row)
    db.flush()
    evidence = StaffAuthorizationEvidence(
        position_id=row.id,
        person_id=row.person_id,
        team_id=row.team_id,
        origin_approval_id=row.origin_approval_id,
        staff_role=role,
        version=1,
        authorizer_user_id=users["accounts"][authorizer].id,
        authorized_role_key="team_manager" if role == "manager" else role,
        role_plan_id=plan.id if plan else None,
        operation_id=result.approval_id,
    )
    db.add(
        PersonTeamStaffAuthorization(
            **evidence.model_dump(
                exclude={"person_id", "team_id", "origin_approval_id", "staff_role"}
            ),
            evidence_digest=staff_authorization_digest(LOOKUP, evidence),
            created_at=row.started_at,
        )
    )
    db.flush()
    return row


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_person_position_is_real_allocation_without_any_account_grant(db, platform_users):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    raw_position(db, platform_users, result, first.id)
    before_roles = count(db, UserRole)
    sync_team_allocation_in_transaction(
        db,
        person_id=result.person_id,
        actor_user_id=platform_users["accounts"]["admin"].id,
        occurred_at=datetime.now(UTC),
    )
    assert db.get(Person, result.person_id).team_allocation_status == "assigned"
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    assert count(db, UserRole) == before_roles
    assert count(db, TeamStaffAssignment) == count(db, TeamManagerAssignment) == 0


def services(db):
    return PersonStaffService(db, lookup=LOOKUP), PersonStaffQuery(db, lookup=LOOKUP)


def assign(db, users, person_id, team_id, role="coach", *, key=None):
    return services(db)[0].assign_person(
        actor(users, "admin"),
        team_id,
        PersonStaffAssignInput(
            person_id=person_id,
            staff_role=role,
            idempotency_key=key or uuid4(),
        ),
    )


def listed(db, users, team_id, *, page=1, page_size=20, status="current"):
    return services(db)[1].list(
        actor(users, "admin"), team_id, status=status, page=page, page_size=page_size
    )


def realize_fixture(db, users, position):
    account = UserAccount(
        id=uuid4(),
        person_id=position.person_id,
        password_hash="synthetic-not-used-for-login",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    db.add(UserRole(user_id=account.id, role_id=ROLES["coach"].id))
    assignment = TeamStaffAssignment(
        account_id=account.id,
        team_id=position.team_id,
        staff_role=position.staff_role,
        created_by_user_id=users["accounts"]["admin"].id,
        created_at=position.started_at,
    )
    db.add(assignment)
    db.flush()
    position.account_assignment_id = assignment.id
    db.flush()
    return account, assignment


def test_multi_team_and_concurrent_positions_are_paged_before_count_without_grants(
    db, platform_users
):
    result = managed_person(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    before = count(db, UserRole)
    values = [
        assign(db, platform_users, result.person_id, team.id, role)
        for team, role in (
            (first, "coach"),
            (first, "assistant_coach"),
            (second, "coach"),
        )
    ]
    pages = [listed(db, platform_users, first.id, page=n, page_size=1) for n in (1, 2)]
    assert [p.total for p in pages] == [2, 2]
    assert {p.items[0].staff_role for p in pages} == {"coach", "assistant_coach"}
    assert len({p.items[0].row_key for p in pages}) == 2
    assert listed(db, platform_users, second.id).total == 1
    assert count(db, PersonTeamStaffPosition) == count(db, PersonTeamStaffAuthorization) == 3
    assert count(db, UserRole) == before
    assert count(db, TeamStaffAssignment) == count(db, TeamManagerAssignment) == 0
    assert (
        db.scalar(select(UserAccount.id).where(UserAccount.person_id == result.person_id)) is None
    )
    for value in values:
        position = db.get(PersonTeamStaffPosition, value.assignment_id)
        assert position.version == 1


@pytest.mark.parametrize(
    "invalid",
    [
        "no_plan",
        "wrong_role",
        "tampered_plan",
        "grantor_deny",
        "inactive_team",
        "inactive_division",
        "activated",
    ],
)
def test_assign_rejects_invalid_sources_without_operation_or_position(db, platform_users, invalid):
    from app.teaching.models import GameProject
    from app.team_management.errors import TeamServiceError
    from app.team_management.models import TeamOperation

    result = managed_person(
        db,
        platform_users,
        keys=[] if invalid == "no_plan" else ["guardian"] if invalid == "wrong_role" else ["coach"],
    )
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    if invalid == "tampered_plan":
        # Plans are append-only: insert a new syntactically valid but unauthentic plan.
        old = latest_plan(db, result.person_id)
        db.add(
            type(old)(
                person_id=old.person_id,
                origin_approval_id=old.origin_approval_id,
                authorizer_user_id=old.authorizer_user_id,
                role_keys=["coach"],
                version=2,
                operation_key=uuid4(),
                operation_digest="1" * 64,
                plan_digest="0" * 64,
            )
        )
    elif invalid == "grantor_deny":
        db.add(
            UserPermissionOverride(
                user_id=platform_users["accounts"]["admin"].id,
                permission_id=PERMISSIONS["permissions.manage"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    elif invalid == "inactive_team":
        first.is_active = False
    elif invalid == "inactive_division":
        db.get(GameProject, first.project_id).is_active = False
    elif invalid == "activated":
        db.add(
            UserAccount(
                person_id=result.person_id, password_hash="synthetic", force_password_change=False
            )
        )
    db.commit()
    before = count(db, TeamOperation)
    with pytest.raises(TeamServiceError) as error:
        assign(db, platform_users, result.person_id, first.id)
    assert error.value.status in {403, 409}
    assert count(db, PersonTeamStaffPosition) == count(db, PersonTeamStaffAuthorization) == 0
    assert count(db, TeamOperation) == before


@pytest.mark.parametrize("invalid", ["not_managed", "non_root", "permission_deny", "out_of_scope"])
def test_assign_access_rejection_is_zero_write(db, platform_users, invalid):
    from app.team_management.errors import TeamServiceError
    from app.team_management.models import TeamOperation

    result = managed_person(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    who = actor(platform_users, "admin")
    pid = result.person_id
    if invalid == "not_managed":
        pid = platform_users["people"]["adult_without_account"].id
    elif invalid == "non_root":
        who = actor(platform_users, "coach")
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
                effect="allow",
                scope_kind="global",
            )
        )
    else:
        if invalid == "out_of_scope":
            # Remove root's broad allow; install only a different team-scoped allow.
            db.execute(
                RolePermission.__table__.delete().where(
                    RolePermission.role_id == ROLES["super_admin"].id,
                    RolePermission.permission_id
                    == PERMISSIONS["team_manager_assignments.manage"].id,
                )
            )
            db.add(
                UserPermissionOverride(
                    user_id=who.account.id,
                    permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
                    effect="allow",
                    scope_kind="assigned_team",
                    scope_id=second.id,
                )
            )
        else:
            db.add(
                UserPermissionOverride(
                    user_id=who.account.id,
                    permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
                    effect="deny",
                    scope_kind="assigned_team",
                    scope_id=first.id,
                )
            )
    db.commit()
    before = count(db, TeamOperation)
    with pytest.raises(TeamServiceError):
        services(db)[0].assign_person(
            who,
            first.id,
            PersonStaffAssignInput(person_id=pid, staff_role="coach", idempotency_key=uuid4()),
        )
    assert count(db, PersonTeamStaffPosition) == count(db, PersonTeamStaffAuthorization) == 0
    assert count(db, TeamOperation) == before


def test_replay_versioned_reconfirm_and_single_end_preserve_old_evidence(db, platform_users):
    from app.audit.models import AuditEvent
    from app.team_management.errors import TeamAssignmentStale, TeamOperationConflict

    result = managed_person(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    key = uuid4()
    one = assign(db, platform_users, result.person_id, first.id, key=key)
    assert assign(db, platform_users, result.person_id, first.id, key=key) == one
    with pytest.raises(TeamOperationConflict):
        assign(db, platform_users, result.person_id, second.id, key=key)
    other = assign(db, platform_users, result.person_id, second.id)
    original = db.scalar(
        select(PersonTeamStaffAuthorization).where(
            PersonTeamStaffAuthorization.position_id == one.assignment_id
        )
    )
    evidence = (
        original.id,
        original.role_plan_id,
        original.evidence_digest,
        original.authorizer_user_id,
    )
    service = services(db)[0]
    command = PersonStaffReconfirmInput(
        expected_version=1, reason="核对虚构岗位", idempotency_key=uuid4()
    )
    service.reconfirm(actor(platform_users, "admin"), one.assignment_id, command)
    before = count(db, AuditEvent)
    assert (
        service.reconfirm(actor(platform_users, "admin"), one.assignment_id, command).assignment_id
        == one.assignment_id
    )
    assert count(db, AuditEvent) == before
    assert db.get(PersonTeamStaffPosition, one.assignment_id).version == 2
    assert (
        original.id,
        original.role_plan_id,
        original.evidence_digest,
        original.authorizer_user_id,
    ) == evidence
    assert count(db, PersonTeamStaffAuthorization) == 3
    with pytest.raises(TeamAssignmentStale):
        service.end(
            actor(platform_users, "admin"),
            one.assignment_id,
            PersonStaffEndInput(expected_version=1, reason="过期版本", idempotency_key=uuid4()),
        )
    end = PersonStaffEndInput(
        expected_version=2, reason="结束单个虚构岗位", idempotency_key=uuid4()
    )
    service.end(actor(platform_users, "admin"), one.assignment_id, end)
    assert (
        service.end(actor(platform_users, "admin"), one.assignment_id, end).assignment_id
        == one.assignment_id
    )
    assert db.get(PersonTeamStaffPosition, other.assignment_id).ended_at is None
    assert db.get(Person, result.person_id).team_allocation_status == "assigned"
    service.end(
        actor(platform_users, "admin"),
        other.assignment_id,
        PersonStaffEndInput(expected_version=1, reason="结束最后岗位", idempotency_key=uuid4()),
    )
    assert db.get(Person, result.person_id).team_allocation_status == "pending"
    assert listed(db, platform_users, first.id, status="ended").total == 1


@pytest.mark.parametrize("old_entry", [False, True])
def test_linked_old_and_new_end_entries_close_both_sources(db, platform_users, old_entry):
    from app.team_management.schemas import UnassignStaffInput
    from app.team_management.staff_service import TeamStaffService

    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    position = raw_position(db, platform_users, result, first.id)
    _account, assignment = realize_fixture(db, platform_users, position)
    sync_team_allocation_in_transaction(
        db,
        person_id=result.person_id,
        actor_user_id=platform_users["accounts"]["admin"].id,
        occurred_at=datetime.now(UTC),
    )
    before = listed(db, platform_users, first.id)
    assert before.total == 1 and before.items[0].row_key == f"person:{position.id}"
    if old_entry:
        TeamStaffService(db).unassign(
            actor(platform_users, "admin"),
            assignment.id,
            UnassignStaffInput(idempotency_key=uuid4()),
        )
    else:
        services(db)[0].end(
            actor(platform_users, "admin"),
            position.id,
            PersonStaffEndInput(expected_version=1, reason="统一结束", idempotency_key=uuid4()),
        )
    db.refresh(position)
    db.refresh(assignment)
    assert position.ended_at is not None and assignment.ended_at is not None
    assert db.get(Person, result.person_id).team_allocation_status == "pending"
    assert listed(db, platform_users, first.id).total == 0
    history = listed(db, platform_users, first.id, status="ended")
    assert history.total == 1 and history.items[0].row_key == f"person:{position.id}"


def test_history_keeps_separate_reappointments_and_current_source_stays_stable(db, platform_users):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    one = assign(db, platform_users, result.person_id, first.id)
    services(db)[0].end(
        actor(platform_users, "admin"),
        one.assignment_id,
        PersonStaffEndInput(expected_version=1, reason="第一段结束", idempotency_key=uuid4()),
    )
    two = assign(db, platform_users, result.person_id, first.id)
    position = db.get(PersonTeamStaffPosition, two.assignment_id)
    before_key = listed(db, platform_users, first.id).items[0].row_key
    realize_fixture(db, platform_users, position)
    assert listed(db, platform_users, first.id).total == 1
    assert listed(db, platform_users, first.id).items[0].row_key == before_key
    services(db)[0].end(
        actor(platform_users, "admin"),
        two.assignment_id,
        PersonStaffEndInput(expected_version=1, reason="第二段结束", idempotency_key=uuid4()),
    )
    history = listed(db, platform_users, first.id, status="ended", page_size=1)
    assert history.total == 2
    assert (
        listed(db, platform_users, first.id, status="ended", page=2, page_size=1).items[0].row_key
        != history.items[0].row_key
    )


def test_roster_batches_status_across_distinct_people_and_grantors(db, platform_users):
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    result = managed_person(db, platform_users)
    raw_position(db, platform_users, result, first.id)

    def measured():
        # Compare identical cold identity-map conditions, not a warm actor vs
        # fixture commits which expired that actor's ORM state.
        db.expire_all()
        statements = []

        def observe(conn, cursor, sql, parameters, context, executemany):
            statements.append(sql)

        event.listen(db.get_bind(), "before_cursor_execute", observe)
        try:
            page = listed(db, platform_users, first.id)
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
        return page, len(statements)

    one, one_count = measured()
    for sequence, authorizer in zip(
        (323, 325, 327, 329, 331),
        ("coach", "customer_service", "guardian", "self_student", "minor_student"),
        strict=True,
    ):
        db.add(
            UserRole(
                user_id=platform_users["accounts"][authorizer].id, role_id=ROLES["super_admin"].id
            )
        )
        db.flush()
        result = managed_person(db, platform_users, sequence=sequence, authorizer=authorizer)
        raw_position(db, platform_users, result, first.id, authorizer=authorizer)
    many, many_count = measured()
    assert one.total == 1 and many.total == 6
    assert many_count == one_count
    assert many_count <= 30
    print(f"ROSTER_SQL one={one_count} six_people_six_grantors={many_count}")


def test_original_proof_role_source_survives_unrelated_addition_but_not_revocation(
    db, platform_users
):
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate

    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    one = assign(db, platform_users, result.person_id, first.id)
    old = db.scalar(
        select(PersonTeamStaffAuthorization).where(
            PersonTeamStaffAuthorization.position_id == one.assignment_id
        )
    )
    old_plan_id = old.role_plan_id
    ActivationRoleService(db, lookup=LOOKUP).update(
        actor(platform_users, "admin"),
        result.person_id,
        ActivationRoleUpdate(
            role_keys=["coach", "assistant_coach", "guardian"],
            expected_version=1,
            idempotency_key=uuid4(),
        ),
    )
    assert listed(db, platform_users, first.id).items[0].authorization_status == "valid"
    assert old.role_plan_id == old_plan_id
    db.add(
        UserRole(user_id=platform_users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id)
    )
    db.flush()
    ActivationRoleService(db, lookup=LOOKUP).update(
        actor(platform_users, "coach"),
        result.person_id,
        ActivationRoleUpdate(
            role_keys=["coach", "assistant_coach", "guardian"],
            expected_version=2,
            idempotency_key=uuid4(),
        ),
    )
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    row = listed(db, platform_users, first.id).items[0]
    assert (
        row.authorization_status == "reconfirmation_required" and "reconfirm" in row.allowed_actions
    )
    services(db)[0].reconfirm(
        actor(platform_users, "admin"),
        one.assignment_id,
        PersonStaffReconfirmInput(
            expected_version=1, reason="新角色授权后明确确认岗位", idempotency_key=uuid4()
        ),
    )
    assert listed(db, platform_users, first.id).items[0].authorization_status == "valid"
    proofs = list(
        db.scalars(
            select(PersonTeamStaffAuthorization)
            .where(PersonTeamStaffAuthorization.position_id == one.assignment_id)
            .order_by(PersonTeamStaffAuthorization.version)
        )
    )
    assert proofs[0].role_plan_id == old_plan_id
    assert proofs[1].role_plan_id == latest_plan(db, result.person_id).id


def test_realized_authority_does_not_depend_on_old_grantor_and_can_reconfirm(db, platform_users):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    position = raw_position(db, platform_users, result, first.id)
    account, _ = realize_fixture(db, platform_users, position)
    db.add(
        UserRole(user_id=platform_users["accounts"]["coach"].id, role_id=ROLES["super_admin"].id)
    )
    db.flush()
    # Another root views/confirms; former grantor is no longer a root.
    db.execute(
        UserRole.__table__.delete().where(
            UserRole.user_id == platform_users["accounts"]["admin"].id,
            UserRole.role_id == ROLES["super_admin"].id,
        )
    )
    db.flush()
    query = services(db)[1]
    page = query.list(
        actor(platform_users, "coach"), first.id, status="current", page=1, page_size=20
    )
    assert (
        page.items[0].authorization_status == "valid" and page.items[0].account_status == "active"
    )
    services(db)[0].reconfirm(
        actor(platform_users, "coach"),
        position.id,
        PersonStaffReconfirmInput(
            expected_version=1, reason="已激活岗位明确确认", idempotency_key=uuid4()
        ),
    )
    assert position.version == 2
    account.status = "disabled"
    db.flush()
    page = query.list(
        actor(platform_users, "coach"), first.id, status="current", page=1, page_size=20
    )
    assert page.items[0].account_status == "disabled"
    assert page.items[0].authorization_status == "reconfirmation_required"
    assert page.items[0].allowed_actions == ["end"]


@pytest.mark.parametrize(
    "case,want",
    [
        ("root", True),
        ("nonroot", True),
        ("target_deny", False),
        ("family_manager_deny", False),
        ("only_scoped_manager", False),
        ("disabled", False),
        ("password", False),
        ("deleted", False),
        ("finance", False),
        ("team_bundle", False),
        ("global_scoped_deny", False),
        ("finite_ceiling", False),
        ("future_deny", False),
        ("person_bundle", True),
        ("person_bundle_deny", False),
        ("family_ceiling_expires", False),
        ("future_family_deny", False),
    ],
)
def test_batched_prospective_role_decisions_match_canonical_validator(
    db, platform_users, case, want
):
    """Break caught: missing deny, time boundary, actual-root or resource mapping."""
    from app.core.time import business_today
    from app.identity.models import GuardianRelationship
    from app.rbac.prospective_batch import ProspectiveRoleBatch, ProspectiveRoleCheck
    from app.rbac.service import PermissionDelegationDenied, RbacService

    who = actor(platform_users, "admin" if case == "root" else "customer_service")
    target = platform_users["people"]["adult_without_account"].id
    now = datetime.now(UTC)
    role = db.get(__import__("app.rbac.models", fromlist=["Role"]).Role, ROLES["property"].id)
    # A mutable, non-staff bundle demonstrates that the batch is not a whitelist
    # or a shortcut inferred from today's three staff-role seed bundles.
    db.execute(RolePermission.__table__.delete().where(RolePermission.role_id == role.id))
    permission = "team_memberships.read" if case == "team_bundle" else "people.read"
    scope = (
        "self"
        if case
        in {"person_bundle", "person_bundle_deny", "family_ceiling_expires", "future_family_deny"}
        else "global"
    )
    db.add(
        RolePermission(role_id=role.id, permission_id=PERMISSIONS[permission].id, scope_kind=scope)
    )

    def override(permission, *, effect="allow", scope="global", sid=None, start=None, end=None):
        db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS[permission].id,
                effect=effect,
                scope_kind=scope,
                scope_id=None if scope == "family" else sid,
                valid_from=start or (now - timedelta(days=1) if end is not None else None),
                valid_until=end,
            )
        )

    if case != "root":
        override(
            "permissions.manage",
            scope="person" if case == "only_scoped_manager" else "global",
            sid=target if case == "only_scoped_manager" else None,
        )
    if case == "target_deny":
        override("permissions.manage", effect="deny", scope="person", sid=target)
    if case == "family_manager_deny":
        db.add(
            GuardianRelationship(
                guardian_person_id=who.person.id,
                student_person_id=target,
                relationship="synthetic",
                valid_from=business_today(),
            )
        )
        override("permissions.manage", effect="deny", scope="family", sid=target)
    if case == "disabled":
        who.account.status = "disabled"
    elif case == "password":
        who.account.force_password_change = True
    elif case == "deleted":
        db.get(Person, who.person.id).deleted_at = now
    elif case == "global_scoped_deny":
        override("people.read", effect="deny", scope="person", sid=uuid4())
    elif case in {"person_bundle_deny", "future_deny"}:
        override(
            "people.read",
            effect="deny",
            scope="person",
            sid=target,
            start=now + timedelta(days=2) if case == "future_deny" else None,
        )
    elif case == "finite_ceiling":
        # Remove the current role allow and supply only an expiring override.
        db.execute(
            RolePermission.__table__.delete().where(
                RolePermission.role_id == ROLES["customer_service"].id,
                RolePermission.permission_id == PERMISSIONS["people.read"].id,
            )
        )
        override("people.read", end=now + timedelta(days=2))
    elif case in {"family_ceiling_expires", "future_family_deny"}:
        relation = GuardianRelationship(
            guardian_person_id=who.person.id,
            student_person_id=target,
            relationship="synthetic",
            valid_from=business_today(),
            valid_until=business_today() + timedelta(days=1),
        )
        db.add(relation)
        if case == "family_ceiling_expires":
            db.execute(
                RolePermission.__table__.delete().where(
                    RolePermission.role_id == ROLES["customer_service"].id,
                    RolePermission.permission_id == PERMISSIONS["people.read"].id,
                )
            )
            override("people.read", scope="family", sid=target)
        else:
            relation.valid_from = business_today() + timedelta(days=1)
            override("people.read", effect="deny", scope="family", sid=target)
    if case == "finance":
        role = db.get(type(role), ROLES["finance"].id)
    db.flush()
    check = ProspectiveRoleCheck(who.account.id, target, role.key)
    batched = ProspectiveRoleBatch(db, [check]).allowed(check)
    try:
        RbacService(db).require_prospective_role_grant(
            actor_user_id=who.account.id, person_id=target, role=role
        )
        canonical = True
    except PermissionDelegationDenied:
        canonical = False
    assert canonical is want
    assert batched is canonical


def test_candidates_and_options_do_not_leak_unmanaged_or_unauthorized_people(db, platform_users):
    result = managed_person(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    query = services(db)[1]
    options = query.staff_team_options(actor(platform_users, "coach"))
    assert options.can_assign is False and options.items == [] and options.reason
    options = query.staff_team_options(actor(platform_users, "admin"))
    assert {first.id, second.id} <= {t.id for t in options.items}
    first_page = query.candidates(
        actor(platform_users, "admin"), first.id, staff_role="coach", query="隔离", cursor=None
    )
    assert [(p.person_id, p.account_id) for p in first_page.items] == [(result.person_id, None)]
    assign(db, platform_users, result.person_id, first.id)
    assert (
        query.candidates(
            actor(platform_users, "admin"), first.id, staff_role="coach", query="隔离", cursor=None
        ).items
        == []
    )
    assert (
        query.candidates(
            actor(platform_users, "admin"), second.id, staff_role="coach", query="glj", cursor=None
        )
        .items[0]
        .person_id
        == result.person_id
    )


def test_locked_create_is_atomic_on_late_team_denial_and_never_uses_public_assign(
    db, platform_users
):
    from app.core.transactions import atomic_write_transaction
    from app.team_management import write_support as support
    from app.team_management.errors import TeamServiceError
    from app.team_management.person_staff_schemas import StaffTeamSelection
    from app.team_management.person_staff_service import PreparedPersonStaffLocks

    result = managed_person(db, platform_users)
    first, second = teams(db, platform_users["accounts"]["admin"].id)
    second.is_active = False
    db.commit()
    who = actor(platform_users, "admin")
    with pytest.raises(TeamServiceError):  # noqa: SIM117 - keep rollback scope explicit
        with atomic_write_transaction(db):
            prepared = PreparedPersonStaffLocks.discover(db, result.person_id)
            support.lock_participants(
                db,
                sorted(prepared.person_ids | {who.person.id}),
                sorted(prepared.account_ids | {who.account.id}),
            )
            prepared.lock_before_directory(db)
            support.lock_directory(db, [first.id, second.id])
            services(db)[0].create_in_transaction(
                who,
                person_id=result.person_id,
                approval_id=result.approval_id,
                selections=[StaffTeamSelection(staff_role="coach", team_ids=[first.id, second.id])],
                operation_id=result.approval_id,
                occurred_at=datetime.now(UTC),
            )
    assert count(db, PersonTeamStaffPosition) == count(db, PersonTeamStaffAuthorization) == 0


def test_invalid_unrealized_source_can_be_ended_without_restoring_authority(db, platform_users):
    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    one = assign(db, platform_users, result.person_id, first.id)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["permissions.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    row = listed(db, platform_users, first.id).items[0]
    assert row.authorization_status == "reconfirmation_required" and row.allowed_actions == ["end"]
    services(db)[0].end(
        actor(platform_users, "admin"),
        one.assignment_id,
        PersonStaffEndInput(
            expected_version=1, reason="结束失效的虚构授权", idempotency_key=uuid4()
        ),
    )
    assert db.get(PersonTeamStaffPosition, one.assignment_id).ended_at is not None
    assert db.get(Person, result.person_id).team_allocation_status == "pending"


def test_active_reconfirmation_repairs_only_proof_not_missing_account_authority(db, platform_users):
    from app.team_management.errors import TeamServiceError

    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    position = raw_position(db, platform_users, result, first.id)
    account, assignment = realize_fixture(db, platform_users, position)
    original = db.scalar(
        select(PersonTeamStaffAuthorization).where(
            PersonTeamStaffAuthorization.position_id == position.id
        )
    )
    position.version = 2
    db.add(
        PersonTeamStaffAuthorization(
            position_id=position.id,
            version=2,
            authorizer_user_id=original.authorizer_user_id,
            authorized_role_key="coach",
            role_plan_id=original.role_plan_id,
            operation_id=uuid4(),
            evidence_digest="0" * 64,
        )
    )
    db.flush()
    row = listed(db, platform_users, first.id).items[0]
    assert row.account_status == "active" and row.authorization_status == "reconfirmation_required"
    assert row.allowed_actions == ["end", "reconfirm"]
    services(db)[0].reconfirm(
        actor(platform_users, "admin"),
        position.id,
        PersonStaffReconfirmInput(
            expected_version=2, reason="确认已激活账号的既有岗位", idempotency_key=uuid4()
        ),
    )
    assert listed(db, platform_users, first.id).items[0].authorization_status == "valid"
    assert assignment.ended_at is None and count(db, PersonTeamStaffAuthorization) == 3
    db.execute(
        UserRole.__table__.delete().where(
            UserRole.user_id == account.id, UserRole.role_id == ROLES["coach"].id
        )
    )
    db.commit()
    with pytest.raises(TeamServiceError):
        services(db)[0].reconfirm(
            actor(platform_users, "admin"),
            position.id,
            PersonStaffReconfirmInput(
                expected_version=3, reason="不得恢复被撤销的账号角色", idempotency_key=uuid4()
            ),
        )
    assert count(db, PersonTeamStaffAuthorization) == 3


def test_candidate_cursor_is_bound_to_role_team_and_query_without_duplicates(db, platform_users):
    from app.core.errors import ProblemError

    first, second = teams(db, platform_users["accounts"]["admin"].id)
    for sequence in (321, 323, 325):
        managed_person(db, platform_users, sequence=sequence)
    query = services(db)[1]
    who = actor(platform_users, "admin")
    seen, cursor = [], None
    for _ in range(3):
        result = query.candidates(
            who, first.id, staff_role="coach", query="隔离", cursor=cursor, page_size=1
        )
        seen.extend(p.person_id for p in result.items)
        cursor = result.next_cursor
        if cursor is not None:
            for team, role, text in (
                (second.id, "coach", "隔离"),
                (first.id, "assistant_coach", "隔离"),
                (first.id, "coach", "别的"),
            ):
                with pytest.raises(ProblemError) as error:
                    query.candidates(
                        who, team, staff_role=role, query=text, cursor=cursor, page_size=1
                    )
                assert error.value.status == 422
    assert len(seen) == len(set(seen)) == 3
    assert cursor is None


def test_prepared_staff_lock_snapshot_detects_role_plan_drift(db, platform_users):
    from app.personnel_intake.role_grants import ActivationRoleService
    from app.personnel_intake.schemas import ActivationRoleUpdate
    from app.team_management.errors import TeamAssignmentStale
    from app.team_management.person_staff_service import PreparedPersonStaffLocks

    result = managed_person(db, platform_users)
    prepared = PreparedPersonStaffLocks.discover(db, result.person_id)
    ActivationRoleService(db, lookup=LOOKUP).update(
        actor(platform_users, "admin"),
        result.person_id,
        ActivationRoleUpdate(role_keys=["coach"], expected_version=1, idempotency_key=uuid4()),
    )
    with pytest.raises(TeamAssignmentStale):
        prepared.lock_before_directory(db)
    assert count(db, PersonTeamStaffPosition) == 0


def unlinked_account_fixture(db, person_id, state):
    """Real account, deliberately not a realization or an authority repair."""
    account = UserAccount(
        person_id=person_id,
        password_hash="synthetic-not-used-for-login",
        status="disabled" if state == "disabled" else "active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    if state != "active_roleless":
        db.add(UserRole(user_id=account.id, role_id=ROLES["coach"].id))
    db.commit()
    return account


def staff_write_snapshot(db, position_id):
    from app.audit.models import AuditEvent
    from app.team_management.models import TeamOperation

    position = db.get(PersonTeamStaffPosition, position_id)
    return (
        position.version,
        position.ended_at,
        position.account_assignment_id,
        count(db, PersonTeamStaffAuthorization),
        count(db, TeamOperation),
        count(db, AuditEvent),
        count(db, UserRole),
        count(db, TeamStaffAssignment),
    )


@pytest.mark.parametrize("state", ["active_roleless", "disabled", "active_with_role"])
def test_reconfirm_rejects_account_present_without_link_without_any_write(
    db, platform_users, state
):
    from app.team_management.person_staff_service import PersonStaffSourceInvalid

    result = managed_person(db, platform_users)
    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    position = raw_position(db, platform_users, result, first.id)
    unlinked_account_fixture(db, result.person_id, state)
    assert listed(db, platform_users, first.id).items[0].allowed_actions == ["end"]
    before = staff_write_snapshot(db, position.id)
    with pytest.raises(PersonStaffSourceInvalid):
        services(db)[0].reconfirm(
            actor(platform_users, "admin"),
            position.id,
            PersonStaffReconfirmInput(
                expected_version=1, reason="不可确认缺失兑现关联的账号岗位", idempotency_key=uuid4()
            ),
        )
    db.expire_all()
    assert staff_write_snapshot(db, position.id) == before


@pytest.fixture
def candidate_scan_population(db, platform_users):
    """450 real constrained query fixtures; none is an activation credential.

    The first 200 match the name but have a non-delegating plan grantor.
    Nonmatching names and sparse valid matches force post-SQL scanning across
    chunk boundaries. UUIDs, facts and expected eligible indices are hand-set.
    """
    from app.personnel_intake.models import (
        AssistedApproval,
        AssistedPersonProfile,
        PersonnelIntakeRequest,
        PersonnelIntakeRevision,
    )
    from app.personnel_intake.role_grant_models import ActivationRolePlan
    from app.personnel_intake.role_grants import ActivationRoleService

    first, _ = teams(db, platform_users["accounts"]["admin"].id)
    root_id = platform_users["accounts"]["admin"].id
    unqualified_id = platform_users["accounts"]["coach"].id
    expected_indices = (210, 211, 212, 410, 449)
    people, requests, revisions, approvals_rows, profiles, plans = [], [], [], [], [], []
    roles = ActivationRoleService(db, lookup=LOOKUP)
    for index in range(450):
        person_id, request_id, approval_id = UUID(int=(1 << 120) + index), uuid4(), uuid4()
        identity_digest = LOOKUP.digest_domain("synthetic-candidate-test", str(index))
        people.append(
            {
                "id": person_id,
                "full_name": f"Alpha {index}"
                if index < 200 or index in expected_indices
                else f"Beta {index}",
                "identity_ciphertext": "synthetic-not-activation-facts",
                "identity_digest": identity_digest,
                "sex": "male",
                "date_of_birth": date(2000, 1, 2),
            }
        )
        requests.append(
            {
                "id": request_id,
                "person_id": person_id,
                "submitted_by_user_id": root_id,
                "version": 1,
                "state": "approved",
                "kind": "staff",
            }
        )
        revisions.append(
            {
                "id": uuid4(),
                "request_id": request_id,
                "version": 1,
                "payload_ciphertext": "synthetic-query-only",
                "payload_digest": "1" * 64,
                "identity_digest": identity_digest,
                "created_by_user_id": root_id,
            }
        )
        approvals_rows.append(
            {
                "id": approval_id,
                "request_id": request_id,
                "revision_version": 1,
                "person_id": person_id,
                "approved_by_user_id": root_id,
                "snapshot_ciphertext": "synthetic-query-only",
                "snapshot_digest": "1" * 64,
                "guardian_relationship_version": 1,
                "guardian_relationship_ids": [],
                "guardian_snapshot_ciphertext": "synthetic-query-only",
                "guardian_snapshot_digest": "1" * 64,
            }
        )
        profiles.append(
            {
                "person_id": person_id,
                "current_approval_id": approval_id,
                "version": 1,
                "activation_requirement": "reviewed_claim",
            }
        )
        plan = ActivationRolePlan(
            id=uuid4(),
            person_id=person_id,
            origin_approval_id=approval_id,
            authorizer_user_id=unqualified_id if index < 200 else root_id,
            role_keys=["coach"],
            version=1,
            operation_key=uuid4(),
            operation_digest="2" * 64,
        )
        plan.plan_digest = roles._digest(plan)
        plans.append(
            {
                column.name: getattr(plan, column.name)
                for column in ActivationRolePlan.__table__.columns
                if column.name != "created_at"
            }
        )
    for model, rows in (
        (Person, people),
        (PersonnelIntakeRequest, requests),
        (PersonnelIntakeRevision, revisions),
        (AssistedApproval, approvals_rows),
        (AssistedPersonProfile, profiles),
        (ActivationRolePlan, plans),
    ):
        db.execute(insert(model), rows)
    db.flush()
    return first, [UUID(int=(1 << 120) + index) for index in expected_indices]


def candidate_scan_probe(db, monkeypatch):
    """Observe actual fetches and real batch construction, not fake decisions."""
    from app.rbac.prospective_batch import ProspectiveRoleBatch
    from app.team_management import person_staff_query

    fetch_sizes, batch_sizes = [], []

    class BatchProbe(ProspectiveRoleBatch):
        def __init__(self, db, checks, **kwargs):
            checks = list(checks)
            batch_sizes.append(len(checks))
            super().__init__(db, checks, **kwargs)

    def observe(conn, cursor, sql, parameters, context, executemany):
        if "FROM people JOIN assisted_person_profiles" in sql:
            fetch_sizes.append(cursor.rowcount)

    monkeypatch.setattr(person_staff_query, "ProspectiveRoleBatch", BatchProbe)
    event.listen(db.get_bind(), "after_cursor_execute", observe)
    return fetch_sizes, batch_sizes, observe


def test_candidate_scan_bounds_fetch_and_authority_batches_and_stops_early(
    db, platform_users, candidate_scan_population, monkeypatch
):
    first, expected = candidate_scan_population
    fetched, batched, observe = candidate_scan_probe(db, monkeypatch)
    try:
        page = services(db)[1].candidates(
            actor(platform_users, "admin"),
            first.id,
            staff_role="coach",
            query="a",
            cursor=None,
            page_size=1,
        )
    finally:
        event.remove(db.get_bind(), "after_cursor_execute", observe)
    assert [item.person_id for item in page.items] == expected[:1]
    assert page.next_cursor is not None
    assert fetched == [200, 200], "Stop after two matches; never fetch the remaining suffix"
    assert batched == [200, 200], "Authority batches must be bounded to their fetched chunk"


def test_candidate_scan_preserves_complete_cursor_pages_across_rejected_chunks(
    db, platform_users, candidate_scan_population, monkeypatch
):
    first, expected = candidate_scan_population
    fetched, batched, observe = candidate_scan_probe(db, monkeypatch)
    seen, cursor, pages = [], None, 0
    try:
        while True:
            page = services(db)[1].candidates(
                actor(platform_users, "admin"),
                first.id,
                staff_role="coach",
                query="a",
                cursor=cursor,
                page_size=2,
            )
            seen.extend(item.person_id for item in page.items)
            pages += 1
            assert pages <= 3, "A keyset scan must advance even across rejected candidates"
            cursor = page.next_cursor
            if cursor is None:
                break
    finally:
        event.remove(db.get_bind(), "after_cursor_execute", observe)
    assert seen == expected and len(set(seen)) == 5
    assert pages == 3
    assert fetched and max(fetched) <= 200
    assert batched and max(batched) <= 200
    assert fetched == batched
