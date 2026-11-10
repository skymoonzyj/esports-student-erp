"""Exercise real activation and approval, including deferred team access."""

from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.personnel_intake.activation_models import AssistedActivationSession
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.identity_selection import ManagedIdentityInput, StaffTeamSelection
from app.personnel_intake.role_intent_models import IntakeRoleDecision
from app.personnel_intake.role_intents import RoleIntentApproval, RoleIntentService
from app.rbac.models import Role, UserRole
from app.team_management.models import TeamStaffAssignment
from tests.api.conftest import platform_users as _platform_users
from tests.api.test_intake_role_intents import administration
from tests.services.test_assisted_activation import activation
from tests.services.test_intake_activation_roles import committed_db as _committed_db
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_team_access import teams


@pytest.fixture
def committed_db(postgres_engine):
    yield from _committed_db.__wrapped__(postgres_engine)


def prepare(db):
    users = _platform_users.__wrapped__(db)
    first, _ = teams(db, users["accounts"]["admin"].id)
    administration(db, users)
    data = intake(users).model_copy(update={"kind": "staff", "coach_person_ids": []})
    result = approvals(db).direct_create(actor(users, "coach"), data, uuid4(),
        identity_selection=ManagedIdentityInput(primary_identity="coach"),
        staff_teams=[StaffTeamSelection(staff_role="coach", team_ids=[first.id])])
    db.commit()
    return users, result, first


def activate(db, person_id):
    service, sender = activation(db)
    token = service.start("intake@example.com", "127.0.9.92").session_token.get_secret_value()
    session = db.scalar(select(AssistedActivationSession).where(AssistedActivationSession.person_id == person_id))
    code = SecretStr(sender.code_for(session.challenge_id))
    service.verify(token, code)
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    return db.scalar(select(UserAccount).where(UserAccount.person_id == person_id))


def roles(db, account):
    return set(db.scalars(select(Role.key).join(UserRole).where(UserRole.user_id == account.id)))


@pytest.mark.parametrize("approve_before", [True, False])
def test_pending_identity_only_gets_team_access_after_root_approval(committed_db, approve_before):
    db = committed_db
    users, result, team = prepare(db)
    reviewer = RoleIntentService(db, lookup=approvals(db).lookup)
    command = RoleIntentApproval(expected_version=1, idempotency_key=uuid4())
    if not approve_before:
        account = activate(db, result.person_id)
        assert roles(db, account) == {"registered_user"}
        assert db.scalar(select(func.count()).select_from(TeamStaffAssignment).where(TeamStaffAssignment.account_id == account.id)) == 0
    assert reviewer.approve(actor(users, "admin"), result.person_id, command).status == "approved"
    if approve_before:
        account = activate(db, result.person_id)
    assert roles(db, account) == {"registered_user", "coach"}
    assigned = db.scalars(select(TeamStaffAssignment).where(TeamStaffAssignment.account_id == account.id)).all()
    assert [(a.team_id, a.staff_role) for a in assigned] == [(team.id, "coach")]
    assert reviewer.approve(actor(users, "admin"), result.person_id, command).status == "approved"
    assert db.scalar(select(func.count()).select_from(IntakeRoleDecision)) == 1


def test_approval_replay_never_restores_a_later_removed_role(committed_db):
    db = committed_db
    users, result, _ = prepare(db)
    account = activate(db, result.person_id)
    reviewer = RoleIntentService(db, lookup=approvals(db).lookup)
    command = RoleIntentApproval(expected_version=1, idempotency_key=uuid4())
    reviewer.approve(actor(users, "admin"), result.person_id, command)
    coach_id = db.scalar(select(Role.id).where(Role.key == "coach"))
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == account.id, UserRole.role_id == coach_id))
    db.commit()
    reviewer.approve(actor(users, "admin"), result.person_id, command)
    assert roles(db, account) == {"registered_user"}


def test_stale_or_unauthorized_approval_keeps_intent_pending(committed_db):
    db = committed_db
    users, result, _ = prepare(db)
    reviewer = RoleIntentService(db, lookup=approvals(db).lookup)
    for who, version, status in [("coach", 1, 403), ("admin", 2, 409)]:
        with pytest.raises(PersonnelIntakeError) as error:
            reviewer.approve(actor(users, who), result.person_id,
                RoleIntentApproval(expected_version=version, idempotency_key=uuid4()))
        assert error.value.status == status
    assert reviewer.get(actor(users, "coach"), result.person_id).status == "pending"


def test_target_read_deny_hides_queue_and_blocks_approval(committed_db):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    db = committed_db
    users, result, _ = prepare(db)
    db.add(UserPermissionOverride(user_id=users["accounts"]["admin"].id,
        permission_id=PERMISSIONS["people.read"].id, effect="deny", scope_kind="person", scope_id=result.person_id))
    db.commit()
    reviewer = RoleIntentService(db, lookup=approvals(db).lookup)
    assert reviewer.list(actor(users, "admin")).total == 0
    with pytest.raises(PersonnelIntakeError) as error:
        reviewer.approve(actor(users, "admin"), result.person_id,
            RoleIntentApproval(expected_version=1, idempotency_key=uuid4()))
    assert error.value.status == 403


def test_late_team_failure_rolls_back_role_grant_and_decision(committed_db, monkeypatch):
    from app.team_management import staff_service
    db = committed_db
    users, result, _ = prepare(db)
    account = activate(db, result.person_id)
    reviewer = RoleIntentService(db, lookup=approvals(db).lookup)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic late assignment failure")
    monkeypatch.setattr(staff_service, "_assign_staff_in_transaction", fail)
    with pytest.raises(RuntimeError, match="synthetic late"):
        reviewer.approve(actor(users, "admin"), result.person_id,
            RoleIntentApproval(expected_version=1, idempotency_key=uuid4()))
    assert roles(db, account) == {"registered_user"}
    assert reviewer.get(actor(users, "coach"), result.person_id).status == "pending"


def test_concurrent_approval_and_activation_preserve_single_account_and_assignment(committed_db, postgres_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.auth.service import AuthContext
    from app.identity.models import Person

    db = committed_db
    users, result, _ = prepare(db)
    reviewer_id = users["accounts"]["admin"].id
    reviewer_person_id = users["accounts"]["admin"].person_id
    service, sender = activation(db)
    token = service.start("intake@example.com", "127.0.9.93").session_token.get_secret_value()
    session = db.scalar(select(AssistedActivationSession).where(AssistedActivationSession.person_id == result.person_id))
    code = SecretStr(sender.code_for(session.challenge_id))
    service.verify(token, code)
    db.commit()
    start = Barrier(2)
    command = RoleIntentApproval(expected_version=1, idempotency_key=uuid4())
    def worker(approve):
        with Session(postgres_engine) as other:
            other.execute(text("SET lock_timeout='8s'"))
            other.commit()
            start.wait(timeout=5)
            try:
                if approve:
                    who = AuthContext(account=other.get(UserAccount, reviewer_id), person=other.get(Person, reviewer_person_id), session=None)
                    RoleIntentService(other, lookup=approvals(other).lookup).approve(who, result.person_id, command)
                else:
                    activation(other)[0].finish(token, code, SecretStr("SecurePass1"))
                return "done"
            except PersonnelIntakeError as error:
                assert error.status == 409
                return "retry"
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(worker, True), pool.submit(worker, False)
        outcomes = (first.result(timeout=20), second.result(timeout=20))
    db.expire_all()
    if outcomes[0] == "retry":
        RoleIntentService(db, lookup=approvals(db).lookup).approve(actor(users, "admin"), result.person_id, command)
    if outcomes[1] == "retry":
        assert activation(db)[0].finish(token, code, SecretStr("SecurePass1")).completed
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    assert roles(db, account) == {"registered_user", "coach"}
    assert db.scalar(select(func.count()).select_from(IntakeRoleDecision)) == 1
    assert db.scalar(select(func.count()).select_from(TeamStaffAssignment).where(TeamStaffAssignment.account_id == account.id)) == 1


def test_historical_plan_is_not_regranted_by_partial_active_application(committed_db):
    from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
    from tests.services.test_intake_activation_roles import prepared_activation
    db = committed_db
    _, result, service, token, code, _ = prepared_activation(db, ["coach", "life_teacher"])
    assert service.finish(token, code, SecretStr("SecurePass1")).completed
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    teacher = db.scalar(select(Role.id).where(Role.key == "life_teacher"))
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == account.id, UserRole.role_id == teacher))
    ActivationRoleService(db, lookup=approvals(db).lookup).apply_in_transaction(
        latest_plan(db, result.person_id), account, only_role_keys={"coach"})
    assert roles(db, account) == {"student", "coach"}


@pytest.mark.parametrize("permission", ["people.read", "personnel_intake.review"])
def test_team_scope_deny_hides_details_and_blocks_direct_approval(committed_db, permission):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    db = committed_db
    users = _platform_users.__wrapped__(db)
    first, _ = teams(db, users["accounts"]["admin"].id)
    administration(db, users)
    result = approvals(db).direct_create(actor(users, "coach"),
        intake(users).model_copy(update={"kind": "player", "coach_person_ids": [], "team_id": first.id}),
        uuid4(), identity_selection=ManagedIdentityInput(primary_identity="team_member", concurrent_identities=["coach"]))
    db.add(UserPermissionOverride(user_id=users["accounts"]["admin"].id,
        permission_id=PERMISSIONS[permission].id, effect="deny", scope_kind="assigned_team", scope_id=first.id))
    db.commit()
    reviewer = RoleIntentService(db, lookup=approvals(db).lookup)
    assert reviewer.list(actor(users, "admin")).total == 0
    with pytest.raises(PersonnelIntakeError) as detail_error:
        reviewer.get(actor(users, "admin"), result.person_id)
    assert detail_error.value.status == 403
    with pytest.raises(PersonnelIntakeError) as error:
        reviewer.approve(actor(users, "admin"), result.person_id,
            RoleIntentApproval(expected_version=1, idempotency_key=uuid4()))
    assert error.value.status == 403
