from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES, seed_roles_and_permissions
from app.teaching.models import GameProject, ProfessionalTeam
from app.team_management.errors import TeamServiceError
from app.team_management.membership_service import TeamMembershipService
from app.team_management.models import TeamManagerAssignment, TeamMembership, TeamOperation
from app.team_management.schemas import JoinTeamInput, LeaveTeamInput, TransferTeamInput


def make_world(db):
    seed_roles_and_permissions(db)
    actors = {}
    for key, role in (
        ("admin", "super_admin"),
        ("admin2", "super_admin"),
        ("manager", "team_manager"),
        ("member", "team_member"),
        ("member2", "team_member"),
    ):
        person = Person(
            full_name=f"名单测试{key}",
            identity_ciphertext="encrypted",
            identity_digest=uuid4().hex + uuid4().hex,
            sex="male",
            date_of_birth=date(1995, 1, 1),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            password_hash="unused",
            status="active",
            force_password_change=False,
        )
        db.add(account)
        db.flush()
        db.add(UserRole(user_id=account.id, role_id=ROLES[role].id))
        actors[key] = AuthContext(account=account, person=person, session=None)
    projects = list(
        db.scalars(
            select(GameProject)
            .where(GameProject.is_team_division)
            .order_by(GameProject.id)
            .limit(2)
        )
    )
    assert len(projects) == 2
    teams = [
        ProfessionalTeam(
            project_id=p.id,
            name=f"名单队伍{uuid4().hex}",
            created_by_user_id=actors["admin"].account.id,
        )
        for p in projects
    ]
    db.add_all(teams)
    db.flush()
    db.commit()
    return actors, teams


@pytest.fixture
def world(db):
    return make_world(db)


def join(db, actor, team, member, key=None):
    return TeamMembershipService(db).join(
        actor, team.id, JoinTeamInput(person_id=member.person.id, idempotency_key=key or uuid4())
    )


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (TeamMembership, TeamOperation, AuditEvent)
    )


def test_approved_accountless_player_can_rejoin_but_generic_person_cannot(db, world):
    from app.personnel_intake.schemas import IntakeInput
    from app.team_management.query_service import TeamQueryService
    from tests.api.conftest import make_cn_id
    from tests.services.test_personnel_approval import approvals

    actors, teams = world
    data = IntakeInput(
        kind="player",
        full_name="无账号批准队员",
        identity_number=make_cn_id("20000102", 895),
        sex="male",
        date_of_birth=date(2000, 1, 2),
        phone="13900000895",
        email="player-no-account@example.com",
        team_id=teams[0].id,
    )
    result = approvals(db).direct_create(actors["admin"], data, uuid4())
    membership = db.scalar(
        select(TeamMembership).where(TeamMembership.person_id == result.person_id)
    )
    TeamMembershipService(db).leave(
        actors["admin"], membership.id, LeaveTeamInput(idempotency_key=uuid4())
    )
    candidates = TeamQueryService(db).candidates(
        actors["admin"], teams[1].id, kind="member", query="批准", cursor=None
    )
    assert [(row.person_id, row.account_id) for row in candidates.items] == [
        (result.person_id, None)
    ]
    assert (
        TeamQueryService(db)
        .candidates(actors["admin"], teams[1].id, kind="manager", query="批准", cursor=None)
        .items
        == []
    )
    TeamMembershipService(db).join(
        actors["admin"],
        teams[1].id,
        JoinTeamInput(person_id=result.person_id, idempotency_key=uuid4()),
    )
    generic = Person(
        full_name="普通无账号",
        identity_ciphertext="synthetic",
        identity_digest="f" * 64,
        sex="male",
        date_of_birth=date(2000, 1, 2),
    )
    db.add(generic)
    db.flush()
    with pytest.raises(TeamServiceError):
        TeamMembershipService(db).join(
            actors["admin"],
            teams[0].id,
            JoinTeamInput(person_id=generic.id, idempotency_key=uuid4()),
        )


def test_retry_returns_original_result_without_duplicate_audit(db, world):
    actors, teams = world
    key = uuid4()
    before = counts(db)
    first = join(db, actors["admin"], teams[0], actors["member"], key)
    second = join(db, actors["admin"], teams[0], actors["member"], key)
    assert first == second
    assert counts(db) == tuple(n + 1 for n in before)
    assert set(first.model_dump()) == {
        "operation_id",
        "membership_id",
        "closed_membership_id",
        "assignment_id",
    }
    operation = db.get(TeamOperation, first.operation_id)
    assert operation.result_ids == first.model_dump(mode="json")
    for team, member in ((teams[1], actors["member"]), (teams[0], actors["member2"])):
        with pytest.raises(TeamServiceError) as error:
            join(db, actors["admin"], team, member, key)
        assert error.value.code == "team_operation_conflict"
    with pytest.raises(TeamServiceError) as error:
        TeamMembershipService(db).leave(
            actors["admin"], first.membership_id, LeaveTeamInput(idempotency_key=key)
        )
    assert error.value.code == "team_operation_conflict"


@pytest.mark.parametrize("revoke", ["deny", "role", "disabled", "password", "deleted"])
def test_replay_rechecks_current_authority(db, world, revoke):
    actors, teams = world
    actor = actors["admin"]
    key = uuid4()
    join(db, actor, teams[0], actors["member"], key)
    if revoke == "deny":
        db.add(
            UserPermissionOverride(
                user_id=actor.account.id,
                permission_id=PERMISSIONS["team_memberships.manage"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    elif revoke == "role":
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == actor.account.id))
    elif revoke == "disabled":
        actor.account.status = "disabled"
    elif revoke == "password":
        actor.account.force_password_change = True
    else:
        actor.person.deleted_at = datetime.now(UTC)
    db.flush()
    before = counts(db)
    with pytest.raises(TeamServiceError) as error:
        join(db, actor, teams[0], actors["member"], key)
    assert error.value.status == 404
    assert counts(db) == before


def test_transfer_rollback_and_old_membership_rejection(db, world, monkeypatch):
    actors, teams = world
    svc = TeamMembershipService(db)
    first = join(db, actors["admin"], teams[0], actors["member"])
    before = counts(db)
    with pytest.raises(TeamServiceError) as error:
        svc.transfer(
            actors["admin"],
            first.membership_id,
            TransferTeamInput(
                target_team_id=teams[0].id, reason="安排调整", idempotency_key=uuid4()
            ),
        )
    assert error.value.code == "team_same_team"
    assert counts(db) == before
    original = AuditService.append

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        svc.transfer(
            actors["admin"],
            first.membership_id,
            TransferTeamInput(
                target_team_id=teams[1].id, reason="安排调整", idempotency_key=uuid4()
            ),
        )
    assert counts(db) == before
    assert db.get(TeamMembership, first.membership_id).ended_at is None
    monkeypatch.setattr(AuditService, "append", original)
    reason = "  私密的队伍调整说明  "
    request = TransferTeamInput(target_team_id=teams[1].id, reason=reason, idempotency_key=uuid4())
    moved = svc.transfer(actors["admin"], first.membership_id, request)
    assert moved.closed_membership_id == first.membership_id
    assert moved.membership_id != first.membership_id
    assert svc.transfer(actors["admin"], first.membership_id, request) == moved
    old = db.get(TeamMembership, first.membership_id)
    assert old.end_kind == "transferred" and old.transfer_reason == reason.strip()
    events = list(
        db.scalars(select(AuditEvent).where(AuditEvent.actor_user_id == actors["admin"].account.id))
    )
    assert all(
        event.purpose is None and "私密" not in str(event.event_metadata) for event in events
    )
    before = counts(db)
    with pytest.raises(TeamServiceError) as error:
        svc.leave(actors["admin"], first.membership_id, LeaveTeamInput(idempotency_key=uuid4()))
    assert error.value.status == 409
    assert db.get(TeamMembership, moved.membership_id).ended_at is None
    assert counts(db) == before


def test_same_day_leave_rejoin_and_disabled_leave(db, world):
    actors, teams = world
    svc = TeamMembershipService(db)
    member = actors["member"]
    first = join(db, actors["admin"], teams[0], member)
    member.account.status = "disabled"
    db.flush()
    request = LeaveTeamInput(idempotency_key=uuid4())
    left = svc.leave(actors["admin"], first.membership_id, request)
    assert left.closed_membership_id == first.membership_id
    assert svc.leave(actors["admin"], first.membership_id, request) == left
    member.account.status = "active"
    db.flush()
    second = join(db, actors["admin"], teams[0], member)
    assert first.membership_id != second.membership_id
    with pytest.raises(TeamServiceError) as error:
        svc.leave(actors["admin"], first.membership_id, LeaveTeamInput(idempotency_key=uuid4()))
    assert error.value.status == 409
    assert db.get(TeamMembership, second.membership_id).ended_at is None


@pytest.mark.parametrize(
    "utc, expected",
    [
        (datetime(2026, 9, 29, 15, 59, tzinfo=UTC), date(2026, 9, 29)),
        (datetime(2026, 9, 29, 16, 1, tzinfo=UTC), date(2026, 9, 30)),
    ],
)
def test_china_business_date_and_cross_midnight_replay(db, world, monkeypatch, utc, expected):
    from app.team_management import write_support

    actors, teams = world
    monkeypatch.setattr(write_support, "utc_now", lambda: utc)
    key = uuid4()
    first = join(db, actors["admin"], teams[0], actors["member"], key)
    assert db.get(TeamMembership, first.membership_id).joined_on == expected
    monkeypatch.setattr(write_support, "utc_now", lambda: utc + timedelta(days=1))
    assert join(db, actors["admin"], teams[0], actors["member"], key) == first


@pytest.mark.parametrize("invalid", ["disabled", "password", "deleted", "role", "team", "project"])
def test_new_join_rejects_ineligible_or_inactive_without_writes(db, world, invalid):
    actors, teams = world
    member = actors["member"]
    if invalid == "disabled":
        member.account.status = "disabled"
    elif invalid == "password":
        member.account.force_password_change = True
    elif invalid == "deleted":
        member.person.deleted_at = datetime.now(UTC)
    elif invalid == "role":
        db.execute(UserRole.__table__.delete().where(UserRole.user_id == member.account.id))
    elif invalid == "team":
        teams[0].is_active = False
    else:
        db.get(GameProject, teams[0].project_id).is_active = False
    db.flush()
    before = counts(db)
    with pytest.raises(TeamServiceError) as error:
        join(db, actors["admin"], teams[0], member)
    assert error.value.status == 409
    assert counts(db) == before


def test_manager_binding_grants_local_writes_never_transfer(db, world):
    actors, teams = world
    manager = actors["manager"]
    for team in teams:
        db.add(
            TeamManagerAssignment(
                account_id=manager.account.id,
                team_id=team.id,
                created_by_user_id=actors["admin"].account.id,
            )
        )
    db.flush()
    joined = join(db, manager, teams[0], actors["member"])
    with pytest.raises(TeamServiceError) as error:
        TeamMembershipService(db).transfer(
            manager,
            joined.membership_id,
            TransferTeamInput(
                target_team_id=teams[1].id, reason="管理调队", idempotency_key=uuid4()
            ),
        )
    assert error.value.status == 404
    TeamMembershipService(db).leave(
        manager, joined.membership_id, LeaveTeamInput(idempotency_key=uuid4())
    )


def test_fingerprint_canonicalization():
    from app.team_management.write_support import fingerprint

    id = uuid4()
    assert fingerprint("transfer", {"reason": "  调整  ", "id": id}) == fingerprint(
        "transfer", {"id": str(id), "reason": "调整"}
    )
    assert fingerprint("join", {"id": id}) != fingerprint("leave", {"id": id})


def test_wire_error_codes_and_private_object_uniformity():
    from app.team_management import errors

    assert errors.TeamMemberConflict.code == "team_already_assigned"
    assert errors.TeamCandidateIneligible.code == "team_member_ineligible"
    for error in (errors.TeamMemberNotFound, errors.TeamAssignmentNotFound):
        assert (error.code, error.status, str(error())) == (
            errors.TeamNotFound.code,
            404,
            str(errors.TeamNotFound()),
        )
    assert errors.TeamMembershipStale.code == "team_membership_stale"
    assert errors.TeamAssignmentConflict.code == "team_already_assigned"
    assert errors.TeamAssignmentStale.code == "team_assignment_stale"
    assert errors.TeamHasActiveRelationships.code == "team_has_active_relationships"


def test_closed_membership_replay_after_team_and_target_disabled(db, world):
    actors, teams = world
    actor, member = actors["admin"], actors["member"]
    join_key = uuid4()
    joined = join(db, actor, teams[0], member, join_key)
    leave = LeaveTeamInput(idempotency_key=uuid4())
    ended = TeamMembershipService(db).leave(actor, joined.membership_id, leave)
    teams[0].is_active = False
    db.get(GameProject, teams[0].project_id).is_team_division = False
    member.account.status = "disabled"
    db.flush()
    assert join(db, actor, teams[0], member, join_key) == joined
    assert TeamMembershipService(db).leave(actor, joined.membership_id, leave) == ended


def test_revoked_manager_binding_prevents_old_key_replay(db, world):
    actors, teams = world
    manager = actors["manager"]
    binding = TeamManagerAssignment(
        account_id=manager.account.id,
        team_id=teams[0].id,
        created_by_user_id=actors["admin"].account.id,
    )
    db.add(binding)
    db.flush()
    key = uuid4()
    join(db, manager, teams[0], actors["member"], key)
    binding.ended_at = datetime.now(UTC)
    binding.end_kind = "unassigned"
    db.flush()
    with pytest.raises(TeamServiceError) as error:
        join(db, manager, teams[0], actors["member"], key)
    assert error.value.status == 404 and error.value.code == "team_not_found"


def test_audit_ids_are_public_and_reason_is_rejected():
    from app.audit.service import AuditMetadataRejected

    metadata = {
        name: str(uuid4())
        for name in (
            "operation_id",
            "membership_id",
            "closed_membership_id",
            "assignment_id",
            "source_team_id",
            "target_team_id",
        )
    }
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({"transfer_reason": "私密调整"})
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({"membership_id": "不是UUID"})
