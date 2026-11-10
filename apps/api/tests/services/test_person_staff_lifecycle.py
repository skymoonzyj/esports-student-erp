from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.auth.models import UserAccount
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.role_grants import ActivationRoleService, latest_plan
from app.personnel_intake.schemas import ActivationRoleUpdate
from app.rbac.seed import ROLES
from app.rbac.service import RbacService
from app.team_management.lifecycle import TeamLifecycleService
from app.team_management.models import TeamStaffAssignment
from app.team_management.person_staff_models import PersonTeamStaffPosition
from tests.services.test_intake_activation_roles import committed_db as _committed_db
from tests.services.test_intake_staff_activation import prepared_staff
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor


@pytest.fixture
def committed_db(postgres_engine):
    yield from _committed_db.__wrapped__(postgres_engine)


def test_role_plan_removal_requires_explicit_confirmation_and_is_atomic(committed_db):
    db = committed_db
    users, result, _, _, _, _, _, _ = prepared_staff(db)
    service = ActivationRoleService(db, lookup=approvals(db).lookup)
    command = ActivationRoleUpdate(role_keys=["team_manager"], expected_version=1,
                                   idempotency_key=uuid4())
    with pytest.raises(PersonnelIntakeError) as error:
        service.update(actor(users, "admin"), result.person_id, command)
    assert error.value.code == "activation_roles_staff_positions_confirmation_required"
    assert latest_plan(db, result.person_id).version == 1
    assert all(p.ended_at is None for p in db.scalars(select(PersonTeamStaffPosition)))
    confirmed = ActivationRoleUpdate.model_validate({**command.model_dump(),
        "confirm_end_staff_positions": True, "idempotency_key": uuid4()})
    view = service.update(actor(users, "admin"), result.person_id, confirmed)
    assert view.version == 2
    assert service.update(actor(users, "admin"), result.person_id, confirmed) == view
    for position in db.scalars(select(PersonTeamStaffPosition)):
        assert (position.ended_at is not None) is (position.staff_role == "coach")


def test_role_removal_ends_person_and_account_sources_without_regrant_revival(committed_db):
    db = committed_db
    users, result, _, _, service, token, code, _ = prepared_staff(db)
    service.finish(token, code, SecretStr("SecurePass1"))
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    rbac = RbacService(db)
    rbac.remove_role(actor_user_id=users["accounts"]["admin"].id, account_id=account.id,
                     role_id=ROLES["coach"].id, request_id="synthetic-person-role-removal")
    positions = list(db.scalars(select(PersonTeamStaffPosition)))
    for position in positions:
        ended = position.staff_role == "coach"
        assert (position.ended_at is not None) is ended
        assert (db.get(TeamStaffAssignment, position.account_assignment_id).ended_at is not None) is ended
    rbac.assign_role(actor_user_id=users["accounts"]["admin"].id,
                     account_id=account.id, role_id=ROLES["coach"].id)
    assert sum(p.ended_at is not None for p in positions) == 2


def test_unactivated_positions_block_team_and_project_clear(committed_db):
    from app.team_management.errors import TeamHasActiveRelationships
    db = committed_db
    _, _, first, _, _, _, _, _ = prepared_staff(db)
    service = TeamLifecycleService(db)
    with pytest.raises(TeamHasActiveRelationships):
        service.assert_team_clear(first.id)
    with pytest.raises(TeamHasActiveRelationships):
        service.assert_project_clear(first.project_id)


def test_missing_lifecycle_actor_fails_closed_for_personnel_sources(committed_db):
    db = committed_db
    _, result, _, _, service, token, code, _ = prepared_staff(db)
    service.finish(token, code, SecretStr("SecurePass1"))
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    with pytest.raises(ValueError):
        TeamLifecycleService(db).before_role_removal(account.id, "coach", None, datetime.now(UTC))
    assert all(p.ended_at is None for p in db.scalars(select(PersonTeamStaffPosition)))
    assert all(p.ended_at is None for p in db.scalars(select(TeamStaffAssignment)))


def test_disable_preserves_personnel_history_but_delete_closes_every_source(committed_db):
    from app.auth.service import AuthContext
    from app.identity.models import Person
    from app.team_management.access import TeamAccessService
    from app.team_management.errors import TeamAccessDenied
    from tests.services.test_team_lifecycle import accounts
    db = committed_db
    users, result, first, _, service, token, code, _ = prepared_staff(db)
    service.finish(token, code, SecretStr("SecurePass1"))
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    account_service = accounts.__wrapped__(db)
    manager_id = users["accounts"]["admin"].id
    account_service.disable_account(account.id, actor_user_id=manager_id, request_id="synthetic-disable")
    assert all(p.ended_at is None for p in db.scalars(select(PersonTeamStaffPosition)))
    assert all(p.ended_at is None for p in db.scalars(select(TeamStaffAssignment)))
    who = AuthContext(account=account, person=db.get(Person, result.person_id), session=None)
    with pytest.raises(TeamAccessDenied):
        TeamAccessService(db).require_team(who, first.id, "read")
    account_service.enable_account(account.id, actor_user_id=manager_id)
    TeamAccessService(db).require_team(who, first.id, "read")
    account_service.delete_account(account.id, actor_user_id=manager_id, request_id="synthetic-delete")
    for position in db.scalars(select(PersonTeamStaffPosition)):
        assignment = db.get(TeamStaffAssignment, position.account_assignment_id)
        assert position.ended_at is not None and assignment.ended_at == position.ended_at
        assert position.ended_by_user_id == assignment.ended_by_user_id == manager_id


@pytest.mark.parametrize("keep", [True, False])
def test_job_change_closes_exact_personnel_roles(committed_db, keep):
    from app.rbac.models import UserRole
    from app.rbac.schemas import JobRoleChangeRequest
    db = committed_db
    users, result, _, _, service, token, code, _ = prepared_staff(db)
    service.finish(token, code, SecretStr("SecurePass1"))
    account = db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id))
    expected = list(db.scalars(select(UserRole.role_id).where(UserRole.user_id == account.id)))
    RbacService(db).change_job_role(actor_user_id=users["accounts"]["admin"].id,
        account_id=account.id, input=JobRoleChangeRequest(from_role_id=ROLES["coach"].id,
            to_role_id=ROLES["life_teacher"].id, keep_concurrent_roles=keep,
            expected_role_ids=expected), request_id="synthetic-person-job-change")
    for position in db.scalars(select(PersonTeamStaffPosition)):
        assert (position.ended_at is None) is (keep and position.staff_role == "manager")


def test_role_plan_confirmation_requires_each_team_permission(committed_db):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    db = committed_db
    users, result, _, _, _, _, _, _ = prepared_staff(db)
    db.add(UserPermissionOverride(user_id=users["accounts"]["admin"].id,
        permission_id=PERMISSIONS["team_manager_assignments.manage"].id,
        effect="deny", scope_kind="global"))
    db.commit()
    service = ActivationRoleService(db, lookup=approvals(db).lookup)
    with pytest.raises(PersonnelIntakeError) as error:
        service.update(actor(users, "admin"), result.person_id,
            ActivationRoleUpdate(role_keys=[], expected_version=1, idempotency_key=uuid4(),
                                 confirm_end_staff_positions=True))
    assert error.value.status == 403
    assert latest_plan(db, result.person_id).version == 1
    assert all(p.ended_at is None for p in db.scalars(select(PersonTeamStaffPosition)))
